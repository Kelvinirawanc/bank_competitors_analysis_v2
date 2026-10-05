@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo Allo Bank Competitive Intelligence - Daily Google Play Run
echo Scheduled target: 00:00 GMT+7 / Asia-Jakarta
echo ============================================================
echo.

py -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Dependency installation failed.
    pause
    exit /b 1
)

echo Starting full public Google Play review collection...
echo This may take a long time because ALL review pages are collected.
echo.

py scrape_google_play.py --lang id --country id

if errorlevel 1 (
    echo.
    echo ERROR: Google Play scraping failed.
    echo Check the collection status CSV in data\
    pause
    exit /b 1
)

echo.
echo Collection completed successfully.
echo Dashboard data rebuilt.
echo.
endlocal
