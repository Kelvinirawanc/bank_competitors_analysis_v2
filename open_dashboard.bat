@echo off
setlocal
cd /d "%~dp0"

title Allo Bank Competitive Intelligence Dashboard

echo Starting dashboard + local scraper controller...
start "" /b py dashboard_server.py
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8000

echo.
echo Dashboard opened at http://127.0.0.1:8000
endlocal
