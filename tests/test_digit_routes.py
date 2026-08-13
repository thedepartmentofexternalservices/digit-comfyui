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


def test_health_reports_reachable_root(tmp_path, monkeypatch):
    async def body(client, root):
        resp = await client.get("/digit/health")
        return resp.status, await resp.json()

    status, payload = _run(tmp_path, monkeypatch, body)
    assert status == 200
    assert payload["ok"] is True
    assert payload["roots"][0]["path"] == str(tmp_path / "PROJEKTS")
    assert payload["roots"][0]["reachable"] is True
    assert payload["roots"][0]["project_count"] == 1


def test_subfolders_and_tasks_list_children(tmp_path, monkeypatch):
    async def body(client, root):
        shot_dir = tmp_path / "PROJEKTS" / "12345_demo" / "shots" / "sh010"
        (shot_dir / "comfy" / "comp").mkdir(parents=True)
        (shot_dir / "plates").mkdir()
        sub = await client.get(
            "/digit/subfolders",
            params={"root": root, "project": "12345_demo", "shot": "sh010"},
        )
        tasks = await client.get(
            "/digit/tasks",
            params={"root": root, "project": "12345_demo", "shot": "sh010", "subfolder": "comfy"},
        )
        missing = await client.get(
            "/digit/subfolders",
            params={"root": root, "project": "12345_demo", "shot": ""},
        )
        return sub.status, await sub.json(), tasks.status, await tasks.json(), missing.status

    sub_status, subfolders, task_status, tasks, missing_status = _run(tmp_path, monkeypatch, body)
    assert sub_status == 200
    assert subfolders == ["comfy", "plates"]
    assert task_status == 200
    assert tasks == ["comp"]
    assert missing_status == 400


def test_folders_lists_nested_paths(tmp_path, monkeypatch):
    async def body(client, root):
        shot_dir = tmp_path / "PROJEKTS" / "12345_demo" / "shots" / "sh010"
        (shot_dir / "comfy" / "comp").mkdir(parents=True)
        (shot_dir / "plates").mkdir()
        listed = await client.get(
            "/digit/folders",
            params={"root": root, "project": "12345_demo", "shot": "sh010"},
        )
        missing = await client.get(
            "/digit/folders",
            params={"root": root, "project": "12345_demo", "shot": ""},
        )
        return listed.status, await listed.json(), missing.status

    status, folders, missing_status = _run(tmp_path, monkeypatch, body)
    assert status == 200
    assert folders == ["comfy", "comfy/comp", "plates"]
    assert missing_status == 400


def test_output_preview_returns_image_and_video_paths_without_writing(tmp_path, monkeypatch):
    async def body(client, root):
        target = (
            tmp_path / "PROJEKTS" / "12345_demo" / "shots" / "sh010"
            / "comfy" / "comp" / "v001"
        )
        target.mkdir(parents=True)
        (target / "12345_hero_wide.1001.png").write_bytes(b"existing")

        common = {
            "root": root,
            "project": "12345_demo",
            "shot": "sh010",
            "folder": "comfy/comp/v001",
            "filename": "hero_wide",
            "start_frame": "1001",
            "frame_pad": "4",
        }
        image = await client.get(
            "/digit/output_preview",
            params={**common, "saver": "image", "format": "png"},
        )
        video = await client.get(
            "/digit/output_preview",
            params={**common, "saver": "video", "format": "exr"},
        )
        return (
            image.status, await image.json(),
            video.status, await video.json(),
            sorted(path.name for path in target.iterdir()),
        )

    image_status, image, video_status, video, files = _run(tmp_path, monkeypatch, body)
    assert image_status == 200
    assert image["filename"] == "12345_hero_wide.1002.png"
    assert image["frame"] == 1002
    assert video_status == 200
    assert video["filename"] == "12345_hero_wide.1001.mp4"
    assert files == ["12345_hero_wide.1001.png"]


