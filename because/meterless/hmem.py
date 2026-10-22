"""H-MEM: hierarchical memory with provenance, after Meterless engines/hmem/AGENTS.md.

A Python implementation of the Meterless H-MEM spec (Apache-2.0 spec,
https://github.com/Meterless/Meterless/tree/main/engines/hmem). Because uses it
to remember every *finding* with the query that produced it, so the agent can
explain, across sessions, why it believes something.

Implemented from the spec:
  * canonical record (layer, confidence, mandatory source + provenance, graph fields)
  * tiered persistence (long_term + working on disk, short_term in session)
  * enrichment: domain, namespace, entities (cap 10), relatedTo (cap 5), supersedes
  * hybrid retrieval with the canonical formula, threshold 0.35, top-N 5
  * reinjection: domain-grouped context + trace metadata
  * feedback deltas (+0.05 / -0.03 / -0.20 + review tag)
  * append-only trust ledger with the 17 canonical actions
  * conflict detection (opposing phrases, numeric divergence) + auto-resolve gate 0.70
  * dreaming with a human approval boundary
  * preview-first sleep cycle with guardrails, backup and restore

Verified against the official conformance runner (conformance/meterless/) and a
pytest port of the same vectors (tests/test_hmem_conformance.py).

Field names are camelCase on purpose: records serialise 1:1 with the spec.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any, Callable

DAY_MS = 24 * 60 * 60 * 1000

LEDGER_ACTIONS = (
    "create", "read", "update", "delete", "feedback", "promote", "demote", "merge",
    "conflict_detected", "conflict_resolved", "sleep_consolidate", "sleep_archive",
    "sleep_synthesize", "dream_proposed", "dream_approved", "dream_rejected", "restore",
)
TYPES = ("personal", "factual", "preference", "general")
LAYERS = ("short_term", "working", "long_term")
LAYER_WEIGHT = {"long_term": 1.0, "working": 0.8, "short_term": 0.6}
ORIGINS = (
    "harness_query", "news", "document", "user_statement", "user_correction", "model_inference",
    "dreaming", "sleep_synthesis", "direct_capture",
)

# Operational defaults (spec section 13)
TOP_N = 5
THRESHOLD = 0.35
CONSOLIDATE_DAYS = 7
ARCHIVE_DAYS = 30
ARCHIVE_MAX_ACCESS = 2
SYNTH_SIMILARITY = 0.82
DREAM_MIN_CLUSTER = 2
DREAM_RELATEDNESS = 0.35
MAX_PROPOSALS_PER_TYPE = 8
AUTO_RESOLVE_GATE = 0.70
ENTITY_CAP = 10
RELATED_CAP = 5
FEEDBACK = {"helpful": 0.05, "not_helpful": -0.03, "wrong": -0.20}

STOP = set(
    "a an and are as at be but by for from has have i in is it its of on or that the this to was were will with "
    "we our you your do does did when what why how which who than then so not no".split()
)
OPPOSING = [
    ("always", "never"), ("enable", "disable"), ("enabled", "disabled"), ("increase", "decrease"),
    ("rose", "fell"), ("up", "down"), ("true", "false"), ("yes", "no"), ("supports", "contradicts"),
    ("higher", "lower"), ("allow", "deny"),
]
DOMAIN_KEYWORDS = {
    "finance": ["revenue", "margin", "gross", "profit", "income", "cost", "sec", "edgar", "10-k", "earnings", "capex", "depreciation", "impairment", "restructuring"],
    "macro": ["inflation", "cpi", "unemployment", "rate", "gdp", "prices", "energy", "oil", "gas", "currency", "fred", "ons"],
    "news": ["headline", "news", "article", "reported", "gdelt", "wikipedia"],
    "tech": ["python", "docker", "api", "deploy", "react", "telemetry", "scripting", "code"],
    "work": ["meeting", "deadline", "project", "roadmap", "quarterly", "office"],
}


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9\-_.%]*", text.lower()) if t not in STOP]


def embed(text: str, dim: int = 256) -> list[float]:
    """Lightweight deterministic embedding: hashed words + char trigrams, L2-normalised."""
    v = [0.0] * dim
    toks = tokens(text)
    feats = toks + [t[i:i + 3] for t in toks for i in range(max(1, len(t) - 2))]
    for f in feats:
        h = int(hashlib.md5(f.encode()).hexdigest(), 16)
        v[h % dim] += 1.0 if (h >> 8) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / n, 6) for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def infer_domain(text: str, tags: list[str] | None = None) -> str:
    tags = tags or []
    for t in tags:  # pass 1: explicit tags
        if t.startswith("domain:"):
            return t.split(":", 1)[1]
    toks = set(tokens(text)) | {t.split(":", 1)[-1] for t in tags}
    best, score = "general", 0
    for d, kws in DOMAIN_KEYWORDS.items():
        s = sum(1 for k in kws if k in toks)
        if s > score:
            best, score = d, s
    return best


def extract_entities(text: str) -> list[dict[str, str]]:
    """Heuristic entity extraction: capitalised names, tickers, years, metric terms."""
    ents: dict[str, str] = {}
    for m in re.findall(r"\b([A-Z][a-zA-Z&]+(?:\s[A-Z][a-zA-Z&]+)*)\b", text):
        if m.lower() not in STOP and len(m) > 2:
            ents[m.lower()] = "name"
    for m in re.findall(r"\b(19|20)\d{2}\b", text):
        pass
    for m in re.findall(r"\b((?:19|20)\d{2})\b", text):
        ents[m] = "period"
    for k in DOMAIN_KEYWORDS["finance"] + DOMAIN_KEYWORDS["macro"]:
        if re.search(rf"\b{re.escape(k)}\b", text.lower()):
            ents[k] = "concept"
    return [{"name": n, "type": t} for n, t in list(ents.items())[:ENTITY_CAP]]


# --------------------------------------------------------------------------
# Services
# --------------------------------------------------------------------------


class TrustLedger:
    """Append-only audit ledger. Never destructively pruned."""

    def __init__(self, hmem: "HMEM") -> None:
        self.h = hmem
        self.entries: list[dict[str, Any]] = []

    def log(self, memory_id: str | None, action: str, actor: str = "system", previous: Any = None,
            new: Any = None, details: dict | None = None) -> None:
        assert action in LEDGER_ACTIONS, action
        e = {"memoryId": memory_id, "action": action, "actor": actor, "timestamp": self.h.now()}
        if previous is not None:
            e["previousState"] = previous
        if new is not None:
            e["newState"] = new
        if details:
            e["details"] = details
        self.entries.append(e)

    def history(self, memory_id: str) -> list[dict[str, Any]]:
        return [e for e in self.entries if e["memoryId"] == memory_id]

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e["action"]] = out.get(e["action"], 0) + 1
        return out


class MemoryStore:
    def __init__(self, hmem: "HMEM") -> None:
        self.h = hmem
        self.records: dict[str, dict[str, Any]] = {}

    def get(self, mid: str) -> dict[str, Any] | None:
        return self.records.get(mid)

    def all(self) -> list[dict[str, Any]]:
        return list(self.records.values())

    def put(self, rec: dict[str, Any]) -> None:
        self.records[rec["id"]] = rec

    def delete(self, mid: str) -> None:
        self.records.pop(mid, None)


class ConflictService:
    def __init__(self, hmem: "HMEM") -> None:
        self.h = hmem
        self.records: list[dict[str, Any]] = []
        self.queue: list[dict[str, Any]] = []

    def _opposed(self, a: str, b: str) -> str | None:
        ta, tb = set(tokens(a)), set(tokens(b))
        for x, y in OPPOSING:
            if (x in ta and y in tb and y not in ta and x not in tb) or (y in ta and x in tb and x not in ta and y not in tb):
                return f"{x}/{y}"
        return None

    def scan(self) -> list[dict[str, Any]]:
        found = []
        recs = [r for r in self.h.store.all() if not r.get("supersededBy")]
        for i, a in enumerate(recs):
            for b in recs[i + 1:]:
                if a.get("supersedes") == b["id"] or b.get("supersedes") == a["id"]:
                    continue  # intended replacement, not a conflict
                c = self._check(a, b)
                if c:
                    found.append(c)
        for c in found:
            self.records.append(c)
            self.h.ledger.log(c["a"], "conflict_detected", details={"with": c["b"], "reason": c["reason"], "kind": c["kind"]})
        return found

    def _check(self, a: dict, b: dict) -> dict | None:
        # numeric divergence on the same fact key (provenance-aware)
        fa, fb = a.get("factKey"), b.get("factKey")
        if fa and fa == fb and a.get("value") is not None and b.get("value") is not None:
            va, vb = float(a["value"]), float(b["value"])
            denom = max(abs(va), abs(vb), 1e-9)
            if abs(va - vb) / denom > 0.005:
                return self._mk(a, b, "numeric", f"{fa}: {va:g} vs {vb:g}", 0.8)
        opp = self._opposed(a["content"], b["content"])
        if opp:
            strip = {w for pair in OPPOSING for w in pair}
            ta = set(tokens(a["content"])) - strip
            tb = set(tokens(b["content"])) - strip
            sim = jaccard(ta, tb)
            if sim >= 0.5:
                conf = 0.5 + 0.3 * sim
                if set(e["name"] for e in a.get("entities", [])) & set(e["name"] for e in b.get("entities", [])):
                    conf += 0.05
                if a.get("domain") == b.get("domain"):
                    conf += 0.05
                if b["id"] in a.get("relatedTo", []):
                    conf -= 0.05
                return self._mk(a, b, "opposing", f"opposing phrase {opp}", clamp01(conf))
        return None

    def _mk(self, a: dict, b: dict, kind: str, reason: str, conf: float) -> dict:
        return {"id": f"conf-{a['id']}-{b['id']}", "a": a["id"], "b": b["id"], "kind": kind,
                "reason": reason, "confidence": round(conf, 4), "status": "open"}

    def _strength(self, r: dict) -> float:
        age_days = (self.h.now() - r["timestamp"]) / DAY_MS
        recency = math.exp(-age_days / 14)
        access = min(1.0, r.get("accessCount", 0) / 10)
        rich = min(1.0, len(r["content"]) / 200)
        reliab = 1.0 if r.get("source") == "user_correction" else (0.8 if r.get("provenance", {}).get("origin") == "harness_query" else 0.5)
        return 0.45 * r["confidence"] + 0.2 * recency + 0.1 * access + 0.1 * rich + 0.15 * reliab

    def autoResolve(self, conflict: dict) -> str:  # noqa: N802 - spec name
        a, b = self.h.store.get(conflict["a"]), self.h.store.get(conflict["b"])
        if not a or not b:
            return "queued"
        sa, sb = self._strength(a), self._strength(b)
        decision = min(1.0, 0.5 + abs(sa - sb))
        if decision < AUTO_RESOLVE_GATE:
            conflict["status"] = "queued"
            self.queue.append(conflict)
            return "queued"
        winner, loser = (a, b) if sa >= sb else (b, a)
        self.resolve(conflict, "keep_a" if winner is a else "keep_b", actor="auto", decision=decision)
        return "resolved"

    auto_resolve = autoResolve

    def resolve(self, conflict: dict, strategy: str, actor: str = "user", decision: float | None = None) -> None:
        a, b = self.h.store.get(conflict["a"]), self.h.store.get(conflict["b"])
        if strategy in ("keep_a", "keep_b"):
            winner, loser = (a, b) if strategy == "keep_a" else (b, a)
            prev = {"supersededBy": loser.get("supersededBy")}
            loser["supersededBy"] = winner["id"]  # penalised in ranking, never deleted
            self.h.ledger.log(loser["id"], "conflict_resolved", actor, prev, {"supersededBy": winner["id"]},
                              {"strategy": strategy, "decisionConfidence": decision, "conflict": conflict["id"]})
        elif strategy == "keep_both":
            self.h.ledger.log(a["id"], "conflict_resolved", actor, details={"strategy": strategy, "conflict": conflict["id"]})
        elif strategy == "merge":
            merged = self.h.add({"content": f"{a['content']} / {b['content']}", "source": "conflict_merge",
                                 "derivedFrom": [a["id"], b["id"]], "layer": "long_term",
                                 "confidence": max(a["confidence"], b["confidence"])})
            for r in (a, b):
                r["supersededBy"] = merged["id"]
            self.h.ledger.log(merged["id"], "merge", actor, details={"from": [a["id"], b["id"]]})
        elif strategy == "delete_both":
            for r in (a, b):
                self.h.ledger.log(r["id"], "delete", actor, previous=copy.deepcopy(r))
                self.h.store.delete(r["id"])
        conflict["status"] = "resolved"


class DreamingService:
    def __init__(self, hmem: "HMEM") -> None:
        self.h = hmem
        self.proposals: dict[str, dict[str, Any]] = {}

    def _related(self, a: dict, b: dict) -> float:
        cos = cosine(a.get("embedding", []), b.get("embedding", []))
        tag = jaccard(set(a.get("tags", [])), set(b.get("tags", [])))
        same = (0.5 if a["type"] == b["type"] else 0) + (0.5 if a.get("domain") == b.get("domain") else 0)
        ent = jaccard({e["name"] for e in a.get("entities", [])}, {e["name"] for e in b.get("entities", [])})
        return 0.4 * cos + 0.2 * tag + 0.2 * same + 0.2 * ent

    def clusters(self) -> list[list[dict]]:
        recs = [r for r in self.h.store.all() if not r.get("supersededBy")]
        seen: set[str] = set()
        out = []
        for r in recs:
            if r["id"] in seen:
                continue
            group = [r] + [o for o in recs if o["id"] != r["id"] and o["id"] not in seen and self._related(r, o) >= DREAM_RELATEDNESS]
            if len(group) >= DREAM_MIN_CLUSTER:
                out.append(group)
                seen.update(g["id"] for g in group)
        return out

    def dream(self) -> list[dict[str, Any]]:
        """Return proposals. Never writes to the store (approval boundary)."""
        props: list[dict[str, Any]] = []
        for group in self.clusters()[:MAX_PROPOSALS_PER_TYPE]:
            ents: dict[str, int] = {}
            for g in group:
                for e in g.get("entities", []):
                    ents[e["name"]] = ents.get(e["name"], 0) + 1
            common = [k for k, v in sorted(ents.items(), key=lambda kv: -kv[1]) if v > 1][:3]
            props.append(self._prop("insight", f"Pattern across {len(group)} memories" + (f" about {', '.join(common)}" if common else "") + ": "
                                    + " | ".join(g["content"][:80] for g in group[:4]), group))
        general = [r for r in self.h.store.all() if r.get("domain") == "general"]
        for r in general[:MAX_PROPOSALS_PER_TYPE]:
            d = infer_domain(r["content"] + " " + " ".join(r.get("tags", [])))
            if d != "general":
                props.append(self._prop("domain", f"Reclassify into domain '{d}'", [r], domain=d))
        prefs = [r for r in self.h.store.all() if r["type"] in ("preference", "personal") or "correction" in r.get("tags", [])]
        if len(prefs) >= 2:
            props.append(self._prop("invariant", "Stable rule: " + "; ".join(p["content"] for p in prefs[:5]), prefs[:5]))
        for p in props:
            self.proposals[p["id"]] = p
            self.h.ledger.log(None, "dream_proposed", details={"proposal": p["id"], "kind": p["kind"]})
        return props

    def _prop(self, kind: str, content: str, sources: list[dict], **extra: Any) -> dict[str, Any]:
        pid = "dream-" + hashlib.sha1((kind + content).encode()).hexdigest()[:10]
        return {"id": pid, "kind": kind, "content": content, "sources": [s["id"] for s in sources], "status": "pending", **extra}

    def approve(self, pid: str) -> dict[str, Any] | None:
        p = self.proposals.get(pid)
        if not p or p["status"] != "pending":
            return None
        p["status"] = "approved"
        created = None
        if p["kind"] in ("insight", "invariant"):
            created = self.h.add({
                "content": p["content"], "type": "factual" if p["kind"] == "insight" else "preference",
                "layer": "long_term", "source": "dreaming", "derivedFrom": list(p["sources"]),
                "provenance": {"origin": "dreaming", "label": f"approved {p['kind']} proposal {pid}"},
            })
        elif p["kind"] == "domain":
            for sid in p["sources"]:
                r = self.h.store.get(sid)
                if r:
                    prev = {"domain": r.get("domain")}
                    r["domain"] = p["domain"]
                    self.h.ledger.log(sid, "update", "dreaming", prev, {"domain": p["domain"]})
        self.h.ledger.log(created["id"] if created else None, "dream_approved", "user", details={"proposal": pid})
        return created

    def reject(self, pid: str) -> None:
        p = self.proposals.get(pid)
        if p:
            p["status"] = "rejected"
            self.h.ledger.log(None, "dream_rejected", "user", details={"proposal": pid})


class SleepCycleService:
    def __init__(self, hmem: "HMEM") -> None:
        self.h = hmem
        self.backups: dict[str, dict[str, Any]] = {}
        self.reports: list[dict[str, Any]] = []

    def _protected(self, r: dict, referenced: set[str]) -> bool:
        return bool(
            r.get("derivedFrom") or r.get("supersedes") or r.get("source") == "dreaming"
            or r["id"] in referenced or len(r.get("relatedTo", [])) >= 3
        )

    def preview(self) -> dict[str, list]:
        """Pure: computes what execute() would do; never mutates."""
        now = self.h.now()
        recs = self.h.store.all()
        referenced = set()
        for r in recs:
            referenced.update(r.get("derivedFrom", []) or [])
            if r.get("supersedes"):
                referenced.add(r["supersedes"])
            if r.get("supersededBy"):
                referenced.add(r["supersededBy"])
        to_consolidate = [r["id"] for r in recs if r["layer"] == "short_term" and now - r["lastAccessed"] >= CONSOLIDATE_DAYS * DAY_MS]
        to_archive = [
            r["id"] for r in recs
            if now - r["timestamp"] >= ARCHIVE_DAYS * DAY_MS and r.get("accessCount", 0) <= ARCHIVE_MAX_ACCESS
            and not self._protected(r, referenced)
        ]
        groups, used = [], set(to_archive)
        live = [r for r in recs if r["id"] not in used and not self._protected(r, referenced)]
        for i, a in enumerate(live):
            if a["id"] in used:
                continue
            g = [a["id"]] + [b["id"] for b in live[i + 1:] if b["id"] not in used and cosine(a.get("embedding", []), b.get("embedding", [])) >= SYNTH_SIMILARITY]
            if len(g) > 1:
                groups.append(g)
                used.update(g)
        return {"toConsolidate": to_consolidate, "toArchive": to_archive, "toSynthesize": groups}

    def execute(self, preview: dict | None = None) -> dict[str, Any]:
        preview = preview or self.preview()
        backup_id = f"backup-{self.h.now()}-{len(self.backups)}"
        self.backups[backup_id] = copy.deepcopy(self.h.store.records)
        actions = []
        for mid in preview["toConsolidate"]:
            r = self.h.store.get(mid)
            if r:
                r["layer"] = "long_term"
                self.h.ledger.log(mid, "sleep_consolidate", "sleep", {"layer": "short_term"}, {"layer": "long_term"})
                actions.append(("consolidate", mid))
        for mid in preview["toArchive"]:
            r = self.h.store.get(mid)
            if r:
                self.h.ledger.log(mid, "sleep_archive", "sleep", previous=copy.deepcopy(r))
                self.h.store.delete(mid)
                actions.append(("archive", mid))
        for group in preview["toSynthesize"]:
            recs = [self.h.store.get(m) for m in group if self.h.store.get(m)]
            if len(recs) < 2:
                continue
            tags = sorted({t for r in recs for t in r.get("tags", [])})
            syn = self.h.add({"content": max(recs, key=lambda r: len(r["content"]))["content"], "tags": tags,
                              "source": "sleep_synthesis", "layer": "long_term", "derivedFrom": [r["id"] for r in recs],
                              "confidence": max(r["confidence"] for r in recs),
                              "provenance": {"origin": "sleep_synthesis", "label": f"synthesised from {len(recs)} similar memories"}})
            for r in recs:
                self.h.ledger.log(r["id"], "sleep_synthesize", "sleep", previous=copy.deepcopy(r), details={"into": syn["id"]})
                self.h.store.delete(r["id"])
            actions.append(("synthesize", syn["id"]))
        report = {"backupId": backup_id, "actions": actions, "counts": {k: sum(1 for a in actions if a[0] == k) for k in ("consolidate", "archive", "synthesize")}}
        self.reports.append(report)
        return report

    def restore(self, backup_id: str) -> bool:
        snap = self.backups.get(backup_id)
        if snap is None:
            return False
        self.h.store.records = copy.deepcopy(snap)
        self.h.ledger.log(None, "restore", "user", details={"backupId": backup_id})
        return True


# --------------------------------------------------------------------------
# Facade
# --------------------------------------------------------------------------


class HMEM:
    def __init__(self, clock: Callable[[], float] | None = None, path: str | Path | None = None, **_: Any) -> None:
        if isinstance(clock, dict):  # allow HMEM({"clock": fn}) like the TS surface
            clock = clock.get("clock")
        self.clock = clock or (lambda: time.time() * 1000)
        self.path = Path(path) if path else None
        self.store = MemoryStore(self)
        self.ledger = TrustLedger(self)
        self.conflicts = ConflictService(self)
        self.dreaming = DreamingService(self)
        self.sleep = SleepCycleService(self)
        self._seq = 0
        if self.path and self.path.exists():
            self.load_state(json.loads(self.path.read_text()))

    def now(self) -> int:
        return int(self.clock())

    # -- write ---------------------------------------------------------------
    def add(self, item: dict[str, Any], actor: str = "agent") -> dict[str, Any]:
        content = (item.get("content") or "").strip()
        source = (item.get("source") or "").strip()
        if not source:
            raise ValueError("H-MEM: a record with no source is rejected by construction")
        if len(content) < 3:
            raise ValueError("H-MEM: content too short")
        now = self.now()
        self._seq += 1
        mid = item.get("id") or f"mem-{hashlib.sha1(f'{content}|{source}|{now}|{self._seq}'.encode()).hexdigest()[:12]}"
        tags = list(dict.fromkeys(item.get("tags") or []))
        prov = dict(item.get("provenance") or {})
        prov.setdefault("origin", "direct_capture")
        prov.setdefault("learnedAt", now)
        prov.setdefault("label", f"captured from {source}")
        rec: dict[str, Any] = {
            "id": mid,
            "content": content,
            "type": item.get("type") if item.get("type") in TYPES else "general",
            "layer": item.get("layer") if item.get("layer") in LAYERS else "short_term",
            "timestamp": now,
            "lastAccessed": now,
            "accessCount": 0,
            "tags": tags,
            "embedding": embed(content + " " + " ".join(tags)),
            "confidence": clamp01(float(item.get("confidence", 0.7))),
            "source": source,
            "provenance": prov,
            "chatId": item.get("chatId"),
            "missionId": item.get("missionId"),
            "goalRunId": item.get("goalRunId"),
            "domain": item.get("domain") or infer_domain(content, tags),
            "entities": (item.get("entities") or extract_entities(content))[:ENTITY_CAP],
            "relatedTo": [],
            "supersedes": item.get("supersedes"),
            "supersededBy": item.get("supersededBy"),
            "derivedFrom": list(item.get("derivedFrom") or []),
            "factKey": item.get("factKey"),
            "value": item.get("value"),
        }
        rec["namespace"] = item.get("namespace") or f"{rec['domain']}/{rec['type']}"
        self._link(rec)
        self.store.put(rec)
        self.ledger.log(mid, "create", actor, new={"confidence": rec["confidence"], "layer": rec["layer"]},
                        details={"source": source, "origin": prov["origin"]})
        return rec

    def _link(self, rec: dict) -> None:
        names = {e["name"] for e in rec["entities"]}
        for other in self.store.all():
            if len(rec["relatedTo"]) >= RELATED_CAP:
                break
            shared = names & {e["name"] for e in other.get("entities", [])}
            if len(shared) >= 2 and other["domain"] == rec["domain"]:
                rec["relatedTo"].append(other["id"])
                if rec["id"] not in other["relatedTo"] and len(other["relatedTo"]) < RELATED_CAP:
                    other["relatedTo"].append(rec["id"])
            # supersedes detection: same fact key with a newer value, or correction language + high similarity
            if rec.get("factKey") and other.get("factKey") == rec["factKey"] and rec.get("source") == "user_correction":
                rec["supersedes"] = other["id"]
                other["supersededBy"] = rec["id"]

    # -- read ----------------------------------------------------------------
    def score(self, rec: dict, q: str, qemb: list[float], qtoks: list[str], qdomain: str, qents: set[str]) -> tuple[float, dict]:
        sem = clamp01(cosine(qemb, rec.get("embedding", [])))
        top8 = set(qtoks[:8])
        kw = jaccard(top8, set(tokens(rec["content"])))
        tagtoks = {t.split(":", 1)[-1] for t in rec.get("tags", [])}
        tag = len(top8 & tagtoks) / len(top8) if top8 else 0.0
        dom = 1.0 if rec.get("domain") == qdomain else 0.0
        ents = {e["name"] for e in rec.get("entities", [])}
        ent = len(qents & ents) / len(qents) if qents else 0.0
        layer = LAYER_WEIGHT.get(rec["layer"], 0.6)
        age_days = max(0.0, (self.now() - rec["lastAccessed"]) / DAY_MS)
        rec_ = math.exp(-age_days / 14)
        sup = 1.0 if rec.get("supersededBy") else 0.0
        raw = 0.35 * sem + 0.20 * kw + 0.10 * tag + 0.10 * dom + 0.15 * ent + 0.05 * layer + 0.05 * rec_ - 0.20 * sup
        final = clamp01(raw) * rec["confidence"]
        parts = {"semantic": sem, "keyword": kw, "tag": tag, "domain": dom, "entity": ent, "layer": layer, "recency": rec_, "superseded": sup}
        return final, {k: round(v, 3) for k, v in parts.items()}

    def query(self, q: str, opts: dict | None = None, **kw: Any) -> dict[str, Any]:
        opts = {**(opts or {}), **kw}
        threshold = opts.get("threshold", THRESHOLD)
        top_n = opts.get("topN", opts.get("top_n", TOP_N))
        qemb, qtoks = embed(q), tokens(q)
        qdomain = infer_domain(q)
        qents = {e["name"] for e in extract_entities(q)}
        scored = []
        for r in self.store.all():
            if opts.get("chatId") and r.get("chatId") not in (None, opts["chatId"]):
                continue
            s, parts = self.score(r, q, qemb, qtoks, qdomain, qents)
            if s >= threshold:
                scored.append({"memory": r, "score": round(s, 4), "parts": parts})
        scored.sort(key=lambda x: -x["score"])
        picked = scored[:top_n]
        for p in picked:
            p["memory"]["lastAccessed"] = self.now()
            self.ledger.log(p["memory"]["id"], "read", "retrieval", details={"score": p["score"]})
        strategy = "minimal" if len(picked) <= 1 else ("comprehensive" if len(picked) >= 4 else "focused")
        return {
            "memories": picked,
            "context": self.format_context(picked),
            "retrievalReason": f"hybrid score >= {threshold} for query domain '{qdomain}'",
            "strategy": strategy,
        }

    @staticmethod
    def format_context(picked: list[dict]) -> str:
        groups: dict[str, list[str]] = {}
        for p in picked:
            m = p["memory"]
            ents = ", ".join(e["name"] for e in m.get("entities", [])[:4])
            prov = m.get("provenance", {}).get("label", m["source"])
            groups.setdefault(m.get("domain", "general"), []).append(
                f"- [{m['layer']}] {m['content']} (conf {m['confidence']:.2f}; {prov}" + (f"; entities: {ents}" if ents else "") + ")"
            )
        return "\n".join(f"## {d}\n" + "\n".join(lines) for d, lines in groups.items())

    # -- feedback --------------------------------------------------------------
    def feedback(self, mid: str, kind: str, actor: str = "user") -> dict[str, Any]:
        if kind not in FEEDBACK:
            raise ValueError(f"feedback kind must be one of {list(FEEDBACK)}")
        r = self.store.get(mid)
        if r is None:
            raise KeyError(mid)
        prev = {"confidence": r["confidence"], "accessCount": r["accessCount"]}
        r["confidence"] = round(clamp01(r["confidence"] + FEEDBACK[kind]), 10)
        if kind == "helpful":
            r["accessCount"] += 1
        if kind == "wrong" and "review" not in r["tags"]:
            r["tags"].append("review")
        self.ledger.log(mid, "feedback", actor, prev, {"confidence": r["confidence"], "accessCount": r["accessCount"]}, {"kind": kind})
        return r

    # -- persistence -----------------------------------------------------------
    def state(self, include_short_term: bool = True) -> dict[str, Any]:
        recs = [r for r in self.store.all() if include_short_term or r["layer"] != "short_term"]
        return {
            "version": 1, "seq": self._seq, "records": recs, "ledger": self.ledger.entries,
            "proposals": self.dreaming.proposals, "conflicts": self.conflicts.records,
            "backups": self.sleep.backups,
        }

    def load_state(self, st: dict[str, Any]) -> None:
        self._seq = st.get("seq", 0)
        self.store.records = {r["id"]: r for r in st.get("records", [])}
        self.ledger.entries = st.get("ledger", [])
        self.dreaming.proposals = st.get("proposals", {})
        self.conflicts.records = st.get("conflicts", [])
        self.sleep.backups = st.get("backups", {})
        for r in self.store.records.values():  # backfill embeddings for legacy rows
            if not r.get("embedding"):
                r["embedding"] = embed(r["content"])

    def save(self) -> None:
        """Persist long_term + working (short_term stays in session, per spec)."""
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            st = self.state(include_short_term=False)
            st["backups"] = {}  # backups are session-local snapshots
            self.path.write_text(json.dumps(st, indent=1))
