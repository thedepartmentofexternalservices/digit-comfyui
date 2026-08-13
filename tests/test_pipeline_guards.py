"""Guards on DigitImageSaver / DigitImageLoader / DigitVideoSaver pipeline writes."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from digit_loader import load_digit_module

image_saver_node = load_digit_module("image_saver_node")
image_loader_node = load_digit_module("image_loader_node")
video_saver_node = load_digit_module("video_saver_node")


class FakeTensorImage:
    """Minimal stand-in for a torch IMAGE batch of one 4x4 RGB frame."""

    def __init__(self):
        self._np = np.zeros((4, 4, 3), dtype=np.float32)
        self.shape = (1, 4, 4, 3)

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
    with pytest.raises(ValueError, match="must not be"):
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


def test_image_saver_uses_typed_filename(tmp_path):
    root = _tree(tmp_path)
    saver = image_saver_node.DigitImageSaver()
    result = saver.save_image(
        FakeTensorImage(), str(root), "12345_demo", "sh010",
        "comfy", "comp", "png", "linear", 95, 1001, 4, False, "none",
        filename="hero_wide",
    )
    path = Path(result["result"][0])
    assert path.is_file()
    assert path.name == "12345_hero_wide.1001.png"
    with pytest.raises(ValueError, match="path separators"):
        saver.save_image(
            FakeTensorImage(), str(root), "12345_demo", "sh010",
            "comfy", "comp", "png", "linear", 95, 1001, 4, False, "none",
            filename="../evil",
        )


def test_image_saver_writes_the_shared_preview_path(tmp_path):
    root = _tree(tmp_path)
    preview = image_saver_node.next_output_path(
        str(root), "12345_demo", "sh010", "comfy/comp",
        "hero_wide", "png", 1001, 4,
    )

    result = image_saver_node.DigitImageSaver().save_image(
        FakeTensorImage(), str(root), "12345_demo", "sh010",
        "comfy", "comp", "png", "linear", 95, 1001, 4, False, "none",
        filename="hero_wide", folder="comfy/comp",
    )

    assert result["result"][0] == preview["path"]
    assert Path(preview["path"]).is_file()


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
        shots = types["required"]["shot"][0]
        projects = types["required"]["project"][0]
        assert shots == [""]
        assert "(no shots found)" not in shots
        assert "(no projects found)" not in projects
        assert "12345_demo" in projects
        assert types["required"]["folder"][0] == ["comfy/comp"]
        assert "subfolder" not in types["required"]
        assert "task" not in types["required"]
        if cls is not image_loader_node.DigitImageLoader:
            assert types["required"]["filename"][0] == "STRING"


def test_image_saver_writes_to_folder_path(tmp_path):
    root = _tree(tmp_path)
    saver = image_saver_node.DigitImageSaver()
    two = saver.save_image(
        FakeTensorImage(), str(root), "12345_demo", "sh010",
        "comfy", "comp", "png", "linear", 95, 1001, 4, False, "none",
        filename="hero_wide",
        folder="comfy/comp",
    )
    two_path = Path(two["result"][0])
    assert two_path.is_file()
    assert two_path.name == "12345_hero_wide.1001.png"
    assert two_path.parent == root / "12345_demo" / "shots" / "sh010" / "comfy" / "comp"

    one = saver.save_image(
        FakeTensorImage(), str(root), "12345_demo", "sh010",
        "comfy", "comp", "png", "linear", 95, 1001, 4, False, "none",
        filename="plate_ref",
        folder="plates",
    )
    one_path = Path(one["result"][0])
    assert one_path.is_file()
    assert one_path.name == "12345_plate_ref.1001.png"
    assert one_path.parent == root / "12345_demo" / "shots" / "sh010" / "plates"

    deep = saver.save_image(
        FakeTensorImage(), str(root), "12345_demo", "sh010",
        "comfy", "comp", "png", "linear", 95, 1001, 4, False, "none",
        filename="hero_wide_v001",
        folder="comfy/comp/v001",
    )
    deep_path = Path(deep["result"][0])
    assert deep_path.is_file()
    assert deep_path.name == "12345_hero_wide_v001.1001.png"
    assert deep_path.parent == root / "12345_demo" / "shots" / "sh010" / "comfy" / "comp" / "v001"
