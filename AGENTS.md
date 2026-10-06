# Miraval Price Checker: agent instructions

Deterministic wine price tracker. It reads exact retailer product URLs from the
`Wine Catalog` Google Sheet, validates each page or API response, and appends
every result to `Price History`. Prices are never inferred.

## Standing rules

- Never invent, estimate, or hand-edit a price. Report only what `price_tracker.py` records.
- Never commit secrets. `credentials.json`, `token.json`, and `config.json` are gitignored; keep them that way.
- Secrets come from environment variables: `GOOGLE_SERVICE_ACCOUNT_JSON` (the full key JSON) and `GOOGLE_SHEET_ID`.
- If a secret is missing, stop and tell the user which one. Do not create Google accounts, projects, or keys.
- Do not push to `master`. Code changes go on a branch with a pull request.
- Run `pytest -q tests` before and after any code change.

## Commands

| Purpose | Command |
|---|---|
| Install and verify the environment | `scripts/codex_setup.sh` |
| Find product URLs and fill the catalog (first run) | `.venv/bin/python seed_catalog.py` |
| Preview catalog additions without writing | `.venv/bin/python seed_catalog.py --dry-run` |
| Record prices (the weekly run) | `.venv/bin/python price_tracker.py` |
| Check prices without writing | `.venv/bin/python price_tracker.py --dry-run` |
| Retry a failed run idempotently | `.venv/bin/python price_tracker.py --run-id <id from the failed run>` |

`price_tracker.py` and `seed_catalog.py` print a JSON summary on stdout and logs on stderr.

### Exit codes

| Code | Meaning | Action |
|---|---|---|
| 0 | At least one row succeeded or was found unavailable | Report the summary |
| 2 | Config, credentials, or sheet schema problem | Read the error, fix config or ask the user |
| 3 | No row succeeded | Check the `results[].error` values; a retailer change is likely |
| 4 | Unexpected failure | Report the error; retry once with the same `--run-id` |

### Row statuses

- `success`: valid single-bottle AUD price with confirmed stock.
- `unavailable`: out of stock. This is a valid observation.
- `needs_review`: member-only price, pack or case, wrong size, multiple variants, or unreadable stock. Needs a human to check the URL.
- `error`: fetch failure or bot challenge.

## How each retailer is read

- Dan Murphy's, BWS, Jimmy Brings: JSON Product API (`_fetch_endeavour_product`).
- Shopify shops (`/products/<handle>` URLs): `<handle>.js` (`_fetch_shopify_product`).
- Anything else: HTML with structured data (`extract_product_page`). Liquorland, First Choice, and Vintage Cellars cannot be tracked this way, so `seed_catalog.py` skips them.

If a retailer starts failing, fix the adapter in `wine_scraper.py` and add a test in `tests/test_price_tracker.py`. Do not loosen validation to make a row pass.

See `CODEX.md` for the prompts to give Codex for the first run and the weekly schedule.
