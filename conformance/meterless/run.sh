#!/usr/bin/env bash
# Run the OFFICIAL Meterless conformance suites against Because's Python engines.
# Needs node >= 18. Clones Meterless into .because/meterless on first run.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ML="${METERLESS_DIR:-$ROOT/.because/meterless}"
if [ ! -d "$ML" ]; then
  git clone --depth 1 https://github.com/Meterless/Meterless.git "$ML"
fi
echo "== H-MEM conformance (engines/hmem/conformance/runner.ts) =="
(cd "$ML/engines/hmem/reference" && npm install --silent >/dev/null 2>&1 || true)
(cd "$ML/engines/hmem/reference" && HMEM_IMPL="$ROOT/conformance/meterless/hmem-adapter.ts" npx --yes tsx ../conformance/runner.ts) | tee "$ROOT/conformance/meterless/hmem-scorecard.txt"
echo
echo "== Scout Intent evals (engines/scout-intent/evals) =="
(cd "$ML/engines/scout-intent" && npm install --silent >/dev/null 2>&1 || true)
(cd "$ML/engines/scout-intent" && SCOUT_IMPL="$ROOT/conformance/meterless/scout-adapter.ts" npx --yes tsx evals/runner/index.ts all) | tee "$ROOT/conformance/meterless/scout-scorecard.txt" || true
