"""Provider-agnostic LLM client (free tiers or local, all OpenAI-compatible).

    BECAUSE_LLM_PROVIDER = auto | nim (alias nvidia) | groq | gemini | meterless | none
    NVIDIA_API_KEY  -> NVIDIA NIM   https://integrate.api.nvidia.com/v1   (build.nvidia.com)
    GROQ_API_KEY    -> Groq         https://api.groq.com/openai/v1
    GEMINI_API_KEY  -> Gemini       https://generativelanguage.googleapis.com/v1beta/openai
    METERLESS_*    -> Meterless Runtime (Nightshift AI hotspot), local, zero marginal cost

`auto` picks the first provider with a key, NVIDIA NIM first; a Meterless hotspot is used
in `auto` only when it is configured (METERLESS_BASE_URL or a token). With no provider the
agent still works: every answer is built deterministically from the evidence, and the LLM
only ever rewrites that evidence into prose (and is validated).

Meterless (https://github.com/Meterless/Meterless, docs/agent-ready/add-meterless-provider.md,
Meterless Runtime 0.90.0 contract):
    METERLESS_BASE_URL   default http://127.0.0.1:8088/v1; any pasted form (no scheme, /v1,
                          /v1/chat/completions, /v1/models, /healthz) is normalised to <host>/v1
    METERLESS_TOKEN      bearer token; else read from $METERLESS_STATE_DIR/proxy-token.
                          The placeholder `rt-local` is rejected by the runtime, so it is never sent.
    METERLESS_MODEL      a routing mode, default meterless/auto (fast, deep, cheap, local, fusion,
                          meterless/<class>[.<disposition>])
    Every request carries `meterless-app: because`; chat also carries `meterless-scope: user`.
    Timeout 180 s (a cold model load can take minutes). `meterless_status()` implements the
    online / unauthorized / offline rule from GET /v1/models; `meterless_autodetect()` tries
    ports 8088, 8090, 8787, 11434 on 127.0.0.1, localhost and [::1].
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

PROVIDERS: dict[str, dict[str, str]] = {
    # meta/llama-3.3-70b-instruct reached end of life on build.nvidia.com on 2026-08-26 (HTTP 410);
    # Nemotron 3.5 Lightning is the free hosted NIM default now. Override with NIM_MODEL.
    "nim": {"base": "https://integrate.api.nvidia.com/v1", "key": "NVIDIA_API_KEY",
            "model": "nvidia/nemotron-3.5-lightning-30b-a3b", "model_env": "NIM_MODEL"},
    "groq": {"base": "https://api.groq.com/openai/v1", "key": "GROQ_API_KEY", "model": "llama-3.3-70b-versatile", "model_env": "GROQ_MODEL"},
    "gemini": {"base": "https://generativelanguage.googleapis.com/v1beta/openai", "key": "GEMINI_API_KEY", "model": "gemini-2.5-flash", "model_env": "GEMINI_MODEL"},
    "meterless": {"base": "http://127.0.0.1:8088/v1", "key": "METERLESS_TOKEN", "model": "meterless/auto", "model_env": "METERLESS_MODEL"},
}
ALIASES = {"nvidia": "nim", "nvidia-nim": "nim", "nightshift": "meterless", "hotspot": "meterless"}
METERLESS_APP = "because"
METERLESS_PORTS = (8088, 8090, 8787, 11434)
_THINK = re.compile(r"<think>.*?</think>", re.S)


def _env(name: str) -> str | None:
    return os.environ.get(name)


def normalize_meterless_url(url: str) -> str:
    """Any pasted runtime URL -> <scheme>://<host[:port]>/v1 (never /v1/v1)."""
    u = (url or "").strip().rstrip("/")
    if not u:
        return PROVIDERS["meterless"]["base"]
    if "://" not in u:
        u = "http://" + u
    for suffix in ("/v1/chat/completions", "/chat/completions", "/v1/models", "/models", "/healthz", "/v1"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
            break
    u = u.rstrip("/")
    while u.endswith("/v1"):
        u = u[:-3]
    return u + "/v1"


def meterless_token() -> str | None:
    tok = _env("METERLESS_TOKEN")
    if not tok and _env("METERLESS_STATE_DIR"):
        p = Path(_env("METERLESS_STATE_DIR")) / "proxy-token"
        if p.exists():
            tok = p.read_text().strip()
    if tok == "rt-local":  # placeholder the runtime rejects
        return None
    return tok or None


def meterless_status(base: str | None = None, token: str | None = None, timeout: float = 5.0) -> str:
    """online | unauthorized | offline, decided by GET /v1/models (not /healthz)."""
    base = normalize_meterless_url(base or _env("METERLESS_BASE_URL") or "")
    headers = {"meterless-app": METERLESS_APP}
    tok = token if token is not None else meterless_token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        r = httpx.get(f"{base}/models", headers=headers, timeout=timeout)
    except Exception:
        return "offline"
    if r.status_code in (401, 403):
        return "unauthorized"
    if r.status_code == 200:
        try:
            ids = [m.get("id", "") for m in r.json().get("data", [])]
        except Exception:
            ids = []
        return "online" if any(str(i).startswith("meterless/") for i in ids) else "offline"
    return "offline"


def meterless_autodetect(timeout: float = 1.5) -> str | None:
    """First online-or-unauthorized runtime on the documented local ports, else None."""
    for host in ("127.0.0.1", "localhost", "[::1]"):
        for port in METERLESS_PORTS:
            base = f"http://{host}:{port}/v1"
            if meterless_status(base, timeout=timeout) in ("online", "unauthorized"):
                return base
    return None


@dataclass
class LLM:
    provider: str
    model: str
    base: str
    key: str
    extra_headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 90.0

    def chat(self, system: str, user: str, temperature: float = 0.1, max_tokens: int = 900, json_mode: bool = False) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.provider == "nim" and "nemotron" in self.model and os.environ.get("NIM_THINKING", "0") != "1":
            # Nemotron reasoning models otherwise spend the whole budget on hidden reasoning and return no content
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if json_mode and self.provider not in ("nim", "meterless"):
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        last = None
        for attempt in range(3):
            try:
                r = httpx.post(f"{self.base}/chat/completions", json=body, timeout=self.timeout, headers=headers)
                if r.status_code == 429:
                    time.sleep(3 * (attempt + 1))
                    continue
                r.raise_for_status()
                msg = r.json()["choices"][0]["message"]
                return _THINK.sub("", msg.get("content") or "").strip()
            except Exception as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"{self.provider} call failed: {_redact(str(last), self.key)}")

    def chat_json(self, system: str, user: str) -> dict:
        txt = self.chat(system, user, json_mode=True)
        m = re.search(r"\{.*\}", txt, re.S)
        return json.loads(m.group(0)) if m else {}

    def describe(self) -> dict[str, str]:
        d = {"provider": self.provider, "model": self.model}
        if self.provider == "meterless":
            d["base"] = self.base
        return d


