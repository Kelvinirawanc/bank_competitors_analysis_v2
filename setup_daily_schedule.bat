@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo Allo Bank Competitive Intelligence - Daily Schedule
echo Target: 00:00 GMT+7 / Asia-Jakarta
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_daily_schedule.ps1"

if errorlevel 1 (
    echo.
    echo ERROR: Scheduled task installation failed.
    pause
    exit /b 1
)

echo.
echo Schedule installed.
pause
endlocal
