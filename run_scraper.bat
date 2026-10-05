@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Allo Bank Competitive Intelligence - Google Play Scraper

echo ============================================================
echo Allo Bank Competitive Intelligence Monitor
echo Full Google Play Public Review Scraper
echo ============================================================
echo.

echo [1/3] Checking Python...
py --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python launcher ^(py^) was not found.
    echo Install Python 3.11+ and enable the Python Launcher for Windows.
    echo.
    pause
    exit /b 1
)

echo [2/3] Installing / updating dependencies...
py -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Dependency installation failed.
    pause
    exit /b 1
)

echo [3/3] Running full Google Play collection...
echo.
echo Target: 25 apps
echo Storefront: Indonesia ^(id/id^)
echo Reviews: all publicly retrievable records returned by the public endpoint
echo No star/date/keyword/reviewer filters are applied.
echo.

py scrape_google_play.py --lang id --country id
if errorlevel 1 (
    echo.
    echo ============================================================
    echo ERROR: Scraper failed.
    echo Check data\review_collection_status.csv for per-app status.
    echo ============================================================
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo SUCCESS: Scraping completed.
echo The raw archive and dashboard_data.json were rebuilt.
echo ============================================================
echo.
endlocal
exit /b 0
