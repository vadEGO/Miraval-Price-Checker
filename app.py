#!/usr/bin/env python3
"""
Flask web application for Miraval Price Checker
— now with Google OAuth for Sheets integration.
"""

import json
import os
import threading
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect
from flask_cors import CORS
from wine_scraper import WineScraper, GoogleSheetsWriter

# ── Google OAuth imports ──────────────────────────────────────────────
from google.oauth2.credentials import Credentials as OAuthCredentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleAuthRequest

# Allow OAuth over plain HTTP (localhost only — never in production)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# ── Paths ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET_PATH = os.path.join(BASE_DIR, 'client_secret.json')
TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file',
]

# ── Flask setup ───────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.urandom(24)
CORS(app)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# ── Globals ───────────────────────────────────────────────────────────
scraper = None
sheets_writer = None
_app_port = 5050  # updated at startup
_search_progress = {}
_search_lock = threading.Lock()
DEFAULT_WINE_CATALOG = [
    "Famille Perrin Côtes du Rhône Rouge Domaine de Breseyme",
    "Famille Perrin Côtes du Rhône Blanc Domaine de Breseyme",
    "Famille Perrin Vinsobres Rouge Les Hauts de Julien",
    "Famille Perrin Gigondas Rouge L'Argnée",
    "Famille Perrin Châteauneuf-du-Pape Rouge Les Chapouins",
    "Famille Perrin Côtes-du-Rhône Réserve Rouge",
    "Famille Perrin Côtes-du-Rhône Réserve Blanc",
    "Famille Perrin Côtes du Rhône Villages Rouge",
    "La Vieille Ferme Rouge",
    "La Vieille Ferme Blanc",
    "La Vieille Ferme Rosé",
    "La Vieille Ferme Ventoux Rouge",
    "La Vieille Ferme Luberon Blanc",
    "La Vieille Ferme Luberon Rosé",
    "La Vieille Ferme Ventoux Rosé",
    "Miraval Rosé",
    "Miraval Blanc",
    "Studio Blanc by Miraval",
    "Studio Rosé by Miraval",
    "Fleur de Miraval",
]
DEFAULT_COMPETITOR_WINES = [
    "Minuty M Provence Rosé",
    "Whispering Angel Rosé",
    "Château d'Esclans Rock Angel Rosé",
    "AIX Rosé",
    "Maison Saint Aix Provence Rosé",
    "Mirabeau Classic Rosé",
]


# =====================================================================
# Google OAuth helpers
# =====================================================================

def _load_oauth_creds():
    """Load saved OAuth token, refreshing if expired. Returns Credentials or None."""
    if not os.path.exists(TOKEN_PATH):
        return None
    try:
        creds = OAuthCredentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            _save_token(creds)
        if creds and creds.valid:
            return creds
        return None
    except Exception as e:
        print(f"⚠ Could not load OAuth token: {e}")
        return None


def _save_token(creds):
    """Persist OAuth token to disk."""
    data = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': list(creds.scopes or []),
    }
    with open(TOKEN_PATH, 'w') as f:
        json.dump(data, f, indent=2)


def _redirect_uri():
    return f'http://localhost:{_app_port}/api/sheets/callback'


def _build_flow():
    return Flow.from_client_secrets_file(
        CLIENT_SECRET_PATH,
        scopes=SCOPES,
        redirect_uri=_redirect_uri(),
    )


def _read_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def _write_config(cfg):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)


def _clean_wine_list(values):
    if not isinstance(values, list):
        return []
    cleaned = []
    seen = set()
    for item in values:
        if not isinstance(item, str):
            continue
        wine = item.strip()
        if wine and wine.lower() not in seen:
            seen.add(wine.lower())
            cleaned.append(wine)
    return cleaned


def _configured_wine_catalog():
    cfg = _read_config()
    tracked = _clean_wine_list(cfg.get('tracked_wines')) or DEFAULT_WINE_CATALOG
    competitors = _clean_wine_list(cfg.get('competitor_wines')) or DEFAULT_COMPETITOR_WINES
    return tracked, competitors


