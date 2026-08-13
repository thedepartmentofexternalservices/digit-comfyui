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
    assert projekts_utils.validate_segment("shot", "  sh020  ") == "sh020"


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


def test_file_stem_typed_name_wins():
    assert projekts_utils.file_stem("12345_demo", "sh010", "comp") == "12345_sh010_comp"
    assert projekts_utils.file_stem("12345_demo", "sh010", "comp", "") == "12345_sh010_comp"
    assert projekts_utils.file_stem("12345_demo", "sh010", "comp", "hero_wide") == "hero_wide"
    assert projekts_utils.file_stem("12345_demo", "sh010", "comp", "hero_wide.png") == "hero_wide"
    with pytest.raises(ValueError, match="path separators"):
        projekts_utils.file_stem("12345_demo", "sh010", "comp", "../evil")
    with pytest.raises(ValueError, match="Invalid filename"):
        projekts_utils.file_stem("12345_demo", "sh010", "comp", "(no shots found)")


def test_next_frame_increments_from_existing_files():
    with tempfile.TemporaryDirectory() as target_dir:
        stem, ext = "12345_shot_a_comp", "mp4"
        for frame in (1001, 1003):
            name = f"{stem}.{frame:05d}.{ext}"
            open(os.path.join(target_dir, name), "wb").close()

        next_frame = projekts_utils.next_frame(
            target_dir, stem, ext, start_frame=1001, frame_pad=5
        )
        assert next_frame == 1004


def test_next_output_path_is_exact_and_does_not_write(tmp_path):
    root = tmp_path / "PROJEKTS"
    target = root / "12345_demo" / "shots" / "sh010" / "comfy" / "comp" / "v001"
    target.mkdir(parents=True)
    (target / "hero_wide.1001.png").write_bytes(b"existing")

    preview = projekts_utils.next_output_path(
        str(root), "12345_demo", "sh010", "comfy/comp/v001",
        "hero_wide", "png", 1001, 4,
    )

    assert preview == {
        "path": str(target / "hero_wide.1002.png"),
        "directory": str(target),
        "filename": "hero_wide.1002.png",
        "frame": 1002,
        "stem": "hero_wide",
        "extension": "png",
    }
    assert sorted(path.name for path in target.iterdir()) == ["hero_wide.1001.png"]


def test_next_output_path_uses_fallback_name_and_validates_numbers(tmp_path):
    root = tmp_path / "PROJEKTS"
    target = root / "12345_demo" / "shots" / "sh010" / "plates"
    target.mkdir(parents=True)

    preview = projekts_utils.next_output_path(
        str(root), "12345_demo", "sh010", "plates", "", ".mp4", "7", "6",
    )
    assert preview["path"] == str(target / "12345_sh010_plates.000007.mp4")
    assert preview["frame"] == 7

    with pytest.raises(ValueError, match="frame_pad"):
        projekts_utils.next_output_path(
            str(root), "12345_demo", "sh010", "plates", "", "png", 1001, 9,
        )
    with pytest.raises(ValueError, match="start_frame"):
        projekts_utils.next_output_path(
            str(root), "12345_demo", "sh010", "plates", "", "png", -1, 4,
        )


def test_is_within_roots_rejects_escape():
    with tempfile.TemporaryDirectory() as root:
        inside = os.path.join(root, "12345_demo", "shots", "shot_a")
        os.makedirs(inside, exist_ok=True)
        outside = os.path.join(tempfile.gettempdir(), "outside-digit-test")
        os.makedirs(outside, exist_ok=True)

        assert projekts_utils.is_within_roots(inside, roots=[root]) is True
        assert projekts_utils.is_within_roots(outside, roots=[root]) is False


def test_health_payload_reachable_root(tmp_path, monkeypatch):
    root = tmp_path / "PROJEKTS"
    (root / "12345_demo").mkdir(parents=True)
    monkeypatch.setenv("DIGIT_PROJEKTS_ROOTS", str(root))
    payload = projekts_utils.health_payload(pack_version="test", comfyui_version=None)
    assert payload["ok"] is True
    assert payload["pack_version"] == "test"
    assert payload["roots"][0]["reachable"] is True
    assert payload["roots"][0]["project_count"] == 1


def test_scan_child_folders_lists_subfolders_and_tasks():
    with tempfile.TemporaryDirectory() as root:
        shot = os.path.join(root, "12345_demo", "shots", "sh010")
        os.makedirs(os.path.join(shot, "comfy", "comp"))
        os.makedirs(os.path.join(shot, "comfy", "paint"))
        os.makedirs(os.path.join(shot, "plates"))
        assert projekts_utils.scan_child_folders(root, "12345_demo", "sh010") == ["comfy", "plates"]
        assert projekts_utils.scan_child_folders(root, "12345_demo", "sh010", "comfy") == ["comp", "paint"]
        assert projekts_utils.scan_child_folders(root, "12345_demo", "(no shots found)") == [""]


