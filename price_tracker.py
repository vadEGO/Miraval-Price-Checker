#!/usr/bin/env python3
"""Deterministic, sheet-driven price tracking harness."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

from google.oauth2.service_account import Credentials as ServiceAccountCredentials

from wine_scraper import GoogleSheetsWriter, WineScraper

try:
    import fcntl
except ImportError:  # pragma: no cover - Codex runners are Linux
    fcntl = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_run_id(now: Optional[datetime] = None) -> str:
    return (now or utc_now()).strftime("%Y%m%dT%H%M%SZ")


def make_observation_id(run_id: str, wine_id: str) -> str:
    value = f"{run_id}\0{wine_id}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:24]


@contextmanager
def single_run_lock(config_path: str):
    """Prevent overlapping local scheduler runs."""
    if fcntl is None:
        yield
        return
    lock_path = os.path.join(os.path.dirname(os.path.abspath(config_path)),
                             ".price_tracker.lock")
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another price tracking run is already active") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class PriceTrackingService:
    """Read tracked URLs, collect observations, and persist one atomic run."""

    def __init__(self, scraper: WineScraper, writer: GoogleSheetsWriter):
        self.scraper = scraper
        self.writer = writer

    def run(self, run_id: Optional[str] = None, persist: bool = True) -> Dict:
        run_id = run_id or make_run_id()
        catalog = self.writer.read_catalog(enabled_only=True)
        if not catalog:
            raise ValueError("Wine Catalog contains no enabled product URLs")

        observations: List[Dict] = []
        for item in catalog:
            with redirect_stdout(sys.stderr):
                result = self.scraper.fetch_product_url(
                    url=item["product_url"],
                    wine_name=item["wine_name"],
                    retailer=item["retailer"],
                    expected_bottle_size=item.get("expected_bottle_size", ""),
                )
            checked_at = utc_now().replace(microsecond=0).isoformat()
            observations.append({
                **result,
                "observation_id": make_observation_id(run_id, item["wine_id"]),
                "run_id": run_id,
                "wine_id": item["wine_id"],
                "wine_name": item["wine_name"],
                "retailer": item["retailer"],
                "source_url": item["product_url"],
                "observed_at_utc": checked_at,
            })

        write_result = {"appended": 0, "updated": 0, "skipped": 0}
        if persist:
            write_result = self.writer.record_tracking_run(observations)

        counts = {
            state: sum(1 for row in observations if row["status"] == state)
            for state in ("success", "unavailable", "needs_review", "error")
        }
        return {
            "success": counts["success"] + counts["unavailable"] > 0,
            "run_id": run_id,
            "catalog_count": len(catalog),
            "counts": counts,
            "write": write_result,
            "persisted": persist,
            "results": observations,
        }


def _load_config(path: str) -> Dict:
    with open(path, encoding="utf-8") as config_file:
        return json.load(config_file)


def _service_account_credentials(base_dir: str):
    scopes = GoogleSheetsWriter.SCOPES
    inline_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if inline_json:
        return ServiceAccountCredentials.from_service_account_info(
            json.loads(inline_json), scopes=scopes
        )

    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not credentials_path:
        credentials_path = os.path.join(base_dir, "credentials.json")
    if not os.path.exists(credentials_path):
        raise FileNotFoundError(
            "Google credentials not found. Set GOOGLE_SERVICE_ACCOUNT_JSON or "
            "GOOGLE_APPLICATION_CREDENTIALS."
        )
    return ServiceAccountCredentials.from_service_account_file(
        credentials_path, scopes=scopes
    )


def build_service(config_path: str) -> PriceTrackingService:
    config_path = os.path.abspath(config_path)
    base_dir = os.path.dirname(config_path)
    config = _load_config(config_path)
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    sheet_id = sheet_id or str(config.get("google_sheet_id", "")).strip()
    if not sheet_id or sheet_id == "YOUR_GOOGLE_SHEET_ID":
        raise ValueError("Set GOOGLE_SHEET_ID or google_sheet_id in config.json")

    credentials = _service_account_credentials(base_dir)
    writer = GoogleSheetsWriter(credentials=credentials, sheet_id=sheet_id)
    with redirect_stdout(sys.stderr):
        scraper = WineScraper(config_path)
    return PriceTrackingService(scraper, writer)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Track exact product URLs listed in Google Sheets."
    )
    parser.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(__file__), "config.json"),
        help="Path to config.json",
    )
    parser.add_argument(
        "--run-id",
        help="Stable retry ID. Reusing it makes history writes idempotent.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and scrape the catalog without writing observations.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        with single_run_lock(args.config):
            summary = build_service(args.config).run(
                run_id=args.run_id, persist=not args.dry_run
            )
        print(json.dumps(summary, indent=2, sort_keys=True))
        if not summary["success"]:
            return 3
        return 0
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}), file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
