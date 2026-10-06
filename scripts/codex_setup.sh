#!/usr/bin/env bash
# Idempotent environment setup for Codex (or any fresh Linux/macOS checkout).
# Safe to run before every scheduled run.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

# config.json is gitignored; create it from the example on a fresh checkout.
# The sheet ID comes from GOOGLE_SHEET_ID, which overrides config.json.
if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo "Created config.json from config.example.json"
fi

missing=0
for var in GOOGLE_SERVICE_ACCOUNT_JSON GOOGLE_SHEET_ID; do
  if [ -z "${!var:-}" ]; then
    echo "MISSING SECRET: $var is not set" >&2
    missing=1
  fi
done
[ "$missing" -eq 0 ] || exit 2

python -m pytest -q tests
python test_setup.py
