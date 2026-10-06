# Miraval Price Checker

An application that searches for wines on Australian wine websites and adds the results to a Google Sheet.

## Features

- 🎨 **Modern Web UI** - Beautiful, user-friendly interface
- 🔍 **Multi-site Search** - Searches multiple Australian wine websites simultaneously
- 📊 **Google Sheets Integration** - Automatically adds results to Google Sheets
- 🔗 **Exact URL Tracking** - Uses the product URLs in the sheet instead of guessing from search results
- 🧾 **Auditable History** - Records validated prices, availability, errors, and UTC timestamps
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
3. Download the credentials and save as `client_secret.json`
4. OAuth is for the interactive web UI. Scheduled runs should use a service account.

### 3. Configure the Application

1. Copy the example config file:
```bash
cp config.example.json config.json
```

2. Edit `config.json`:
   - Set `google_sheet_id`: Found in your Google Sheet URL (between `/d/` and `/edit`)
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

The scheduled harness is deterministic; it does not use AI to choose or invent a
price.

1. `Wine Catalog` supplies one exact retailer product URL per enabled row.
2. The harness validates the product name, bottle size, AUD price, package type,
   availability, and challenge-page response.
3. It updates the current status columns in `Wine Catalog`.
4. It appends every success, unavailable result, review item, and error to
   `Price History`.

### Spreadsheet schema

`Wine Catalog` is created automatically with these columns:

`Wine ID`, `Wine Name`, `Retailer`, `Product URL`,
`Expected Bottle Size`, `Enabled`, `Latest Price`, `Currency`, `In Stock`,
`Last Checked UTC`, `Status`, `Error`

Populate the first six columns. `Wine ID` must uniquely identify a
wine-and-retailer listing, for example `miraval-rose-danmurphys`. Use `Yes` in
`Enabled` and an absolute HTTPS product URL.

`Price History` is managed by the harness:

`Observation ID`, `Run ID`, `Wine ID`, `Wine Name`, `Retailer`, `Price Amount`,
`Regular Price Amount`, `Currency`, `In Stock`, `Source URL`,
`Observed At UTC`, `Status`, `Error`

### Running on a schedule with Codex

See [CODEX.md](CODEX.md) for the one-time Google setup, the first-run prompt, and
the weekly prompt. `scripts/codex_setup.sh` prepares the environment and
`seed_catalog.py` fills the `Wine Catalog` sheet with product URLs on the first
run. Standing rules for agents are in [AGENTS.md](AGENTS.md).

### Scheduled tracking harness

Validate configuration, credentials, access, and worksheet schemas:

```bash
python3 test_setup.py
```

Run every enabled catalog row and write the results:

```bash
python3 price_tracker.py
```

For an idempotent retry, reuse the same run ID:

```bash
python3 price_tracker.py --run-id codex-2026-07-21
```

Use `--dry-run` to scrape without writing. The command writes a JSON summary to
stdout and exits with:

- `0`: at least one valid or unavailable observation was recorded
- `2`: configuration or credentials are invalid
- `3`: every catalog check failed or needs review
- `4`: an unexpected fetch or Google Sheets write failure occurred

The scheduled environment can provide secrets without files:

- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON` (the complete service-account JSON)
- or `GOOGLE_APPLICATION_CREDENTIALS` (path to the JSON file)

### Codex scheduler handoff

After the harness succeeds locally, configure a weekly Codex task in this
repository with instructions equivalent to:

> Run `python3 test_setup.py`, then run `python3 price_tracker.py` with a stable
> run ID for this scheduled occurrence. Report the JSON counts and fail the task
> when the command exits non-zero. Do not infer, replace, or manually edit any
> price.

No Flask server is required for a scheduled run. The
`POST /api/run-monitoring` endpoint invokes the same sheet-driven service for
interactive use.

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

- CAPTCHA and challenge pages are recorded as `error`; they are never parsed as
  prices.
- A changed page, member-only price, case listing, currency mismatch, or bottle
  size mismatch is recorded as `needs_review`.
- Correct the exact URL or retailer adapter, then retry with the same run ID.

## Notes

- The script includes delays between requests to be respectful to websites
- Results are limited to the first 5 matches per website
- JavaScript-only or protected product pages may require a retailer-specific API
  adapter
- Website structures change frequently - scrapers may need periodic updates
