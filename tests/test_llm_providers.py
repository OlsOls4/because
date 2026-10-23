"""Provider selection: NIM alias, Meterless Runtime contract (no network)."""

import httpx
import pytest

import because.llm as L


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("BECAUSE_LLM_PROVIDER", "NVIDIA_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "METERLESS_TOKEN",
              "METERLESS_BASE_URL", "METERLESS_STATE_DIR", "METERLESS_MODEL", "NIM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BECAUSE_METERLESS_AUTODETECT", "0")


@pytest.mark.parametrize("raw", ["127.0.0.1:8088", "http://127.0.0.1:8088/v1", "http://127.0.0.1:8088/v1/chat/completions",
                                 "http://127.0.0.1:8088/v1/models", "http://127.0.0.1:8088/healthz", "http://127.0.0.1:8088/v1/v1/"])
def test_meterless_url_normalised(raw):
    assert L.normalize_meterless_url(raw) == "http://127.0.0.1:8088/v1"


def test_nvidia_alias_and_default_model(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    llm = L.get_llm("nvidia")
    assert llm.provider == "nim" and llm.base == "https://integrate.api.nvidia.com/v1" and "llama-3.3" not in llm.model


def test_meterless_token_from_state_dir_and_placeholder_rejected(tmp_path, monkeypatch):
    (tmp_path / "proxy-token").write_text("tok-123\n")
    monkeypatch.setenv("METERLESS_STATE_DIR", str(tmp_path))
    assert L.meterless_token() == "tok-123"
    monkeypatch.setenv("METERLESS_TOKEN", "rt-local")
    assert L.meterless_token() is None


def test_meterless_not_in_auto_unless_configured(monkeypatch):
    assert L.get_llm("auto") is None
    monkeypatch.setenv("METERLESS_TOKEN", "t")
    llm = L.get_llm("auto")
    assert llm.provider == "meterless" and llm.model == "meterless/auto" and llm.timeout == 180.0
    assert llm.extra_headers == {"meterless-app": "because", "meterless-scope": "user"}


def test_meterless_chat_sends_contract_headers(monkeypatch):
    monkeypatch.setenv("METERLESS_TOKEN", "t")
    monkeypatch.setenv("METERLESS_BASE_URL", "localhost:8090/v1/models")
    seen = {}

    def post(url, json, timeout, headers):
        seen.update(url=url, headers=headers, timeout=timeout, model=json["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "<think>x</think>hello"}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(L.httpx, "post", post)
    out = L.get_llm("meterless").chat("s", "u")
    assert out == "hello" and seen["url"] == "http://localhost:8090/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer t" and seen["headers"]["meterless-app"] == "because"
    assert seen["headers"]["meterless-scope"] == "user" and seen["timeout"] == 180.0


@pytest.mark.parametrize("code,body,want", [(401, {}, "unauthorized"), (403, {}, "unauthorized"),
                                            (200, {"data": [{"id": "meterless/auto"}]}, "online"),
                                            (200, {"data": [{"id": "gpt-4"}]}, "offline"), (500, {}, "offline")])
def test_meterless_status_rule(monkeypatch, code, body, want):
    monkeypatch.setattr(L.httpx, "get", lambda url, headers, timeout: httpx.Response(code, json=body, request=httpx.Request("GET", url)))
    assert L.meterless_status("127.0.0.1:8088") == want