def test_create_shot_dir_makes_shot_and_pipeline(tmp_path):
    root = tmp_path / "PROJEKTS"
    (root / "12345_demo").mkdir(parents=True)
    shot_dir = projekts_utils.create_shot_dir(str(root), "12345_demo", "sh020")
    assert os.path.isdir(shot_dir)
    assert shot_dir == os.path.join(str(root), "12345_demo", "shots", "sh020")
    pipeline = projekts_utils.create_shot_dir(
        str(root), "12345_demo", "sh020", "comfy", "comp"
    )
    assert os.path.isdir(pipeline)
    assert pipeline.endswith(os.path.join("sh020", "comfy", "comp"))


def test_create_shot_dir_rejects_placeholder_and_missing_project(tmp_path):
    root = tmp_path / "PROJEKTS"
    (root / "12345_demo").mkdir(parents=True)
    with pytest.raises(ValueError, match="Invalid shot"):
        projekts_utils.create_shot_dir(str(root), "12345_demo", "(no shots found)")
    with pytest.raises(FileNotFoundError, match="project not found"):
        projekts_utils.create_shot_dir(str(root), "99999_missing", "sh010")
    junk = root / "12345_demo" / "shots" / "(no shots found)"
    assert not junk.exists()


def test_parse_folder_defaults_and_levels():
    assert projekts_utils.parse_folder("") == ["comfy", "comp"]
    assert projekts_utils.parse_folder("comfy/comp") == ["comfy", "comp"]
    assert projekts_utils.parse_folder("plates") == ["plates"]
    assert projekts_utils.parse_folder("  comfy/paint  ") == ["comfy", "paint"]
    assert projekts_utils.parse_folder("comfy/comp/v001") == ["comfy", "comp", "v001"]
    with pytest.raises(ValueError, match="at most 8 levels"):
        projekts_utils.parse_folder("/".join(f"l{i}" for i in range(9)))
    with pytest.raises(ValueError, match="must not be"):
        projekts_utils.parse_folder("../evil")


def test_effective_folder_prefers_folder_over_legacy():
    assert projekts_utils.effective_folder("comfy/paint", "comfy", "comp") == "comfy/paint"
    assert projekts_utils.effective_folder("", "comfy", "comp") == "comfy/comp"
    assert projekts_utils.effective_folder("", "plates", None) == "plates"
    assert projekts_utils.effective_folder() == "comfy/comp"


def test_resolve_folder_dir_any_depth(tmp_path):
    root = tmp_path / "PROJEKTS"
    two = projekts_utils.resolve_folder_dir(str(root), "12345_demo", "sh010", "comfy/comp")
    one = projekts_utils.resolve_folder_dir(str(root), "12345_demo", "sh010", "plates")
    deep = projekts_utils.resolve_folder_dir(
        str(root), "12345_demo", "sh010", "comfy/comp/v001"
    )
    assert two == os.path.join(str(root), "12345_demo", "shots", "sh010", "comfy", "comp")
    assert one == os.path.join(str(root), "12345_demo", "shots", "sh010", "plates")
    assert deep == os.path.join(
        str(root), "12345_demo", "shots", "sh010", "comfy", "comp", "v001"
    )
    with pytest.raises(ValueError, match="must not be"):
        projekts_utils.resolve_folder_dir(str(root), "12345_demo", "sh010", "../evil")


def test_scan_shot_folders_lists_nested_and_leaf(tmp_path):
    root = tmp_path / "PROJEKTS"
    shot = root / "12345_demo" / "shots" / "sh010"
    (shot / "comfy" / "comp" / "v001").mkdir(parents=True)
    (shot / "comfy" / "paint").mkdir(parents=True)
    (shot / "plates").mkdir(parents=True)
    found = projekts_utils.scan_shot_folders(str(root), "12345_demo", "sh010")
    assert found == ["comfy", "comfy/comp", "comfy/comp/v001", "comfy/paint", "plates"]
    assert projekts_utils.scan_shot_folders(str(root), "12345_demo", "(no shots found)") == [""]


def test_create_folder_dir_makes_path_under_shot(tmp_path):
    root = tmp_path / "PROJEKTS"
    (root / "12345_demo" / "shots" / "sh010").mkdir(parents=True)
    created = projekts_utils.create_folder_dir(str(root), "12345_demo", "sh010", "comfy/paint")
    assert os.path.isdir(created)
    assert created.endswith(os.path.join("sh010", "comfy", "paint"))
    with pytest.raises(FileNotFoundError, match="shot not found"):
        projekts_utils.create_folder_dir(str(root), "12345_demo", "sh099", "comfy/comp")


def test_combo_choices_strips_sentinels():
    assert projekts_utils.combo_choices(["(no shots found)"]) == [""]
    assert projekts_utils.combo_choices(["(storage unavailable)"]) == [""]
    assert projekts_utils.combo_choices([]) == [""]
    assert projekts_utils.combo_choices(["12345_demo", "(no projects found)"]) == ["12345_demo"]
    assert projekts_utils.combo_choices(["sh010", "ROUND_04"]) == ["sh010", "ROUND_04"]
