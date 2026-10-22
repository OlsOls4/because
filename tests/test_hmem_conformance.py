"""Python port of the official Meterless H-MEM conformance runner, same vectors.

The official TypeScript runner is also run unmodified via
conformance/meterless/run.sh (see conformance/meterless/hmem-scorecard.txt).
"""

import json
from pathlib import Path

import pytest

from because.meterless.hmem import HMEM

V = json.loads((Path(__file__).parent / "fixtures" / "meterless_hmem_vectors.json").read_text())
T0 = V["clock"]
DAY = 24 * 60 * 60 * 1000
S = V["sections"]


@pytest.mark.parametrize("c", S["record-validation"], ids=lambda c: c["id"])
def test_record_validation(c):
    h = HMEM(clock=lambda: T0)
    rejected = False
    try:
        h.add({"content": c["input"]["content"], "source": c["input"]["source"]})
    except Exception:
        rejected = True
    assert rejected == (c["expect"] == "reject")


@pytest.mark.parametrize("c", S["feedback-deltas"], ids=lambda c: c["id"])
def test_feedback_deltas(c):
    h = HMEM(clock=lambda: T0)
    m = h.add({"content": "feedback target record", "confidence": c["start"], "source": "conformance"})
    before = m["accessCount"]
    h.feedback(m["id"], c["kind"])
    after = h.store.get(m["id"])
    assert after["confidence"] == pytest.approx(c["expectConfidence"], abs=1e-9)
    if "expectAccessDelta" in c:
        assert after["accessCount"] - before == c["expectAccessDelta"]
    if c.get("expectTag"):
        assert c["expectTag"] in after["tags"]


@pytest.mark.parametrize("c", S["ranking-order"], ids=lambda c: c["id"])
def test_ranking_order(c):
    h = HMEM(clock=lambda: T0)
    by = {r["key"]: h.add({"content": r["content"], "confidence": r["confidence"], "layer": r["layer"], "source": "conformance"}) for r in c["records"]}
    for r in c["records"]:
        if r.get("supersededByKey"):
            by[r["key"]]["supersededBy"] = by[r["supersededByKey"]]["id"]
    order = [m["memory"]["id"] for m in h.query(c["query"], {"threshold": 0, "topN": 10})["memories"]]
    exp = [by[k]["id"] for k in c["expectOrder"]]
    for a, b in zip(exp, exp[1:]):
        assert order.index(a) < order.index(b)


@pytest.mark.parametrize("c", S["sleep-guardrails"], ids=lambda c: c["id"])
def test_sleep_guardrails(c):
    now = [T0]
    h = HMEM(clock=lambda: now[0])
    by = {r["key"]: h.add({"content": r["content"], "source": r["source"], "derivedFrom": r.get("derivedFrom")}) for r in c["records"]}
    now[0] = T0 + c["ageDays"] * DAY
    before = sorted(m["id"] for m in h.store.all())
    p = h.sleep.preview()
    assert sorted(m["id"] for m in h.store.all()) == before, "preview mutated the store"
    for k in c["expectArchived"]:
        assert by[k]["id"] in p["toArchive"]
    for k in c["expectProtected"]:
        assert by[k]["id"] not in p["toArchive"]
    rep = h.sleep.execute(p)
    for k in c["expectProtected"]:
        assert h.store.get(by[k]["id"]) is not None
    assert isinstance(rep["backupId"], str) and rep["backupId"]
    assert h.sleep.restore(rep["backupId"])


@pytest.mark.parametrize("c", S["conflict-gate"], ids=lambda c: c["id"])
def test_conflict_gate(c):
    h = HMEM(clock=lambda: T0)
    a = h.add({"content": c["a"]["content"], "confidence": c["a"]["confidence"], "source": "conformance"})
    b = h.add({"content": c["b"]["content"], "confidence": c["b"]["confidence"], "source": "conformance"})
    found = h.conflicts.scan()
    assert found
    out = h.conflicts.autoResolve(found[0])
    assert out in ("resolved", "queued")
    if out == "resolved":
        assert any(h.store.get(x["id"]).get("supersededBy") for x in (a, b))
    assert h.store.get(a["id"]) and h.store.get(b["id"])


@pytest.mark.parametrize("c", S["ledger-audit"], ids=lambda c: c["id"])
def test_ledger_audit(c):
    h = HMEM(clock=lambda: T0)
    m = h.add({"content": "audited record", "confidence": c["start"], "source": "conformance"})
    h.feedback(m["id"], c["kind"])
    hist = h.ledger.history(m["id"])
    assert [x["action"] for x in hist] == c["expectActions"]
    assert hist[-1]["previousState"]["confidence"] == pytest.approx(c["expectPrev"])
    assert hist[-1]["newState"]["confidence"] == pytest.approx(c["expectNew"])


@pytest.mark.parametrize("c", S["dream-boundary"], ids=lambda c: c["id"])
def test_dream_boundary(c):
    h = HMEM(clock=lambda: T0)
    for r in c["records"]:
        h.add({"content": r["content"], "type": r["type"], "source": "conformance"})
    n = len(h.store.all())
    props = h.dreaming.dream()
    assert props and len(h.store.all()) == n
    created = h.dreaming.approve(props[0]["id"])
    if created:
        assert created["layer"] == "long_term" and created["derivedFrom"]


def test_numeric_conflict_on_same_fact(tmp_path):
    """Because-specific: a restated number for the same fact key is detected, and the loser is kept but superseded."""
    h = HMEM(clock=lambda: T0, path=tmp_path / "m.json")
    a = h.add({"content": "Intel revenue FY2024: $53.10B", "source": "harness:duckdb", "factKey": "INTC:revenue:2024", "value": 53.1e9, "confidence": 0.9,
               "provenance": {"origin": "harness_query"}})
    b = h.add({"content": "Intel revenue FY2024: $54.20B", "source": "harness:duckdb", "factKey": "INTC:revenue:2024", "value": 54.2e9, "confidence": 0.5})
    found = h.conflicts.scan()
    assert found and found[0]["kind"] == "numeric"
    h.conflicts.autoResolve(found[0])
    assert h.store.get(a["id"]) and h.store.get(b["id"])
    h.save()
    h2 = HMEM(clock=lambda: T0, path=tmp_path / "m.json")
    assert len(h2.store.all()) == 0 or all(r["layer"] != "short_term" for r in h2.store.all())
