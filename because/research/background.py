"""Free background/news context: Wikipedia (keyless) and GDELT DOC 2.0 (keyless).

Pre-existing component, declared in README: adapted from the FREE_MODE research
add-on in OlsOls4/ollymcbride-bot (metaculus_bot/research/free_sources.py,
written before 22 Oct 2026): query-term extraction, the polite Wikimedia
User-Agent, the process-wide GDELT 5 s rate gate, and fail-soft behaviour.

Context from here is never treated as proof of a number; it is shown as
"context" evidence and can corroborate (or contradict) a data-backed hypothesis.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import httpx

from because.harness.sources import user_agent

WIKI_API = "https://en.wikipedia.org/w/api.php"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_GDELT_MIN_INTERVAL_S = 5.5
_gdelt_lock = threading.Lock()
_gdelt_last = [0.0]


def _ua() -> dict[str, str]:
    return {"User-Agent": f"because-agent/0.1 (https://github.com/OlsOls4/because; {user_agent().split()[-1]})"}


def wikipedia(query: str, pages: int = 2) -> list[dict[str, Any]]:
    """Top search hits with their lead-section extracts. Fails soft to []."""
    try:
        r = httpx.get(WIKI_API, params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": pages},
                      headers=_ua(), timeout=20)
        hits = r.json().get("query", {}).get("search", [])
        out = []
        for h in hits[:pages]:
            e = httpx.get(WIKI_API, params={"action": "query", "prop": "extracts", "explaintext": 1,
                                            "titles": h["title"], "format": "json"},
                          headers=_ua(), timeout=20).json()
            page = next(iter(e.get("query", {}).get("pages", {}).values()), {})
            text = re.sub(r"\s+", " ", page.get("extract", "")).strip()
            out.append({"title": h["title"], "url": f"https://en.wikipedia.org/wiki/{h['title'].replace(' ', '_')}", "text": text})
        return out
    except Exception:
        return []


def gdelt(query: str, start: str | None = None, end: str | None = None, max_records: int = 10) -> list[dict[str, Any]]:
    """Recent news headlines (GDELT DOC 2.0 covers roughly the last 3 months). Fails soft to []."""
    with _gdelt_lock:
        wait = _GDELT_MIN_INTERVAL_S - (time.time() - _gdelt_last[0])
        if wait > 0:
            time.sleep(wait)
        _gdelt_last[0] = time.time()
    params = {"query": query, "mode": "artlist", "format": "json", "maxrecords": max_records, "sort": "hybridrel"}
    if start:
        params["startdatetime"] = start
    if end:
        params["enddatetime"] = end
    try:
        r = httpx.get(GDELT_URL, params=params, headers=_ua(), timeout=25)
        if not r.text.strip().startswith("{"):
            return []  # rate-limit notice or empty body
        return [{"title": a.get("title"), "url": a.get("url"), "date": a.get("seendate"), "domain": a.get("domain")}
                for a in r.json().get("articles", [])]
    except Exception:
        return []


def sentences_mentioning(text: str, terms: list[str], limit: int = 3) -> list[str]:
    """Pick sentences that mention any of `terms` (case-insensitive)."""
    sents = re.split(r"(?<=[.!?])\s+", text)
    hits = [s.strip() for s in sents if any(t.lower() in s.lower() for t in terms) and 30 < len(s) < 400]
    return hits[:limit]
