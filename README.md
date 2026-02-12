# Miraval Price Checker

An application that searches for wines on Australian wine websites and adds the results to a Google Sheet.

## Features

- 🎨 **Modern Web UI** - Beautiful, user-friendly interface
- 🔍 **Multi-site Search** - Searches multiple Australian wine websites simultaneously
- 📊 **Google Sheets Integration** - Automatically adds results to Google Sheets
- 📱 **Responsive Design** - Works on desktop, tablet, and mobile devices
- ⚡ **Real-time Results** - See search results instantly
- 🏪 **Multiple Retailers** - Supports Dan Murphy's, BWS, Liquorland, First Choice

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up Google Sheets API

#### Option A: Service Account (Recommended)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the **Google Sheets API** and **Google Drive API**
4. Go to "Credentials" → "Create Credentials" → "Service Account"
5. Create a service account and download the JSON key file
6. Save the JSON file as `credentials.json` in the project root
7. Open your Google Sheet and share it with the service account email (found in the JSON file)
   - Give it "Editor" permissions

#### Option B: OAuth 2.0

1. Follow steps 1-3 above
2. Create OAuth 2.0 credentials instead
3. Download the credentials and save as `credentials.json`

### 3. Configure the Application

1. Copy the example config file:
```bash
cp config.example.json config.json
```

2. Edit `config.json`:
   - Set `google_sheet_id`: Found in your Google Sheet URL (between `/d/` and `/edit`)
   - Set `worksheet_name`: Name of the worksheet tab (default: "Wine Prices")
   - Configure `wine_sites`: List of websites to search
   - (Optional) Set `tracked_wines`: wine names used by the UI picker and monitoring endpoint
   - (Optional) Set `competitor_wines`: benchmark labels (e.g. Minuty M Provence Rosé 750mL)

Example `config.json`:
```json
{
  "google_sheet_id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
  "worksheet_name": "Wine Prices",
  "tracked_wines": [
    "Miraval Rosé",
    "Miraval Blanc",
    "Studio Rosé by Miraval",
    "Famille Perrin Côtes-du-Rhône Réserve Rouge"
  ],
  "competitor_wines": [
    "Minuty M Provence Rosé 750mL",
    "Whispering Angel Rosé 750mL",
    "AIX Rosé 750mL"
  ],
  "wine_sites": [
    "danmurphys.com.au",
    "bws.com.au",
    "liquorland.com.au",
    "firstchoice.com.au"
  ]
}
```

## Usage

### Web UI (Recommended)

1. Start the web server:
```bash
python3 app.py
```

2. Open your browser and navigate to:
```
http://localhost:5050
```

3. Enter a wine name in the search box and click "Search"
4. Review the results displayed in cards
5. Click "Add to Google Sheet" to save results

The web UI provides:
- Real-time status indicators for configuration
- Beautiful card-based results display
- One-click export to Google Sheets
- Responsive design for all devices

### Command Line (Alternative)

You can also use the command-line interface:

Search for a specific wine:
```bash
python3 wine_scraper.py "Penfolds Grange"
```

Or use interactive mode:
```bash
python3 wine_scraper.py
```

## How It Works

1. The script searches each configured Australian wine website
2. Extracts wine names, prices, and retailer locations
3. Formats the data and adds it to your Google Sheet
4. Results are appended to the sheet with columns: **Wine**, **Price**, **Location**
5. Rows also include **Scraped At**, **Source URL**, and **In Stock** for better history tracking.

### Scheduled Monitoring (Recommended)

Run all tracked wines in one request and write results to Sheets:

```bash
curl -X POST http://localhost:5050/api/run-monitoring \
  -H 'Content-Type: application/json' \
  -d '{"add_to_sheet": true, "include_competitors": true}'
```

Use cron (macOS/Linux) or Task Scheduler (Windows) to call this endpoint daily.

## Troubleshooting

### "credentials.json not found"
- Make sure you've downloaded the Google API credentials file
- Ensure it's named exactly `credentials.json` and in the project root

### "config.json not found"
- Copy `config.example.json` to `config.json`
- Update the Google Sheet ID and other settings

### "Permission denied" or "Access denied"
- Make sure you've shared your Google Sheet with the service account email
- Check that the Google Sheets API is enabled in Google Cloud Console

### No results found / CAPTCHA detected

**Important:** Australian wine retailer websites (Liquorland, BWS, Dan Murphy's, First Choice) use advanced bot protection (ShieldSquare CAPTCHA) that blocks automated scraping.

**Possible solutions:**
1. **Use Selenium with visible browser** - The scraper now uses Selenium, but you may need to manually solve CAPTCHAs the first time
2. **Use a VPN/Proxy** - Sometimes changing your IP helps
3. **Contact the websites** - They may have APIs or data feeds available
4. **Manual data entry** - For small datasets, manual entry might be faster

**Current limitations:**
- Sites detect automated requests and show CAPTCHA pages
- Even with Selenium, CAPTCHA solving may be required
- This is a common issue with modern e-commerce sites

**Workaround:** Try running the scraper with a visible browser window (not headless) - you may be able to solve CAPTCHAs manually when they appear.

## Notes

- The script includes delays between requests to be respectful to websites
- Results are limited to the first 5 matches per website
- Some websites may require JavaScript rendering (Selenium may be needed for those)
- Website structures change frequently - scrapers may need periodic updates
