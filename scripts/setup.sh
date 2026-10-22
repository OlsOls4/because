#!/usr/bin/env bash
# Clean local setup (DuckDB fallback, no Docker needed):  ./scripts/setup.sh && .venv/bin/because serve
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install -q -e ".[dev]"
[ -f .env.local ] || cp .env.example .env.local
.venv/bin/pytest -q
echo "Ready. Try:  .venv/bin/because ask \"Why did UK inflation spike in 2022?\"   or   .venv/bin/because serve"