def _init_sheets_writer(creds=None):
    """Try to build a GoogleSheetsWriter from OAuth token (preferred) or service-account."""
    global sheets_writer
    sheets_writer = None
    cfg = _read_config()
    sheet_id = cfg.get('google_sheet_id', '')
    worksheet = cfg.get('worksheet_name', 'Wine Prices')

    # 1) OAuth path
    if creds is None:
        creds = _load_oauth_creds()
    if creds:
        try:
            writer = GoogleSheetsWriter(credentials=creds)
            if sheet_id and sheet_id != 'YOUR_GOOGLE_SHEET_ID':
                writer._open_sheet(sheet_id, worksheet)
            sheets_writer = writer
            print("✓ Google Sheets connected via OAuth")
            return
        except Exception as e:
            print(f"⚠ OAuth Sheets init failed: {e}")

    # 2) Legacy service-account fallback
    sa_path = os.path.join(BASE_DIR, 'credentials.json')
    if os.path.exists(sa_path) and sheet_id and sheet_id != 'YOUR_GOOGLE_SHEET_ID':
        try:
            sheets_writer = GoogleSheetsWriter(
                credentials_path=sa_path,
                sheet_id=sheet_id,
                worksheet_name=worksheet,
            )
            print("✓ Google Sheets connected via service account")
            return
        except Exception as e:
            print(f"⚠ Service-account Sheets init failed: {e}")

    print("⚠ Google Sheets not connected")


# =====================================================================
# Scraper init
# =====================================================================

def init_scraper():
    global scraper
    try:
        if os.path.exists(CONFIG_PATH):
            scraper = WineScraper(CONFIG_PATH)
        else:
            print("⚠ config.json not found — scraper not initialised")
            scraper = None
    except Exception as e:
        print(f"✗ Error initialising scraper: {e}")
        import traceback
        traceback.print_exc()
        scraper = None

    _init_sheets_writer()


# =====================================================================
# Routes — pages
# =====================================================================

@app.route('/')
def index():
    return render_template('index.html')


# =====================================================================
# Routes — search
# =====================================================================

def get_demo_results(wine_name: str):
    return [
        {'wine': 'Miraval Côtes de Provence Rosé 750ml', 'price': '$34.99', 'location': "Dan Murphy's"},
        {'wine': 'Miraval Provence Rosé 2024', 'price': '$36.00', 'location': 'BWS'},
        {'wine': 'Miraval Château de Miraval Rosé', 'price': '$32.50', 'location': 'Liquorland'},
        {'wine': 'Miraval Brad Pitt Rosé 750ml', 'price': '$38.00', 'location': 'First Choice'},
    ]


