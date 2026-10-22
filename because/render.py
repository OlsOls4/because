"""Render a Trace as Markdown (CLI, saved examples) and as a Mermaid evidence graph."""

from __future__ import annotations

from typing import Any


def _short(s: str, n: int = 60) -> str:
    s = s.replace('"', "'").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def mermaid(t: dict[str, Any], max_findings: int = 18) -> str:
    """question -> hypotheses (by verdict) -> findings -> queries -> sources."""
    lines = ["flowchart LR", f'  Q(["{_short(t["question"], 70)}"])']
    used_f: list[str] = []
    for h in t.get("hypotheses", []):
        cls = {"supported": "sup", "contradicted": "con"}.get(h["verdict"], "inc")
        lines.append(f'  {h["id"]}["{h["id"]} {_short(h["statement"], 48)}<br/><i>{h["verdict"]}</i>"]:::{cls}')
        lines.append(f"  Q --> {h['id']}")
        for fid in (h.get("evidence") or [])[:4]:
            lines.append(f"  {fid} -->|supports| {h['id']}")
            used_f.append(fid)
        for fid in (h.get("counter") or [])[:2]:
            lines.append(f"  {fid} -.->|against| {h['id']}")
            used_f.append(fid)
    fmap = {f["id"]: f for f in t.get("findings", [])}
    seen_q: set[str] = set()
    for fid in list(dict.fromkeys(used_f))[:max_findings]:
        f = fmap.get(fid)
        if not f:
            continue
        shape = ("[/", "/]") if f["kind"] == "context" else ("[", "]")
        lines.append(f'  {fid}{shape[0]}"{fid} {_short(f["statement"], 44)}"{shape[1]}:::{"ctx" if f["kind"] == "context" else "fact"}')
        if f.get("query_id"):
            qid = f["query_id"].replace("-", "_")
            if qid not in seen_q:
                seen_q.add(qid)
                lines.append(f'  {qid}[("{f["query_id"]}")]:::q')
            lines.append(f"  {qid} --> {fid}")
    lines += [
        "  classDef sup fill:#d1fae5,stroke:#047857,color:#064e3b",
        "  classDef con fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d",
        "  classDef inc fill:#f3f4f6,stroke:#6b7280,color:#374151",
        "  classDef fact fill:#eff6ff,stroke:#1d4ed8,color:#1e3a8a",
        "  classDef ctx fill:#fef9c3,stroke:#a16207,color:#713f12",
        "  classDef q fill:#ede9fe,stroke:#6d28d9,color:#4c1d95",
    ]
    return "\n".join(lines)


def markdown(t: dict[str, Any]) -> str:
    a = t.get("answer", {})
    out = [f"# {t['question']}", ""]
    out.append(f"_Backend: **{t.get('backend')}** · LLM: {t.get('llm', {}).get('provider')} · started {t.get('started_at')}_")
    if t.get("fallback_reason"):
        out.append(f"\n> {t['fallback_reason']}")
    out += ["", "## Answer", "", a.get("summary", "")]
    if a.get("narrative"):
        out += ["", "**Narrative** (LLM, validated against the findings):", "", a["narrative"]]
    c = a.get("confidence")
    if c:
        out += ["", f"**Confidence: {c.get('label')} ({c.get('score')})** - {c.get('rationale')}"]
    if a.get("because"):
        out += ["", "## Because", ""]
        for b in a["because"]:
            out.append(f"- **{b['claim']}** - {b.get('detail', '')} [{', '.join(b['evidence'])}]")
    if a.get("counter_evidence"):
        out += ["", "## Counter-evidence and limits", ""]
        for b in a["counter_evidence"]:
            out.append(f"- {b['claim']} [{', '.join(b.get('evidence', []))}]")
    if a.get("caveats"):
        out += ["", "## Caveats", ""] + [f"- {x}" for x in a["caveats"]]
    if t.get("hypotheses"):
        out += ["", "## Hypotheses tested", "", "| # | Hypothesis | Test | Verdict | Detail |", "|---|---|---|---|---|"]
        for h in t["hypotheses"]:
            out.append(f"| {h['id']} | {h['statement']} | {h['test']} | **{h['verdict']}** | {h.get('detail', '')} |")
    if t.get("findings"):
        out += ["", "## Evidence (every claim's source)", "", "| # | Finding | Kind | Query · rows | Source |", "|---|---|---|---|---|"]
        for f in t["findings"]:
            q = f"`{f['query_id']}` · {f['rows']}" if f.get("query_id") else ""
            src = f"[link]({f['source_url']})" if f.get("source_url") else ""
            out.append(f"| {f['id']} | {f['statement'].replace('|', '/')} | {f['kind']} | {q} | {src} |")
    if t.get("queries"):
        out += ["", "## Queries run through the data harness", ""]
        for q in t["queries"]:
            out.append(f"- `{q['query_id']}` ({q['backend']}, {len(q['rows'])} rows{', ERROR ' + q['error'] if q.get('error') else ''}) - {q.get('purpose', '')}")
            out.append(f"  ```sql\n  {q['sql']}\n  ```")
            for s in q.get("sources", []):
                out.append(f"  source: `{s['table']}` <- {s['url']}")
    if t.get("hypotheses"):
        out += ["", "## Evidence graph", "", "```mermaid", mermaid(t), "```"]
    out += ["", "## Reasoning trace", ""]
    for s in t.get("steps", []):
        out.append(f"1. **{s['stage']}** - {s['message']}")
    sc = t.get("scout") or {}
    if sc.get("contract"):
        k = sc["contract"]
        out += ["", "## Scout execution contract", "",
                f"Intent `{sc.get('intent')}` (band {sc.get('band')}, risk {sc.get('risk', {}).get('level')}). Contract `{k['contractId']}`, "
                f"capabilities {k['scope']['capabilities']}, expires {k['expiresAt']}, HMAC `{k['signature'][:16]}…`."]
    if t.get("memory_recall"):
        out += ["", "## Recalled from memory (H-MEM)", ""]
        for m in t["memory_recall"]:
            out.append(f"- {m['content']} (score {m.get('score')}, {m.get('provenance', {}).get('label')})")
    if t.get("memory_written"):
        out += ["", f"_Wrote {len(t['memory_written'])} memories with provenance; {len(t.get('conflicts', []))} conflicts with prior beliefs._"]
    return "\n".join(out) + "\n"
