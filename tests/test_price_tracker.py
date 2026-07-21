import json
import os
import tempfile
import unittest

from bs4 import BeautifulSoup

from price_tracker import PriceTrackingService, make_observation_id
from wine_scraper import GoogleSheetsWriter, WineScraper
import app as app_module


def product_html(
    *,
    name="Miraval Rosé 750mL",
    offers=None,
    availability="https://schema.org/InStock",
    body="",
):
    if offers is None:
        offers = {
            "@type": "Offer",
            "price": "29.99",
            "priceCurrency": "AUD",
            "availability": availability,
        }
    product = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": name,
        "offers": offers,
    }
    return (
        "<html><body>"
        f'<script type="application/ld+json">{json.dumps(product)}</script>'
        f"<h1>{name}</h1><p>{body}</p>"
        "</body></html>"
    )


class ProductExtractionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"wine_sites": []}, temp)
        temp.close()
        self.config_path = temp.name
        self.scraper = WineScraper(self.config_path)

    def tearDown(self):
        os.unlink(self.config_path)

    def extract(self, html):
        return self.scraper.extract_product_page(
            html,
            "https://retailer.example/miraval",
            "Miraval Rosé",
            "Example Retailer",
            "750mL",
        )

    def test_extracts_structured_aud_price(self):
        result = self.extract(product_html())
        self.assertEqual("success", result["status"])
        self.assertEqual(29.99, result["price_amount"])
        self.assertEqual("AUD", result["currency"])
        self.assertTrue(result["in_stock"])

    def test_records_sale_and_regular_prices(self):
        offers = [
            {
                "@type": "Offer", "price": "25", "priceCurrency": "AUD",
                "availability": "https://schema.org/InStock",
            },
            {
                "@type": "Offer", "price": "30", "priceCurrency": "AUD",
                "availability": "https://schema.org/InStock",
            },
        ]
        result = self.extract(product_html(offers=offers))
        self.assertEqual("success", result["status"])
        self.assertEqual(25.0, result["price_amount"])
        self.assertEqual(30.0, result["regular_price_amount"])

    def test_member_only_price_requires_review(self):
        offers = {
            "@type": "Offer",
            "price": "24",
            "priceCurrency": "AUD",
            "description": "Members only",
        }
        result = self.extract(product_html(offers=offers))
        self.assertEqual("needs_review", result["status"])
        self.assertIn("member", result["error"].lower())

    def test_case_price_requires_review(self):
        result = self.extract(product_html(name="Miraval Rosé Case of 6 750mL"))
        self.assertEqual("needs_review", result["status"])
        self.assertIn("pack or case", result["error"])

    def test_case_text_in_body_requires_review(self):
        result = self.extract(product_html(body="Price shown for 6 bottles"))
        self.assertEqual("needs_review", result["status"])
        self.assertIn("pack or case", result["error"])

    def test_out_of_stock_is_explicit(self):
        result = self.extract(product_html(
            availability="https://schema.org/OutOfStock",
            body="Out of stock",
        ))
        self.assertEqual("unavailable", result["status"])
        self.assertFalse(result["in_stock"])

    def test_captcha_is_an_error(self):
        result = self.extract("<html><title>CAPTCHA</title>Verify you are human</html>")
        self.assertEqual("error", result["status"])
        self.assertIn("bot challenge", result["error"])

    def test_missing_price_requires_review(self):
        result = self.extract(product_html(offers={}))
        self.assertEqual("needs_review", result["status"])
        self.assertIn("No explicit product price", result["error"])

    def test_missing_currency_requires_review(self):
        offers = {
            "@type": "Offer",
            "price": "29.99",
            "availability": "https://schema.org/InStock",
        }
        result = self.extract(product_html(offers=offers))
        self.assertEqual("needs_review", result["status"])
        self.assertIn("no currency", result["error"])

    def test_missing_availability_requires_review(self):
        offers = {
            "@type": "Offer",
            "price": "29.99",
            "priceCurrency": "AUD",
        }
        result = self.extract(product_html(offers=offers))
        self.assertEqual("needs_review", result["status"])
        self.assertIn("availability", result["error"])

    def test_wrong_bottle_size_requires_review(self):
        result = self.extract(product_html(name="Miraval Rosé 1.5L"))
        self.assertEqual("needs_review", result["status"])
        self.assertIn("750mL", result["error"])

    def test_promotional_quantity_is_not_used_as_price(self):
        element = BeautifulSoup(
            '<article><h2>Miraval Rosé</h2><span class="price">6 for $90</span></article>',
            "html.parser",
        ).article
        result = self.scraper._extract_product_info(
            element, "Example Retailer", "Miraval Rosé"
        )
        self.assertEqual("$90", result["price"])

    def test_extra_product_word_is_not_an_exact_match(self):
        self.assertFalse(
            self.scraper._is_relevant_match("Miraval Rosé", "Miraval Studio Rosé")
        )

    def test_requested_vintage_must_match(self):
        self.assertFalse(
            self.scraper._is_relevant_match(
                "Miraval Rosé 2022", "Miraval Rosé 2023 750mL"
            )
        )