@app.route('/api/search', methods=['POST'])
def search_wines():
    global scraper
    try:
        if not scraper:
            init_scraper()
        if not scraper:
            return jsonify({'error': 'Scraper not initialised. Check config.json.'}), 500

        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid request data'}), 400

        wine_name = data.get('wine_name', '').strip()
        wine_names = data.get('wine_names', [])
        use_demo = data.get('demo', False)

        normalized_names = []
        seen = set()
        if isinstance(wine_names, list):
            for name in wine_names:
                if not isinstance(name, str):
                    continue
                clean = name.strip()
                if clean and clean.lower() not in seen:
                    seen.add(clean.lower())
                    normalized_names.append(clean)
        if wine_name and wine_name.lower() not in seen:
            normalized_names.append(wine_name)

        if not normalized_names:
            return jsonify({'error': 'Please select at least one wine'}), 400

        if use_demo:
            results = []
            for selected_name in normalized_names:
                for row in get_demo_results(selected_name):
                    results.append({**row, 'query': selected_name})
            print(f"✓ Demo mode: {len(results)} sample results for {len(normalized_names)} wine(s)")
            return jsonify({
                'success': True, 'results': results, 'count': len(results),
                'searched_wines': normalized_names, 'searched_count': len(normalized_names), 'demo': True,
            })

        if not scraper:
            return jsonify({'error': 'Scraper not initialised. Check config.json.'}), 500

        search_id = str(uuid.uuid4())[:8]
        print(f"\n🔍 Searching {len(normalized_names)} wine(s)… [id={search_id}]")

        def on_progress(site_key, site_index, total_sites, results_so_far):
            label = scraper.get_site_label(site_key)
            with _search_lock:
                _search_progress[search_id] = {
                    'site': label, 'site_index': site_index,
                    'total_sites': total_sites,
                    'results_so_far': results_so_far, 'done': False,
                }

        with _search_lock:
            _search_progress[search_id] = {
                'site': 'Starting…', 'site_index': 0,
                'total_sites': len(scraper.wine_sites),
                'results_so_far': 0, 'done': False,
            }

        results = []
        for selected_name in normalized_names:
            print(f"   • {selected_name}")
            for row in scraper.search_all_sites(selected_name, progress_callback=on_progress):
                results.append({
                    **row,
                    'query': selected_name,
                    'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                })

        with _search_lock:
            if search_id in _search_progress:
                _search_progress[search_id]['done'] = True

        # De-duplicate
        unique, dedupe_seen = [], set()
        for row in results:
            key = (row.get('query', '').strip().lower(),
                   row.get('wine', '').strip().lower(),
                   row.get('price', '').strip(),
                   row.get('location', '').strip().lower())
            if key not in dedupe_seen:
                dedupe_seen.add(key)
                unique.append(row)
        results = unique

        if not results:
            print("⚠ No results from live scrape")

        print(f"✓ Search completed: {len(results)} results")
        return jsonify({
            'success': True, 'results': results, 'count': len(results),
            'searched_wines': normalized_names, 'searched_count': len(normalized_names),
        })
    except Exception as e:
        import traceback
        print(f"✗ search_wines error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e), 'results': [], 'count': 0}), 500


# =====================================================================
# Routes — search progress
# =====================================================================

@app.route('/api/search/progress/<search_id>')
def search_progress(search_id):
    with _search_lock:
        info = _search_progress.get(search_id)
    if not info:
        return jsonify({'error': 'Unknown search'}), 404
    return jsonify(info)


@app.route('/api/search/sites')
def search_sites():
    """Return the ordered list of sites that will be searched, with labels."""
    if not scraper:
        init_scraper()
    if not scraper:
        return jsonify({'sites': []})
    sites = [
        {'key': s, 'label': scraper.get_site_label(s)}
        for s in scraper.wine_sites
    ]
    return jsonify({'sites': sites, 'count': len(sites)})


# =====================================================================
# Routes — Google Sheets OAuth
# =====================================================================

@app.route('/api/sheets/connect')
def sheets_connect():
    """Start OAuth flow — returns JSON with auth_url to open."""
    if not os.path.exists(CLIENT_SECRET_PATH):
        return jsonify({
            'error': 'client_secret.json not found',
            'setup_needed': True,
        }), 400
    try:
        flow = _build_flow()
        auth_url, state = flow.authorization_url(
            access_type='offline',
            prompt='consent',
        )
        # persist state for CSRF check in callback
        with open(os.path.join(BASE_DIR, '_oauth_state.tmp'), 'w') as f:
            f.write(state)
        return jsonify({'auth_url': auth_url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sheets/callback')
def sheets_callback():
    """Google redirects here after the user grants consent."""
    try:
        state_path = os.path.join(BASE_DIR, '_oauth_state.tmp')
        expected_state = None
        if os.path.exists(state_path):
            with open(state_path) as f:
                expected_state = f.read().strip()
            os.remove(state_path)
        incoming_state = request.args.get('state', '').strip()
        if expected_state and incoming_state != expected_state:
            raise ValueError('OAuth state mismatch. Please retry connection.')

        flow = _build_flow()
        flow.fetch_token(code=request.args.get('code'))
        _save_token(flow.credentials)
        _init_sheets_writer(flow.credentials)
        print("✓ OAuth token saved")
        # Redirect back to main UI with success flag
        return redirect('/?sheets_connected=1')
    except Exception as e:
        print(f"✗ OAuth callback error: {e}")
        return redirect(f'/?sheets_error={e}')


@app.route('/api/sheets/disconnect', methods=['POST'])
def sheets_disconnect():
    """Remove saved token and disconnect Sheets."""
    global sheets_writer
    if os.path.exists(TOKEN_PATH):
        os.remove(TOKEN_PATH)
    sheets_writer = None
    print("✓ Google Sheets disconnected")
    return jsonify({'success': True})


@app.route('/api/sheets/create', methods=['POST'])
def sheets_create():
    """Create a new Google Spreadsheet and set it as the active sheet."""
    global sheets_writer
    creds = _load_oauth_creds()
    if not creds:
        return jsonify({'error': 'Not authenticated. Connect Google first.'}), 401

    try:
        writer = GoogleSheetsWriter(credentials=creds)
        info = writer.create_spreadsheet("Wine Prices – Miraval Price Checker")
        # Save the new sheet ID to config
        cfg = _read_config()
        cfg['google_sheet_id'] = info['id']
        _write_config(cfg)
        sheets_writer = writer
        print(f"✓ Created spreadsheet: {info['url']}")
        return jsonify({'success': True, **info})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sheets/set', methods=['POST'])
def sheets_set():
    """Set an existing Google Sheet ID as the active sheet."""
    global sheets_writer
    data = request.get_json() or {}
    sheet_id = data.get('sheet_id', '').strip()
    if not sheet_id:
        return jsonify({'error': 'sheet_id is required'}), 400

    creds = _load_oauth_creds()
    if not creds:
        return jsonify({'error': 'Not authenticated. Connect Google first.'}), 401

    try:
        writer = GoogleSheetsWriter(credentials=creds, sheet_id=sheet_id)
        cfg = _read_config()
        cfg['google_sheet_id'] = sheet_id
        _write_config(cfg)
        sheets_writer = writer
        return jsonify({
            'success': True,
            'id': sheet_id,
            'title': writer.spreadsheet.title,
            'url': writer.spreadsheet.url,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/add-to-sheet', methods=['POST'])
def add_to_sheet():
    """Add search results to the active Google Sheet."""
    global sheets_writer
    try:
        if not sheets_writer:
            _init_sheets_writer()

        data = request.get_json()
        results = data.get('results', [])
        if not results:
            return jsonify({'error': 'No results to add'}), 400

        if not sheets_writer:
            return jsonify({'error': 'Google Sheets not connected. Click "Connect Google Sheets" first.'}), 400

        if not sheets_writer.worksheet:
            return jsonify({'error': 'No spreadsheet selected. Create or select one first.'}), 400

        sheets_writer.add_results(results)
        return jsonify({
            'success': True,
            'message': f'Added {len(results)} results to Google Sheet',
            'count': len(results),
            'sheet_url': sheets_writer.spreadsheet.url if sheets_writer.spreadsheet else None,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/catalog', methods=['GET'])
def get_catalog():
    tracked_wines, competitor_wines = _configured_wine_catalog()
    combined = []
    seen = set()
    for wine in tracked_wines + competitor_wines:
        k = wine.lower()
        if k not in seen:
            seen.add(k)
            combined.append(wine)
    return jsonify({
        'success': True,
        'tracked_wines': tracked_wines,
        'competitor_wines': competitor_wines,
        'wines': combined,
        'count': len(combined),
    })


@app.route('/api/run-monitoring', methods=['POST'])
def run_monitoring():
    """Run the full monitoring workflow for configured wines and optionally write to Sheets."""
    global scraper
    try:
        if not scraper:
            init_scraper()
        if not scraper:
            return jsonify({'error': 'Scraper not initialised. Check config.json.'}), 500

        payload = request.get_json(silent=True) or {}
        wines = payload.get('wine_names')
        include_competitors = bool(payload.get('include_competitors', True))
        if not isinstance(wines, list) or not wines:
            tracked_wines, competitor_wines = _configured_wine_catalog()
            wines = tracked_wines + (competitor_wines if include_competitors else [])

        do_write = bool(payload.get('add_to_sheet', True))
        results = []
        for wine in wines:
            if not isinstance(wine, str) or not wine.strip():
                continue
            selected_name = wine.strip()
            for row in scraper.search_all_sites(selected_name):
                results.append({
                    **row,
                    'query': selected_name,
                    'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                })

        # dedupe
        unique = []
        dedupe_seen = set()
        for row in results:
            key = (
                row.get('query', '').strip().lower(),
                row.get('wine', '').strip().lower(),
                row.get('price', '').strip(),
                row.get('location', '').strip().lower(),
            )
            if key in dedupe_seen:
                continue
            dedupe_seen.add(key)
            unique.append(row)

        wrote_to_sheet = False
        if do_write and unique:
            if not sheets_writer:
                _init_sheets_writer()
            if not sheets_writer or not sheets_writer.worksheet:
                return jsonify({
                    'error': 'Google Sheets not connected or sheet not selected.',
                    'searched_count': len(wines),
                    'count': len(unique),
                }), 400
            sheets_writer.add_results(unique)
            wrote_to_sheet = True

        return jsonify({
            'success': True,
            'searched_count': len(wines),
            'count': len(unique),
            'wrote_to_sheet': wrote_to_sheet,
            'results': unique,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =====================================================================
# Routes — status
# =====================================================================

@app.route('/api/status', methods=['GET'])
def status():
    config_exists = os.path.exists(CONFIG_PATH)
    client_secret_exists = os.path.exists(CLIENT_SECRET_PATH)
    oauth_connected = _load_oauth_creds() is not None
    scraper_ready = scraper is not None
    sheets_ready = sheets_writer is not None and sheets_writer.worksheet is not None
    cfg = _read_config()
    sheet_id = cfg.get('google_sheet_id', '')
    sheet_configured = bool(sheet_id and sheet_id != 'YOUR_GOOGLE_SHEET_ID')

    return jsonify({
        'config_exists': config_exists,
        'client_secret_exists': client_secret_exists,
        'oauth_connected': oauth_connected,
        'scraper_ready': scraper_ready,
        'sheets_ready': sheets_ready,
        'sheet_configured': sheet_configured,
        'sheet_id': sheet_id if sheet_configured else None,
        'sheet_url': (f'https://docs.google.com/spreadsheets/d/{sheet_id}'
                      if sheet_configured else None),
        # Legacy compat
        'credentials_exists': client_secret_exists or os.path.exists(
            os.path.join(BASE_DIR, 'credentials.json')),
    })


# =====================================================================
# Server start
# =====================================================================

def find_available_port(start_port=5050, max_attempts=15):
    import socket
    for i in range(max_attempts):
        port = start_port + i
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    return start_port


if __name__ == '__main__':
    try:
        _app_port = find_available_port(5050)
        init_scraper()
        print("\n" + "=" * 60)
        print("🍷 Miraval Price Checker Web Server")
        print("=" * 60)
        print(f"Server starting on http://localhost:{_app_port}")
        print(f"OAuth callback URI:  {_redirect_uri()}")
        print("Press Ctrl+C to stop the server")
        print("=" * 60 + "\n")
        from werkzeug.serving import WSGIRequestHandler
        WSGIRequestHandler.timeout = 300
        app.run(debug=True, host='0.0.0.0', port=_app_port,
                threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\n\nServer stopped by user")
    except Exception as e:
        print(f"\n\nError starting server: {e}")
        import traceback
        traceback.print_exc()
