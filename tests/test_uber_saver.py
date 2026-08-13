"""DIGIT Uber Saver image/video behavior."""

from pathlib import Path

import numpy as np
import pytest
from digit_loader import load_digit_module

uber_saver_node = load_digit_module("uber_saver_node")


class FakeTensorImage:
    def __init__(self):
        self._np = np.zeros((4, 4, 3), dtype=np.float32)
        self.shape = (1, 4, 4, 3)

    def __getitem__(self, _idx):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._np


def _tree(tmp_path):
    root = tmp_path / "PROJEKTS"
    (root / "12345_demo" / "shots" / "sh010").mkdir(parents=True)
    return root


def _save_kwargs(root):
    return {
        "projekts_root": str(root),
        "project": "12345_demo",
        "shot": "sh010",
        "folder": "comfy/comp/v001",
        "name": "hero_wide",
        "format": "png",
        "tonemap": "linear",
        "quality": 95,
        "start_frame": 1001,
        "frame_pad": 4,
        "show_preview": False,
        "save_workflow": "none",
    }


def test_input_types_use_one_union_media_socket():
    types = uber_saver_node.DigitUberSaver.INPUT_TYPES()
    assert list(types["required"])[:6] == [
        "media", "projekts_root", "project", "shot", "folder", "name",
    ]
    assert types["required"]["media"][0] == "IMAGE,VIDEO,VIDEO_PATHS"
    assert "optional" not in types
    assert types["required"]["tonemap"][0][0] == "sRGB"
    assert types["required"]["quality"][1]["default"] == 100


def test_uber_saver_saves_image(tmp_path):
    root = _tree(tmp_path)
    result = uber_saver_node.DigitUberSaver().save(
        **_save_kwargs(root),
        media=FakeTensorImage(),
    )
    path = Path(result["result"][0])
    assert path.is_file()
    assert path == (
        root / "12345_demo" / "shots" / "sh010"
        / "comfy" / "comp" / "v001" / "hero_wide.1001.png"
    )


def test_uber_saver_saves_video_path(tmp_path):
    root = _tree(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    result = uber_saver_node.DigitUberSaver().save(
        **_save_kwargs(root),
        media=[str(source)],
    )
    path = Path(result["result"][0])
    assert path.read_bytes() == b"video"
    assert path.name == "hero_wide.1001.mp4"


def test_uber_saver_requires_media(tmp_path):
    root = _tree(tmp_path)
    saver = uber_saver_node.DigitUberSaver()
    with pytest.raises(ValueError, match="needs an image or video"):
        saver.save(**_save_kwargs(root), media=None)
    assert not (root / "12345_demo" / "shots" / "sh010" / "comfy").exists()
