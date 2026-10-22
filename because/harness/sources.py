"""Public sources the harness can register, for both backends.

Each source kind has:
  * `source_url(spec)`         - the exact upstream endpoint
  * `fetch_rows(spec)`         - local fallback: fetch + flatten to canonical rows
  * `zetaris_ddl(spec, ...)`   - Lightning SQL that registers the same endpoint as
                                 a REST table and flattens it with a SchemaStore view
  * `zetaris_file_ddl(spec, uri)` - Lightning FILESTORE CSV path for FRED without an API key

All sources are free and keyless. FRED in Zetaris can use the JSON API (needs a free
`FRED_API_KEY`), a FILESTORE CSV under `BECAUSE_ZETARIS_FILE_DIR`, or the local DuckDB
hybrid path that reads the keyless fredgraph CSV.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from because.harness.base import HarnessError, SourceSpec

MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04", "may": "05", "june": "06",
    "july": "07", "august": "08", "september": "09", "october": "10", "november": "11", "december": "12",
}


def user_agent() -> str:
    # SEC requires "app_name contact@email". Never a credential.
    return os.environ.get("BECAUSE_USER_AGENT") or os.environ.get("ZETARIS_USER_AGENT") or "because-agent because@example.com"


def cache_dir() -> Path:
    p = Path(os.environ.get("BECAUSE_HOME", ".because")) / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------

def source_url(spec: SourceSpec) -> str:
    if spec.kind == "edgar_concept":
        cik, tag = spec.key.split(":")
        return f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
    if spec.kind == "ons_series":
        sid, dataset = (spec.key.split(":") + ["mm23"])[:2]
        return f"https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/{sid.lower()}/{dataset}/data"
    if spec.kind == "fred_series":
        return f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={spec.key}"
    raise HarnessError(f"unknown source kind {spec.kind}")


def fred_api_url(series_id: str, key_placeholder: str = "{FRED_API_KEY}") -> str:
    return (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={key_placeholder}&file_type=json"
    )


# --------------------------------------------------------------------------
# Local fetch (DuckDB backend)
# --------------------------------------------------------------------------

def _get(url: str, ttl_s: int = 86400) -> bytes:
    """GET with a polite UA and an on-disk cache (gitignored)."""
    import hashlib

    path = cache_dir() / hashlib.sha1(url.encode()).hexdigest()
    if path.exists() and time.time() - path.stat().st_mtime < ttl_s:
        return path.read_bytes()
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = httpx.get(url, headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip"}, timeout=40, follow_redirects=True)
            if r.status_code == 404:
                raise HarnessError(f"404 for {url}")
            r.raise_for_status()
            path.write_bytes(r.content)
            return r.content
        except HarnessError:
            raise
        except Exception as e:  # network blips: back off and retry
            last = e
            time.sleep(1.5 * (attempt + 1))
    if path.exists():  # stale cache beats nothing
        return path.read_bytes()
    raise HarnessError(f"fetch failed for {url}: {last}")


def fetch_rows(spec: SourceSpec) -> list[tuple[Any, ...]]:
    if spec.kind == "edgar_concept":
        try:
            d = json.loads(_get(source_url(spec)))
        except HarnessError as e:
            if "404" in str(e):
                return []  # company does not report this concept: an empty table, not an error
            raise
        out = []
        for unit, facts in (d.get("units") or {}).items():
            if unit != "USD":
                continue
            for f in facts:
                out.append((
                    str(d.get("cik")), d.get("entityName"), d.get("tag"), f.get("start"), f.get("end"),
                    float(f["val"]) if f.get("val") is not None else None, f.get("accn"), f.get("fy"),
                    f.get("fp"), f.get("form"), f.get("filed"),
                ))
        return out
    if spec.kind == "ons_series":
        d = json.loads(_get(source_url(spec)))
        out = []
        for m in d.get("months") or []:
            mm = MONTHS.get((m.get("month") or "").lower())
            if mm and m.get("value") not in (None, ""):
                out.append((spec.key, f"{m['year']}-{mm}", float(m["value"])))
        return out
    if spec.kind == "fred_series":
        text = _get(source_url(spec)).decode()
        out = []
        rdr = csv.reader(io.StringIO(text))
        next(rdr, None)
        for row in rdr:
            if len(row) >= 2 and row[1] not in ("", "."):
                out.append((spec.key, row[0], float(row[1])))
        return out
    raise HarnessError(f"unknown source kind {spec.kind}")


def source_title(spec: SourceSpec) -> str:
    """Human label for the trace (best effort, never fails)."""
    if spec.label:
        return spec.label
    try:
        if spec.kind == "ons_series":
            return json.loads(_get(source_url(spec)))["description"]["title"]
    except Exception:
        pass
    return spec.key


# --------------------------------------------------------------------------
# Zetaris Lightning DDL (same endpoints, same canonical columns)
# --------------------------------------------------------------------------

ZETARIS_DATABASE = "BECAUSE_REST"  # Lightning database holding the raw REST tables
ZETARIS_FILE_DATABASE = "BECAUSE_FILES"  # Lightning database holding FILESTORE CSV tables
ZETARIS_CONTAINER = "because"  # SchemaStore container holding flattened views
DEFAULT_ZETARIS_FILE_URI = "file:///home/zetaris/customer-data/shared/because"

_ONS_MONTH_CASE = "CASE m.month " + " ".join(
    f"WHEN '{k.capitalize()}' THEN '{v}'" for k, v in MONTHS.items()
) + " END"


def zetaris_file_uri_prefix() -> str:
    """Container-visible URI prefix for CSVs under BECAUSE_ZETARIS_FILE_DIR."""
    return os.environ.get("BECAUSE_ZETARIS_FILE_URI", DEFAULT_ZETARIS_FILE_URI).rstrip("/")


def fred_file_store_active() -> bool:
    """True when FRED should register via FILESTORE CSV (no API key, shared file dir)."""
    return bool(os.environ.get("BECAUSE_ZETARIS_FILE_DIR")) and not os.environ.get("FRED_API_KEY")


def zetaris_ddl(spec: SourceSpec) -> list[str]:
    """Statements that register `spec` in Zetaris. One statement per entry.

    Pattern from zetaris.github.io/getting-started REST cookbook:
    CREATE LIGHTNING REST TABLE (raw JSON)  ->  CREATE SCHEMASTORE VIEW (flatten).
    """
    raw = f"{spec.table_name}_raw"
    view = spec.table_name
    ua = user_agent()
    if spec.kind == "edgar_concept":
        return [
            f'CREATE LIGHTNING REST TABLE {raw} FROM {ZETARIS_DATABASE} REQUEST(\n'
            f'    endpoint "{source_url(spec)}",\n    method "get",\n    response_type "json",\n'
            f'    http_encoding "URLENCODED"\n) HEADER (\n    user-agent "{ua}"\n) BODY ()',
            f"CREATE SCHEMASTORE VIEW {view} WITH CONTAINER {ZETARIS_CONTAINER} AS\nSELECT\n"
            "    CAST(cik AS STRING) AS cik,\n    `entityName` AS entity_name,\n    tag AS concept,\n"
            "    fact.`start` AS period_start,\n    fact.`end` AS period_end,\n"
            "    CAST(fact.val AS DOUBLE) AS value_usd,\n    fact.accn AS accession_number,\n"
            "    CAST(fact.fy AS BIGINT) AS fiscal_year,\n    fact.fp AS fiscal_period,\n"
            "    fact.form AS form_type,\n    fact.filed AS filed_date\n"
            f"FROM {ZETARIS_DATABASE.lower()}.{raw}\nLATERAL VIEW explode(units.USD) AS fact",
        ]
    if spec.kind == "ons_series":
        return [
            f'CREATE LIGHTNING REST TABLE {raw} FROM {ZETARIS_DATABASE} REQUEST(\n'
            f'    endpoint "{source_url(spec)}",\n    method "get",\n    response_type "json",\n'
            f'    http_encoding "URLENCODED"\n) HEADER (\n    user-agent "{ua}"\n) BODY ()',
            f"CREATE SCHEMASTORE VIEW {view} WITH CONTAINER {ZETARIS_CONTAINER} AS\nSELECT\n"
            f"    '{spec.key}' AS series_id,\n    CONCAT(m.year, '-', {_ONS_MONTH_CASE}) AS period,\n"
            "    CAST(m.value AS DOUBLE) AS value\n"
            f"FROM {ZETARIS_DATABASE.lower()}.{raw}\nLATERAL VIEW explode(months) AS m",
        ]
    if spec.kind == "fred_series":
        key = os.environ.get("FRED_API_KEY", "")
        if not key:
            raise HarnessError(
                "FRED in Zetaris needs a free FRED_API_KEY (fred.stlouisfed.org/docs/api/api_key.html); "
                "the local DuckDB backend does not."
            )
        return [
            f'CREATE LIGHTNING REST TABLE {raw} FROM {ZETARIS_DATABASE} REQUEST(\n'
            f'    endpoint "{fred_api_url(spec.key, key)}",\n    method "get",\n    response_type "json",\n'
            f'    http_encoding "URLENCODED"\n) HEADER (\n) BODY ()',
            f"CREATE SCHEMASTORE VIEW {view} WITH CONTAINER {ZETARIS_CONTAINER} AS\nSELECT\n"
            f"    '{spec.key}' AS series_id,\n    o.date AS period,\n    CAST(o.value AS DOUBLE) AS value\n"
            f"FROM {ZETARIS_DATABASE.lower()}.{raw}\nLATERAL VIEW explode(observations) AS o",
        ]
    raise HarnessError(f"unknown source kind {spec.kind}")


def zetaris_file_ddl(spec: SourceSpec, uri: str) -> list[str]:
    """Lightning FILESTORE + SchemaStore view for a locally written FRED CSV.

    CSV header is always ``observation_date,value`` (written by the backend).
    """
    if spec.kind != "fred_series":
        raise HarnessError(f"zetaris_file_ddl only supports fred_series, got {spec.kind}")
    raw = f"{spec.table_name}_raw"
    view = spec.table_name
    return [
        f'CREATE LIGHTNING FILESTORE TABLE {raw} FROM {ZETARIS_FILE_DATABASE} FORMAT CSV '
        f'OPTIONS (path "{uri}", header "true", inferSchema "false")',
        f"CREATE SCHEMASTORE VIEW {view} WITH CONTAINER {ZETARIS_CONTAINER} AS\n"
        f"SELECT\n"
        f"    '{spec.key}' AS series_id,\n"
        f"    CAST(`observation_date` AS STRING) AS period,\n"
        f"    CAST(`value` AS DOUBLE) AS value\n"
        f"FROM {ZETARIS_FILE_DATABASE.lower()}.{raw}\n"
        f"WHERE `value` <> '.'",
    ]


def redact(sql: str) -> str:
    """Hide API keys before SQL is logged or shown."""
    key = os.environ.get("FRED_API_KEY")
    return sql.replace(key, "***") if key else sql
