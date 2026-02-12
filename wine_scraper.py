#!/usr/bin/env python3
"""
Miraval Price Checker - Searches Australian wine websites and adds results to Google Sheets.

Uses a combination of:
  1. Direct retailer APIs (BWS/Dan Murphy's Endeavour Group API) - fast and reliable
  2. curl_cffi with Chrome TLS impersonation - bypasses 403 blocks on HTML sites
  3. Plain requests fallback for simpler sites (Kent Street Cellars)
"""

import json
import sys
import re
import time
import unicodedata
from typing import List, Dict, Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials

# Try to import curl_cffi for TLS fingerprint impersonation (bypasses 403 blocks)
try:
    from curl_cffi import requests as cffi_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    print("⚠ curl_cffi not installed - run: pip install curl_cffi")


class WineScraper:
    """Scrapes wine prices from Australian wine websites using APIs and web scraping."""

    INDEPENDENT_SITE_CONFIG = {
        "kentstreetcellars.com.au": {
            "label": "Kent Street Cellars",
            "search_url": "https://kentstreetcellars.com.au/search?q={query}",
        },
        "nicks.com.au": {
            "label": "Nicks Wine Merchants",
            "search_url": "https://www.nicks.com.au/search?query={query}",
        },
        "senseoftaste.com.au": {
            "label": "Sense of Taste",
            "search_url": "https://www.senseoftaste.com.au/search?type=product&q={query}",
        },
        "princewinestore.com.au": {
            "label": "Prince Wine Store",
            "search_url": "https://www.princewinestore.com.au/?s={query}&post_type=product",
        },
        "differentdrop.com": {
            "label": "Different Drop",
            "search_url": "https://www.differentdrop.com/search?type=product&q={query}",
        },
        "cellarbrations.com.au": {
            "label": "Cellarbrations",
            "search_url": "https://www.cellarbrations.com.au/search?q={query}",
        },
        "grandcruwineshop.com.au": {
            "label": "Grand Cru Wine Shop",
            "search_url": "https://www.grandcruwineshop.com.au/search?type=product&q={query}",
        },
    }

    def __init__(self, config_path: str = "config.json", use_selenium: bool = False):
        """Initialize the scraper with configuration."""
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.wine_sites = self.config.get('wine_sites', [])
        self.use_selenium = False  # No longer needed - using APIs + curl_cffi instead
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-AU,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        print(f"✓ Scraper initialized with {len(self.wine_sites)} wine sites"
              f" (API mode{'+ curl_cffi' if CURL_CFFI_AVAILABLE else ''})")

    # ── Endeavour Group API (BWS + Dan Murphy's) ────────────────────────

    # Only truly structural filler words — NOT wine style/colour/brand words
    _FILLER_WORDS = {
        'du', 'de', 'la', 'le', 'les', 'des', 'by', 'the', 'and', 'et',
        'bottle', 'ml', '750ml', '750', '700ml', 'france',
    }

    # Wine colour/style words that MUST match if present in the query
    _COLOUR_STYLE = {
        'blanc', 'rouge', 'rose', 'rosé', 'red', 'white', 'pink',
    }

    def _normalize_text(self, text: str) -> str:
        """Normalize text for robust matching (accents/case/punctuation)."""
        if not text:
            return ""
        value = unicodedata.normalize('NFKD', text)
        value = ''.join(c for c in value if not unicodedata.combining(c))
        value = value.lower()
        value = re.sub(r"[^a-z0-9\s]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _is_relevant_match(self, search_term: str, candidate_name: str) -> bool:
        """Return True only if candidate strongly matches the requested wine.

        Rules:
        1. If query contains a colour/style word (blanc/rouge/rosé), the
           candidate MUST also contain it — a Blanc must not match a Rosé.
        2. All meaningful (non-filler) tokens are checked; majority must match.
        """
        query = self._normalize_text(search_term)
        candidate = self._normalize_text(candidate_name)
        if not query or not candidate:
            return False

        # Fast-path: exact phrase match
        if query in candidate:
            return True

        query_tokens = [t for t in query.split() if len(t) > 1]
        if not query_tokens:
            return False

        # --- Hard rule: colour/style words must match if present ---
        query_colours = [t for t in query_tokens if t in self._COLOUR_STYLE]
        if query_colours:
            candidate_tokens = set(candidate.split())
            # "rose" in query should also accept "rosé" → both normalize to "rose"
            if not any(c in candidate_tokens for c in query_colours):
                return False

        # --- Token overlap scoring ---
        meaningful_tokens = [t for t in query_tokens if t not in self._FILLER_WORDS]
        if not meaningful_tokens:
            meaningful_tokens = query_tokens

        overlap = sum(1 for t in meaningful_tokens if t in candidate)

        # Require majority overlap
        required = max(1, len(meaningful_tokens) // 2 + 1)  # e.g. 3 of 5, 2 of 3, 1 of 1
        return overlap >= required

    def _search_endeavour_api(self, wine_name: str, brand: str) -> List[Dict]:
        """Search BWS or Dan Murphy's via their JSON API (Endeavour Group).

        Both BWS and DM share the same backend. BWS API reliably returns product
        JSON; DM API returns a redirect for known brands, so we use BWS API for both
        and flag products that are ranged to DM.
        """
        results = []
        api_configs = {
            'bws': {
                'api_url': 'https://api.bws.com.au/apis/ui/Search/products',
                'origin': 'https://www.bws.com.au',
                'referer': 'https://www.bws.com.au/',
                'location': 'BWS',
            },
            'danmurphys': {
                'api_url': 'https://api.bws.com.au/apis/ui/Search/products',
                'origin': 'https://www.bws.com.au',
                'referer': 'https://www.bws.com.au/',
                'location': "Dan Murphy's",  # flagged via rangedtodm
            },
        }

        config = api_configs.get(brand)
        if not config:
            return results

        headers = {
            'User-Agent': self.headers['User-Agent'],
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-AU,en;q=0.9',
            'Origin': config['origin'],
            'Referer': config['referer'],
        }

        try:
            params = {'searchTerm': wine_name, 'pageSize': 10}
            if CURL_CFFI_AVAILABLE:
                r = cffi_requests.get(
                    config['api_url'], headers=headers, params=params,
                    timeout=15, impersonate='chrome',
                )
            else:
                r = requests.get(config['api_url'], headers=headers, params=params, timeout=15)

            if r.status_code != 200:
                print(f"    ⚠ API returned HTTP {r.status_code}")
                return results

            data = r.json()

            # API returns a list of "packs", each with nested "Products"
            if isinstance(data, dict) and 'Products' in data:
                packs = data['Products']
                for pack in packs[:5]:
                    inner_products = pack.get('Products', [])
                    for product in inner_products[:1]:  # First variant per pack
                        name = product.get('Name', '').strip()
                        name = re.sub(r'<br\s*/?>', ' ', name)  # clean HTML
                        price = product.get('Price')
                        was_price = product.get('WasPrice')
                        on_special = product.get('IsOnSpecial', False)
                        size = product.get('PackageSize', '')
                        available = product.get('IsAvailable', True)

                        if not name:
                            continue
                        if not self._is_relevant_match(wine_name, name):
                            continue

                        # Check if ranged to Dan Murphy's (for DM searches)
                        ranged_to_dm = False
                        for detail in product.get('AdditionalDetails', []):
                            if detail.get('Name') == 'rangedtodm':
                                ranged_to_dm = str(detail.get('Value', '')).lower() == 'yes'

                        location = config['location']
                        if brand == 'danmurphys' and not ranged_to_dm:
                            continue  # Skip products not available at DM

                        display_name = f"{name} {size}".strip() if size else name
                        price_str = f"${price:.2f}" if price else "N/A"

                        info = {
                            'wine': display_name,
                            'price': price_str,
                            'location': location,
                        }

                        # Add sale info
                        if on_special and was_price and price and was_price > price:
                            info['price'] = f"${price:.2f} (was ${was_price:.2f})"

                        if not available:
                            info['price'] += " [Out of Stock]"

                        results.append(info)

            elif isinstance(data, list):
                # Redirect response (DM returns this for known brands)
                print(f"    API returned redirect, no inline products")

        except Exception as e:
            print(f"    ✗ API error: {e}")

        return results[:5]

    def search_dan_murphys(self, wine_name: str) -> List[Dict]:
        """Search Dan Murphy's via Endeavour Group API."""
        return self._search_endeavour_api(wine_name, 'danmurphys')

    def search_bws(self, wine_name: str) -> List[Dict]:
        """Search BWS via Endeavour Group API."""
        return self._search_endeavour_api(wine_name, 'bws')

    # ── HTML scraping (Kent Street Cellars, Liquorland, First Choice) ───

    def _get_page_content(self, url: str) -> Optional[str]:
        """Get page content using the best available HTTP method.

        Priority: curl_cffi (TLS impersonation) > plain requests
        """
        # Method 1: curl_cffi with Chrome TLS impersonation
        if CURL_CFFI_AVAILABLE:
            try:
                print(f"    Fetching {url} (impersonating Chrome)...")
                response = cffi_requests.get(
                    url, headers=self.headers, timeout=15,
                    impersonate="chrome", allow_redirects=True,
                )
                if response.status_code == 200:
                    return response.text
                elif response.status_code == 403:
                    print(f"    ⚠ 403 even with impersonation")
                else:
                    print(f"    ⚠ HTTP {response.status_code}")
            except Exception as e:
                print(f"    ✗ curl_cffi error: {e}")

        # Method 2: Plain requests (may get 403 on protected sites)
        try:
            print(f"    Fetching {url} (plain request)...")
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 403:
                print(f"    ⚠ Site blocked (403) - install curl_cffi: pip install curl_cffi")
                return None
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"    ✗ Request error: {e}")
            return None

    def _extract_product_info(self, product_element, location: str,
                               wine_search_term: str = "") -> Optional[Dict]:
        """Extract product info from an HTML element."""
        try:
            name = None
            price = None

            # Try data attributes
            for attr in ['data-product-name', 'data-name', 'data-title', 'aria-label', 'title']:
                val = product_element.get(attr, '').strip()
                if val and len(val) > 3:
                    name = val
                    break

            for attr in ['data-price', 'data-product-price', 'data-amount']:
                val = product_element.get(attr, '').strip()
                if val:
                    price = val
                    break

            # Try class-based selectors
            if not name:
                for tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'a']:
                    elem = product_element.find(tag)
                    if elem:
                        text = elem.get_text(strip=True)
                        if text and 5 < len(text) < 200:
                            name = text
                            break

            if not price:
                price_elem = product_element.find(
                    ['span', 'div', 'p'],
                    class_=re.compile(r'price|cost|amount|dollar', re.I)
                )
                if price_elem:
                    price = price_elem.get_text(strip=True)

            # Fallback: look for $ in text
            if not price:
                all_text = product_element.get_text()
                m = re.search(r'\$\s*(\d+\.?\d{0,2})', all_text)
                if m:
                    val = float(m.group(1))
                    if 5 <= val <= 1000:
                        price = f"${m.group(1)}"

            # JSON-LD structured data
            if not name or not price:
                for script in product_element.find_all('script', type='application/ld+json'):
                    try:
                        data = json.loads(script.string)
                        if isinstance(data, dict):
                            if not name and 'name' in data:
                                name = data['name']
                            if not price and 'offers' in data:
                                offers = data['offers']
                                if isinstance(offers, dict) and 'price' in offers:
                                    price = f"${offers['price']}"
                    except Exception:
                        continue

            if not name or len(name) <= 3:
                return None

            name_lower = name.lower()

            # Skip navigation/UI elements
            nav_keywords = [
                'home', 'menu', 'search', 'cart', 'account', 'login',
                'categories', 'filter', 'sort', 'wine club', 'gift',
                'delivery', 'help', 'contact', 'about', 'terms', 'privacy',
            ]
            if any(kw == name_lower or (len(name) < 20 and kw in name_lower) for kw in nav_keywords):
                return None

            # Format price
            if price:
                pm = re.search(r'\$?\s*(\d+\.?\d*)', str(price))
                if pm:
                    pv = float(pm.group(1))
                    formatted_price = f"${pm.group(1)}" if 5 <= pv <= 500 else "N/A"
                else:
                    formatted_price = str(price)
            else:
                formatted_price = "N/A"

            # Filter by search term
            if wine_search_term and not self._is_relevant_match(wine_search_term, name):
                return None

            if formatted_price == "N/A" and wine_search_term:
                return None  # Skip priceless results when we have a specific search

            return {'wine': name[:200], 'price': formatted_price, 'location': location}

        except Exception:
            return None

    def _scrape_html_site(self, url: str, location: str, wine_name: str) -> List[Dict]:
        """Generic HTML scraper for sites without APIs."""
        results = []
        page_content = self._get_page_content(url)
        if not page_content:
            return results

        soup = BeautifulSoup(page_content, 'html.parser')

        # Find product containers
        product_elements = (
            soup.find_all(['div', 'article', 'li'],
                          class_=re.compile(r'product|tile|card|item', re.I)) or
            soup.find_all(['div'], attrs={'data-product-id': True}) or
            soup.find_all(['article'], attrs={'itemtype': re.compile(r'Product', re.I)})
        )

        seen = set()
        for elem in product_elements[:20]:
            eid = id(elem)
            if eid in seen:
                continue
            seen.add(eid)
            info = self._extract_product_info(elem, location, wine_name)
            if info and info not in results:
                results.append(info)
                if len(results) >= 5:
                    break

        return results[:5]

    def search_liquorland(self, wine_name: str) -> List[Dict]:
        """Search Liquorland website."""
        url = f"https://www.liquorland.com.au/search?q={quote_plus(wine_name)}"
        return self._scrape_html_site(url, 'Liquorland', wine_name)

    def search_first_choice(self, wine_name: str) -> List[Dict]:
        """Search First Choice website."""
        url = f"https://www.firstchoice.com.au/search?q={quote_plus(wine_name)}"
        return self._scrape_html_site(url, 'First Choice', wine_name)

    def search_independent_site(self, wine_name: str, site_key: str) -> List[Dict]:
        """Search an independent retailer site using configured URL pattern."""
        config = self.INDEPENDENT_SITE_CONFIG.get(site_key)
        if not config:
            return []
        url = config["search_url"].format(query=quote_plus(wine_name))
        return self._scrape_html_site(url, config["label"], wine_name)

    # ── Orchestrator ────────────────────────────────────────────────────

    def search_all_sites(self, wine_name: str) -> List[Dict]:
        """Search all configured wine sites."""
        all_results = []

        site_handlers = {
            'danmurphys.com.au': self.search_dan_murphys,
            'bws.com.au': self.search_bws,
            'liquorland.com.au': self.search_liquorland,
            'firstchoice.com.au': self.search_first_choice,
        }

        for site in self.wine_sites:
            print(f"Searching {site}...")
            try:
                handler = site_handlers.get(site)
                if handler:
                    results = handler(wine_name)
                elif site in self.INDEPENDENT_SITE_CONFIG:
                    results = self.search_independent_site(wine_name, site)
                else:
                    print(f"  ⚠ Site not configured: {site}")
                    results = []
                print(f"  ✓ Found {len(results)} results from {site}")
                all_results.extend(results)
            except Exception as e:
                print(f"  ✗ Error searching {site}: {e}")
            time.sleep(0.3)

        print(f"\nTotal results found: {len(all_results)}")
        return all_results


