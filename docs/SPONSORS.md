# Sponsor technology and pre-existing components

Because is an evidence-first "why" agent: it answers questions such as *Why did Intel's gross margin fall in 2024?* by
querying public data, testing competing explanations with explicit numeric rules, and citing the query and source row
behind every claim. This page explains how each sponsor technology is used and declares everything that existed before
the build window (22 Oct 2026 00:00 UTC).

## Zetaris: the federated data harness

**Role.** Every number in an answer comes from SQL run through `DataHarness`
([`because/harness/base.py`](../because/harness/base.py)). `ZetarisBackend`
([`because/harness/zetaris_backend.py`](../because/harness/zetaris_backend.py)) is the primary backend.

- **Sources registered as Lightning tables**, following the Zetaris cookbook pattern
  ([`because/harness/sources.py`](../because/harness/sources.py), full SQL in [`zetaris_ddl.sql`](zetaris_ddl.sql)):
  - SEC EDGAR XBRL and ONS: `CREATE LIGHTNING REST TABLE` (raw JSON), then `CACHE TABLE`, then a
    `CREATE SCHEMASTORE VIEW ... LATERAL VIEW explode(...)` that flattens the JSON into canonical columns.
  - FRED (no API key): `CREATE LIGHTNING FILESTORE TABLE ... FORMAT CSV` in a `BECAUSE_FILES` database, flattened by a SchemaStore view.
  - So all three source families are federated by Lightning, and new companies are registered lazily on first question (existing views are reused, never dropped).
- **Three channels**: JDBC with the bundled Lightning driver (`jaydebeapi`), the UI SQL proxy used by the bundle's `verify.sh`,
  and the API-key REST channel. `ZETARIS_CHANNEL=auto` tries them in order.
- **Portable SQL.** The analyst writes ANSI SQL over canonical columns, so the identical queries run on a DuckDB fallback
  (`SAMPLE_MODE`) when no Zetaris instance is available. The public demo and the saved examples use that fallback, and
  the UI shows which backend answered.
- **Real instance in CI.** [`.github/workflows/zetaris-e2e.yml`](../.github/workflows/zetaris-e2e.yml) downloads the free
  Hobby Docker bundle, starts it on a GitHub runner, waits for `verify.sh`, registers every source, and runs the example
  questions with `--backend zetaris --strict` over JDBC. Traces are written to [`examples/zetaris_runs/`](../examples/zetaris_runs/).
  It needs three repository secrets (bundle URL and registry login); see the status note at the end.
- Local setup: [`scripts/zetaris_setup.sh`](../scripts/zetaris_setup.sh) and [`docs/ZETARIS.md`](ZETARIS.md).

**What is unique.** Zetaris is used as the agent's single query surface over heterogeneous public APIs (JSON REST and CSV
files), and each query's id, SQL and backend label travel with every finding into the answer and into memory, so the
reader can rerun the exact statement behind a claim.

## Meterless: routing, guarding and memory with provenance

Both engines are implemented in Python from the Meterless `AGENTS.md` specs and checked with the official Meterless test suites.

- **Scout Intent** ([`because/meterless/scout.py`](../because/meterless/scout.py)): intent registry
  ([`because/agent/intents.json`](../because/agent/intents.json)), lexical and semantic scoring with the canonical weights
  and bands, four clarification triggers, a five-layer guard (prompt injection, control bytes, role and surface), and an
  **HMAC-SHA256 execution contract** with a TTL and a capability scope. The analyst calls `scout.enforce(contract, capability)`
  before every tool call (`harness.query`, `background.search`, `memory.recall`, `memory.write`, `llm.narrate`).
  A prompt injection is refused before any tool runs ([`examples/outputs/guard-blocked.json`](../examples/outputs/guard-blocked.json)).
  Official eval harness: **PASS** on corpus v1 (top-1 1.00, injection precision and recall 1.00)
  ([`scout-eval-report.md`](../conformance/meterless/scout-eval-report.md)). Corpus v1 is a 32-example smoke set, so this is conformance, not a benchmark.
- **H-MEM** ([`because/meterless/hmem.py`](../because/meterless/hmem.py)): every finding is stored as a memory whose
  provenance is `{origin: harness_query, query_id, sql, rows, source_url, backend}`; conclusions keep `derivedFrom` lineage;
  a conflict scan flags restated numbers; an append-only trust ledger records feedback; dreaming and sleep are preview-first.
  Official conformance runner: **13/13 passed** ([`hmem-scorecard.txt`](../conformance/meterless/hmem-scorecard.txt)).
  Reproduce both suites with `bash conformance/meterless/run.sh`.

