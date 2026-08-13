"""Unit tests for PROJEKTS pipeline path utilities."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest
from digit_loader import load_digit_module

projekts_utils = load_digit_module("projekts_utils")


def test_get_projekts_roots_from_env(monkeypatch):
    monkeypatch.setenv("DIGIT_PROJEKTS_ROOTS", "/tmp/a:/tmp/b")
    assert projekts_utils.get_projekts_roots() == ["/tmp/a", "/tmp/b"]


def test_scan_projects_filters_five_digit_prefix():
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "12345_project_a"))
        os.makedirs(os.path.join(root, "bad_name"))
        projects = projekts_utils.scan_projects(root)
        assert projects == ["12345_project_a"]


def test_scan_projects_empty_root():
    with tempfile.TemporaryDirectory() as root:
        assert projekts_utils.scan_projects(root) == ["(no projects found)"]


def test_scan_shots_lists_shot_folders():
    with tempfile.TemporaryDirectory() as root:
        shots_dir = os.path.join(root, "12345_demo", "shots")
        os.makedirs(os.path.join(shots_dir, "shot_a"))
        os.makedirs(os.path.join(shots_dir, "shot_b"))
        shots = projekts_utils.scan_shots(root, "12345_demo")
        assert shots == ["shot_a", "shot_b"]


def test_scan_shots_filters_placeholder_junk_folders():
    with tempfile.TemporaryDirectory() as root:
        shots_dir = os.path.join(root, "12345_demo", "shots")
        os.makedirs(os.path.join(shots_dir, "shot_a"))
        os.makedirs(os.path.join(shots_dir, "(no shots found)"))
        shots = projekts_utils.scan_shots(root, "12345_demo")
        assert shots == ["shot_a"]
        assert "(no shots found)" not in shots


def test_scan_shots_empty_or_placeholder_project():
    with tempfile.TemporaryDirectory() as root:
        assert projekts_utils.scan_shots(root, "") == ["(no shots found)"]
        assert projekts_utils.scan_shots(root, "(no projects found)") == ["(no shots found)"]


def test_scan_projects_skips_unreadable_child():
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "12345_ok"))
        os.makedirs(os.path.join(root, "12345_secret"))

        original_isdir = os.path.isdir

        def isdir_flaky(path):
            if path.endswith("12345_secret"):
                raise PermissionError(13, "Permission denied")
            return original_isdir(path)

        with patch.object(projekts_utils.os.path, "isdir", side_effect=isdir_flaky):
            # listdir still sees both names; the secret child is skipped.
            projects = projekts_utils.scan_projects(root)
        assert projects == ["12345_ok"]


def test_scan_shots_listdir_oserror_returns_storage_sentinel():
    with tempfile.TemporaryDirectory() as root:
        shots_dir = os.path.join(root, "12345_demo", "shots")
        os.makedirs(os.path.join(shots_dir, "shot_a"))

        def boom(_path):
            raise OSError(107, "Transport endpoint is not connected")

        with patch.object(projekts_utils.os, "listdir", side_effect=boom):
            with patch.object(projekts_utils.time, "sleep"):
                shots = projekts_utils.scan_shots(root, "12345_demo")
        assert shots == ["(storage unavailable)"]
        assert projekts_utils.is_storage_unavailable(shots)


def test_listdir_resilient_retries_then_raises():
    calls = {"n": 0}

    def boom(_path):
        calls["n"] += 1
        raise OSError(107, "Transport endpoint is not connected")

    with patch.object(projekts_utils.os, "listdir", side_effect=boom):
        with pytest.raises(projekts_utils.StorageUnavailableError):
            projekts_utils.listdir_resilient("/mnt/gone", retries=3, delay=0, sleeper=lambda _d: None)
    assert calls["n"] == 3


def test_validate_segment_rejects_placeholders_and_separators():
    with pytest.raises(ValueError, match="Invalid shot"):
        projekts_utils.validate_segment("shot", "(no shots found)")
    with pytest.raises(ValueError, match="path separators"):
        projekts_utils.validate_segment("shot", "../other")
    with pytest.raises(ValueError, match="absolute"):
        projekts_utils.validate_segment("task", "/tmp/evil")
    with pytest.raises(ValueError, match="required"):
        projekts_utils.validate_segment("project", "")
    assert projekts_utils.validate_segment("shot", "ROUND_04") == "ROUND_04"


def test_resolve_pipeline_dir_rejects_traversal(tmp_path):
    root = tmp_path / "PROJEKTS"
    (root / "12345_demo" / "shots" / "sh010").mkdir(parents=True)
    with pytest.raises(ValueError, match="path separators"):
        projekts_utils.resolve_pipeline_dir(
            str(root), "12345_demo", "sh010", "../../../../OUTSIDE", "comp"
        )
    with pytest.raises(ValueError, match="Invalid shot"):
        projekts_utils.resolve_pipeline_dir(
            str(root), "12345_demo", "(no shots found)", "comfy", "comp"
        )


def test_resolve_pipeline_dir_happy_path(tmp_path):
    root = tmp_path / "PROJEKTS"
    target = projekts_utils.resolve_pipeline_dir(
        str(root), "12345_demo", "sh010", "comfy", "comp"
    )
    assert target == os.path.join(str(root), "12345_demo", "shots", "sh010", "comfy", "comp")
    assert projekts_utils.is_within_roots(target, roots=[str(root)])


def test_next_frame_increments_from_existing_files():
    with tempfile.TemporaryDirectory() as target_dir:
        prefix, shot, task, ext = "12345_demo", "shot_a", "comp", "mp4"
        for frame in (1001, 1003):
            name = f"{prefix}_{shot}_{task}.{frame:05d}.{ext}"
            open(os.path.join(target_dir, name), "wb").close()

        next_frame = projekts_utils.next_frame(
            target_dir, prefix, shot, task, ext, start_frame=1001, frame_pad=5
        )
        assert next_frame == 1004


def test_is_within_roots_rejects_escape():
    with tempfile.TemporaryDirectory() as root:
        inside = os.path.join(root, "12345_demo", "shots", "shot_a")
        os.makedirs(inside, exist_ok=True)
        outside = os.path.join(tempfile.gettempdir(), "outside-digit-test")
        os.makedirs(outside, exist_ok=True)

        assert projekts_utils.is_within_roots(inside, roots=[root]) is True
        assert projekts_utils.is_within_roots(outside, roots=[root]) is False
