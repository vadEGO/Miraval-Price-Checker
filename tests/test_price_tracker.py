import json
import os
import tempfile
import unittest
from unittest.mock import patch

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

    def test_unrelated_body_pack_mention_is_not_a_pack(self):
        # Cross-sell/upsell copy elsewhere on the page (e.g. "buy 6 and save")
        # must not cause a single-bottle product to be rejected as a case.
        result = self.extract(product_html(body="Price shown for 6 bottles"))
        self.assertEqual("success", result["status"])

    def test_case_price_in_title_requires_review(self):
        result = self.extract(product_html(name="Miraval Rosé 6 bottles 750mL"))
        self.assertEqual("needs_review", result["status"])
        self.assertIn("pack or case", result["error"])

    def test_out_of_stock_is_explicit(self):
        result = self.extract(product_html(
            availability="https://schema.org/OutOfStock",
            body="Out of stock",
        ))
        self.assertEqual("unavailable", result["status"])
        self.assertFalse(result["in_stock"])

    def test_cloudflare_challenge_is_an_error(self):
        result = self.extract(
            "<html><title>Attention Required! | Cloudflare</title></html>"
        )
        self.assertEqual("error", result["status"])
        self.assertIn("bot challenge", result["error"])

    def test_captcha_widget_on_real_product_page_is_not_a_challenge(self):
        # Shopify (and others) embed a captcha bot-protection script on every
        # normal page, including real product pages — that alone must not
        # trigger the challenge error when structured product data is present.
        html = product_html() + '<script id="captcha-bootstrap">true</script>'
        result = self.extract(html)
        self.assertEqual("success", result["status"])

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

    def test_missing_size_assumes_standard_bottle(self):
        # Many retailers (e.g. Shopify shops) omit the bottle size from the
        # title entirely for their standard 750mL listing.
        result = self.extract(product_html(name="Miraval Rosé"))
        self.assertEqual("success", result["status"])

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


class JsonProductFetchTests(unittest.TestCase):
    """Dan Murphy's/BWS/Jimmy Brings and Shopify product pages are rendered
    client-side, so fetch_product_url resolves them via each site's JSON API
    instead of HTML scraping. These exercise the real response shapes
    captured from each API.
    """

    def setUp(self):
        temp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"wine_sites": []}, temp)
        temp.close()
        self.config_path = temp.name
        self.scraper = WineScraper(self.config_path)

    def tearDown(self):
        os.unlink(self.config_path)

    def test_dan_murphys_member_offer_uses_single_price(self):
        api_response = {
            "Products": [{
                "Description": "Miraval Cotes De Provence<br>Rose ",
                "PackageSize": "750ML",
                "Prices": {
                    "singleprice": {"Value": 42.99},
                    "promoprice": {"Value": 39.0, "IsMemberOffer": True},
                },
                "Inventory": {"availableinventoryqty": 25},
            }]
        }
        with patch.object(self.scraper, "_get_json", return_value=api_response):
            result = self.scraper.fetch_product_url(
                "https://www.danmurphys.com.au/product/DM_768858/miraval-cotes-de-provence-rose",
                "Miraval Rosé", "Dan Murphy's", "750mL",
            )
        self.assertEqual("success", result["status"])
        self.assertEqual(42.99, result["price_amount"])
        self.assertTrue(result["in_stock"])

    def test_bws_special_records_was_price(self):
        api_response = {
            "Products": [{
                "Name": "Miraval Cotes De Provence Rose ",
                "PackageSize": "750ML",
                "Price": 27, "WasPrice": 33, "IsOnSpecial": True,
                "StockOnHand": 5, "IsAvailable": True,
            }]
        }
        with patch.object(self.scraper, "_get_json", return_value=api_response):
            result = self.scraper.fetch_product_url(
                "https://www.bws.com.au/product/39947/miraval-studio-ros-",
                "Miraval Rosé", "BWS", "750mL",
            )
        self.assertEqual("success", result["status"])
        self.assertEqual(27, result["price_amount"])
        self.assertEqual(33, result["regular_price_amount"])

    def test_endeavour_api_failure_falls_back_to_html(self):
        with patch.object(self.scraper, "_get_json", return_value=None), \
             patch.object(self.scraper, "_get_page_content", return_value=""):
            result = self.scraper.fetch_product_url(
                "https://www.bws.com.au/product/39947/miraval-studio-ros-",
                "Miraval Rosé", "BWS", "750mL",
            )
        self.assertEqual("error", result["status"])
        self.assertEqual("Empty retailer response", result["error"])

    def test_shopify_single_variant_product(self):
        api_response = {
            "title": "Miraval Côtes de Provence Rosé 2025",
            "available": True,
            "variants": [{"title": "Default Title", "price": 4499, "available": True}],
        }
        with patch.object(self.scraper, "_get_json", return_value=api_response):
            result = self.scraper.fetch_product_url(
                "https://kentstreetcellars.com.au/products/miraval-cotes-de-provence-rose-2025",
                "Miraval Rosé", "Kent Street Cellars", "750mL",
            )
        self.assertEqual("success", result["status"])
        self.assertEqual(44.99, result["price_amount"])

    def test_shopify_multi_variant_product_requires_review(self):
        api_response = {
            "title": "Miraval Rosé 2025",
            "available": True,
            "variants": [
                {"title": "750mL", "price": 5300, "available": True},
                {"title": "1.5L", "price": 11800, "available": True},
            ],
        }
        with patch.object(self.scraper, "_get_json", return_value=api_response):
            result = self.scraper.fetch_product_url(
                "https://www.crackawines.com.au/products/miraval-rose-2025",
                "Miraval Rosé", "Cracka Wines", "750mL",
            )
        self.assertEqual("needs_review", result["status"])
        self.assertIn("variant", result["error"])


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
