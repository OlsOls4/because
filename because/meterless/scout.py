"""Scout Intent: sense -> interpret -> guard -> route -> recommend -> signed contract.

A Python implementation of the Meterless Scout Intent spec (Apache-2.0,
https://github.com/Meterless/Meterless/tree/main/engines/scout-intent).
Because runs Scout before every investigation: the question is classified
against a registry of investigation intents, guarded (prompt injection, role,
surface), routed to harness capabilities, and turned into an HMAC-signed
execution contract. The analyst's executor refuses any tool call outside the
contract's capability scope, and the contract is stored in the trace so a
reader can see *why* the agent was allowed to do what it did.

Implementer's choices (the spec leaves these open) are marked `# choice:`.
Verified with the official eval harness via conformance/meterless/scout-adapter.ts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from because.meterless.hmem import cosine, embed

# Canonical defaults (spec section 12)
BAND_HIGH, BAND_MEDIUM, BAND_LOW = 0.80, 0.55, 0.40
WEIGHTS = {"lexical": 0.40, "semantic": 0.35, "contextual": 0.20, "recency": 0.05}
TIE_WINDOW = 0.05
HIGH_RISK_PROCEED = 0.95
SECONDARY_FLOOR = 0.55
STAGE1_BUDGET_MS = 16.0
CONTRACT_TTL_S = 5 * 60
MAX_CLARIFY_OPTIONS = 4

STOP = set("a an the of to for and or in on at by with my me i you your our we is are be it this that these those please can could would will do does".split())

_ulid_lock = threading.Lock()
_last_ulid = [0, 0]


def ulid() -> str:
    """Monotonic, sortable id (ULID-like: ms timestamp + counter + randomness)."""
    with _ulid_lock:
        ms = int(time.time() * 1000)
        if ms == _last_ulid[0]:
            _last_ulid[1] += 1
        else:
            _last_ulid[:] = [ms, 0]
        return f"{ms:013x}{_last_ulid[1]:04x}{secrets.token_hex(5)}".upper()


def stem(t: str) -> str:
    for suf in ("ing", "ies", "ed", "es", "s"):
        if len(t) > 4 and t.endswith(suf):
            return t[: -len(suf)] + ("y" if suf == "ies" else "")
    return t


def toks(text: str) -> list[str]:
    return [stem(t) for t in re.findall(r"[a-z0-9$][a-z0-9\-$.%']*", text.lower()) if t not in STOP]


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


class IntentRegistry:
    def __init__(self, source: str | Path | dict | list) -> None:
        if isinstance(source, (str, Path)):
            p = Path(source)
            if p.is_dir():
                intents = [json.loads(f.read_text()) for f in sorted(p.glob("*.json"))]
                data = {"version": 1, "intents": intents}
            else:
                data = json.loads(p.read_text())
        elif isinstance(source, list):  # composition of registries
            data = {"version": 1, "intents": []}
            for s in source:
                data["intents"] += IntentRegistry(s).intents
        else:
            data = source
        self.version = int(data.get("registryVersion", data.get("version", 1)))
        self.intents: list[dict[str, Any]] = []
        for it in data["intents"]:
            if not it.get("examples"):
                raise ValueError(f"intent {it.get('id')} has no examples; it cannot be classified or evaluated")
            it = dict(it)
            it["_ex_toks"] = [set(toks(e)) for e in it["examples"]]
            it["_ex_emb"] = [embed(e) for e in it["examples"]]
            it["_cat"] = stem(it.get("category", "").lower())
            self.intents.append(it)
        self.by_id = {i["id"]: i for i in self.intents}
        if "GENERAL" not in self.by_id:
            raise ValueError("registry must contain the safe GENERAL fallback intent")


# --------------------------------------------------------------------------
# Guard
# --------------------------------------------------------------------------

INJECTION_PATTERNS: list[tuple[str, str]] = [
    ("instruction-override", r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|the user|your)\b[^.\n]{0,30}\b(instruction|rule|prompt|polic|direction|user)"),
    ("instruction-override", r"\b(ignore|disregard)\s+(previous|prior|above|everything)"),
    ("role-drift", r"\byou are now\b|\bunrestricted mode\b|\bdeveloper mode\b|\bjailbreak\b|\bpretend (to be|you are) (the )?(system|admin|developer)"),
    ("role-confusion", r"</?\s*(system|user|assistant)\s*>|\[/?INST\]|<\|im_(start|end)\|>"),
    ("exfiltration", r"\b(reveal|print|dump|show|leak|exfiltrate)\b[^.\n]{0,40}\b(credential|api key|secret|password|token|system prompt)"),
    ("privilege-escalation", r"\bgrant (me |myself )?(admin|root|superuser)|\bdrop (the )?\w+ table\b"),
    ("out-of-band-tool", r"\buse\s+[a-z]+(_[a-z]+)+\b"),
]
CONTROL_CHARS = {"\u202e", "\u202d", "\u200b", "\u200c", "\u200d", "\u2066", "\u2067", "\u2068", "\u2069", "\ufeff"}


def injection_check(prompt: str, allowed_capabilities: set[str] | None = None) -> dict[str, Any]:
    evidence = []
    if any(c in prompt for c in CONTROL_CHARS) or any(unicodedata.category(c) == "Cc" and c not in "\n\t\r" for c in prompt):
        evidence.append("control-bytes")
    norm = unicodedata.normalize("NFKC", prompt)
    for kind, pat in INJECTION_PATTERNS:
        if re.search(pat, norm, re.I):
            evidence.append(kind)
    score = min(1.0, 0.6 * len(evidence)) if evidence else 0.0
    status = "block" if (score >= 0.6 and set(evidence) - {"out-of-band-tool"}) or len(evidence) >= 2 else ("warn" if evidence else "pass")
    # choice: payloads are never logged in full, only kinds + score
    return {"layer": "injection", "status": status, "kind": sorted(set(evidence)), "score": round(score, 2)}


# --------------------------------------------------------------------------
# Scout
# --------------------------------------------------------------------------


@dataclass
class Signer:
    """HMAC-SHA256 signer. Pluggable for stronger schemes."""

    secret: bytes = field(default_factory=lambda: (os.environ.get("SCOUT_SIGNING_SECRET") or secrets.token_hex(32)).encode())

    def sign(self, payload: dict) -> str:
        return hmac.new(self.secret, canonical_json(payload).encode(), hashlib.sha256).hexdigest()

    def verify(self, payload: dict, signature: str) -> bool:
        return hmac.compare_digest(self.sign(payload), signature or "")


class ContractRefused(PermissionError):
    pass


class Scout:
    def __init__(self, options: dict | None = None, *, intentRegistry: Any = None, policyPack: Any = "default",  # noqa: N803
                 modelProfiles: list[dict] | None = None, signer: Signer | None = None,
                 capability_graph: dict[str, list[dict]] | None = None) -> None:
        options = options or {}
        reg = options.get("intentRegistry", intentRegistry)
        self.registry = reg if isinstance(reg, IntentRegistry) else IntentRegistry(reg)
        pp = options.get("policyPack", policyPack)
        self.policy_pack = pp if isinstance(pp, list) else [pp]
        self.policy_pack_version = "+".join(self.policy_pack) + "@1"
        self.model_profiles = options.get("modelProfiles", modelProfiles) or DEFAULT_MODEL_PROFILES
        self.signer = signer or Signer()
        self.graph = capability_graph or {}
        self.telemetry: list[dict[str, Any]] = []
        self.recent: list[str] = []  # in-session intent history for the recency term
        self.contracts: dict[str, dict] = {}
        self._rate: dict[tuple, list[float]] = {}
        self._self_test()

    # -- telemetry -----------------------------------------------------------
    def emit(self, event: str, **kw: Any) -> None:
        self.telemetry.append({"event": event, "ts": time.time(), **kw})

    def _self_test(self) -> None:
        probes = ["hello there", "why did revenue fall", "summarize the account", "draft an email"] * 5
        t0 = time.perf_counter()
        for p in probes:
            self._stage1(p)
        per = (time.perf_counter() - t0) * 1000 / len(probes)
        if per > STAGE1_BUDGET_MS:
            raise RuntimeError(f"Scout stage-1 self-test failed: {per:.1f} ms > {STAGE1_BUDGET_MS} ms budget")
        self.stage1_ms = round(per, 3)

    # -- Sense ---------------------------------------------------------------
    def _stage1(self, prompt: str) -> dict[str, float]:
        q = set(toks(prompt))
        out = {}
        for it in self.registry.intents:
            best = 0.0
            for ex in it["_ex_toks"]:
                if not ex or not q:
                    continue
                inter = len(q & ex)
                best = max(best, inter / ((len(q) * len(ex)) ** 0.5))
            if it["_cat"] and it["_cat"] in q:  # choice: category word is a trigger
                best = min(1.0, best + 0.15)
            for trig in it.get("triggers", []):
                if trig.lower() in prompt.lower():
                    best = min(1.0, best + 0.2)
            out[it["id"]] = best
        return out

    def classify(self, input: dict) -> dict[str, Any]:  # noqa: A002
        prompt = input["prompt"]
        lex = self._stage1(prompt)
        qemb = embed(prompt)
        ctx = input.get("context") or {}
        cands = []
        for it in self.registry.intents:
            sem = max((max(0.0, cosine(qemb, e)) for e in it["_ex_emb"]), default=0.0)  # stage 2 (local)
            contextual = float((ctx.get("intentPriors") or {}).get(it["id"], 0.0)) if ctx else 0.0
            recency = 1.0 if it["id"] in self.recent[-3:] else 0.0
            # choice: normalise by the weights of terms that are actually available, so a
            # stage-1+local-stage-2 run lands in the canonical bands without a context feed.
            avail = WEIGHTS["lexical"] + WEIGHTS["semantic"] + (WEIGHTS["contextual"] if ctx.get("intentPriors") else 0)
            raw = WEIGHTS["lexical"] * lex[it["id"]] + WEIGHTS["semantic"] * sem + WEIGHTS["contextual"] * contextual
            score = raw / avail + WEIGHTS["recency"] * recency
            score -= self._risk_penalty(it, input)
            cands.append({"intent": it["id"], "score": round(max(0.0, min(1.0, score)), 4),
                          "parts": {"lexical": round(lex[it["id"]], 3), "semantic": round(sem, 3), "contextual": contextual, "recency": recency}})
        cands.sort(key=lambda c: -c["score"])
        return {"candidates": cands}

    def _risk_penalty(self, it: dict, input: dict) -> float:
        user = input.get("user") or {}
        roles = it.get("roles", ["*"])
        if "*" not in roles and user.get("role") and user["role"] not in roles:
            return 0.25  # down-rank intents this role may not invoke
        return 0.0

    @staticmethod
    def band(score: float) -> str:
        if score >= BAND_HIGH:
            return "high"
        if score >= BAND_MEDIUM:
            return "medium"
        if score >= BAND_LOW:
            return "low"
        return "reject"

    # -- Interpret -------------------------------------------------------------
    def interpret(self, intent: dict, prompt: str, binder: Any = None) -> tuple[dict, dict, list[str]]:
        params: dict[str, Any] = {}
        if binder:
            params = binder(intent, prompt) or {}
        else:
            m = re.search(r"\b(\d{1,3})\b", prompt)
            props = (intent.get("parameters") or {}).get("properties", {})
            if m and "count" in props:
                params["count"] = int(m.group(1))
            for name, spec in props.items():
                for opt in spec.get("enum", []) if isinstance(spec, dict) else []:
                    if re.search(rf"\b{re.escape(str(opt))}", prompt, re.I):
                        params[name] = opt
        required = (intent.get("parameters") or {}).get("required", [])
        missing = [r for r in required if params.get(r) in (None, "", [])]
        refs = {k: v for k, v in params.items() if isinstance(v, str)}
        return params, refs, missing

    # -- Guard -----------------------------------------------------------------
    def guard(self, intent: dict, input: dict, params: dict) -> dict[str, Any]:
        layers = []
        allowed = set(intent.get("capabilities", []))
        layers.append(injection_check(input["prompt"], allowed))
        user = input.get("user") or {}
        roles = intent.get("roles", ["*"])
        surf = input.get("surface", "chat")
        pol = "pass"
        reason = None
        if "*" not in roles and user.get("role") and user["role"] not in roles:
            pol, reason = "warn", f"role '{user['role']}' not in {roles}"
        layers.append({"layer": "policy", "status": pol, "reason": reason})
        scope = "pass"
        if intent.get("surfaces") and surf not in intent["surfaces"]:
            scope, reason = "block", f"surface '{surf}' not allowed for {intent['id']}"
        layers.append({"layer": "scope", "status": scope, "reason": reason if scope != "pass" else None})
        redactions = []
        for label, pat in (("email", r"[\w.+-]+@[\w-]+\.[\w.]+"), ("card", r"\b(?:\d[ -]?){13,19}\b")):
            if re.search(pat, input["prompt"]):
                redactions.append(label)
        layers.append({"layer": "sensitivity", "status": "warn" if redactions else "pass"})
        key = (user.get("id"), intent["id"], surf)
        window = [t for t in self._rate.get(key, []) if time.time() - t < 60]
        window.append(time.time())
        self._rate[key] = window
        layers.append({"layer": "rate", "status": "block" if len(window) > 120 else "pass"})  # choice: 120/min
        statuses = [l["status"] for l in layers]
        floor = {"low": 0, "medium": 1, "high": 2}[intent.get("riskClass", "low")]
        if "block" in statuses:
            level = "block"
        else:
            lvl = floor + statuses.count("warn")
            level = ["low", "medium", "high", "high"][min(lvl, 3)]
        blk = next((l for l in layers if l["status"] == "block"), None)
        flags = sorted({k for l in layers for k in l.get("kind", [])} | {l["layer"] for l in layers if l["status"] != "pass"})
        return {"level": level, "flags": flags, "redactions": redactions, "layers": layers,
                "reason": (blk.get("reason") or f"{blk['layer']}: {','.join(blk.get('kind', []))}") if blk else None}

    # -- Route -----------------------------------------------------------------
    def route(self, intent: dict, user: dict, surface: str) -> list[dict]:
        plan = []
        for cap in intent.get("capabilities", []):
            tools = [t for t in self.graph.get(cap, [{"tool": cap, "local": True, "available": True, "cost": 0}])
                     if t.get("available", True) and (not t.get("roles") or user.get("role") in t["roles"])]
            if not tools:
                continue  # degrade: never substitute an out-of-scope tool
            tools.sort(key=lambda t: (t.get("cost", 0), t.get("latencyMs", 0), not t.get("local", True)))
            plan.append({"capability": cap, "tool": tools[0]["tool"], "input": {}})
        return plan

    # -- Recommend ---------------------------------------------------------------
    def pick_model(self, data_class: str = "public", needs: tuple[str, ...] = ()) -> dict:
        ok = [p for p in self.model_profiles if data_class in p.get("dataClasses", ["public"]) and all(n in p.get("capabilities", []) for n in needs)]
        ok = [p for p in ok if p.get("available", True)] or ok
        ok.sort(key=lambda p: (p.get("cost", 0), p.get("latencyP95", 0)))
        return ok[0] if ok else {"id": "none", "provider": "none"}

    def issue(self, *, intent: dict, cands: list[dict], params: dict, refs: dict, risk: dict, plan: list[dict],
              user: dict, surface: str, parent: str | None = None, trace_id: str | None = None) -> dict:
        now = time.time()
        top = cands[0]
        body = {
            "contractId": ulid(),
            "parentId": parent,
            "traceId": trace_id or uuid.uuid4().hex,
            "intent": {"primary": {"id": intent["id"], "version": intent.get("version", 1)}, "parameters": params,
                       "references": refs, "confidence": top["score"], "alternates": cands[1:4]},
            "risk": {"level": risk["level"], "flags": risk["flags"], "redactions": risk["redactions"], "approvals": []},
            "toolPlan": plan,
            "modelProfile": {k: v for k, v in self.pick_model().items() if k in ("id", "provider", "model")},
            "scope": {"user": user.get("id"), "surface": surface, "capabilities": sorted({s["capability"] for s in plan} | set(intent.get("capabilities", [])))},
            "issuedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "expiresAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + CONTRACT_TTL_S)),
            "registryVersion": self.registry.version,
            "policyPackVersion": self.policy_pack_version,
            "signedBy": "scout",
        }
        body["signature"] = self.signer.sign(body)
        self.contracts[body["contractId"]] = body
        self.emit("contract.issued", contractId=body["contractId"], intent=intent["id"], risk=risk["level"], profile=body["modelProfile"].get("id"))
        return body

    def verify(self, contract: dict, engine: str = "executor") -> bool:
        payload = {k: v for k, v in contract.items() if k != "signature"}
        if not self.signer.verify(payload, contract.get("signature", "")):
            self.emit("contract.refused", contractId=contract.get("contractId"), engine=engine, reason="bad-signature")
            return False
        exp = time.mktime(time.strptime(contract["expiresAt"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        if time.time() > exp:
            self.emit("contract.expired", contractId=contract.get("contractId"), engine=engine)
            return False
        self.emit("contract.verified", contractId=contract.get("contractId"), engine=engine)
        return True

    def enforce(self, contract: dict, capability: str, engine: str = "executor") -> None:
        """Executor boundary: refuse unsigned, expired or out-of-scope calls."""
        if not self.verify(contract, engine):
            raise ContractRefused("contract invalid or expired")
        if capability not in contract["scope"]["capabilities"]:
            self.emit("contract.refused", contractId=contract["contractId"], engine=engine, reason="scope-mismatch", capability=capability)
            raise ContractRefused(f"capability '{capability}' is outside the contract scope {contract['scope']['capabilities']}")

    # -- Pipeline --------------------------------------------------------------
    def decide_sync(self, input: dict, binder: Any = None, parent: str | None = None, trace_id: str | None = None) -> dict[str, Any]:  # noqa: A002
        t0 = time.perf_counter()
        user = input.get("user") or {}
        surface = input.get("surface", "chat")
        trace_id = trace_id or uuid.uuid4().hex
        inj = injection_check(input["prompt"])
        cands = self.classify(input)["candidates"]
        top = cands[0]
        b = self.band(top["score"])
        intent = self.registry.by_id[top["intent"]] if b != "reject" else self.registry.by_id["GENERAL"]
        params, refs, missing = self.interpret(intent, input["prompt"], binder)
        risk = self.guard(intent, input, params)
        if inj["status"] == "block":
            risk["level"] = "block"
        out: dict[str, Any] = {"traceId": trace_id, "candidates": cands[:5], "band": b, "risk": risk,
                               "intent": intent["id"], "parameters": params}
        clar = None
        if risk["level"] != "block":
            second = cands[1]["score"] if len(cands) > 1 else 0
            trigger = None
            if b == "low":
                trigger = "low-band"
            elif b == "medium" and top["score"] - second < TIE_WINDOW and cands[1]["intent"] != "GENERAL":
                trigger = "tie"
            elif missing:
                trigger = "params"
            elif intent.get("riskClass") == "high" and top["score"] < HIGH_RISK_PROCEED:
                trigger = "high-risk"
            if trigger:
                opts = [c["intent"] for c in cands[:MAX_CLARIFY_OPTIONS] if c["intent"] != "GENERAL"]
                clar = {"trigger": trigger, "question": self._question(trigger, missing, opts), "options": opts, "missing": missing}
                self.emit("scout.clarification", traceId=trace_id, trigger=trigger)
        out["clarification"] = clar
        # multi-intent: clauses that each land in the High band on different intents
        out["plan"] = self._multi(input) if risk["level"] != "block" else []
        if risk["level"] != "block" and not clar:
            plan = self.route(intent, user, surface)
            out["toolPlan"] = plan
            out["executionContract"] = self.issue(intent=intent, cands=cands, params=params, refs=refs, risk=risk, plan=plan,
                                                  user=user, surface=surface, parent=parent, trace_id=trace_id)
            self.recent.append(intent["id"])
        else:
            out["toolPlan"] = []
        self.emit("scout.decision", traceId=trace_id, intent=intent["id"], band=b, latencyMs=round((time.perf_counter() - t0) * 1000, 2), stage2="used")
        return out

    async def decide(self, input: dict, **kw: Any) -> dict[str, Any]:  # noqa: A002
        return self.decide_sync(input, **kw)

    def replan(self, input: dict, prompt: str, binder: Any = None) -> dict[str, Any]:
        parent = self.contracts.get(input["parent"])
        if not parent:
            raise KeyError("unknown parent contract")
        self.emit("scout.replan", parent=input["parent"], reason=input.get("reason"))
        return self.decide_sync({"prompt": prompt, "surface": parent["scope"]["surface"], "user": {"id": parent["scope"]["user"]}},
                                binder=binder, parent=parent["contractId"], trace_id=parent["traceId"])

    def _multi(self, input: dict) -> list[dict]:
        parts = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\band then\b|\bthen\b|\band\b", input["prompt"]) if len(p.strip()) > 8]
        if len(parts) < 2:
            return []
        picks = []
        for p in parts:
            c = self.classify({**input, "prompt": p})["candidates"][0]
            if c["score"] >= SECONDARY_FLOOR and c["intent"] != "GENERAL":
                picks.append({"intent": c["intent"], "score": c["score"], "span": p})
        ids = {p["intent"] for p in picks}
        return picks if len(ids) >= 2 and sum(1 for p in picks if p["score"] >= BAND_HIGH) >= 1 else []

    @staticmethod
    def _question(trigger: str, missing: list[str], opts: list[str]) -> str:
        if trigger == "params":
            return f"I need {', '.join(missing)} to proceed. Which do you mean?"
        if trigger == "high-risk":
            return "This is a high-impact action. Please confirm exactly what you want."
        return "Which of these did you mean: " + ", ".join(opts) + "?"


DEFAULT_MODEL_PROFILES = [
    {"id": "deterministic", "provider": "local", "model": "rules", "cost": 0, "latencyP95": 5, "dataClasses": ["public", "sensitive"], "capabilities": ["json"]},
    {"id": "nim-llama-3.3-70b", "provider": "nvidia-nim", "model": "meta/llama-3.3-70b-instruct", "cost": 0, "latencyP95": 4000, "dataClasses": ["public"], "capabilities": ["json", "function_calling"]},
    {"id": "groq-llama-3.3-70b", "provider": "groq", "model": "llama-3.3-70b-versatile", "cost": 0, "latencyP95": 1500, "dataClasses": ["public"], "capabilities": ["json", "function_calling"]},
    {"id": "gemini-2.5-flash", "provider": "gemini", "model": "gemini-2.5-flash", "cost": 0, "latencyP95": 3000, "dataClasses": ["public"], "capabilities": ["json", "function_calling", "vision"]},
]
