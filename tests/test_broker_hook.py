"""Unit tests for the Digit broker execution hook (DIGIT-86)."""

from __future__ import annotations

import json
import sys
import types
from io import BytesIO
from urllib.error import HTTPError

import pytest
from digit_loader import load_digit_module

broker_hook = load_digit_module("broker_hook")


@pytest.fixture(autouse=True)
def _reset_hook_state(monkeypatch):
    broker_hook._reset_runtime_state_for_tests()
    monkeypatch.delenv("DIGIT_BROKER_URL", raising=False)
    monkeypatch.delenv("RENDER_HOOK_SECRET", raising=False)
    yield
    broker_hook._reset_runtime_state_for_tests()


def _install_fake_prompt_server(monkeypatch, calls=None):
    calls = calls if calls is not None else []

    class FakePromptServer:
        instance = types.SimpleNamespace(address="0.0.0.0", port=8199)

        def send_sync(self, event, data, sid=None):
            calls.append((event, data, sid))
            return "ok"

    server_mod = types.ModuleType("server")
    server_mod.PromptServer = FakePromptServer
    monkeypatch.setitem(sys.modules, "server", server_mod)
    return FakePromptServer, calls


def test_init_skips_when_url_missing(monkeypatch):
    monkeypatch.setenv("RENDER_HOOK_SECRET", "secret")
    FakePromptServer, _ = _install_fake_prompt_server(monkeypatch)
    original = FakePromptServer.send_sync

    broker_hook.init_broker_hook()

    assert FakePromptServer.send_sync is original
    assert broker_hook._hook_installed is False


def test_init_skips_when_secret_missing(monkeypatch):
    monkeypatch.setenv("DIGIT_BROKER_URL", "http://broker.example/api/renders/log-execution")
    FakePromptServer, _ = _install_fake_prompt_server(monkeypatch)
    original = FakePromptServer.send_sync

    broker_hook.init_broker_hook()

    assert FakePromptServer.send_sync is original
    assert broker_hook._hook_installed is False


def test_init_patches_once_when_fully_configured(monkeypatch):
    monkeypatch.setenv("DIGIT_BROKER_URL", "http://broker.example/api/renders/log-execution")
    monkeypatch.setenv("RENDER_HOOK_SECRET", "secret")
    FakePromptServer, calls = _install_fake_prompt_server(monkeypatch)

    broker_hook.init_broker_hook()
    first = FakePromptServer.send_sync
    broker_hook.init_broker_hook()

    assert first is FakePromptServer.send_sync
    assert getattr(FakePromptServer.send_sync, "_digit_broker_hook", False) is True

    ps = FakePromptServer()
    assert ps.send_sync("status", {"ok": True}) == "ok"
    assert calls == [("status", {"ok": True}, None)]


def test_fetch_skips_without_config(monkeypatch):
    posted = []

    def boom(*_a, **_k):
        posted.append(True)
        raise AssertionError("should not post")

    monkeypatch.setattr(broker_hook.urllib.request, "urlopen", boom)
    broker_hook.fetch_and_post_history("prompt-1")
    assert posted == []


def test_fetch_posts_with_bearer_and_records_success(monkeypatch):
    monkeypatch.setenv("DIGIT_BROKER_URL", "http://broker.example/api/renders/log-execution")
    monkeypatch.setenv("RENDER_HOOK_SECRET", "top-secret")
    _install_fake_prompt_server(monkeypatch)

    history = {
        "prompt-1": {
            "prompt": [],
            "outputs": {},
        }
    }
    requests = []

    class FakeResponse:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def fake_urlopen(req, timeout=5):
        body_bytes = req.data if hasattr(req, "data") else None
        requests.append(
            (req.full_url, req.get_method(), dict(req.header_items()), body_bytes)
        )
        if req.full_url.endswith("/history/prompt-1"):
            return FakeResponse(json.dumps(history).encode("utf-8"))
        return FakeResponse(b'{"ok":true}')

    monkeypatch.setattr(broker_hook.urllib.request, "urlopen", fake_urlopen)
    broker_hook.fetch_and_post_history("prompt-1")

    assert len(requests) == 2
    assert requests[0][0] == "http://127.0.0.1:8199/history/prompt-1"
    assert requests[1][0] == "http://broker.example/api/renders/log-execution"
    assert requests[1][1] == "POST"
    auth = requests[1][2].get("Authorization") or requests[1][2].get("authorization")
    assert auth == "Bearer top-secret"
    assert broker_hook._consecutive_failures == 0
    body = json.loads(requests[1][3].decode("utf-8"))
    assert body["prompt_id"] == "prompt-1"
    assert "priced_nodes" in body
    assert isinstance(body["priced_nodes"], list)


def test_circuit_opens_after_repeated_http_401(monkeypatch):
    monkeypatch.setenv("DIGIT_BROKER_URL", "http://broker.example/api/renders/log-execution")
    monkeypatch.setenv("RENDER_HOOK_SECRET", "wrong")
    _install_fake_prompt_server(monkeypatch)
    monkeypatch.setattr(broker_hook, "_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(broker_hook, "_CIRCUIT_COOLDOWN_SEC", 60)

    history = {"prompt-1": {"prompt": [], "outputs": {}}}

    class FakeResponse:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def fake_urlopen(req, timeout=5):
        if "/history/" in req.full_url:
            return FakeResponse(json.dumps(history).encode("utf-8"))
        raise HTTPError(
            req.full_url, 401, "Unauthorized", hdrs=None, fp=BytesIO(b"nope")
        )

    monkeypatch.setattr(broker_hook.urllib.request, "urlopen", fake_urlopen)

    broker_hook.fetch_and_post_history("prompt-1")
    assert broker_hook._circuit_allows() is True

    broker_hook.fetch_and_post_history("prompt-1")
    assert broker_hook._circuit_allows() is False

    # While open, further attempts must not touch the network.
    hits = []

    def no_network(*_a, **_k):
        hits.append(True)
        raise AssertionError("circuit should block network")

    monkeypatch.setattr(broker_hook.urllib.request, "urlopen", no_network)
    broker_hook.fetch_and_post_history("prompt-1")
    assert hits == []