class GoogleSheetsWriter:
    """Handles writing data to Google Sheets.

    Supports two auth modes:
      1. OAuth credentials  – pass a google.oauth2.credentials.Credentials object
      2. Service account     – pass a path to a service-account JSON file
    """

    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive.file',
    ]

    def __init__(self, credentials=None, credentials_path: str = None,
                 sheet_id: str = None, worksheet_name: str = "Wine Prices"):
        self.sheet_id = sheet_id
        self.worksheet_name = worksheet_name
        self.spreadsheet = None
        self.worksheet = None

        if credentials is not None:
            # OAuth credentials object (already authenticated)
            self.client = gspread.authorize(credentials)
        elif credentials_path:
            # Legacy service-account file
            creds = ServiceAccountCredentials.from_service_account_file(
                credentials_path, scopes=self.SCOPES)
            self.client = gspread.authorize(creds)
        else:
            raise ValueError("Provide either 'credentials' (OAuth) or 'credentials_path' (service account)")

        if sheet_id:
            self._open_sheet(sheet_id, worksheet_name)

    def _open_sheet(self, sheet_id: str, worksheet_name: str = "Wine Prices"):
        """Open an existing spreadsheet by ID."""
        self.sheet_id = sheet_id
        self.worksheet_name = worksheet_name
        self.spreadsheet = self.client.open_by_key(sheet_id)
        try:
            self.worksheet = self.spreadsheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            self.worksheet = self.spreadsheet.add_worksheet(
                title=worksheet_name, rows=1000, cols=10)
            self.worksheet.append_row(['Search Term', 'Wine', 'Price', 'Location', 'Date'])

    def create_spreadsheet(self, title: str = "Wine Prices – Miraval Price Checker"):
        """Create a new spreadsheet and set it as active. Returns metadata dict."""
        spreadsheet = self.client.create(title)
        self.spreadsheet = spreadsheet
        self.sheet_id = spreadsheet.id
        self.worksheet = spreadsheet.sheet1
        self.worksheet.update_title("Wine Prices")
        self.worksheet.append_row(['Search Term', 'Wine', 'Price', 'Location', 'Date'])
        # Auto-bold header row and freeze it
        self.worksheet.format('A1:E1', {'textFormat': {'bold': True}})
        self.worksheet.freeze(rows=1)
        return {
            'id': spreadsheet.id,
            'url': spreadsheet.url,
            'title': spreadsheet.title,
        }

    def add_results(self, results: List[Dict]):
        if not results:
            print("No results to add.")
            return
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
        rows = [
            [r.get('query', ''), r['wine'], r['price'], r['location'], today]
            for r in results
        ]
        self.worksheet.append_rows(rows)
        print(f"Added {len(results)} results to Google Sheet.")


