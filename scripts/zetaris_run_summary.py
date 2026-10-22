#!/usr/bin/env python3
"""Summarise a directory of Because traces (used by the zetaris-e2e workflow).

    python scripts/zetaris_run_summary.py zetaris_runs/  > SUMMARY.md

For each trace: the backend that answered, how many queries ran on Zetaris vs. a local
fallback, how many returned rows, the answer summary and confidence. Exit code is 0.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def main(d: str) -> int:
    root = Path(d)
    print("# Because on Zetaris: run summary\n")
    ping = root / "ping.json"
    if ping.exists():
        print("**Zetaris ping:** `" + " ".join(ping.read_text().split())[:300] + "`\n")
    print("| Question | Backend | Queries (by backend) | With rows | Errors | Confidence | Answer |")
    print("|---|---|---|---|---|---|---|")
    for f in sorted(root.glob("*.json")):
        try:
            t = json.loads(f.read_text())
        except Exception:
            continue
        if not isinstance(t, dict) or "question" not in t:
            continue
        qs = t.get("queries") or []
        by = Counter(q.get("backend", "?") for q in qs)
        rows = sum(1 for q in qs if q.get("rows"))
        errs = [q for q in qs if q.get("error")]
        ans = t.get("answer") or {}
        c = ans.get("confidence") or {}
        summ = " ".join((ans.get("summary") or "").split()).replace("|", "/")[:220]
        bys = ", ".join(f"{k}: {v}" for k, v in by.most_common())
        print(f"| [{t['question']}]({f.stem}.md) | {t.get('backend')} | {bys} | {rows}/{len(qs)} | {len(errs)} | "
              f"{c.get('label', 'n/a')} {c.get('score', '')} | {summ} |")
    errs_all = []
    for f in sorted(root.glob("*.json")):
        try:
            t = json.loads(f.read_text())
        except Exception:
            continue
        for q in (t.get("queries") or []) if isinstance(t, dict) else []:
            if q.get("error"):
                errs_all.append((t.get("question"), q.get("query_id"), q["error"][:300]))
    if errs_all:
        print("\n## Query errors\n")
        for qn, qid, e in errs_all[:40]:
            print(f"- {qn} `{qid}`: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "zetaris_runs"))
