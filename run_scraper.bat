@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Allo Bank Competitive Intelligence - Adaptive Google Play Scraper

echo ============================================================
echo Allo Bank Competitive Intelligence Monitor
echo Adaptive Google Play Scraper
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

echo [3/3] Running adaptive scraper...
echo.
echo Package IDs are validated and can be re-resolved from Google Play by app name.
echo Storefront: Indonesia ^(id/id^)
echo Reviews: all publicly retrievable records returned by reviews_all().
echo.

py scrape_google_play_adaptive.py --lang id --country id
if errorlevel 1 (
    echo.
    echo ============================================================
    echo ERROR: Scraper failed.
    echo Check data\review_collection_status.csv and
    echo data\last_scraper_run.log when using the dashboard controller.
    echo ============================================================
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo SUCCESS: Adaptive scraping completed.
echo dashboard_data.json has been rebuilt.
echo ============================================================
echo.
endlocal
exit /b 0