def main():
    """CLI entry point."""
    if len(sys.argv) > 1:
        wine_name = ' '.join(sys.argv[1:])
    else:
        wine_name = input("Enter wine name to search: ").strip()

    if not wine_name:
        print("Error: Wine name is required.")
        sys.exit(1)

    print(f"\nSearching for: {wine_name}\n")

    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print("Error: config.json not found.")
        sys.exit(1)

    scraper = WineScraper('config.json')
    results = scraper.search_all_sites(wine_name)

    if not results:
        print("No results found.")
        return

    print(f"\nFound {len(results)} results:")
    for r in results:
        print(f"  - {r['wine']} | {r['price']} | {r['location']}")

    try:
        sheet_id = config.get('google_sheet_id', '')
        if not sheet_id or sheet_id == 'YOUR_GOOGLE_SHEET_ID':
            print("\nSkipping Google Sheets (sheet ID not configured).")
        elif os.path.exists('credentials.json'):
            writer = GoogleSheetsWriter(
                credentials_path='credentials.json',
                sheet_id=sheet_id,
                worksheet_name=config.get('worksheet_name', 'Wine Prices'),
            )
            writer.add_results(results)
            print("\n✓ Results successfully added to Google Sheet!")
        else:
            print("\nSkipping Google Sheets (no credentials file found).")
    except Exception as e:
        print(f"\nError writing to Google Sheets: {e}")


if __name__ == "__main__":
    main()
