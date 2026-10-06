# Running the price tracker with Codex

## One-time human setup (about 10 minutes)

Codex cannot do these steps for you.

1. In Google Cloud Console, create a project and enable the **Google Sheets API** and **Google Drive API**.
2. Create a **service account** and download its JSON key.
3. Create an empty Google Sheet and **share it with the service account's email** (it is in the key file as `client_email`) with Editor access. The sheet ID is the part of the URL between `/d/` and `/edit`.
4. In your Codex environment settings, add two secrets:
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: the entire key file contents
   - `GOOGLE_SHEET_ID`: the sheet ID
5. Give the environment internet access, since the tracker queries retailer sites.
6. Set the environment's setup script to `scripts/codex_setup.sh`.

## First run prompt

Paste this into Codex once:

> Read AGENTS.md. Set up and run the Miraval price tracker for the first time.
>
> 1. Run `scripts/codex_setup.sh`. If it reports a missing secret or a failing check, stop and tell me exactly what is missing.
> 2. Run `.venv/bin/python seed_catalog.py --dry-run` and show me the retailers and URLs it would add and anything it skipped.
> 3. If the dry run found at least one URL, run `.venv/bin/python seed_catalog.py` for real.
> 4. Run `.venv/bin/python price_tracker.py`.
> 5. Report: how many rows succeeded, were unavailable, need review, or errored. For every `needs_review` or `error` row, give the wine, retailer, and reason. List the wines that have no catalog row at any retailer.
>
> Do not change code or edit any prices. If something fails, report the error rather than working around it.

## Weekly schedule prompt

Create a weekly Codex automation (for example Monday 07:00 Australia/Sydney) with this prompt:

> Read AGENTS.md. Run the weekly Miraval price check.
>
> 1. Run `scripts/codex_setup.sh`. Stop and report if it fails.
> 2. Run `.venv/bin/python price_tracker.py` and keep the printed `run_id`.
> 3. On exit code 4, retry once with `--run-id <that run_id>`. On exit code 2 or 3, do not retry; report the error.
> 4. Report a short summary: run ID, counts per status, and any `needs_review` or `error` rows with wine, retailer, and reason.
> 5. If the same URL has failed, needs review, or is marked unavailable and it is plausibly a dead link, say so, but do not edit the sheet or the code unless I ask.
>
> Never invent or adjust a price.

## Adding wines or retailers later

- Add the wine to `tracked_wines` or `competitor_wines` in `config.example.json`, then re-run `seed_catalog.py`. It only adds missing rows.
- To track a specific listing by hand, add a row to `Wine Catalog` with a unique `Wine ID`, the `Wine Name`, `Retailer`, an absolute HTTPS `Product URL`, `Expected Bottle Size` (for example `750mL`), and `Enabled` set to `Yes`.
- To stop tracking a row, set `Enabled` to `No`. Do not delete history rows.
