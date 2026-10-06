#!/usr/bin/env python3
"""Discover exact retailer product URLs and add them to the Wine Catalog sheet.

The weekly tracker (price_tracker.py) only reads URLs already in the sheet. This
script fills the sheet for a first run by searching every configured wine and
keeping the product URLs the tracker can read reliably. It never overwrites or
edits existing rows, so it is safe to re-run after adding wines to config.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from contextlib import redirect_stdout
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from price_tracker import _load_config, build_service

DEFAULT_BOTTLE_SIZE = "750mL"


def slugify(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(c for c in value if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def clean_url(url: str) -> Optional[str]:
    """Drop query strings and fragments (search-position tracking params)."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return f"https://{parsed.netloc}{parsed.path}"


def plan_catalog_rows(
    wines: List[str],
    search: Callable[[str], List[Dict]],
    supports: Callable[[str], bool],
    existing_ids: Set[str],
    existing_urls: Set[str],
) -> Tuple[List[List[str]], List[Dict]]:
    """Return (rows to append, skipped entries with a reason).

    One row per wine and retailer. A retailer with several distinct matching
    products is skipped as ambiguous rather than guessed at.
    """
    rows: List[List[str]] = []
    skipped: List[Dict] = []
    for wine in wines:
        candidates: Dict[str, Dict[str, Dict]] = {}
        for result in search(wine):
            url = clean_url(result.get("source_url", ""))
            if not url or not supports(url):
                continue
            candidates.setdefault(result.get("location", ""), {})[url] = result

        for retailer, by_url in sorted(candidates.items()):
            wine_id = f"{slugify(wine)}-{slugify(retailer)}"
            if wine_id in existing_ids or any(u in existing_urls for u in by_url):
                skipped.append({"wine": wine, "retailer": retailer,
                                "reason": "already in catalog"})
                continue
            if len(by_url) > 1:
                skipped.append({"wine": wine, "retailer": retailer,
                                "reason": "ambiguous: multiple matching products",
                                "candidates": sorted(by_url)})
                continue
            url = next(iter(by_url))
            existing_ids.add(wine_id)
            existing_urls.add(url)
            rows.append([wine_id, wine, retailer, url, DEFAULT_BOTTLE_SIZE, "Yes"])
    return rows, skipped


def configured_wines(config: Dict) -> List[str]:
    seen, wines = set(), []
    for name in (config.get("tracked_wines") or []) + (config.get("competitor_wines") or []):
        clean = str(name).strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            wines.append(clean)
    return wines


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Seed the Wine Catalog sheet with discovered product URLs."
    )
    parser.add_argument(
        "--config", default=os.path.join(os.path.dirname(__file__), "config.json"),
        help="Path to config.json",
    )
    parser.add_argument("--wine", action="append",
                        help="Wine to search for (repeatable). Defaults to config wines.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be added without writing to the sheet.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        config = _load_config(args.config)
        wines = args.wine or configured_wines(config)
        if not wines:
            raise ValueError("No wines to search: set tracked_wines in config.json")

        service = build_service(args.config)
        scraper, writer = service.scraper, service.writer
        existing = writer.read_catalog(enabled_only=False)

        def search(wine: str) -> List[Dict]:
            with redirect_stdout(sys.stderr):
                return scraper.search_all_sites(wine)

        rows, skipped = plan_catalog_rows(
            wines, search, scraper.supports_product_url,
            {row["wine_id"] for row in existing},
            {row["product_url"] for row in existing},
        )
        if rows and not args.dry_run:
            writer.catalog_worksheet.append_rows(rows, value_input_option="USER_ENTERED")

        print(json.dumps({
            "success": bool(rows or existing),
            "persisted": bool(rows) and not args.dry_run,
            "added_count": len(rows),
            "added": [{"wine_id": r[0], "wine": r[1], "retailer": r[2], "url": r[3]}
                      for r in rows],
            "skipped": skipped,
            "existing_count": len(existing),
        }, indent=2))
        return 0 if (rows or existing) else 3
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}), file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