def test_output_preview_rejects_bad_destination_and_format(tmp_path, monkeypatch):
    async def body(client, root):
        traversal = await client.get(
            "/digit/output_preview",
            params={
                "root": root,
                "project": "12345_demo",
                "shot": "sh010",
                "folder": "../outside",
            },
        )
        bad_format = await client.get(
            "/digit/output_preview",
            params={
                "root": root,
                "project": "12345_demo",
                "shot": "sh010",
                "folder": "comfy/comp",
                "format": "gif",
            },
        )
        outside = await client.get(
            "/digit/output_preview",
            params={
                "root": "/etc",
                "project": "12345_demo",
                "shot": "sh010",
                "folder": "comfy/comp",
            },
        )
        return traversal.status, bad_format.status, outside.status

    traversal, bad_format, outside = _run(tmp_path, monkeypatch, body)
    assert traversal == 400
    assert bad_format == 400
    assert outside == 403


def test_create_folder_makes_path_and_lists_it(tmp_path, monkeypatch):
    async def body(client, root):
        resp = await client.post(
            "/digit/create_folder",
            json={
                "root": root,
                "project": "12345_demo",
                "shot": "sh010",
                "folder": "comfy/comp/v001",
            },
        )
        return resp.status, await resp.json()

    status, payload = _run(tmp_path, monkeypatch, body)
    assert status == 200
    assert payload["ok"] is True
    assert payload["folder"] == "comfy/comp/v001"
    assert "comfy/comp/v001" in payload["folders"]
    assert (
        tmp_path / "PROJEKTS" / "12345_demo" / "shots" / "sh010" / "comfy" / "comp" / "v001"
    ).is_dir()


def test_create_shot_makes_folder_and_lists_it(tmp_path, monkeypatch):
    async def body(client, root):
        resp = await client.post(
            "/digit/create_shot",
            json={
                "root": root,
                "project": "12345_demo",
                "shot": "sh020",
                "subfolder": "comfy",
                "task": "comp",
            },
        )
        return resp.status, await resp.json()

    status, payload = _run(tmp_path, monkeypatch, body)
    assert status == 200
    assert payload["ok"] is True
    assert payload["shot"] == "sh020"
    assert "sh020" in payload["shots"]
    assert (tmp_path / "PROJEKTS" / "12345_demo" / "shots" / "sh020" / "comfy" / "comp").is_dir()


def test_create_shot_rejects_placeholder_and_outside_root(tmp_path, monkeypatch):
    async def body(client, root):
        bad = await client.post(
            "/digit/create_shot",
            json={"root": root, "project": "12345_demo", "shot": "(no projects found)"},
        )
        outside = await client.post(
            "/digit/create_shot",
            json={"root": "/etc", "project": "12345_demo", "shot": "sh020"},
        )
        missing = await client.post(
            "/digit/create_shot",
            json={"root": root, "project": "99999_missing", "shot": "sh020"},
        )
        return bad.status, await bad.json(), outside.status, missing.status

    bad_status, bad_payload, outside_status, missing_status = _run(tmp_path, monkeypatch, body)
    assert bad_status == 400
    assert "Invalid shot" in bad_payload["error"]
    assert outside_status == 403
    assert missing_status == 404
    assert not (tmp_path / "PROJEKTS" / "12345_demo" / "shots" / "(no projects found)").exists()


def test_health_is_503_when_root_unlistable(tmp_path, monkeypatch):
    async def body(client, _root):
        def boom(_path):
            raise OSError(107, "Transport endpoint is not connected")

        with patch.object(projekts_utils.os, "listdir", side_effect=boom):
            with patch.object(projekts_utils.time, "sleep"):
                resp = await client.get("/digit/health")
                return resp.status, await resp.json()

    status, payload = _run(tmp_path, monkeypatch, body)
    assert status == 503
    assert payload["ok"] is False
    assert payload["last_scan_error"] is not None
