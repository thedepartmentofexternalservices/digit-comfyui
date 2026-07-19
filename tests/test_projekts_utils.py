"""Unit tests for PROJEKTS pipeline path utilities."""

from __future__ import annotations

import os
import tempfile

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