def _redact(s: str, key: str) -> str:
    return s.replace(key, "***") if key else s


def _meterless_llm(explicit: bool) -> LLM | None:
    base_env = _env("METERLESS_BASE_URL")
    tok = meterless_token()
    if not explicit and not (base_env or tok):
        return None
    base = normalize_meterless_url(base_env or "")
    if explicit and not base_env and os.environ.get("BECAUSE_METERLESS_AUTODETECT", "1") != "0":
        base = meterless_autodetect() or base
    model = _env("METERLESS_MODEL") or PROVIDERS["meterless"]["model"]
    return LLM("meterless", model, base, tok or "", {"meterless-app": METERLESS_APP, "meterless-scope": "user"}, timeout=180.0)


def get_llm(provider: str | None = None) -> LLM | None:
    choice = (provider or os.environ.get("BECAUSE_LLM_PROVIDER", "auto")).lower()
    choice = ALIASES.get(choice, choice)
    if choice in ("none", "off", ""):
        return None
    order = list(PROVIDERS) if choice == "auto" else [choice]
    for name in order:
        if name == "meterless":
            llm = _meterless_llm(explicit=choice == "meterless")
            if llm:
                return llm
            continue
        cfg = PROVIDERS.get(name)
        if cfg and os.environ.get(cfg["key"]):
            return LLM(name, os.environ.get(cfg["model_env"]) or cfg["model"], cfg["base"], os.environ[cfg["key"]])
    return None
