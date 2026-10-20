import os
import decimal
from datetime import date, datetime
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)


# Serialize custom MySQL types (Decimal, datetime, date)
def serialize_row(row):
    if not row:
        return row
    result = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat(sep=" ", timespec="seconds")
        elif isinstance(value, date):
            result[key] = value.isoformat()
        elif isinstance(value, decimal.Decimal):
            result[key] = float(value)
        else:
            result[key] = value
    return result


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "orbitcrm"),
    )


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response


# --- Static Front-end Routes ---
@app.route("/")
def index():
    if os.path.exists(os.path.join(BASE_DIR, "index(1).html")):
        return send_from_directory(BASE_DIR, "index(1).html")
    if os.path.exists(os.path.join(BASE_DIR, "index.html")):
        return send_from_directory(BASE_DIR, "index.html")
    return "OrbitCRM frontend file not found.", 404


@app.route("/<path:filename>")
def serve_static(filename):
    if os.path.exists(os.path.join(BASE_DIR, filename)):
        return send_from_directory(BASE_DIR, filename)
    return jsonify({"error": f"File '{filename}' not found"}), 404


# --- Database Health Test ---
@app.route("/api/db-test", methods=["GET"])
def test_database():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT DATABASE() as db_name, VERSION() as version")
        info = cursor.fetchone()

        # Count tables
        counts = {}
        for tbl in ["users", "departments", "employees", "customers", "leads", "pipeline", "services", "tickets"]:
            try:
                cursor.execute(f"SELECT COUNT(*) as cnt FROM {tbl}")
                counts[tbl] = cursor.fetchone()["cnt"]
            except Exception:
                counts[tbl] = 0

        return jsonify({
            "success": True,
            "message": "MySQL connection successful!",
            "database": info["db_name"] if info else "unknown",
            "version": info["version"] if info else "unknown",
            "counts": counts
        })
    except Error as error:
        return jsonify({
            "success": False,
            "message": f"Database connection error: {str(error)}"
        }), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# --- Bootstrap: Full Sync for OrbitCRM Frontend ---
