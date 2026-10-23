"""Offline tests for the FastAPI web UI (no /api/ask — network)."""

from fastapi.testclient import TestClient

from because.web.app import app

client = TestClient(app)


def test_index_returns_because():
    r = client.get("/")
    assert r.status_code == 200
    assert "Because" in r.text


def test_examples_list_and_detail():
    r = client.get("/api/examples")
    assert r.status_code == 200
    examples = r.json()
    assert isinstance(examples, list)
    assert examples, "expected at least one saved example under examples/outputs"
    slug = examples[0]["slug"]
    detail = client.get(f"/api/examples/{slug}")
    assert detail.status_code == 200
    body = detail.json()
    assert "trace" in body and "mermaid" in body
    assert body["trace"].get("question")


def test_zetaris_runs_list_and_unknown_404():
    r = client.get("/api/zetaris_runs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    missing = client.get("/api/zetaris_runs/__no_such_zetaris_run__")
    assert missing.status_code == 404


def test_index_ui_copy():
    """Judge-facing copy: no internal mode label, visible Zetaris proof, explicit re-run control."""
    html = client.get("/").text
    assert "DuckDB fallback (SAMPLE_MODE)" not in html
    assert "Demo data: same SQL as on Zetaris" in html
    assert "Ran on Zetaris Lightning" in html
    assert "actions/runs/39949595787" in html
    assert "Re-run live" in html
    assert "What do you already know about Intel's margins?" in html
    assert "How it works" in html


def test_memory_recall_example_loads():
    """The memory chip opens the saved recall run instantly."""
    r = client.get("/api/examples/memory-recall-intel")
    assert r.status_code == 200
    assert r.json()["trace"]["answer"].get("memory")
