@echo off
title OrbitCRM - Local Server
echo ========================================================
echo Starting OrbitCRM connected to MySQL Server (orbitcrm)...
echo ========================================================
echo.

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    start http://127.0.0.1:5000
    ".venv\Scripts\python.exe" app.py
) else (
    start http://127.0.0.1:5000
    python app.py
)

pause
