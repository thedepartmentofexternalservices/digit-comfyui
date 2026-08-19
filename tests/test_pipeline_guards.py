"""Guards on DigitImageSaver / DigitImageLoader / DigitVideoSaver pipeline writes."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from digit_loader import load_digit_module

image_saver_node = load_digit_module("image_saver_node")
image_loader_node = load_digit_module("image_loader_node")
video_saver_node = load_digit_module("video_saver_node")


class FakeTensorImage:
    """Minimal stand-in for a torch IMAGE batch of one 4x4 RGB frame."""

    def __init__(self, width=4, height=4):
        self._np = np.zeros((height, width, 3), dtype=np.float32)
        self.shape = (1, height, width, 3)

    def __getitem__(self, idx):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._np


def _tree(tmp_path):
    root = tmp_path / "PROJEKTS"
    (root / "12345_demo" / "shots" / "sh010").mkdir(parents=True)
    return root


def test_image_saver_rejects_placeholder_shot(tmp_path):
    root = _tree(tmp_path)
    saver = image_saver_node.DigitImageSaver()
    with pytest.raises(ValueError, match="Invalid shot"):
        saver.save_image(
            FakeTensorImage(), str(root), "12345_demo", "(no shots found)",
            "comfy", "comp", "png", "linear", 95, 1001, 4, False, "none",
        )
    junk = root / "12345_demo" / "shots" / "(no shots found)"
    assert not junk.exists()


def test_image_saver_rejects_traversal_subfolder(tmp_path):
    root = _tree(tmp_path)
    outside = tmp_path / "OUTSIDE"
    outside.mkdir()
    saver = image_saver_node.DigitImageSaver()
    with pytest.raises(ValueError, match="path separators"):
        saver.save_image(
            FakeTensorImage(), str(root), "12345_demo", "sh010",
            "../../../../OUTSIDE/evil", "comp", "png", "linear", 95, 1001, 4, False, "none",
        )
    assert list(outside.rglob("*.png")) == []


def test_image_saver_writes_png_inside_root(tmp_path):
    root = _tree(tmp_path)
    saver = image_saver_node.DigitImageSaver()
    result = saver.save_image(
        FakeTensorImage(), str(root), "12345_demo", "sh010",
        "comfy", "comp", "png", "linear", 95, 1001, 4, False, "none",
    )
    path = Path(result["result"][0])
    assert path.is_file()
    assert str(path).startswith(str(root))
    assert path.name == "12345_sh010_comp.1001.png"


def test_image_saver_preview_is_bounded(tmp_path, monkeypatch):
    from PIL import Image

    root = _tree(tmp_path)
    temp = tmp_path / "temp"
    monkeypatch.setattr(image_saver_node.folder_paths, "get_temp_directory", lambda: str(temp))
    result = image_saver_node.DigitImageSaver().save_image(
        FakeTensorImage(width=3000, height=1000),
        str(root),
        "12345_demo",
        "sh010",
        "comfy",
        "comp",
        "png",
        "linear",
        95,
        1001,
        4,
        True,
        "none",
    )
    preview_path = temp / result["ui"]["images"][0]["filename"]
    with Image.open(preview_path) as preview:
        assert max(preview.size) == image_saver_node.PREVIEW_MAX_EDGE
    with Image.open(result["result"][0]) as saved:
        assert saved.size == (3000, 1000)


def test_video_saver_rejects_placeholder_shot(tmp_path):
    root = _tree(tmp_path)
    saver = video_saver_node.DigitVideoSaver()
    with pytest.raises(ValueError, match="Invalid shot"):
        saver.save_video(
            str(root), "12345_demo", "(no shots found)", "comfy", "comp",
            1001, 4, "none",
        )
    junk = root / "12345_demo" / "shots" / "(no shots found)"
    assert not junk.exists()


def test_image_loader_rejects_browse_path_outside_roots(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    monkeypatch.setenv("DIGIT_PROJEKTS_ROOTS", str(root))
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"not-an-image")
    loader = image_loader_node.DigitImageLoader()
    with pytest.raises(ValueError, match="outside the allowed PROJEKTS roots"):
        loader.load_latest(
            str(root), "12345_demo", "sh010", "comfy", "comp", "png",
            browse_path=str(outside),
        )


def test_image_loader_blank_on_missing_frames(tmp_path):
    root = _tree(tmp_path)
    loader = image_loader_node.DigitImageLoader()
    result = loader.load_latest(
        str(root), "12345_demo", "sh010", "comfy", "comp", "png",
        on_missing="blank",
    )
    assert result["result"][1] == ""
    assert result["result"][2] == 0


def test_image_loader_picks_latest_and_pinned_frame(tmp_path):
    from PIL import Image

    root = _tree(tmp_path)
    target = root / "12345_demo" / "shots" / "sh010" / "comfy" / "comp"
    target.mkdir(parents=True)
    for frame in (1001, 1003):
        Image.new("RGB", (2, 2), color=(frame % 255, 0, 0)).save(
            target / f"12345_sh010_comp.{frame:04d}.png"
        )
    loader = image_loader_node.DigitImageLoader()
    latest = loader.load_latest(
        str(root), "12345_demo", "sh010", "comfy", "comp", "png",
    )
    assert latest["result"][2] == 1003
    assert latest["result"][1].endswith("12345_sh010_comp.1003.png")
    pinned = loader.load_latest(
        str(root), "12345_demo", "sh010", "comfy", "comp", "png",
        frame_mode="pinned", frame=1001,
    )
    assert pinned["result"][2] == 1001
    assert pinned["result"][1].endswith("12345_sh010_comp.1001.png")


def test_image_loader_errors_on_missing_frames(tmp_path):
    root = _tree(tmp_path)
    loader = image_loader_node.DigitImageLoader()
    with pytest.raises(FileNotFoundError, match="no frames found"):
        loader.load_latest(
            str(root), "12345_demo", "sh010", "comfy", "comp", "png",
            on_missing="error",
        )


def test_input_types_does_not_bake_no_shots_found(tmp_path, monkeypatch):
    root = tmp_path / "PROJEKTS"
    (root / "00000_empty").mkdir(parents=True)
    (root / "12345_demo" / "shots" / "sh010").mkdir(parents=True)
    monkeypatch.setenv("DIGIT_PROJEKTS_ROOTS", str(root))

    for cls in (
        image_saver_node.DigitImageSaver,
        image_loader_node.DigitImageLoader,
        video_saver_node.DigitVideoSaver,
    ):
        types = cls.INPUT_TYPES()
        shot = types["required"]["shot"]
        projects = types["required"]["project"][0]
        assert shot[0] == "STRING"
        assert "(no shots found)" not in str(shot)
        assert "(no projects found)" not in projects
        assert projects == [""]


def test_image_loader_change_key_tracks_selected_file(tmp_path):
    from PIL import Image

    path = tmp_path / "source.png"
    Image.new("RGB", (8, 8), color=(1, 2, 3)).save(path)
    first = image_loader_node.DigitImageLoader.IS_CHANGED(browse_path=str(path))
    second = image_loader_node.DigitImageLoader.IS_CHANGED(browse_path=str(path))
    assert first == second

    Image.new("RGB", (9, 8), color=(4, 5, 6)).save(path)
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1))
    assert image_loader_node.DigitImageLoader.IS_CHANGED(browse_path=str(path)) != first


def test_image_loader_preview_is_bounded(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setenv("DIGIT_PROJEKTS_ROOTS", str(tmp_path))
    monkeypatch.setattr(image_loader_node.folder_paths, "get_temp_directory", lambda: str(tmp_path))
    source = tmp_path / "large.png"
    Image.new("RGB", (3000, 1000), color=(1, 2, 3)).save(source)

    result = image_loader_node.DigitImageLoader().load_latest(
        str(tmp_path),
        "12345_demo",
        "sh010",
        "comfy",
        "comp",
        "png",
        browse_path=str(source),
    )
    preview = tmp_path / result["ui"]["images"][0]["filename"]
    with Image.open(preview) as image:
        assert max(image.size) == image_loader_node.PREVIEW_MAX_EDGE
    assert result["result"][0].shape[2] == 3000
