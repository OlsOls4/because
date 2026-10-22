"""The reasoning trace: plan -> queries -> findings -> hypotheses tested -> answer.

Everything the UI, CLI and saved examples show is this object serialised.
Each Finding points at the exact query (query_id + SQL) and row(s) behind it.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Finding:
    id: str
    statement: str
    kind: str  # data | derived | context
    query_id: str | None = None
    rows: list[int] = field(default_factory=list)
    source_url: str | None = None
    value: float | None = None
    unit: str = ""
    fact_key: str | None = None


@dataclass
class Hypothesis:
    id: str
    statement: str
    test: str
    verdict: str = "untested"  # supported | contradicted | inconclusive | untested
    weight: float = 0.0  # magnitude used for ranking (e.g. pts of revenue)
    evidence: list[str] = field(default_factory=list)
    counter: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class Trace:
    question: str
    backend: str = ""
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    finished_at: str = ""
    fallback_reason: str | None = None
    scout: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    memory_recall: list[dict[str, Any]] = field(default_factory=list)
    queries: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    answer: dict[str, Any] = field(default_factory=dict)
    memory_written: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    llm: dict[str, Any] = field(default_factory=dict)

    def step(self, stage: str, message: str, **data: Any) -> None:
        self.steps.append({"t": round(time.time(), 3), "stage": stage, "message": message, **({"data": data} if data else {})})

    def add_finding(self, **kw: Any) -> Finding:
        f = Finding(id=f"F{len(self.findings) + 1}", **kw)
        self.findings.append(f)
        return f

    def add_hypothesis(self, **kw: Any) -> Hypothesis:
        h = Hypothesis(id=f"H{len(self.hypotheses) + 1}", **kw)
        self.hypotheses.append(h)
        return h

    def finding(self, fid: str) -> Finding | None:
        return next((f for f in self.findings if f.id == fid), None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)
