#!/bin/bash
# Fix Selenium/ChromeDriver for Miraval Price Checker
# Run this in Terminal.app (not Cursor) if you see "403 - Selenium required"

set -e
echo "🍷 Fixing Selenium setup for Miraval Price Checker..."
echo ""

# 1. Find chromedriver
CHROMEDRIVER=$(find ~/.wdm/drivers/chromedriver -name "chromedriver" -type f 2>/dev/null | head -1)

if [ -z "$CHROMEDRIVER" ]; then
    echo "ChromeDriver not found. Run the app once to download it:"
    echo "  python3 app.py"
    echo ""
    exit 1
fi

echo "Found: $CHROMEDRIVER"
echo ""

# 2. Remove macOS quarantine (allows chromedriver to run)
echo "Removing macOS quarantine..."
xattr -cr "$(dirname "$CHROMEDRIVER")" 2>/dev/null || true
xattr -d com.apple.quarantine "$CHROMEDRIVER" 2>/dev/null || true
echo "✓ Done"
echo ""

# 3. Check Chrome
if [ -d "/Applications/Google Chrome.app" ]; then
    echo "✓ Google Chrome is installed"
else
    echo "⚠ Install Google Chrome: https://www.google.com/chrome/"
fi
echo ""

echo "Try starting the app again:"
echo "  python3 app.py"
echo ""
echo "If it still fails, run ChromeDriver once manually to trigger any security prompts:"
echo "  $CHROMEDRIVER --version"
echo ""
