"""Route-level tests for /digit/projects, /digit/shots, /digit/browse."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from digit_loader import load_digit_module

load_digit_module("image_saver_node")
load_digit_module("image_loader_node")
projekts_utils = load_digit_module("projekts_utils")


def _run(tmp_path, monkeypatch, coro_factory):
    root = tmp_path / "PROJEKTS"
    (root / "12345_demo" / "shots" / "sh010").mkdir(parents=True)
    (root / "12345_demo" / "shots" / "(no shots found)").mkdir()
    monkeypatch.setenv("DIGIT_PROJEKTS_ROOTS", str(root))

    async def runner():
        app = web.Application()
        from server import PromptServer

        app.add_routes(PromptServer.instance.routes)
        async with TestClient(TestServer(app)) as client:
            return await coro_factory(client, str(root))

    return asyncio.run(runner())


def test_shots_lists_real_folders_not_junk(tmp_path, monkeypatch):
    async def body(client, root):
        resp = await client.get("/digit/shots", params={"root": root, "project": "12345_demo"})
        return resp.status, await resp.json()

    status, payload = _run(tmp_path, monkeypatch, body)
    assert status == 200
    assert payload == ["sh010"]


def test_shots_empty_project_is_400(tmp_path, monkeypatch):
    async def body(client, root):
        resp = await client.get("/digit/shots", params={"root": root, "project": ""})
        return resp.status, await resp.json()

    status, payload = _run(tmp_path, monkeypatch, body)
    assert status == 400
    assert payload == ["(no shots found)"]


def test_shots_outside_root_is_403(tmp_path, monkeypatch):
    async def body(client, _root):
        resp = await client.get("/digit/shots", params={"root": "/etc", "project": "12345_demo"})
        return resp.status

    status = _run(tmp_path, monkeypatch, body)
    assert status == 403


def test_shots_storage_error_is_503(tmp_path, monkeypatch):
    async def body(client, root):
        def boom(_path):
            raise OSError(107, "Transport endpoint is not connected")

        with patch.object(projekts_utils.os, "listdir", side_effect=boom):
            with patch.object(projekts_utils.time, "sleep"):
                resp = await client.get(
                    "/digit/shots", params={"root": root, "project": "12345_demo"}
                )
                return resp.status, await resp.json()

    status, payload = _run(tmp_path, monkeypatch, body)
    assert status == 503
    assert payload == ["(storage unavailable)"]


def test_browse_forbidden_outside_roots(tmp_path, monkeypatch):
    async def body(client, _root):
        resp = await client.get("/digit/browse", params={"path": "/etc"})
        return resp.status

    status = _run(tmp_path, monkeypatch, body)
    assert status == 403
