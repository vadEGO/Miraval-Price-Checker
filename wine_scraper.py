#!/usr/bin/env python3
"""
Miraval Price Checker - Searches Australian wine websites and adds results to Google Sheets.

Uses a combination of:
  1. Direct retailer APIs (BWS/Dan Murphy's Endeavour Group API) - fast and reliable
  2. curl_cffi with Chrome TLS impersonation - bypasses 403 blocks on HTML sites
  3. Plain requests fallback for simpler sites (Kent Street Cellars)
"""

import json
import os
import sys
import re
import time
import unicodedata
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Optional
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from concurrent.futures import ThreadPoolExecutor, as_completed

# Try to import curl_cffi for TLS fingerprint impersonation (bypasses 403 blocks)
try:
    from curl_cffi import requests as cffi_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    print("⚠ curl_cffi not installed - run: pip install curl_cffi", file=sys.stderr)


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
        "vinomofo.com": {
            "label": "Vinomofo",
            "search_url": "https://www.vinomofo.com/search?q={query}",
        },
        "justwines.com.au": {
            "label": "Just Wines",
            "search_url": "https://www.justwines.com.au/search?q={query}",
        },
        "boozebud.com": {
            "label": "BoozeBud",
            "search_url": "https://www.boozebud.com/search/{query}",
        },
        "langtons.com.au": {
            "label": "Langton's",
            "search_url": "https://www.langtons.com.au/search?searchTerm={query}",
        },
        "nakedwines.com.au": {
            "label": "Naked Wines",
            "search_url": "https://www.nakedwines.com.au/full-site-search.htm?searchTerm={query}",
        },
        "getwinesdirect.com": {
            "label": "Get Wines Direct",
            "search_url": "https://www.getwinesdirect.com/search?q={query}",
        },
        "crackawines.com.au": {
            "label": "Cracka Wines",
            "search_url": "https://www.crackawines.com.au/search?q={query}",
        },
        "wine.com.au": {
            "label": "Wine.com.au",
            "search_url": "https://www.wine.com.au/search?q={query}",
        },
        "qantaswine.com": {
            "label": "Qantas Wine",
            "search_url": "https://www.qantaswine.com/search?q={query}",
        },
        "winestar.com.au": {
            "label": "WineStar",
            "search_url": "https://www.winestar.com.au/catalogsearch/result/?q={query}",
        },
        "decanterswinecellar.com.au": {
            "label": "Decanters Wine Cellar",
            "search_url": "https://www.decanterswinecellar.com.au/search?type=product&q={query}",
        },
        "winepeople.com.au": {
            "label": "Wine People",
            "search_url": "https://www.winepeople.com.au/search?q={query}",
        },
    }

    def __init__(self, config_path: str = "config.json", use_selenium: bool = False):
        """Initialize the scraper with configuration."""
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.wine_sites = self.config.get('wine_sites', [])
        self.use_selenium = False  # No longer needed - using APIs + curl_cffi instead
        self._last_fetch_error = ""
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
        3. Candidate must NOT contain significant extra words absent from the
           query — e.g. "Miraval Rosé" must not match "Miraval Studio Rosé"
           because "studio" is a meaningful differentiator not in the query.
        """
        query = self._normalize_text(search_term)
        candidate = self._normalize_text(candidate_name)
        if not query or not candidate:
            return False
        query_years = set(re.findall(r"\b(?:19|20)\d{2}\b", query))
        candidate_years = set(re.findall(r"\b(?:19|20)\d{2}\b", candidate))
        if query_years and query_years != candidate_years:
            return False

        query_tokens = [t for t in query.split() if len(t) > 1]
        if not query_tokens:
            return False

        # --- Hard rule: colour/style words must match if present ---
        # Exception: if the candidate has NO colour word at all (e.g. retailer omits
        # "Rosé" from "Whispering Angel Rosé" → just "Whispering Angel"), we still
        # accept the match provided the non-colour tokens strongly match, because the
        # product category is implied by the brand name.
        query_colours = [t for t in query_tokens if t in self._COLOUR_STYLE]
        if query_colours:
            candidate_tokens_set = set(candidate.split())
            candidate_has_any_colour = any(c in candidate_tokens_set for c in self._COLOUR_STYLE)
            if candidate_has_any_colour:
                # Candidate has a colour word — it must match the query colour
                if not any(c in candidate_tokens_set for c in query_colours):
                    return False
            # else: candidate has no colour word at all — allow through (checked by token overlap)

        # --- Token overlap scoring ---
        meaningful_tokens = [t for t in query_tokens if t not in self._FILLER_WORDS]
        if not meaningful_tokens:
            meaningful_tokens = query_tokens

        meaningful_set = set(meaningful_tokens)
        candidate_set = set(candidate.split())
        overlap = sum(1 for t in meaningful_tokens if t in candidate_set)

        # Require majority overlap
        required = max(1, len(meaningful_tokens) // 2 + 1)  # e.g. 3 of 5, 2 of 3, 1 of 1
        if overlap < required:
            return False

        # --- Hard rule: reject candidates with significant extra words ---
        # Candidate tokens that are meaningful (not filler, not size/vintage noise)
        # Wine appellation/region words that retailers commonly append but don't
        # differentiate the product — e.g. "Miraval Rosé" == "Miraval Cotes de Provence Rosé"
        _APPELLATIONS = {
            # French appellations & regions
            'cotes', 'du', 'rhone', 'rhône', 'provence', 'luberon', 'ventoux',
            'gigondas', 'vinsobres', 'chateauneuf', 'pape', 'villages',
            'languedoc', 'bordeaux', 'bourgogne', 'burgundy', 'alsace',
            'champagne', 'loire', 'val', 'medoc', 'graves', 'pessac',
            'leognan', 'pomerol', 'margaux', 'pauillac', 'sauternes',
            # Australian regions
            'australia', 'barossa', 'valley', 'coonawarra', 'mclaren',
            'yarra', 'clare', 'eden', 'margaret', 'river', 'hunter',
            # Estate/château prefixes retailers add to brand names
            # e.g. "Château D'Esclans Whispering Angel" → query is just "Whispering Angel"
            'chateau', 'domaine', 'maison', 'mas', 'cave', 'caves',
            'estate', 'winery', 'cellars', 'cellier',
            # Producer words that appear in retailer product titles but not in short queries
            'esclans', 'perrin', 'famille',
        }
        _NOISE = self._FILLER_WORDS | _APPELLATIONS | {
            '750ml', '750', '700ml', '1l', '2024', '2025', '2023',
            '2022', '2021', '2020', '2019', '2018', 'nv', 'each',
            'bottle', 'pack', '6pk', '12pk', 'case', 'single',
        }
        candidate_tokens = [
            t for t in candidate.split()
            if len(t) > 1 and t not in _NOISE and not re.fullmatch(r"(?:19|20)\d{2}", t)
        ]
        extra = [t for t in candidate_tokens if t not in meaningful_set]
        # For short queries (≤3 meaningful tokens), zero tolerance for extra differentiating
        # words — "Miraval Rosé" must NOT match "Miraval Studio Rosé".
        # For longer, more descriptive queries allow 1 extra word (regional/appellation suffix etc.)
        max_extra = 0 if len(meaningful_tokens) <= 3 else 1
        if len(extra) > max_extra:
            return False

        return True

    def _search_endeavour_api(self, wine_name: str, brand: str) -> List[Dict]:
        """Search BWS or Jimmy Brings via the BWS JSON API (Endeavour Group).

        Uses Australia-wide inventory (backorder stock) so results reflect
        national availability, not a single local store.
        """
        results = []
        api_configs = {
            'bws': {
                'api_url': 'https://api.bws.com.au/apis/ui/Search/products',
                'origin': 'https://www.bws.com.au',
                'referer': 'https://www.bws.com.au/',
                'location': 'BWS',
            },
            'jimmybrings': {
                'api_url': 'https://api.bws.com.au/apis/ui/Search/products',
                'origin': 'https://www.jimmybrings.com.au',
                'referer': 'https://www.jimmybrings.com.au/',
                'location': 'Jimmy Brings',
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

            if isinstance(data, dict) and 'Products' in data:
                packs = data['Products']
                for pack in packs[:5]:
                    inner_products = pack.get('Products', [])
                    for product in inner_products[:1]:
                        name = product.get('Name', '').strip()
                        name = re.sub(r'<br\s*/?>', ' ', name)
                        price = product.get('Price')
                        was_price = product.get('WasPrice')
                        on_special = product.get('IsOnSpecial', False)
                        size = product.get('PackageSize', '')
                        # Use backorder stock for Australia-wide availability
                        stock_on_hand = (product.get('BackorderStockOnHand') or
                                         product.get('StockOnHand') or 0)
                        available = bool(stock_on_hand) or product.get('IsAvailable', False)

                        if not name:
                            continue
                        if not self._is_relevant_match(wine_name, name):
                            continue

                        bws_product_slug = None
                        for detail in product.get('AdditionalDetails', []):
                            dname = detail.get('Name', '').lower()
                            dval = str(detail.get('Value', ''))
                            if dname == 'bwsproducturl':
                                bws_product_slug = dval.strip()

                        stockcode = product.get('Stockcode') or product.get('ParentStockCode')
                        slug = bws_product_slug or product.get('UrlFriendlyName', '')

                        if brand == 'jimmybrings':
                            if slug and stockcode:
                                product_url = f"https://www.jimmybrings.com.au/product/JB_{stockcode}/{slug}"
                            else:
                                product_url = f"https://www.jimmybrings.com.au/search?q={quote_plus(wine_name)}"
                        else:  # bws
                            if slug and stockcode:
                                product_url = f"https://www.bws.com.au/product/{stockcode}/{slug}"
                            elif slug:
                                product_url = f"https://www.bws.com.au/product/{slug}"
                            else:
                                product_url = f"https://www.bws.com.au/search?q={quote_plus(wine_name)}"

                        display_name = f"{name} {size}".strip() if size else name
                        price_str = f"${price:.2f}" if price else "N/A"
                        if on_special and was_price and price and was_price > price:
                            price_str = f"${price:.2f} (was ${was_price:.2f})"
                        if not available:
                            price_str += " [Out of Stock]"

                        results.append({
                            'wine': display_name,
                            'price': price_str,
                            'location': config['location'],
                            'source_url': product_url,
                            'in_stock': bool(available),
                        })

        except Exception as e:
            print(f"    ✗ API error: {e}")

        return results[:5]

    def search_dan_murphys(self, wine_name: str) -> List[Dict]:
        """Search Dan Murphy's via their own JSON API.

        Uses Australia-wide (national) inventory so results reflect
        what is available for delivery anywhere in Australia.
        """
        results = []
        headers = {
            'User-Agent': self.headers['User-Agent'],
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-AU,en;q=0.9',
            'Origin': 'https://www.danmurphys.com.au',
            'Referer': 'https://www.danmurphys.com.au/',
        }
        try:
            params = {'searchTerm': wine_name, 'pageSize': 10}
            if CURL_CFFI_AVAILABLE:
                r = cffi_requests.get(
                    'https://api.danmurphys.com.au/apis/ui/Search/products',
                    headers=headers, params=params, timeout=15, impersonate='chrome',
                )
            else:
                r = requests.get(
                    'https://api.danmurphys.com.au/apis/ui/Search/products',
                    headers=headers, params=params, timeout=15,
                )

            if r.status_code != 200:
                print(f"    ⚠ DM API returned HTTP {r.status_code}")
                return results

            data = r.json()
            if not (isinstance(data, dict) and 'Products' in data):
                return results

            for pack in data['Products'][:5]:
                inner = pack.get('Products', [])
                for product in inner[:1]:
                    name = re.sub(r'<br\s*/?>', ' ', pack.get('Name', ''))
                    name = re.sub(r'\s+', ' ', name.replace('\n', ' ')).strip()
                    if not name:
                        name = product.get('UrlFriendlyName', '').replace('-', ' ').title()
                    if not name or not self._is_relevant_match(wine_name, name):
                        continue

                    # DM uses a Prices dict with singleprice / promoprice
                    prices = product.get('Prices', {})
                    single = prices.get('singleprice', {})
                    member = prices.get('promoprice', {})
                    single_val = single.get('Value')
                    member_val = member.get('Value') if member.get('IsMemberOffer') else None

                    if single_val:
                        price_str = f"${single_val:.2f}"
                        if member_val and member_val < single_val:
                            price_str += f" (member ${member_val:.2f})"
                    else:
                        price_str = "N/A"

                    size = product.get('PackageSize', '')
                    display_name = f"{name} {size}".strip() if size else name

                    # Australia-wide: use backorder inventory
                    inv = product.get('Inventory', {})
                    stock = (inv.get('backorderavailableinventoryqty') or
                             inv.get('availableinventoryqty') or
                             product.get('StockOnHand') or 0)
                    available = bool(stock)
                    if not available:
                        price_str += " [Out of Stock]"

                    stockcode = product.get('Stockcode') or product.get('ParentStockCode')
                    slug = product.get('UrlFriendlyName', '')
                    # DM uses dm_stockcode from AdditionalDetails for the URL
                    dm_code = None
                    for d in product.get('AdditionalDetails', []):
                        if d.get('Name', '').lower() == 'dm_stockcode':
                            dm_code = d.get('Value', '').strip()
                    if slug and dm_code:
                        product_url = f"https://www.danmurphys.com.au/product/{dm_code}/{slug}"
                    elif slug and stockcode:
                        product_url = f"https://www.danmurphys.com.au/product/DM_{stockcode}/{slug}"
                    else:
                        product_url = f"https://www.danmurphys.com.au/buy/search-results/q={quote_plus(wine_name)}"

                    results.append({
                        'wine': display_name,
                        'price': price_str,
                        'location': "Dan Murphy's",
                        'source_url': product_url,
                        'in_stock': bool(available),
                    })

        except Exception as e:
            print(f"    ✗ DM API error: {e}")

        return results[:5]

    def search_bws(self, wine_name: str) -> List[Dict]:
        """Search BWS via Endeavour Group API."""
        return self._search_endeavour_api(wine_name, 'bws')

    def search_jimmy_brings(self, wine_name: str) -> List[Dict]:
        """Search Jimmy Brings via Endeavour Group API."""
        return self._search_endeavour_api(wine_name, 'jimmybrings')

    def search_vintage_cellars(self, wine_name: str) -> List[Dict]:
        """Search Vintage Cellars using their search page."""
        url = f"https://www.vintagecellars.com.au/search?q={quote_plus(wine_name)}"
        return self._scrape_html_site(url, 'Vintage Cellars', wine_name)

    # ── HTML scraping (Kent Street Cellars, Liquorland, First Choice) ───

    def _get_page_content(self, url: str) -> Optional[str]:
        """Get page content using the best available HTTP method.

        Priority: curl_cffi (TLS impersonation) > plain requests
        """
        self._last_fetch_error = ""
        retryable_statuses = {408, 429, 500, 502, 503, 504}

        def request_with_retry(label, request_call):
            for attempt in range(3):
                try:
                    response = request_call()
                    if response.status_code == 200:
                        return response.text
                    self._last_fetch_error = (
                        f"{label} returned HTTP {response.status_code}"
                    )
                    if response.status_code not in retryable_statuses:
                        break
                except Exception as exc:
                    self._last_fetch_error = f"{label} request failed: {exc}"
                if attempt < 2:
                    time.sleep(2 ** attempt)
            return None

        # Method 1: curl_cffi with Chrome TLS impersonation
        if CURL_CFFI_AVAILABLE:
            print(f"    Fetching {url} (impersonating Chrome)...")
            content = request_with_retry(
                "Chrome-compatible fetch",
                lambda: cffi_requests.get(
                    url, headers=self.headers, timeout=15,
                    impersonate="chrome", allow_redirects=True,
                ),
            )
            if content is not None:
                return content
            print(f"    ⚠ {self._last_fetch_error}")

        # Method 2: Plain requests (may get 403 on protected sites)
        print(f"    Fetching {url} (plain request)...")
        content = request_with_retry(
            "Plain fetch",
            lambda: requests.get(
                url, headers=self.headers, timeout=10, allow_redirects=True
            ),
        )
        if content is None:
            print(f"    ✗ {self._last_fetch_error}")
        return content

    _CHALLENGE_MARKERS = (
        "captcha", "access denied", "verify you are human", "cf-chl-",
        "attention required! | cloudflare",
        "perimeterx", "shieldsquare", "bot detection", "security challenge",
    )

    @staticmethod
    def _size_in_ml(value: str) -> Optional[float]:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(ml|cl|l)\b", value or "", re.I)
        if not match:
            return None
        amount = float(match.group(1))
        unit = match.group(2).lower()
        return amount * {"ml": 1, "cl": 10, "l": 1000}[unit]

    @staticmethod
    def _json_nodes(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from WineScraper._json_nodes(child)
        elif isinstance(value, list):
            for child in value:
                yield from WineScraper._json_nodes(child)

    def extract_product_page(self, html: str, url: str, wine_name: str,
                             retailer: str, expected_bottle_size: str = "") -> Dict:
        """Extract one exact product page without inferring ambiguous prices."""
        base = {
            "product_name": "",
            "price_amount": None,
            "regular_price_amount": None,
            "currency": "",
            "in_stock": None,
            "status": "needs_review",
            "error": "",
        }
        lowered = (html or "").lower()
        if not html:
            return {**base, "status": "error", "error": "Empty retailer response"}
        if any(marker in lowered for marker in self._CHALLENGE_MARKERS):
            return {
                **base,
                "status": "error",
                "error": "Retailer returned a bot challenge instead of a product page",
            }

        soup = BeautifulSoup(html, "html.parser")
        product_nodes = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                payload = json.loads(script.string or script.get_text() or "")
                for node in self._json_nodes(payload):
                    node_type = node.get("@type", "")
                    node_types = node_type if isinstance(node_type, list) else [node_type]
                    if any(str(value).lower() == "product" for value in node_types):
                        product_nodes.append(node)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

        matching_nodes = [
            node for node in product_nodes
            if self._is_relevant_match(wine_name, str(node.get("name", "")))
        ]
        product = (matching_nodes or product_nodes or [{}])[0]
        product_name = str(product.get("name", "")).strip()
        if not product_name:
            heading = soup.find("h1")
            product_name = heading.get_text(" ", strip=True) if heading else ""
        if not product_name:
            title_meta = soup.find("meta", property="og:title")
            product_name = (title_meta or {}).get("content", "").strip()
        base["product_name"] = product_name[:200]
        if not product_name:
            base["error"] = "No product title could be validated"
            return base

        offers = product.get("offers", {})
        if isinstance(offers, list):
            offer_list = [item for item in offers if isinstance(item, dict)]
        elif isinstance(offers, dict):
            offer_list = [offers]
        else:
            offer_list = []

        amounts = []
        member_amounts = []
        currency = ""
        explicit_dollar_price = False
        availability_text = ""
        for offer in offer_list:
            candidate = offer.get("price", offer.get("lowPrice"))
            try:
                if candidate not in (None, ""):
                    amount = float(str(candidate).replace(",", ""))
                    offer_text = " ".join(str(offer.get(key, "")) for key in (
                        "name", "description", "eligibleCustomerType", "priceType"
                    ))
                    if re.search(r"\b(members?|loyalty|club)\b", offer_text, re.I):
                        member_amounts.append(amount)
                    else:
                        amounts.append(amount)
            except (TypeError, ValueError):
                pass
            currency = currency or str(offer.get("priceCurrency", "")).upper()
            availability_text += " " + str(offer.get("availability", "")).lower()

        if not amounts and not member_amounts:
            price_meta = (
                soup.find("meta", attrs={"itemprop": "price"})
                or soup.find("meta", property="product:price:amount")
            )
            raw_meta_price = (price_meta or {}).get("content", "")
            try:
                if raw_meta_price:
                    amounts.append(float(str(raw_meta_price).replace(",", "")))
            except (TypeError, ValueError):
                pass
            currency_meta = (
                soup.find("meta", attrs={"itemprop": "priceCurrency"})
                or soup.find("meta", property="product:price:currency")
            )
            currency = currency or (currency_meta or {}).get("content", "").upper()

        if not amounts and not member_amounts:
            price_elements = soup.find_all(
                ["span", "div", "p"],
                class_=re.compile(r"(^|[-_\s])(sale-)?price($|[-_\s])", re.I),
            )
            for element in price_elements[:10]:
                text = element.get_text(" ", strip=True)
                if re.search(r"\b(save|saving|off)\b", text, re.I):
                    continue
                match = re.search(r"\$\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)", text)
                if match:
                    amounts.append(float(match.group(1).replace(",", "")))
                    explicit_dollar_price = True

        unique_amounts = sorted({value for value in amounts if 5 <= value <= 1000})
        if not unique_amounts and member_amounts:
            base["error"] = "Only a member or loyalty price was found"
            return base
        if unique_amounts:
            base["price_amount"] = unique_amounts[0]
            if len(unique_amounts) > 1:
                base["regular_price_amount"] = unique_amounts[-1]
            base["currency"] = currency or ("AUD" if explicit_dollar_price else "")

        page_text = soup.get_text(" ", strip=True)
        combined_identity = f"{product_name} {page_text[:5000]}"
        expected_ml = self._size_in_ml(expected_bottle_size)
        observed_sizes = {
            self._size_in_ml(match.group(0))
            for match in re.finditer(r"\d+(?:\.\d+)?\s*(?:ml|cl|l)\b", combined_identity, re.I)
        }
        observed_sizes.discard(None)
        if expected_ml and expected_ml not in observed_sizes:
            base["error"] = (
                f"Expected {expected_bottle_size}, but that bottle size was not "
                "confirmed on the product page"
            )
            return base

        pack_pattern = (
            r"\b(case|dozen|pack\s+of\s+\d+|\d+\s*pk|\d+\s+bottles?|"
            r"\d+\s*x\s*\d+\s*(?:ml|cl|l))\b"
        )
        if re.search(pack_pattern, f"{product_name} {page_text[:5000]}", re.I):
            base["error"] = "Product appears to be a multi-bottle pack or case"
            return base

        if wine_name and product_name and not self._is_relevant_match(wine_name, product_name):
            base["error"] = "Product title does not match the catalog wine name"
            return base

        if availability_text.strip():
            normalized_availability = availability_text.replace("/", "")
            out_of_stock = "outofstock" in normalized_availability
            in_stock = "instock" in normalized_availability and not out_of_stock
        else:
            out_of_stock = bool(
                re.search(r"\b(out of stock|sold out|unavailable)\b", page_text, re.I)
            )
            in_stock = bool(
                re.search(r"\b(in stock|available now)\b", page_text, re.I)
            )
        base["in_stock"] = False if out_of_stock else (True if in_stock else None)

        if out_of_stock:
            base["status"] = "unavailable"
            return base
        if base["price_amount"] is None:
            base["error"] = "No explicit product price could be validated"
            return base
        if base["currency"] != "AUD":
            base["error"] = f"Expected AUD pricing, found {base['currency'] or 'no currency'}"
            return base
        if base["in_stock"] is None:
            base["error"] = "Product availability could not be validated"
            return base

        base["status"] = "success"
        return base

    def fetch_product_url(self, url: str, wine_name: str, retailer: str,
                          expected_bottle_size: str = "") -> Dict:
        """Fetch and validate one catalog URL."""
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            return {
                "product_name": "",
                "price_amount": None,
                "regular_price_amount": None,
                "currency": "",
                "in_stock": None,
                "status": "error",
                "error": "Product URL must be an absolute HTTPS URL",
            }
        html = self._get_page_content(url)
        result = self.extract_product_page(
            html or "", url, wine_name, retailer, expected_bottle_size
        )
        if not html and self._last_fetch_error:
            result["error"] = self._last_fetch_error
        return result

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
                price_text = str(price).strip()
                pm = re.search(r'\$\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)', price_text)
                if not pm:
                    pm = re.fullmatch(r'\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*', price_text)
                if pm:
                    numeric = pm.group(1).replace(',', '')
                    pv = float(numeric)
                    formatted_price = f"${numeric}" if 5 <= pv <= 500 else "N/A"
                else:
                    formatted_price = str(price)
            else:
                formatted_price = "N/A"

            # Filter by search term
            if wine_search_term and not self._is_relevant_match(wine_search_term, name):
                return None

            if formatted_price == "N/A" and wine_search_term:
                return None  # Skip priceless results when we have a specific search

            link = product_element.find('a', href=True)
            source_url = link['href'].strip() if link and link.get('href') else ''
            if source_url.startswith('//'):
                source_url = f"https:{source_url}"

            return {
                'wine': name[:200],
                'price': formatted_price,
                'location': location,
                'source_url': source_url,
                'in_stock': formatted_price != 'N/A',
            }

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
                if not info.get('source_url'):
                    info['source_url'] = url
                elif isinstance(info['source_url'], str) and info['source_url'].startswith('/'):
                    domain = '/'.join(url.split('/')[:3])
                    info['source_url'] = f"{domain}{info['source_url']}"
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

    def get_site_label(self, site_key: str) -> str:
        """Return a human-readable label for a site key."""
        labels = {
            'danmurphys.com.au': "Dan Murphy's",
            'bws.com.au': 'BWS',
            'jimmybrings.com.au': 'Jimmy Brings',
            'liquorland.com.au': 'Liquorland',
            'firstchoice.com.au': 'First Choice',
            'vintagecellars.com.au': 'Vintage Cellars',
        }
        if site_key in labels:
            return labels[site_key]
        cfg = self.INDEPENDENT_SITE_CONFIG.get(site_key)
        return cfg["label"] if cfg else site_key

    def search_all_sites(self, wine_name: str, progress_callback=None) -> List[Dict]:
        """Search all configured wine sites in parallel.

        Args:
            wine_name: Wine to search for.
            progress_callback: Optional callable(site_key, site_index, total_sites, results_so_far)
                               invoked as each site completes.
        """
        site_handlers = {
            'danmurphys.com.au': self.search_dan_murphys,
            'bws.com.au': self.search_bws,
            'jimmybrings.com.au': self.search_jimmy_brings,
            'liquorland.com.au': self.search_liquorland,
            'firstchoice.com.au': self.search_first_choice,
            'vintagecellars.com.au': self.search_vintage_cellars,
        }

        total = len(self.wine_sites)
        all_results = []
        completed = 0

        def _search_one(site):
            handler = site_handlers.get(site)
            if handler:
                return site, handler(wine_name)
            elif site in self.INDEPENDENT_SITE_CONFIG:
                return site, self.search_independent_site(wine_name, site)
            return site, []

        # API-backed sites (fast, no rate-limit concerns) run at full concurrency;
        # HTML scrapers run with a modest cap to avoid hammering sites.
        api_sites = [s for s in self.wine_sites if s in site_handlers]
        html_sites = [s for s in self.wine_sites if s not in site_handlers]

        futures_map = {}
        executor = ThreadPoolExecutor(max_workers=10)

        for site in api_sites + html_sites:
            futures_map[executor.submit(_search_one, site)] = site

        try:
            for future in as_completed(futures_map):
                site = futures_map[future]
                try:
                    _, results = future.result()
                    print(f"  ✓ {site}: {len(results)} result(s)")
                    all_results.extend(results)
                except Exception as e:
                    print(f"  ✗ {site}: {e}")
                completed += 1
                if progress_callback:
                    progress_callback(site, completed - 1, total, len(all_results))
        finally:
            executor.shutdown(wait=False)

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
    CATALOG_WORKSHEET = "Wine Catalog"
    HISTORY_WORKSHEET = "Price History"
    CATALOG_HEADERS = [
        "Wine ID", "Wine Name", "Retailer", "Product URL",
        "Expected Bottle Size", "Enabled", "Latest Price", "Currency",
        "In Stock", "Last Checked UTC", "Status", "Error",
    ]
    HISTORY_HEADERS = [
        "Observation ID", "Run ID", "Wine ID", "Wine Name", "Retailer",
        "Price Amount", "Regular Price Amount", "Currency", "In Stock",
        "Source URL", "Observed At UTC", "Status", "Error",
    ]

    def __init__(self, credentials=None, credentials_path: str = None,
                 sheet_id: str = None, worksheet_name: str = "Wine Prices"):
        self.sheet_id = sheet_id
        self.worksheet_name = worksheet_name
        self.spreadsheet = None
        self.worksheet = None
        self.catalog_worksheet = None
        self.history_worksheet = None

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
        """Open an existing spreadsheet and validate tracking worksheets."""
        self.sheet_id = sheet_id
        self.worksheet_name = worksheet_name
        self.spreadsheet = self.client.open_by_key(sheet_id)
        self.ensure_tracking_schema()

    def _ensure_worksheet(self, title: str, headers: List[str]):
        try:
            worksheet = self.spreadsheet.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = self.spreadsheet.add_worksheet(
                title=title, rows=1000, cols=len(headers)
            )
        existing = worksheet.row_values(1)
        if not existing:
            worksheet.append_row(headers)
            worksheet.format(
                f"A1:{self._column_letter(len(headers))}1",
                {"textFormat": {"bold": True}},
            )
            worksheet.freeze(rows=1)
        elif existing != headers:
            raise ValueError(
                f"Worksheet '{title}' has an unexpected schema. "
                f"Expected headers: {', '.join(headers)}"
            )
        return worksheet

    def ensure_tracking_schema(self):
        if not self.spreadsheet:
            raise ValueError("No spreadsheet is open")
        self.catalog_worksheet = self._ensure_worksheet(
            self.CATALOG_WORKSHEET, self.CATALOG_HEADERS
        )
        self.history_worksheet = self._ensure_worksheet(
            self.HISTORY_WORKSHEET, self.HISTORY_HEADERS
        )
        self.worksheet = self.history_worksheet

    def create_spreadsheet(self, title: str = "Wine Prices – Miraval Price Checker"):
        """Create a new spreadsheet and set it as active. Returns metadata dict."""
        spreadsheet = self.client.create(title)
        self.spreadsheet = spreadsheet
        self.sheet_id = spreadsheet.id
        spreadsheet.sheet1.update_title(self.CATALOG_WORKSHEET)
        self.ensure_tracking_schema()
        return {
            'id': spreadsheet.id,
            'url': spreadsheet.url,
            'title': spreadsheet.title,
        }

    @staticmethod
    def _enabled(value) -> bool:
        return str(value).strip().lower() in {"1", "true", "yes", "y", "enabled"}

    @staticmethod
    def _column_letter(number: int) -> str:
        letters = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            letters = chr(65 + remainder) + letters
        return letters

    def read_catalog(self, enabled_only: bool = True) -> List[Dict]:
        if not self.catalog_worksheet:
            self.ensure_tracking_schema()
        records = self.catalog_worksheet.get_all_records()
        catalog = []
        seen_ids = set()
        for row_number, record in enumerate(records, start=2):
            enabled = self._enabled(record.get("Enabled", ""))
            if enabled_only and not enabled:
                continue
            wine_id = str(record.get("Wine ID", "")).strip()
            wine_name = str(record.get("Wine Name", "")).strip()
            retailer = str(record.get("Retailer", "")).strip()
            product_url = str(record.get("Product URL", "")).strip()
            if not all((wine_id, wine_name, retailer, product_url)):
                raise ValueError(
                    f"Wine Catalog row {row_number} is missing Wine ID, Wine Name, "
                    "Retailer, or Product URL"
                )
            if wine_id in seen_ids:
                raise ValueError(f"Duplicate Wine ID in Wine Catalog: {wine_id}")
            parsed = urlparse(product_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError(
                    f"Wine Catalog row {row_number} has an invalid HTTPS Product URL"
                )
            seen_ids.add(wine_id)
            catalog.append({
                "row_number": row_number,
                "wine_id": wine_id,
                "wine_name": wine_name,
                "retailer": retailer,
                "product_url": product_url,
                "expected_bottle_size": str(
                    record.get("Expected Bottle Size", "")
                ).strip(),
                "enabled": enabled,
            })
        return catalog

    def record_tracking_run(self, observations: List[Dict]) -> Dict:
        if not self.history_worksheet or not self.catalog_worksheet:
            self.ensure_tracking_schema()
        history_records = self.history_worksheet.get_all_records()
        existing_records = {
            str(row.get("Observation ID", "")).strip(): row
            for row in history_records
            if str(row.get("Observation ID", "")).strip()
        }
        existing_ids = set(existing_records)
        new_observations = [
            row for row in observations
            if row["observation_id"] not in existing_ids
        ]
        rows = []
        for row in new_observations:
            in_stock = row.get("in_stock")
            rows.append([
                row["observation_id"],
                row["run_id"],
                row["wine_id"],
                row["wine_name"],
                row["retailer"],
                row.get("price_amount", ""),
                row.get("regular_price_amount", ""),
                row.get("currency", ""),
                "" if in_stock is None else ("Yes" if in_stock else "No"),
                row["source_url"],
                row["observed_at_utc"],
                row["status"],
                row.get("error", ""),
            ])
        if rows:
            self.history_worksheet.append_rows(
                rows, value_input_option="USER_ENTERED"
            )

        catalog = {
            row["wine_id"]: row for row in self.read_catalog(enabled_only=False)
        }
        header_index = {
            name: index + 1 for index, name in enumerate(self.CATALOG_HEADERS)
        }
        updated = 0
        cell_updates = []
        for observation in observations:
            persisted = existing_records.get(observation["observation_id"])
            if persisted:
                stock_value = str(persisted.get("In Stock", "")).strip().lower()
                persisted_stock = (
                    True if stock_value == "yes"
                    else False if stock_value == "no"
                    else None
                )
                observation = {
                    **observation,
                    "price_amount": persisted.get("Price Amount", ""),
                    "currency": persisted.get("Currency", ""),
                    "in_stock": persisted_stock,
                    "observed_at_utc": persisted.get("Observed At UTC", ""),
                    "status": persisted.get("Status", ""),
                    "error": persisted.get("Error", ""),
                }
            item = catalog.get(observation["wine_id"])
            if not item:
                continue
            row_number = item["row_number"]
            in_stock = observation.get("in_stock")
            updates = {
                "Last Checked UTC": observation["observed_at_utc"],
                "Status": observation["status"],
                "Error": observation.get("error", ""),
            }
            if observation.get("price_amount") is not None:
                updates["Latest Price"] = observation["price_amount"]
                updates["Currency"] = observation.get("currency", "")
            if in_stock is not None:
                updates["In Stock"] = "Yes" if in_stock else "No"
            for column, value in updates.items():
                cell = f"{self._column_letter(header_index[column])}{row_number}"
                cell_updates.append({
                    "range": cell,
                    "values": [["" if value is None else value]],
                })
            updated += 1
        if cell_updates:
            self.catalog_worksheet.batch_update(
                cell_updates, value_input_option="USER_ENTERED"
            )
        return {
            "appended": len(rows),
            "updated": updated,
            "skipped": len(observations) - len(rows),
        }

    def add_results(self, results: List[Dict]):
        if not results:
            print("No results to add.")
            return
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        run_id = datetime.now(timezone.utc).strftime("manual-%Y%m%dT%H%M%SZ")
        rows = []
        for index, result in enumerate(results):
            price_match = re.search(
                r"\$\s*(\d+(?:\.\d{1,2})?)", str(result.get("price", ""))
            )
            observation_id = hashlib.sha256(
                f"{run_id}\0{index}\0{result.get('source_url', '')}".encode()
            ).hexdigest()[:24]
            rows.append([
                observation_id,
                run_id,
                result.get("query", ""),
                result.get("wine", ""),
                result.get("location", ""),
                float(price_match.group(1)) if price_match else "",
                "",
                "AUD" if price_match else "",
                "Yes" if result.get("in_stock", True) else "No",
                result.get("source_url", ""),
                now,
                "needs_review",
                "Legacy browser result; verify against the source URL",
            ])
        self.history_worksheet.append_rows(rows, value_input_option="USER_ENTERED")
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
