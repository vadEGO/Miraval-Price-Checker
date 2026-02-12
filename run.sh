#!/bin/bash
# Startup script for Miraval Price Checker Web UI

echo "🍷 Starting Miraval Price Checker..."
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install/update dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

# Check if config.json exists
if [ ! -f "config.json" ]; then
    echo ""
    echo "⚠️  Warning: config.json not found!"
    echo "Please copy config.example.json to config.json and configure it."
    echo ""
fi

# Check if credentials.json exists
if [ ! -f "credentials.json" ]; then
    echo ""
    echo "⚠️  Warning: credentials.json not found!"
    echo "Please set up Google Sheets API credentials."
    echo ""
fi

echo ""
echo "🚀 Starting web server..."
echo "📱 Open http://localhost:5050 in your browser (or next available port)"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Run the Flask app
python3 app.py
