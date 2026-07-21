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
        
        if (config['google_sheet_id'] == 'YOUR_GOOGLE_SHEET_ID'
                and not os.environ.get('GOOGLE_SHEET_ID', '').strip()):
            print("❌ Please update google_sheet_id in config.json or set GOOGLE_SHEET_ID")
            return False
        
        print("✓ Configuration file is valid")
        return True
    except json.JSONDecodeError:
        print("❌ config.json is not valid JSON")
        return False
    except Exception as e:
        print(f"❌ Error reading config.json: {e}")
        return False

def _load_service_account_credentials():
    from google.oauth2.service_account import Credentials
    from wine_scraper import GoogleSheetsWriter

    inline_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '').strip()
    if inline_json:
        return Credentials.from_service_account_info(
            json.loads(inline_json), scopes=GoogleSheetsWriter.SCOPES)
    credentials_path = os.environ.get(
        'GOOGLE_APPLICATION_CREDENTIALS', 'credentials.json')
    return Credentials.from_service_account_file(
        credentials_path, scopes=GoogleSheetsWriter.SCOPES)


def test_credentials():
    """Test service-account credentials used by the scheduled harness."""
    print("\nTesting Google credentials...")

    try:
        _load_service_account_credentials()
        print("✓ Service-account credentials are valid")
        return True
    except FileNotFoundError:
        print(
            "❌ Google credentials not found. Set GOOGLE_SERVICE_ACCOUNT_JSON, "
            "GOOGLE_APPLICATION_CREDENTIALS, or add credentials.json")
        return False
    except json.JSONDecodeError:
        print("❌ GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON")
        return False
    except Exception as e:
        print(f"❌ Error reading Google credentials: {e}")
        return False

def test_google_sheets_connection():
    """Test Google Sheets connection"""
    print("\nTesting Google Sheets connection...")
    
    try:
        from wine_scraper import GoogleSheetsWriter

        with open('config.json', 'r') as f:
            config = json.load(f)
        sheet_id = os.environ.get(
            'GOOGLE_SHEET_ID', config.get('google_sheet_id', '')).strip()
        writer = GoogleSheetsWriter(
            credentials=_load_service_account_credentials(),
            sheet_id=sheet_id,
        )
        writer.read_catalog(enabled_only=False)
        print(f"✓ Successfully connected to Google Sheet: {writer.spreadsheet.title}")
        print("✓ Wine Catalog and Price History schemas are valid")
        return True
    except ImportError:
        print("❌ Required packages not installed. Run: pip install -r requirements.txt")
        return False
    except Exception as e:
        print(f"❌ Error connecting to Google Sheets: {e}")
        print("   Make sure:")
        print("   1. Service-account credentials are valid")
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
