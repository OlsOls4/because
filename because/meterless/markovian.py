"""Markovian engine (Meterless): bounded-chunk reasoning with compressed carryover.

Python port of the parts of the Meterless Markovian spec that Because needs
(https://github.com/Meterless/Meterless/tree/main/engines/markovian, AGENTS.md sections 2, 3, 4.3,
6 and 7; docs/efficiency-model.md). Each step sends only the (truncated) goal, a bounded
carryover block and that step's input, so per-step context stays O(1) in chain length.

Because uses it for *hypothesis rounds*: `review_hypotheses()` walks the tested hypotheses a
few at a time; each round sees only its own hypotheses plus the carried state
(COMPLETED / REMAINING / DECISIONS / CONTEXT), never the transcript of earlier rounds.

Conformance: tests/test_markovian_conformance.py ports the 8 checks of the upstream
conformance runner (engines/markovian/conformance/runner.ts) to pytest.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

STATE = "[STATE_CHECKPOINT]"
FINAL = "[TASK_COMPLETE]"
STATE_ALIASES = (STATE, "@@@STATE@@@", "[STATE]", "---STATE---")
FINAL_ALIASES = (FINAL, "@@@FINAL@@@", "[FINAL]", "---FINAL---")
FRAMING_TOKENS = 400
OUTPUT_BUDGET = 1200
BOUNDS = {"chunkSize": (1000, 128000), "maxChunks": (1, 32), "carryoverTokens": (128, 32768), "overlapTokens": (0, 4096)}


class ConfigError(ValueError):
    """Typed config error: out-of-range values are rejected, never clamped."""


def est_tokens(text: str) -> int:
    return math.ceil(len(text or "") / 4)


@dataclass
class ChunkConfig:
    chunkSize: int = 8000
    maxChunks: int = 24
    carryoverTokens: int = 800
    overlapTokens: int = 0

    def validate(self) -> "ChunkConfig":
        for k, (lo, hi) in BOUNDS.items():
            v = getattr(self, k)
            if not isinstance(v, int) or v < lo or v > hi:
                raise ConfigError(f"{k}={v} out of range [{lo}, {hi}]")
        if self.carryoverTokens <= 0:
            raise ConfigError("carryoverTokens must be > 0")
        if self.chunkSize < FRAMING_TOKENS + self.carryoverTokens + OUTPUT_BUDGET:
            raise ConfigError(f"chunkSize={self.chunkSize} < framing {FRAMING_TOKENS} + carryover {self.carryoverTokens} + output {OUTPUT_BUDGET}")
        return self


def model_totals(n: int, F: int = 400, G: int = 200, S: int = 300, O: int = 1200, C: int = 800) -> dict[str, float]:
    """docs/efficiency-model.md: naive (append-history) vs Markovian totals over n steps."""
    naive = n * (F + G + S) + O * n * (n - 1) // 2
    mark = n * (F + G + C + S)
    return {"naiveTotal": naive, "markovianTotal": mark, "efficiency": (naive - mark) / naive if naive else 0.0}


def run_efficiency(chunks: list[dict[str, Any]], config: dict[str, Any] | ChunkConfig) -> dict[str, float]:
    """AGENTS.md 6.3, always against the run's own config snapshot."""
    cfg = asdict(config) if isinstance(config, ChunkConfig) else dict(config)
    toks = [int(c["tokens"]) for c in chunks]
    total = sum(toks)
    standard = sum(sum(toks[: i + 1]) for i in range(len(toks)))
    mark = len(toks) * int(cfg["carryoverTokens"]) + total
    saved = max(0, standard - mark)
    return {"totalTokensUsed": total, "standardCost": standard, "markovianCost": mark, "tokensSaved": saved,
            "efficiencyPercent": (saved / standard * 100) if standard else 0.0}


def _find(text: str, markers: tuple[str, ...]) -> int:
    idx = [text.find(m) for m in markers if m in text]
    return min(idx) if idx else -1


def clean(text: str) -> str:
    """Strip markers and the state block from display content."""
    i = _find(text, STATE_ALIASES)
    if i >= 0:
        text = text[:i]
    for m in FINAL_ALIASES:
        text = text.replace(m, "")
    return text.strip()