**What is unique.** Memory is used for *explanation*, not only recall: "What do you already know about Intel's margins?"
answers from H-MEM and shows, for each belief, the query and source it came from
([`examples/outputs/memory-recall-intel.md`](../examples/outputs/memory-recall-intel.md)), and `because memory why <id>` prints the full audit trail.

## Cursor (with Space XAI)

- The repository carries Cursor project rules ([`.cursor/rules/because.mdc`](../.cursor/rules/because.mdc),
  [`.cursor/rules/zetaris.mdc`](../.cursor/rules/zetaris.mdc)) and [`AGENTS.md`](../AGENTS.md), which steer every Cursor agent run.
- Three headless **Cursor Agent CLI** sessions (`cursor-agent -p --force --output-format stream-json`) did real work on this
  repository; prompts, tool calls, results and raw event streams are in [`docs/cursor-sessions/`](cursor-sessions/):
  Zetaris backend hardening (UI-proxy channel, `auto` fallback, `CACHE TABLE`, column normalisation, 12 tests);
  FRED as FILESTORE tables plus `because zetaris sql`; and web UI polish (pipeline strip, Meterless card, Zetaris badge).
- The free plan's agent usage limit was reached during session 2; the log notes the one test fixed by hand afterwards.

## NVIDIA

- **NVIDIA NIM** (build.nvidia.com, OpenAI-compatible API) is the first-choice LLM provider in
  [`because/llm.py`](../because/llm.py) (`NVIDIA_API_KEY`, default `meta/llama-3.3-70b-instruct`), used for the optional
  narrative and as a fallback question parser. Scout's model profiles route to it.
- The narrative is accepted only if every sentence cites a finding and every number appears in the evidence (`validate_narrative`).
- Without a key, Because runs fully deterministic. The saved examples, the public demo and the demo video were produced
  with no LLM, so they show the NIM path in code and configuration, not in recorded output.

## Pre-existing components (declared)

All of the Because code in this repository was written during the build window: the first commit is
2026-10-22 10:51 UTC. These components predate the window or come from elsewhere:

| Component | What was reused | Where |
|---|---|---|
| Free research add-on from the author's Metaculus forecasting bot, [`OlsOls4/ollymcbride-bot`](https://github.com/OlsOls4/ollymcbride-bot) (`metaculus_bot/research/free_sources.py`, before 22 Oct 2026, same author) | Ideas and patterns, rewritten: query-term extraction, polite Wikimedia User-Agent, process-wide 5 s GDELT rate gate, fail-soft behaviour | [`because/research/background.py`](../because/research/background.py) |
| Meterless specs and test vectors ([github.com/Meterless/Meterless](https://github.com/Meterless/Meterless), Apache-2.0) | H-MEM and Scout specs implemented from scratch; `tests/fixtures/meterless_hmem_vectors.json` copied unmodified (see `tests/fixtures/NOTICE.md`); official runners invoked, not vendored | `because/meterless/`, `conformance/meterless/` |
| Zetaris getting-started docs and cookbooks ([zetaris/getting-started](https://github.com/zetaris/getting-started)) | JDBC/REST channel shapes, the REST-table + SchemaStore-view pattern, the `verify.sh` login flow | `because/harness/` |
| Open-source libraries | duckdb, httpx, fastapi, uvicorn, python-dotenv, JayDeBeApi/JPype1 (optional), mermaid (UI) | `pyproject.toml` |
| Public data | SEC EDGAR, ONS, FRED, Wikipedia, GDELT, fetched live at question time; no dataset was prepared before the window | n/a |

Code was written with AI coding agents during the window, including the logged Cursor Agent sessions; generated code is
allowed by the rules and is owned by the author.

## Status notes

- `zetaris-e2e` runs on a real Zetaris Lightning 2.4.3.1 instance (repository secrets `ZETARIS_BUNDLE_URL`, `ZETARIS_ACR_USER`,
  `ZETARIS_ACR_PASSWORD`). Recorded run [39928173953](https://github.com/OlsOls4/because/actions/runs/39928173953) (23 Oct 2026):
  all five example questions answered over JDBC on `zetaris-jdbc` with 0 query errors; traces in
  [`examples/zetaris_runs/`](../examples/zetaris_runs/).
- Commit history: v0.1 (commit `fa77036`, 22 Oct 10:51 UTC) was written from scratch inside the build window (22 Oct
  00:00-10:51 UTC) and committed in one go; everything since is incremental commits. Only the components listed under
  "Pre-existing components" predate the window.
