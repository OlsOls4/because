"""Live checks against public APIs. Run with: pytest -m live"""

import pytest

from because.agent import Analyst
from because.harness.duckdb_backend import DuckDBBackend
from because.meterless.hmem import HMEM


@pytest.mark.live
def test_live_intel(tmp_path):
    t = Analyst(harness=DuckDBBackend(), memory=HMEM(path=tmp_path / "m.json"), llm=None, background=False).ask(
        "Why did Intel's gross margin fall in 2024?")
    assert t.answer["because"] and t.answer["confidence"]["score"] > 0


@pytest.mark.live
def test_live_uk_inflation(tmp_path):
    t = Analyst(harness=DuckDBBackend(), memory=HMEM(path=tmp_path / "m.json"), llm=None, background=False).ask(
        "Why did UK inflation spike in 2022?")
    assert any(h.verdict == "supported" for h in t.hypotheses)