class Markovian:
    def __init__(self, chunkConfig: dict[str, Any] | ChunkConfig | None = None, clock: Callable[[], float] | None = None,
                 compressor: Callable[[str, str], str] | None = None) -> None:
        cfg = chunkConfig if isinstance(chunkConfig, ChunkConfig) else ChunkConfig(**{**asdict(ChunkConfig()), **(chunkConfig or {})})
        self.config = cfg.validate()
        self.clock = clock or (lambda: time.time() * 1000)
        self.compressor = compressor

    # -- carryover cascade (AGENTS.md 4.3) --------------------------------------
    def _carryover(self, raw: str, prev: str) -> str:
        budget = self.config.carryoverTokens * 4
        i = _find(raw, STATE_ALIASES)
        if i >= 0:
            after = raw[i:]
            for m in STATE_ALIASES:
                after = after.replace(m, "")
            after = after.replace(FINAL, "").strip()
            if len(after) >= 24 and len(after) <= budget:
                return after
        if self.compressor:
            try:
                out = self.compressor(
                    f"Previous state:\n{prev}\n\nLatest chunk (tail):\n{raw[-2000:]}\n\nReturn 3-5 critical points as:\n"
                    "COMPLETED: ...\nREMAINING: ...\nDECISIONS: ...\nCONTEXT: ...",
                    "Compress the working state. Be concise.").strip()
                if 24 <= len(out) <= budget:
                    return out
            except Exception:
                pass
        pts = re.findall(r"(?:Therefore|Key insight|Status|Decision|Supported|Contradicted)[^.\n]*", raw)
        if pts:
            s = "; ".join(p.strip() for p in pts[:5])
            if len(s) <= budget:
                return s
        words = clean(raw).split()
        out: list[str] = []
        for w in reversed(words):
            if est_tokens(" ".join([w] + out)) > self.config.carryoverTokens:
                break
            out.insert(0, w)
        return " ".join(out)

    # -- prompts (AGENTS.md 4.4) ---------------------------------------------------
    def _prompt(self, goal: str, step: int, carry: str, step_input: str, overlap: str, memory: str) -> str:
        rules = (f"End with {STATE} followed by the state block (COMPLETED / REMAINING / DECISIONS / CONTEXT) if work remains, "
                 f"or {FINAL} when the whole goal is done. Exactly one marker; never narrate it.")
        if step == 1:
            head = (memory + "\n\nUser Request: " + goal) if memory else goal
            body = f"{head}\n\n{step_input}".strip()
        else:
            g = goal if len(goal) <= 200 else goal[:197] + "..."
            body = f"Goal (summary): {g}\n\nCarried state:\n{carry}"
            if overlap:
                body += f"\n\n[OVERLAP]\n{overlap}\n[/OVERLAP]"
            if step_input:
                body += f"\n\n{step_input}"
            body += "\n\nContinue from the carried state only."
        return body + "\n\n" + rules

    # -- run (AGENTS.md 4.5) ---------------------------------------------------------
    def run(self, goal: str, generator: Callable[[str], Any], reflect: bool = True, memoryContext: str = "",
            inputs: list[str] | None = None, mode: str = "RESEARCH") -> dict[str, Any]:
        t0 = self.clock()
        chunks: list[dict[str, Any]] = []
        carry, prev_clean, status, reflection = "", "", "max-chunks", None
        try:
            for step in range(1, self.config.maxChunks + 1):
                step_input = inputs[step - 1] if inputs and step - 1 < len(inputs) else ""
                overlap = prev_clean[-self.config.overlapTokens * 4:] if self.config.overlapTokens and prev_clean else ""
                prompt = self._prompt(goal, step, carry, step_input, overlap, memoryContext if step == 1 else "")
                out = generator(prompt)
                raw = out.get("text", "") if isinstance(out, dict) else str(out or "")
                shown = clean(raw)
                chunks.append({"id": step, "tokens": est_tokens(shown), "carryover": carry, "content": shown,
                               "promptTokens": est_tokens(prompt)})
                prev_clean = shown
                if _find(raw, FINAL_ALIASES) >= 0:
                    status = "completed"
                    break
                carry = self._carryover(raw, carry)
        except Exception as e:  # provider failure mid-chain
            status = "errored"
            reflection = {"error": f"{type(e).__name__}: {e}"}
        if status == "completed" and reflect and chunks:
            try:  # AGENTS.md 7: reflection failure never fails the run
                r = generator(f"Reflect on the completed work for: {goal[:200]}\n\nFinal state:\n{carry}\n\nOne short paragraph.")
                reflection = {"text": clean(r.get("text", "") if isinstance(r, dict) else str(r))}
            except Exception as e:
                reflection = {"error": f"{type(e).__name__}: {e}"}
        eff = run_efficiency(chunks, self.config)
        return {
            "id": str(uuid.uuid4()), "timestamp": t0, "prompt": goal[:500], "mode": mode,
            "chunkConfig": asdict(self.config), "chunks": chunks, "status": status,
            "output": "\n\n".join(c["content"] for c in chunks), "reflection": reflection,
            "totalTokensUsed": eff["totalTokensUsed"], "totalTokensSaved": eff["tokensSaved"],
            "efficiencyPercent": round(eff["efficiencyPercent"], 2), "durationMs": self.clock() - t0,
            "tokenLabel": "estimated", "maxPromptTokens": max((c["promptTokens"] for c in chunks), default=0),
        }


def review_hypotheses(llm: Any, trace: Any, per_round: int = 2, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Hypothesis rounds as a Markovian chain: each round reviews the next `per_round` tested hypotheses
    (with only the findings they cite) plus the carried state. Returns the run record; `output` is the
    concatenated round notes (cite-only prose meant for validate_narrative)."""
    hyps = list(trace.hypotheses)
    fmap = {f.id: f for f in trace.findings}
    rounds = []
    for i in range(0, len(hyps), per_round):
        lines = []
        for h in hyps[i:i + per_round]:
            ev = [e for e in (h.evidence or h.counter) if e in fmap]
            lines.append(f"{h.id} {h.verdict.upper()}: {h.statement} - {h.detail}")
            lines += [f"  [{e}] {fmap[e].statement}" for e in ev]
        last = i + per_round >= len(hyps)
        lines.append(f"This is the final round: finish with {FINAL}." if last else f"More hypotheses follow: end with {STATE}.")
        rounds.append("Hypotheses for this round:\n" + "\n".join(lines))
    goal = (f"Explain '{trace.question}' from tested hypotheses, one round at a time. Write 1-2 sentences per hypothesis "
            "in this round, each ending with citations like [F3]; use only numbers that appear in the cited findings.")
    m = Markovian(config or {"chunkSize": 4000, "maxChunks": max(1, min(32, len(rounds))), "carryoverTokens": 384})

    def gen(prompt: str) -> str:
        return llm.chat("You are a careful analyst. Follow the marker rules exactly.", prompt, temperature=0.1, max_tokens=600)

    return m.run(goal, gen, reflect=False, inputs=rounds)
