"""Meterless Markovian conformance: the 8 checks of engines/markovian/conformance/runner.ts, ported."""

import pytest

from because.meterless.markovian import ConfigError, Markovian, model_totals, review_hypotheses, run_efficiency

T0 = 1_750_000_000_000


def step_gen(total):
    calls = {"n": 0}

    def fn(prompt):
        calls["n"] += 1
        n = calls["n"]
        body = f"Output for step {n} with enough text to register in the accounting math. " * 10
        marker = "[TASK_COMPLETE]" if n >= total else f"[STATE_CHECKPOINT]\nCOMPLETED: steps 1-{n}\nREMAINING: {total - n}\nDECISIONS: none\nCONTEXT: conformance"
        return {"text": body + "\n" + marker}

    return fn


def test_eff_001_worked_example():
    t = model_totals(20)
    assert t["naiveTotal"] == 246_000 and t["markovianTotal"] == 34_000
    assert abs(t["efficiency"] * 100 - 86.18) < 0.5


def test_snap_001_run_carries_config_snapshot():
    run = Markovian({"chunkSize": 4000, "carryoverTokens": 640}, clock=lambda: T0).run("snapshot check", step_gen(3), reflect=False)
    assert run["chunkConfig"]["carryoverTokens"] == 640


def test_snap_002_efficiency_against_own_snapshot():
    chunks = [{"id": i, "tokens": 1000, "carryover": "", "content": ""} for i in range(1, 5)]
    a = run_efficiency(chunks, {"chunkSize": 8000, "maxChunks": 24, "carryoverTokens": 512, "overlapTokens": 0})
    b = run_efficiency(chunks, {"chunkSize": 8000, "maxChunks": 24, "carryoverTokens": 2048, "overlapTokens": 0})
    assert a["standardCost"] == 10_000 and a["markovianCost"] == 6_048
    assert a["tokensSaved"] != b["tokensSaved"]


def test_ovl_001_overlap_is_implemented():
    prompts = []

    def gen(p):
        prompts.append(p)
        done = len(prompts) >= 3
        return {"text": f"chunk {len(prompts)} content " * 20 + ("[TASK_COMPLETE]" if done else "[STATE_CHECKPOINT]\nCOMPLETED: chunk recorded with carried state")}

    Markovian({"chunkSize": 4000, "carryoverTokens": 640, "overlapTokens": 64}, clock=lambda: T0).run("overlap conformance", gen, reflect=False)
    assert any("[OVERLAP]" in p for p in prompts[1:])


def test_lab_001_token_label():
    run = Markovian({"chunkSize": 4000, "carryoverTokens": 640}, clock=lambda: T0).run("label check", step_gen(2), reflect=False)
    assert run["tokenLabel"] in ("estimated", "measured")


def test_ref_001_reflection_failure_does_not_fail_run():
    calls = {"n": 0}

    def gen(p):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("provider exploded in reflection")
        return {"text": "done in one chunk [TASK_COMPLETE]"}

    run = Markovian({"chunkSize": 4000, "carryoverTokens": 640}, clock=lambda: T0).run("reflection failure", gen)
    assert run["status"] == "completed"


def test_mrk_001_markers_cleaned_and_drive_completion():
    run = Markovian({"chunkSize": 4000, "carryoverTokens": 640}, clock=lambda: T0).run("marker cleaning", step_gen(3), reflect=False)
    assert run["status"] == "completed" and len(run["chunks"]) == 3
    assert "STATE_CHECKPOINT" not in run["output"] and "TASK_COMPLETE" not in run["output"]


def test_cfg_001_out_of_range_rejected():
    with pytest.raises(ConfigError):
        Markovian({"chunkSize": 128, "carryoverTokens": 640}, clock=lambda: T0)


# -- Because usage: hypothesis rounds stay bounded ---------------------------------------------

class _H:
    def __init__(self, i):
        self.id, self.verdict, self.statement, self.detail = f"H{i}", "supported", f"driver {i} moved", f"detail {i}"
        self.evidence, self.counter = [f"F{i}"], []


class _F:
    def __init__(self, i):
        self.id, self.statement = f"F{i}", f"finding {i}: value {i}.0 " + "x" * 400


class _T:
    question = "why?"
    hypotheses = [_H(i) for i in range(1, 9)]
    findings = [_F(i) for i in range(1, 9)]


class _FakeLLM:
    def __init__(self):
        self.prompts = []

    def chat(self, system, user, **kw):
        self.prompts.append(user)
        ids = sorted(set(__import__("re").findall(r"\[(F\d+)\]", user.split("Hypotheses for this round:")[-1])))
        tail = "[TASK_COMPLETE]" if "final round" in user else "[STATE_CHECKPOINT]\nCOMPLETED: reviewed " + ", ".join(ids) + "\nREMAINING: more\nDECISIONS: none\nCONTEXT: none"
        return " ".join(f"Driver moved [{i}]." for i in ids) + "\n" + tail


def test_hypothesis_rounds_have_flat_prompt_size():
    llm = _FakeLLM()
    run = review_hypotheses(llm, _T(), per_round=2)
    assert run["status"] == "completed" and len(run["chunks"]) == 4
    sizes = [len(p) for p in llm.prompts]
    assert max(sizes) - min(sizes) < 600  # O(1) per round: no growing transcript
    assert "[F1]" not in llm.prompts[-1]  # earlier rounds' evidence is not re-sent
    assert "[F7]" in run["output"] and "TASK_COMPLETE" not in run["output"]
