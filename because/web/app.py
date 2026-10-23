"""FastAPI web UI: question -> evidence graph + trace -> answer with citations.

    because serve            # http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from because.cli import EXAMPLES, _load_env, slug
from because.render import markdown, mermaid

_load_env()
STATIC = Path(__file__).parent / "static"
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples" / "outputs"
ZETARIS_RUNS_DIR = Path(__file__).resolve().parents[2] / "examples" / "zetaris_runs"



@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await run_in_threadpool(shared_memory)  # preload the Intel memories so the memory chip works on a fresh visit
    yield


app = FastAPI(title="Because", version="0.1.0", lifespan=_lifespan)

# Concurrency: a small pool of analysts per backend (each owns its harness connection and per-question state),
# all sharing one memory behind one lock. A slow question holds only its own slot; others wait at most
# QUEUE_WAIT_S for a free slot, and every question is cut off after ASK_TIMEOUT_S.
ASK_TIMEOUT_S = float(os.environ.get("BECAUSE_ASK_TIMEOUT", "120"))
QUEUE_WAIT_S = float(os.environ.get("BECAUSE_QUEUE_WAIT", "20"))
POOL_SIZE = max(1, int(os.environ.get("BECAUSE_WEB_WORKERS", "2")))
FEEDBACK_PER_MIN = int(os.environ.get("BECAUSE_FEEDBACK_PER_MIN", "10"))
INTEL_EXAMPLE = "why-did-intel-s-gross-margin-fall-in-2024"
MEMORY_CHIP_QUERY = "What do you already know about Intel's margins?"

_pool_lock = threading.Lock()
_pools: dict[str, dict[str, Any]] = {}
_memory: dict[str, Any] = {}
_feedback_hits: dict[str, deque] = defaultdict(deque)


def sample_mode() -> bool:
    return os.environ.get("SAMPLE_MODE", "").lower() in ("1", "true", "yes")


def shared_memory():
    """One H-MEM (and its lock) for every analyst in this process; seeded with the saved Intel run on first use."""
    with _pool_lock:
        if "hmem" not in _memory:
            from because.agent.analyst import _home, seed_memory_from_trace
            from because.meterless.hmem import HMEM

            mem, lock = HMEM(path=_home() / "memory.json"), threading.RLock()
            _memory.update(hmem=mem, lock=lock, seeded=0)
            p = EXAMPLES_DIR / f"{INTEL_EXAMPLE}.json"
            if os.environ.get("BECAUSE_SEED_MEMORY", "1") != "0" and p.exists():
                try:
                    _memory["seeded"] = seed_memory_from_trace(mem, json.loads(p.read_text()), INTEL_EXAMPLE, lock)
                except Exception as e:  # a bad example file must never stop the app
                    _memory["seed_error"] = f"{type(e).__name__}: {e}"
        return _memory["hmem"], _memory["lock"]


def _pool(backend: str | None) -> dict[str, Any]:
    from because.agent import Analyst
    from because.harness import backend_choice, get_harness

    key = backend or backend_choice()
    mem, lock = shared_memory()
    with _pool_lock:
        if key not in _pools:
            free: deque = deque()
            for _ in range(POOL_SIZE):
                free.append(Analyst(harness=get_harness(key), memory=mem, memory_lock=lock))
            _pools[key] = {"free": free, "sem": threading.BoundedSemaphore(POOL_SIZE), "all": list(free), "lock": threading.Lock()}
        return _pools[key]


def analyst(backend: str | None):
    """Any analyst of the pool, for read-only memory endpoints (memory is shared)."""
    return _pool(backend)["all"][0]


def _run_question(pool: dict[str, Any], question: str) -> dict[str, Any]:
    """Runs in a worker thread with a slot already held; always releases it, even after the caller timed out."""
    with pool["lock"]:
        a = pool["free"].popleft()
    try:
        return a.ask(question, surface="web").to_dict()
    finally:
        with pool["lock"]:
            pool["free"].append(a)
        pool["sem"].release()


class AskIn(BaseModel):
    question: str = Field(..., max_length=300)
    backend: str | None = Field(None, pattern="^(duckdb|zetaris)$")


class FeedbackIn(BaseModel):
    memory_id: str = Field(..., max_length=80)
    kind: str = Field(..., max_length=20)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    from because.harness import backend_choice
    from because.llm import get_llm

    llm = get_llm()
    p = _pools.get(backend_choice())
    a = p["all"][0] if p else None
    return {"backend": a.harness.name if a else backend_choice(), "llm": llm.describe() if llm else None,
            "fallback_reason": getattr(a.harness, "fallback_reason", None) if a else None, "examples": EXAMPLES,
            "memory_chip_query": MEMORY_CHIP_QUERY, "sample_mode": sample_mode(),
            "memory_seeded": _memory.get("seeded"), "limits": {"question_max_length": 300, "ask_timeout_s": ASK_TIMEOUT_S}}


@app.post("/api/ask")
async def ask(body: AskIn) -> JSONResponse:
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "empty question")
    pool = await run_in_threadpool(_pool, body.backend)
    got = await run_in_threadpool(pool["sem"].acquire, True, QUEUE_WAIT_S)
    if not got:
        raise HTTPException(503, f"Busy: {POOL_SIZE} investigations are already running. Try again in a moment.")
    # the slot is released by _run_question when the investigation really ends (it cannot be killed mid-query)
    fut = asyncio.ensure_future(run_in_threadpool(_run_question, pool, q))
    try:
        t = await asyncio.wait_for(asyncio.shield(fut), timeout=ASK_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"The investigation took longer than {ASK_TIMEOUT_S:.0f}s and was abandoned. "
                                 "Saved example runs load instantly.")
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


@app.get("/api/zetaris_runs")
def zetaris_runs() -> list[dict[str, str]]:
    """Saved traces recorded on a live Zetaris instance (examples/zetaris_runs/)."""
    if not ZETARIS_RUNS_DIR.is_dir():
        return []
    out = []
    for p in sorted(ZETARIS_RUNS_DIR.glob("*.json")):
        try:
            t = json.loads(p.read_text())
            q = t.get("question") or p.stem
        except (OSError, json.JSONDecodeError):
            q = p.stem
        out.append({"question": q, "slug": p.stem})
    return out


@app.get("/api/zetaris_runs/{name}")
def zetaris_run(name: str) -> JSONResponse:
    if not ZETARIS_RUNS_DIR.is_dir():
        raise HTTPException(404, "no such zetaris run")
    p = ZETARIS_RUNS_DIR / f"{Path(name).name}.json"
    if not p.exists():
        raise HTTPException(404, "no such zetaris run")
    t = json.loads(p.read_text())
    return JSONResponse({"trace": t, "mermaid": mermaid(t), "markdown": markdown(t)})


@app.get("/api/memory")
def memory(q: str = "", n: int = 20) -> list[dict[str, Any]]:
    a = analyst(None)
    n = max(1, min(n, 100))
    with a.memory_lock:
        if q:
            res = a.memory.query(q[:300], {"threshold": 0.0, "topN": n})["memories"]
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


def _rate_limited(key: str, per_min: int) -> bool:
    now, q = time.time(), _feedback_hits[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_min:
        return True
    q.append(now)
    return False


@app.post("/api/feedback")
def feedback(body: FeedbackIn, request: Request) -> dict[str, Any]:
    a = analyst(None)
    r = a.memory.store.get(body.memory_id)
    if not r:
        raise HTTPException(400, f"unknown memory {body.memory_id}")
    if sample_mode():
        # Public demo: shared memory is read-only for visitors. Echo what the feedback would do, change nothing.
        return {"id": r["id"], "confidence": r["confidence"], "tags": r.get("tags"), "simulated": True,
                "note": "Demo mode (SAMPLE_MODE): feedback is not saved to the shared memory."}
    if _rate_limited(request.client.host if request.client else "?", FEEDBACK_PER_MIN):
        raise HTTPException(429, "too much feedback; try again in a minute")
    try:
        r = a.feedback(body.memory_id, body.kind)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"id": r["id"], "confidence": r["confidence"], "tags": r["tags"]}
