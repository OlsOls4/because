"""Bind a why-question to investigation parameters (Scout's Interpret stage).

Rule-based first (deterministic, free); an LLM is only asked when rules fail.
"""

from __future__ import annotations

import json
import re
from typing import Any

from because.harness.sources import _get

METRICS = [
    ("gross_margin", r"gross\s+margin"),
    ("operating_margin", r"operating\s+margin"),
    ("operating_income", r"operating\s+(income|profit)"),
    ("net_income", r"net\s+(income|profit|loss)|\bprofits?\b|\bearnings\b"),
    ("rd", r"\br&d\b|research\s+and\s+development"),
    ("revenue", r"\brevenues?\b|\bsales\b|\bturnover\b"),
]
INDICATORS = [("inflation", r"inflation|\bcpi\b|consumer prices|cost of living"), ("unemployment", r"unemployment|jobless")]
COUNTRIES = [
    ("GB", r"\bu\.?k\.?\b|britain|british|united kingdom|england"),
    ("US", r"\bu\.?s\.?a?\.?\b|america|united states"),
]
DOWN = r"fall|fell|drop|declin|shrink|shrank|collaps|plung|slump|lower|decreas|weak|squeez|crash|down"
UP = r"rise|rose|jump|spike|surg|soar|grow|grew|increas|higher|boom|up\b|climb|rocket|balloon"

_TICKERS: list[dict[str, Any]] | None = None


def sec_tickers() -> list[dict[str, Any]]:
    global _TICKERS
    if _TICKERS is None:
        try:
            d = json.loads(_get("https://www.sec.gov/files/company_tickers.json", ttl_s=7 * 86400))
            _TICKERS = list(d.values())
        except Exception:
            _TICKERS = []
    return _TICKERS


ALIASES = {"google": "alphabet", "facebook": "meta platforms", "meta": "meta platforms", "tsmc": "taiwan semiconductor"}


def resolve_company(question: str) -> dict[str, Any] | None:
    """Find a listed company in the question via SEC's company_tickers.json."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9&.\-]+", question)
    cands = []
    for w in words:
        base = re.sub(r"'s$|’s$", "", w)
        if base.lower() in {"why", "did", "what", "the", "in", "its", "and", "how", "was", "is", "uk", "us", "fiscal", "gross", "net", "margin", "revenue"}:
            continue
        if base[0].isupper() or base.isupper():
            cands.append(base)
    tickers = sec_tickers()
    for c in cands:
        name = ALIASES.get(c.lower(), c.lower())
        if c.isupper() and 1 < len(c) <= 5:
            for t in tickers:
                if t["ticker"] == c:
                    return _co(t)
        for t in tickers:  # SEC file is ordered roughly by size: first match is the big one
            title = t["title"].lower()
            if title == name or title.startswith(name + " ") or title.startswith(name + ","):
                return _co(t)
    return None


def _co(t: dict[str, Any]) -> dict[str, Any]:
    return {"cik": f"{int(t['cik_str']):010d}", "name": t["title"], "ticker": t["ticker"]}


def parse_question(question: str) -> dict[str, Any]:
    q = question.lower()
    out: dict[str, Any] = {}
    m = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", q)
    if m:
        out["year"] = int(m.group(1))
    out["fiscal"] = bool(re.search(r"\bfiscal\b|\bfy\s?\d", q))
    if re.search(DOWN, q):
        out["direction"] = "down"
    elif re.search(UP, q):
        out["direction"] = "up"
    for name, pat in INDICATORS:
        if re.search(pat, q):
            out["indicator"] = name
            break
    for code, pat in COUNTRIES:
        if re.search(pat, q):
            out["country"] = code
            break
    if "indicator" not in out:
        for name, pat in METRICS:
            if re.search(pat, q):
                out["metric"] = name
                break
        co = resolve_company(question)
        if co:
            out["company"] = co
    return out


def llm_parse(llm: Any, question: str) -> dict[str, Any]:
    """Ask an LLM for the same fields when rules could not bind them."""
    sys = ("Extract fields from a why-question. Reply with JSON only: "
           '{"kind":"company"|"macro"|"other","company":str|null,"ticker":str|null,'
           '"metric":"revenue"|"gross_margin"|"operating_margin"|"operating_income"|"net_income"|"rd"|null,'
           '"indicator":"inflation"|"unemployment"|null,"country":"GB"|"US"|null,"year":int|null,"direction":"up"|"down"|null}')
    try:
        d = llm.chat_json(sys, question)
    except Exception:
        return {}
    out: dict[str, Any] = {k: d[k] for k in ("metric", "indicator", "country", "year", "direction") if d.get(k)}
    if d.get("ticker") or d.get("company"):
        co = resolve_company(d.get("ticker") or d.get("company"))
        if co:
            out["company"] = co
    return out
