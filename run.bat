@echo off
REM Startup script for Miraval Price Checker Web UI (Windows)

echo 🍷 Starting Miraval Price Checker...
echo.

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install/update dependencies
echo Installing dependencies...
pip install -q -r requirements.txt

REM Check if config.json exists
if not exist "config.json" (
    echo.
    echo ⚠️  Warning: config.json not found!
    echo Please copy config.example.json to config.json and configure it.
    echo.
)

REM Check if credentials.json exists
if not exist "credentials.json" (
    echo.
    echo ⚠️  Warning: credentials.json not found!
    echo Please set up Google Sheets API credentials.
    echo.
)

echo.
echo 🚀 Starting web server...
echo 📱 Open http://localhost:5000 in your browser
echo.
echo Press Ctrl+C to stop the server
echo.

REM Run the Flask app
python app.py

pause