class FakeWorksheet:
    def __init__(self, records=None, first_column=None):
        self.records = records or []
        self.first_column = first_column or ["Observation ID"]
        self.appended = []
        self.updated = []

    def get_all_records(self):
        return self.records

    def col_values(self, column):
        return self.first_column

    def append_rows(self, rows, **kwargs):
        self.appended.extend(rows)
        self.first_column.extend(row[0] for row in rows)
        for row in rows:
            if len(row) == len(GoogleSheetsWriter.HISTORY_HEADERS):
                self.records.append(dict(zip(
                    GoogleSheetsWriter.HISTORY_HEADERS, row
                )))

    def update_cell(self, row, column, value):
        self.updated.append((row, column, value))

    def batch_update(self, updates, **kwargs):
        self.updated.extend(updates)


class SheetContractTests(unittest.TestCase):
    def make_writer(self):
        writer = object.__new__(GoogleSheetsWriter)
        writer.catalog_worksheet = FakeWorksheet(records=[{
            "Wine ID": "miraval-dm",
            "Wine Name": "Miraval Rosé",
            "Retailer": "Dan Murphy's",
            "Product URL": "https://example.com/miraval",
            "Expected Bottle Size": "750mL",
            "Enabled": "Yes",
        }])
        writer.history_worksheet = FakeWorksheet()
        writer.worksheet = writer.history_worksheet
        writer.spreadsheet = object()
        return writer

    def test_catalog_is_normalized(self):
        catalog = self.make_writer().read_catalog()
        self.assertEqual("miraval-dm", catalog[0]["wine_id"])
        self.assertTrue(catalog[0]["enabled"])

    def test_history_write_is_idempotent(self):
        writer = self.make_writer()
        observation = {
            "observation_id": "observation-1",
            "run_id": "run-1",
            "wine_id": "miraval-dm",
            "wine_name": "Miraval Rosé",
            "retailer": "Dan Murphy's",
            "price_amount": 29.99,
            "regular_price_amount": None,
            "currency": "AUD",
            "in_stock": True,
            "source_url": "https://example.com/miraval",
            "observed_at_utc": "2026-07-21T02:00:00+00:00",
            "status": "success",
            "error": "",
        }
        first = writer.record_tracking_run([observation])
        retried_observation = {**observation, "price_amount": 99.99}
        second = writer.record_tracking_run([retried_observation])
        self.assertEqual(1, first["appended"])
        self.assertEqual(0, second["appended"])
        self.assertEqual(1, second["skipped"])
        retried_updates = writer.catalog_worksheet.updated[-6:]
        self.assertNotIn(99.99, [
            update["values"][0][0] for update in retried_updates
        ])

    def test_observation_id_is_stable_for_retry(self):
        self.assertEqual(
            make_observation_id("run-1", "miraval-dm"),
            make_observation_id("run-1", "miraval-dm"),
        )


class TrackingServiceTests(unittest.TestCase):
    def test_service_tracks_every_catalog_row(self):
        class Writer:
            def read_catalog(self, enabled_only=True):
                return [{
                    "wine_id": "miraval-dm",
                    "wine_name": "Miraval Rosé",
                    "retailer": "Dan Murphy's",
                    "product_url": "https://example.com/miraval",
                    "expected_bottle_size": "750mL",
                }]

            def record_tracking_run(self, rows):
                self.rows = rows
                return {"appended": 1, "updated": 1, "skipped": 0}

        class Scraper:
            def fetch_product_url(self, **kwargs):
                return {
                    "product_name": "Miraval Rosé 750mL",
                    "price_amount": 29.99,
                    "regular_price_amount": None,
                    "currency": "AUD",
                    "in_stock": True,
                    "status": "success",
                    "error": "",
                }

        writer = Writer()
        summary = PriceTrackingService(Scraper(), writer).run(run_id="run-1")
        self.assertTrue(summary["success"])
        self.assertEqual(1, summary["counts"]["success"])
        self.assertEqual("miraval-dm", writer.rows[0]["wine_id"])

    def test_monitoring_endpoint_uses_sheet_catalog(self):
        class Writer:
            catalog_worksheet = object()

            def read_catalog(self, enabled_only=True):
                return [{
                    "wine_id": "miraval-dm",
                    "wine_name": "Miraval Rosé",
                    "retailer": "Dan Murphy's",
                    "product_url": "https://example.com/miraval",
                    "expected_bottle_size": "750mL",
                }]

            def record_tracking_run(self, rows):
                return {"appended": 1, "updated": 1, "skipped": 0}

        class Scraper:
            def fetch_product_url(self, **kwargs):
                return {
                    "product_name": "Miraval Rosé 750mL",
                    "price_amount": 29.99,
                    "regular_price_amount": None,
                    "currency": "AUD",
                    "in_stock": True,
                    "status": "success",
                    "error": "",
                }

        previous_scraper = app_module.scraper
        previous_writer = app_module.sheets_writer
        app_module.scraper = Scraper()
        app_module.sheets_writer = Writer()
        try:
            response = app_module.app.test_client().post(
                "/api/run-monitoring",
                json={"run_id": "run-1", "add_to_sheet": True},
            )
        finally:
            app_module.scraper = previous_scraper
            app_module.sheets_writer = previous_writer
        self.assertEqual(200, response.status_code)
        self.assertEqual("run-1", response.get_json()["run_id"])


if __name__ == "__main__":
    unittest.main()
