#!/usr/bin/env python3
"""
Test script to verify the setup is correct
"""

import json
import os
import sys

def test_config():
    """Test if config.json exists and is valid"""
    print("Testing configuration...")
    
    if not os.path.exists('config.json'):
        print("❌ config.json not found. Please copy config.example.json to config.json")
        return False
    
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        required_keys = ['google_sheet_id', 'wine_sites']
        missing_keys = [key for key in required_keys if key not in config]
        
        if missing_keys:
            print(f"❌ Missing required keys in config.json: {', '.join(missing_keys)}")
            return False
        
        if config['google_sheet_id'] == 'YOUR_GOOGLE_SHEET_ID':
            print("⚠️  Warning: Please update google_sheet_id in config.json")
        
        print("✓ Configuration file is valid")
        return True
    except json.JSONDecodeError:
        print("❌ config.json is not valid JSON")
        return False
    except Exception as e:
        print(f"❌ Error reading config.json: {e}")
        return False

def test_credentials():
    """Test if credentials.json exists"""
    print("\nTesting Google credentials...")
    
    if not os.path.exists('credentials.json'):
        print("❌ credentials.json not found. Please download from Google Cloud Console")
        return False
    
    try:
        with open('credentials.json', 'r') as f:
            creds = json.load(f)
        
        if 'type' not in creds:
            print("⚠️  Warning: credentials.json format may be incorrect")
        
        print("✓ Credentials file found")
        return True
    except json.JSONDecodeError:
        print("❌ credentials.json is not valid JSON")
        return False
    except Exception as e:
        print(f"❌ Error reading credentials.json: {e}")
        return False

def test_google_sheets_connection():
    """Test Google Sheets connection"""
    print("\nTesting Google Sheets connection...")
    
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        scope = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        creds = Credentials.from_service_account_file('credentials.json', scopes=scope)
        client = gspread.authorize(creds)
        
        spreadsheet = client.open_by_key(config['google_sheet_id'])
        worksheet_name = config.get('worksheet_name', 'Wine Prices')
        
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
            print(f"✓ Successfully connected to Google Sheet: {spreadsheet.title}")
            print(f"✓ Worksheet '{worksheet_name}' found")
            return True
        except gspread.exceptions.WorksheetNotFound:
            print(f"⚠️  Worksheet '{worksheet_name}' not found. It will be created on first run.")
            return True
    except ImportError:
        print("❌ Required packages not installed. Run: pip install -r requirements.txt")
        return False
    except Exception as e:
        print(f"❌ Error connecting to Google Sheets: {e}")
        print("   Make sure:")
        print("   1. credentials.json is valid")
        print("   2. Google Sheet is shared with the service account email")
        print("   3. Google Sheets API is enabled in Google Cloud Console")
        return False

def test_dependencies():
    """Test if all required packages are installed"""
    print("\nTesting dependencies...")
    
    required_packages = [
        'requests',
        'beautifulsoup4',
        'gspread',
        'google.auth',
    ]
    
    missing = []
    for package in required_packages:
        try:
            if package == 'beautifulsoup4':
                __import__('bs4')
            elif package == 'google.auth':
                __import__('google.oauth2')
            else:
                __import__(package)
        except ImportError:
            missing.append(package)
    
    if missing:
        print(f"❌ Missing packages: {', '.join(missing)}")
        print("   Run: pip install -r requirements.txt")
        return False
    
    print("✓ All required packages are installed")
    return True

def main():
    """Run all tests"""
    print("=" * 50)
    print("Miraval Price Checker - Setup Verification")
    print("=" * 50)
    
    results = []
    results.append(("Dependencies", test_dependencies()))
    results.append(("Configuration", test_config()))
    results.append(("Credentials", test_credentials()))
    
    if all(r[1] for r in results[:3]):  # Only test Google Sheets if basics pass
        results.append(("Google Sheets Connection", test_google_sheets_connection()))
    
    print("\n" + "=" * 50)
    print("Summary:")
    print("=" * 50)
    
    all_passed = True
    for name, passed in results:
        status = "✓ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    print("=" * 50)
    
    if all_passed:
        print("\n🎉 All tests passed! You're ready to use the wine scraper.")
        return 0
    else:
        print("\n⚠️  Some tests failed. Please fix the issues above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
