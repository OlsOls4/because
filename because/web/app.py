"""FastAPI web UI: question -> evidence graph + trace -> answer with citations.

    because serve            # http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from because.cli import EXAMPLES, _load_env, slug
from because.render import markdown, mermaid

_load_env()
STATIC = Path(__file__).parent / "static"
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples" / "outputs"

app = FastAPI(title="Because", version="0.1.0")
_lock = threading.Lock()
_analysts: dict[str, Any] = {}


def analyst(backend: str | None):
    from because.agent import Analyst
    from because.harness import backend_choice, get_harness

    key = backend or backend_choice()
    if key not in _analysts:
        _analysts[key] = Analyst(harness=get_harness(key))
    return _analysts[key]


class AskIn(BaseModel):
    question: str
    backend: str | None = None


class FeedbackIn(BaseModel):
    memory_id: str
    kind: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    from because.harness import backend_choice
    from because.llm import get_llm

    llm = get_llm()
    a = _analysts.get(backend_choice())
    return {"backend": a.harness.name if a else backend_choice(), "llm": llm.describe() if llm else None,
            "fallback_reason": getattr(a.harness, "fallback_reason", None) if a else None, "examples": EXAMPLES}


@app.post("/api/ask")
def ask(body: AskIn) -> JSONResponse:
    if not body.question.strip():
        raise HTTPException(400, "empty question")
    with _lock:  # one investigation at a time keeps SEC/ONS politeness simple
        t = analyst(body.backend).ask(body.question.strip(), surface="web").to_dict()
    return JSONResponse({"trace": t, "mermaid": mermaid(t), "markdown": markdown(t)})


@app.get("/api/examples")
def examples() -> list[dict[str, str]]:
    out = []
    for q in EXAMPLES:
        p = EXAMPLES_DIR / f"{slug(q)}.json"
        if p.exists():
            out.append({"question": q, "slug": slug(q)})
    return out


@app.get("/api/examples/{name}")
def example(name: str) -> JSONResponse:
    p = EXAMPLES_DIR / f"{Path(name).name}.json"
    if not p.exists():
        raise HTTPException(404, "no such example")
    t = json.loads(p.read_text())
    return JSONResponse({"trace": t, "mermaid": mermaid(t), "markdown": markdown(t)})


@app.get("/api/memory")
def memory(q: str = "", n: int = 20) -> list[dict[str, Any]]:
    a = analyst(None)
    if q:
        res = a.memory.query(q, {"threshold": 0.0, "topN": n})["memories"]
    else:
        res = [{"memory": r, "score": None} for r in a.memory.store.all()[-n:]][::-1]
    return [{"id": x["memory"]["id"], "content": x["memory"]["content"], "confidence": x["memory"]["confidence"],
             "layer": x["memory"]["layer"], "provenance": x["memory"].get("provenance"), "score": x["score"],
             "supersededBy": x["memory"].get("supersededBy"), "tags": x["memory"].get("tags")} for x in res]


@app.get("/api/memory/{mid}")
def memory_why(mid: str) -> dict[str, Any]:
    a = analyst(None)
    r = a.memory.store.get(mid)
    if not r:
        raise HTTPException(404, "unknown memory")
    r = {k: v for k, v in r.items() if k != "embedding"}
    return {"memory": r, "history": a.memory.ledger.history(mid)}


@app.post("/api/feedback")
def feedback(body: FeedbackIn) -> dict[str, Any]:
    a = analyst(None)
    try:
        r = a.feedback(body.memory_id, body.kind)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"id": r["id"], "confidence": r["confidence"], "tags": r["tags"]}
