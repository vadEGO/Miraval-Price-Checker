#!/bin/bash
# Start script for Miraval Price Checker

cd "$(dirname "$0")"

# Kill any existing Flask processes on common ports
for p in 5050 5051 5001; do lsof -ti:$p | xargs kill -9 2>/dev/null; done
sleep 1

echo "Starting Miraval Price Checker..."
echo "Server will be available at: http://localhost:5050"
echo "Press Ctrl+C to stop"
echo ""

python3 app.py
