"""Web hardening: input limits, read-only feedback in SAMPLE_MODE, memory preloaded for the public demo's memory chip."""

import pytest
from fastapi.testclient import TestClient

import because.agent.parse as parse
import because.web.app as webapp
from because.harness.duckdb_backend import DuckDBBackend

client = TestClient(webapp.app)


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    """Fresh shared memory + an offline analyst pool (no network)."""
    monkeypatch.setenv("BECAUSE_HOME", str(tmp_path))
    monkeypatch.setattr(webapp, "_memory", {})
    monkeypatch.setattr(webapp, "_pools", {})
    monkeypatch.setattr(parse, "sec_tickers", lambda: [{"cik_str": 50863, "ticker": "INTC", "title": "INTEL CORP"}])
    monkeypatch.setattr("because.harness.get_harness", lambda *a, **k: DuckDBBackend(row_loader=lambda s: []))
    yield


def test_question_length_is_capped():
    r = client.post("/api/ask", json={"question": "Why " * 100})
    assert r.status_code == 422


def test_backend_value_is_validated():
    r = client.post("/api/ask", json={"question": "Why did UK inflation spike in 2022?", "backend": "../../etc"})
    assert r.status_code == 422


def test_memory_preloaded_and_chip_query_works_on_fresh_visit(fresh):
    mem = client.get("/api/memory?n=50").json()
    assert any("Intel" in m["content"] for m in mem), "Intel memories should be seeded from the saved example run"
    h = client.get("/api/health").json()
    assert h["memory_chip_query"] == "What do you already know about Intel's margins?"
    r = client.post("/api/ask", json={"question": h["memory_chip_query"]})
    assert r.status_code == 200
    t = r.json()["trace"]
    assert t["scout"]["intent"] == "memory.explain_belief" and t["memory_recall"]


def test_feedback_does_not_mutate_shared_memory_in_sample_mode(fresh, monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "1")
    m = client.get("/api/memory?n=1").json()[0]
    r = client.post("/api/feedback", json={"memory_id": m["id"], "kind": "negative"})
    assert r.status_code == 200 and r.json()["simulated"]
    after = client.get(f"/api/memory/{m['id']}").json()["memory"]
    assert after["confidence"] == m["confidence"]


def test_feedback_unknown_memory_is_400(fresh):
    r = client.post("/api/feedback", json={"memory_id": "nope", "kind": "positive"})
    assert r.status_code == 400