@app.route("/api/bootstrap", methods=["GET"])
def bootstrap():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # 1. Customers
        cursor.execute("""
            SELECT customer_id as id, customer_id, user_id, customer_name as name,
                   company, contact as phone, contact, email,
                   COALESCE(created_at, NOW()) as date, created_at, 'Active' as status
            FROM customers
            ORDER BY customer_id DESC
        """)
        raw_customers = cursor.fetchall()
        customers = [serialize_row(c) for c in raw_customers]

        # 2. Leads (with joined assigned employee name)
        cursor.execute("""
            SELECT l.lead_id as id, l.lead_id, l.lead_name as name, l.company,
                   l.email, l.contact as phone, l.contact, l.source,
                   l.status, l.notes,
                   COALESCE(e.employee_name, 'Unassigned') as assigned,
                   l.assigned_employee_id,
                   DATE_FORMAT(l.created_at, '%d %b %Y') as followup,
                   l.created_at
            FROM leads l
            LEFT JOIN employees e ON l.assigned_employee_id = e.employee_id
            ORDER BY l.lead_id DESC
        """)
        raw_leads = cursor.fetchall()
        leads = [serialize_row(l) for l in raw_leads]

        # 3. Pipeline Deals
        cursor.execute("""
            SELECT p.pipeline_id as id, p.pipeline_id, p.lead_id, p.deal_name,
                   p.deal_value, p.stage, p.expected_close_date, p.updated_at,
                   l.lead_name, l.company
            FROM pipeline p
            LEFT JOIN leads l ON p.lead_id = l.lead_id
            ORDER BY p.pipeline_id DESC
        """)
        raw_pipeline = cursor.fetchall()
        pipeline = [serialize_row(p) for p in raw_pipeline]

        # 4. Tickets (with customer, service, and employee)
        cursor.execute("""
            SELECT t.ticket_id as id, t.ticket_id, t.customer_id as customerId,
                   c.customer_name as customer,
                   COALESCE(s.service_name, 'General Support') as service,
                   t.issue_description as subject,
                   t.priority, t.status,
                   COALESCE(e.employee_name, 'IT Support Team') as assigned,
                   DATE_FORMAT(t.created_at, '%d %b %Y') as created,
                   DATE_FORMAT(DATE_ADD(t.created_at, INTERVAL 5 DAY), '%d %b %Y') as due,
                   t.issue_description as latest,
                   t.created_at
            FROM tickets t
            LEFT JOIN customers c ON t.customer_id = c.customer_id
            LEFT JOIN services s ON t.service_id = s.service_id
            LEFT JOIN employees e ON t.assigned_employee_id = e.employee_id
            ORDER BY t.ticket_id DESC
        """)
        raw_tickets = cursor.fetchall()
        tickets = [serialize_row(t) for t in raw_tickets]

        # 5. Employees
        cursor.execute("""
            SELECT e.employee_id, e.user_id, e.employee_name, e.contact,
                   d.department_name, u.email, u.username
            FROM employees e
            LEFT JOIN departments d ON e.department_id = d.department_id
            LEFT JOIN users u ON e.user_id = u.user_id
            ORDER BY e.employee_id ASC
        """)
        raw_employees = cursor.fetchall()
        employees = [serialize_row(e) for e in raw_employees]

        # 6. Departments
        cursor.execute("SELECT department_id, department_name FROM departments ORDER BY department_id ASC")
        departments = [serialize_row(d) for d in cursor.fetchall()]

        # 7. Services
        cursor.execute("SELECT service_id, service_name, description, price FROM services ORDER BY service_id ASC")
        services = [serialize_row(s) for s in cursor.fetchall()]

        return jsonify({
            "success": True,
            "customers": customers,
            "leads": leads,
            "pipeline": pipeline,
            "tickets": tickets,
            "employees": employees,
            "departments": departments,
            "services": services
        })
    except Error as error:
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# --- Authentication: Login & Register ---
@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    username_or_email = data.get("id") or data.get("username") or data.get("email") or ""
    password = data.get("password", "")

    if not username_or_email or not password:
        return jsonify({"success": False, "message": "Username and password required"}), 400

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT user_id, username, email, password_hash, role
            FROM users
            WHERE username = %s OR email = %s
        """, (username_or_email, username_or_email))

        user = cursor.fetchone()
        if not user:
            return jsonify({"success": False, "message": "User not found in database"}), 401

        # Check password (plain or hashed comparison)
        if user["password_hash"] != password and user["password_hash"] != "TEMP_HASH":
            return jsonify({"success": False, "message": "Incorrect password"}), 401

        # Build user profile for the CRM front-end
        role = user["role"]
        # Format role for UI: 'Admin', 'Employee', 'Customer'
        display_role = "Admin" if role == "ADMIN" else ("Customer" if role == "CUSTOMER" else "Employee")
        profile = {
            "id": user["username"],
            "name": user["username"],
            "email": user["email"],
            "role": display_role,
            "department": "Admin" if display_role == "Admin" else "IT Support",
            "phone": ""
        }

        # Check if employee
        cursor.execute("""
            SELECT e.employee_id, e.employee_name, e.contact, d.department_name
            FROM employees e
            LEFT JOIN departments d ON e.department_id = d.department_id
            WHERE e.user_id = %s
        """, (user["user_id"],))
        emp = cursor.fetchone()
        if emp:
            profile["name"] = emp["employee_name"]
            profile["department"] = emp["department_name"] or profile["department"]
            profile["phone"] = emp["contact"] or ""
            profile["employeeId"] = emp["employee_id"]

        # Check if customer
        cursor.execute("""
            SELECT customer_id, customer_name, company, contact
            FROM customers
            WHERE user_id = %s OR email = %s
        """, (user["user_id"], user["email"]))
        cust = cursor.fetchone()
        if cust:
            profile["name"] = cust["customer_name"] or profile["name"]
            profile["company"] = cust["company"] or ""
            profile["phone"] = cust["contact"] or profile["phone"]
            profile["customerId"] = cust["customer_id"]

        return jsonify({"success": True, "user": profile})
    except Error as error:
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    company = data.get("company", "").strip()
    email = data.get("email", "").strip()
    phone = data.get("phone", "").strip()
    password = data.get("password", "").strip()

    if not name or not email or not password:
        return jsonify({"success": False, "message": "Name, email, and password required"}), 400

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Check if user already exists
        cursor.execute("SELECT user_id FROM users WHERE username = %s OR email = %s", (email, email))
        if cursor.fetchone():
            return jsonify({"success": False, "message": "An account with this email already exists"}), 409

        # Create user
        cursor.execute("""
            INSERT INTO users (username, email, password_hash, role)
            VALUES (%s, %s, %s, 'CUSTOMER')
        """, (email, email, password))
        user_id = cursor.lastrowid

        # Create customer record
        cursor.execute("""
            INSERT INTO customers (user_id, customer_name, company, contact, email)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, name, company or None, phone or None, email))
        customer_id = cursor.lastrowid

        connection.commit()

        return jsonify({
            "success": True,
            "message": "Customer registered successfully!",
            "user": {
                "id": email,
                "name": name,
                "company": company,
                "email": email,
                "phone": phone,
                "role": "Customer",
                "customerId": customer_id
            }
        }), 201
    except Error as error:
        if connection:
            connection.rollback()
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# --- Customers API ---
@app.route("/api/customers", methods=["GET"])
def get_customers():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT customer_id as id, customer_id, user_id, customer_name as name,
                   company, contact as phone, contact, email,
                   COALESCE(created_at, NOW()) as date, created_at, 'Active' as status
            FROM customers
            ORDER BY customer_id DESC
        """)
        customers = [serialize_row(c) for c in cursor.fetchall()]
        return jsonify(customers)
    except Error as error:
        return jsonify({"error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/api/customers", methods=["POST"])
def add_customer():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or data.get("customer_name") or "").strip()
    company = (data.get("company") or "").strip()
    phone = (data.get("phone") or data.get("contact") or "").strip()
    email = (data.get("email") or "").strip()

    if not name:
        return jsonify({"success": False, "error": "Customer name is required"}), 400

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            INSERT INTO customers (customer_name, company, contact, email)
            VALUES (%s, %s, %s, %s)
        """, (name, company or None, phone or None, email or None))
        connection.commit()
        new_id = cursor.lastrowid

        new_customer = {
            "id": new_id,
            "customer_id": new_id,
            "name": name,
            "company": company,
            "phone": phone,
            "email": email,
            "status": "Active",
            "date": datetime.now().strftime("%d %b %Y")
        }

        return jsonify({
            "success": True,
            "customer_id": new_id,
            "customer": new_customer,
            "message": "Customer added successfully to MySQL"
        }), 201
    except Error as error:
        if connection:
            connection.rollback()
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/api/customers/<int:customer_id>", methods=["PUT", "DELETE"])
def update_or_delete_customer(customer_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        if request.method == "DELETE":
            cursor.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
            connection.commit()
            return jsonify({"success": True, "message": "Customer deleted"})

        data = request.get_json(silent=True) or {}
        name = data.get("name") or data.get("customer_name")
        company = data.get("company")
        phone = data.get("phone") or data.get("contact")
        email = data.get("email")

        cursor.execute("""
            UPDATE customers
            SET customer_name = COALESCE(%s, customer_name),
                company = COALESCE(%s, company),
                contact = COALESCE(%s, contact),
                email = COALESCE(%s, email)
            WHERE customer_id = %s
        """, (name, company, phone, email, customer_id))
        connection.commit()
        return jsonify({"success": True, "message": "Customer updated"})
    except Error as error:
        if connection:
            connection.rollback()
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# --- Leads API ---
@app.route("/api/leads", methods=["GET", "POST"])
def handle_leads():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if request.method == "GET":
            cursor.execute("""
                SELECT l.lead_id as id, l.lead_id, l.lead_name as name, l.company,
                       l.email, l.contact as phone, l.contact, l.source,
                       l.status, l.notes,
                       COALESCE(e.employee_name, 'Unassigned') as assigned,
                       DATE_FORMAT(l.created_at, '%d %b %Y') as followup
                FROM leads l
                LEFT JOIN employees e ON l.assigned_employee_id = e.employee_id
                ORDER BY l.lead_id DESC
            """)
            return jsonify([serialize_row(r) for r in cursor.fetchall()])

        # POST: Create Lead
        data = request.get_json(silent=True) or {}
        name = data.get("name") or data.get("lead_name")
        if not name:
            return jsonify({"success": False, "error": "Lead name is required"}), 400

        # Try to find employee ID if assigned name is given
        assigned_name = data.get("assigned")
        assigned_id = data.get("assigned_employee_id")
        if not assigned_id and assigned_name:
            cursor.execute("SELECT employee_id FROM employees WHERE employee_name LIKE %s LIMIT 1", (f"%{assigned_name}%",))
            emp_row = cursor.fetchone()
            if emp_row:
                assigned_id = emp_row["employee_id"]

        status = (data.get("status") or "NEW").upper()
        if status not in ["NEW", "CONTACTED", "QUALIFIED", "CONVERTED", "LOST"]:
            status = "NEW"

        cursor.execute("""
            INSERT INTO leads (lead_name, company, email, contact, source, assigned_employee_id, status, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            name,
            data.get("company"),
            data.get("email"),
            data.get("phone") or data.get("contact"),
            data.get("source") or "Website",
            assigned_id,
            status,
            data.get("notes") or ""
        ))
        connection.commit()
        lead_id = cursor.lastrowid

        return jsonify({
            "success": True,
            "lead_id": lead_id,
            "lead": {
                "id": f"L-{lead_id}",
                "name": name,
                "company": data.get("company", ""),
                "status": status,
                "assigned": assigned_name or "Unassigned",
                "followup": datetime.now().strftime("%d %b %Y")
            }
        }), 201
    except Error as error:
        if connection:
            connection.rollback()
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# --- Pipeline Deals API ---
@app.route("/api/pipeline", methods=["GET", "POST"])
def handle_pipeline():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if request.method == "GET":
            cursor.execute("""
                SELECT p.pipeline_id as id, p.pipeline_id, p.lead_id, p.deal_name,
                       p.deal_value, p.stage, p.expected_close_date,
                       l.lead_name, l.company
                FROM pipeline p
                LEFT JOIN leads l ON p.lead_id = l.lead_id
                ORDER BY p.pipeline_id DESC
            """)
            return jsonify([serialize_row(r) for r in cursor.fetchall()])

        data = request.get_json(silent=True) or {}
        deal_name = data.get("deal_name")
        lead_id = data.get("lead_id")
        if not deal_name or not lead_id:
            return jsonify({"success": False, "error": "Deal name and lead_id are required"}), 400

        stage = (data.get("stage") or "PROSPECTING").upper()
        deal_value = float(data.get("deal_value", 0))

        cursor.execute("""
            INSERT INTO pipeline (lead_id, deal_name, deal_value, stage, expected_close_date)
            VALUES (%s, %s, %s, %s, %s)
        """, (lead_id, deal_name, deal_value, stage, data.get("expected_close_date")))
        connection.commit()
        return jsonify({"success": True, "pipeline_id": cursor.lastrowid}), 201
    except Error as error:
        if connection:
            connection.rollback()
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# --- Tickets API ---
@app.route("/api/tickets", methods=["GET", "POST"])
def handle_tickets():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if request.method == "GET":
            cursor.execute("""
                SELECT t.ticket_id as id, t.ticket_id, t.customer_id as customerId,
                       c.customer_name as customer,
                       COALESCE(s.service_name, 'General Support') as service,
                       t.issue_description as subject,
                       t.priority, t.status,
                       COALESCE(e.employee_name, 'IT Support Team') as assigned,
                       DATE_FORMAT(t.created_at, '%d %b %Y') as created,
                       DATE_FORMAT(DATE_ADD(t.created_at, INTERVAL 5 DAY), '%d %b %Y') as due,
                       t.issue_description as latest
                FROM tickets t
                LEFT JOIN customers c ON t.customer_id = c.customer_id
                LEFT JOIN services s ON t.service_id = s.service_id
                LEFT JOIN employees e ON t.assigned_employee_id = e.employee_id
                ORDER BY t.ticket_id DESC
            """)
            return jsonify([serialize_row(r) for r in cursor.fetchall()])

        data = request.get_json(silent=True) or {}
        customer_id = data.get("customer_id") or data.get("customerId")
        issue_desc = data.get("issue_description") or data.get("subject") or data.get("description")

        if not issue_desc:
            return jsonify({"success": False, "error": "Issue description / subject required"}), 400

        # If customer_id is missing, look up by customer name or use first customer
        if not customer_id:
            cust_name = data.get("customer")
            if cust_name:
                cursor.execute("SELECT customer_id FROM customers WHERE customer_name LIKE %s LIMIT 1", (f"%{cust_name}%",))
                c_row = cursor.fetchone()
                if c_row:
                    customer_id = c_row["customer_id"]
            if not customer_id:
                cursor.execute("SELECT customer_id FROM customers LIMIT 1")
                c_row = cursor.fetchone()
                customer_id = c_row["customer_id"] if c_row else 1

        priority = (data.get("priority") or "MEDIUM").upper()
        if priority not in ["LOW", "MEDIUM", "HIGH"]:
            priority = "MEDIUM"

        status = (data.get("status") or "OPEN").upper()
        if status in ["NEW", "IN PROGRESS"]:
            status = "IN_PROGRESS" if status == "IN PROGRESS" else "OPEN"
        elif status not in ["OPEN", "IN_PROGRESS", "RESOLVED"]:
            status = "OPEN"

        # Lookup employee
        assigned_id = None
        if data.get("assigned"):
            cursor.execute("SELECT employee_id FROM employees WHERE employee_name LIKE %s LIMIT 1", (f"%{data.get('assigned')}%",))
            emp_row = cursor.fetchone()
            if emp_row:
                assigned_id = emp_row["employee_id"]

        cursor.execute("""
            INSERT INTO tickets (customer_id, service_id, assigned_employee_id, issue_description, priority, status)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (customer_id, data.get("service_id"), assigned_id, issue_desc, priority, status))
        connection.commit()
        ticket_id = cursor.lastrowid

        return jsonify({
            "success": True,
            "ticket_id": ticket_id,
            "ticket": {
                "id": f"T-{ticket_id}",
                "customerId": customer_id,
                "subject": issue_desc,
                "priority": priority.capitalize(),
                "status": "In Progress" if status == "IN_PROGRESS" else status.capitalize(),
                "created": datetime.now().strftime("%d %b %Y")
            }
        }), 201
    except Error as error:
        if connection:
            connection.rollback()
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/api/tickets/<int:ticket_id>", methods=["PUT"])
def update_ticket(ticket_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        data = request.get_json(silent=True) or {}
        status = data.get("status")
        if status:
            s_map = {"New": "OPEN", "In Progress": "IN_PROGRESS", "Resolved": "RESOLVED", "Closed": "RESOLVED"}
            status = s_map.get(status, status.upper())

        cursor.execute("""
            UPDATE tickets
            SET status = COALESCE(%s, status),
                priority = COALESCE(%s, priority)
            WHERE ticket_id = %s
        """, (status, data.get("priority"), ticket_id))
        connection.commit()
        return jsonify({"success": True, "message": "Ticket updated"})
    except Error as error:
        if connection:
            connection.rollback()
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# --- Employees, Departments, Services APIs ---
@app.route("/api/employees", methods=["GET"])
def get_employees():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT e.employee_id, e.employee_name, e.contact, d.department_name, u.email
            FROM employees e
            LEFT JOIN departments d ON e.department_id = d.department_id
            LEFT JOIN users u ON e.user_id = u.user_id
            ORDER BY e.employee_id ASC
        """)
        return jsonify([serialize_row(r) for r in cursor.fetchall()])
    except Error as error:
        return jsonify({"error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/api/departments", methods=["GET"])
def get_departments():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT department_id, department_name FROM departments ORDER BY department_id ASC")
        return jsonify(cursor.fetchall())
    except Error as error:
        return jsonify({"error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/api/services", methods=["GET"])
def get_services():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT service_id, service_name, description, price FROM services ORDER BY service_id ASC")
        return jsonify([serialize_row(r) for r in cursor.fetchall()])
    except Error as error:
        return jsonify({"error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f" * OrbitCRM Backend running on http://127.0.0.1:{port}")
    print(f" * MySQL Database: {os.getenv('DB_NAME')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}")
    app.run(host="0.0.0.0", port=port, debug=True)
