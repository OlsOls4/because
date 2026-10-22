"""Provider-agnostic LLM client (all free tiers, all OpenAI-compatible).

    BECAUSE_LLM_PROVIDER = auto | nim | groq | gemini | none
    NVIDIA_API_KEY  -> NVIDIA NIM   https://integrate.api.nvidia.com/v1   (build.nvidia.com)
    GROQ_API_KEY    -> Groq         https://api.groq.com/openai/v1
    GEMINI_API_KEY  -> Gemini       https://generativelanguage.googleapis.com/v1beta/openai

`auto` picks the first provider with a key, NVIDIA NIM first. With no key the
agent still works: every answer is built deterministically from the evidence,
and the LLM only ever rewrites that evidence into prose (and is validated).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

PROVIDERS: dict[str, dict[str, str]] = {
    "nim": {"base": "https://integrate.api.nvidia.com/v1", "key": "NVIDIA_API_KEY", "model": "meta/llama-3.3-70b-instruct", "model_env": "NIM_MODEL"},
    "groq": {"base": "https://api.groq.com/openai/v1", "key": "GROQ_API_KEY", "model": "llama-3.3-70b-versatile", "model_env": "GROQ_MODEL"},
    "gemini": {"base": "https://generativelanguage.googleapis.com/v1beta/openai", "key": "GEMINI_API_KEY", "model": "gemini-2.5-flash", "model_env": "GEMINI_MODEL"},
}


@dataclass
class LLM:
    provider: str
    model: str
    base: str
    key: str

    def chat(self, system: str, user: str, temperature: float = 0.1, max_tokens: int = 900, json_mode: bool = False) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and self.provider != "nim":
            body["response_format"] = {"type": "json_object"}
        last = None
        for attempt in range(3):
            try:
                r = httpx.post(f"{self.base}/chat/completions", json=body, timeout=90,
                               headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
                if r.status_code == 429:
                    time.sleep(3 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"] or ""
            except Exception as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"{self.provider} call failed: {last}")

    def chat_json(self, system: str, user: str) -> dict:
        txt = self.chat(system, user, json_mode=True)
        m = re.search(r"\{.*\}", txt, re.S)
        return json.loads(m.group(0)) if m else {}

    def describe(self) -> dict[str, str]:
        return {"provider": self.provider, "model": self.model}


def get_llm(provider: str | None = None) -> LLM | None:
    choice = (provider or os.environ.get("BECAUSE_LLM_PROVIDER", "auto")).lower()
    if choice in ("none", "off", ""):
        return None
    order = list(PROVIDERS) if choice == "auto" else [choice]
    for name in order:
        cfg = PROVIDERS.get(name)
        if cfg and os.environ.get(cfg["key"]):
            return LLM(name, os.environ.get(cfg["model_env"]) or cfg["model"], cfg["base"], os.environ[cfg["key"]])
    return None
