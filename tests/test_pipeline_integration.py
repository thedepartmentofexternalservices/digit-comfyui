"""Marked integration: save→load on a live PROJEKTS scratch tree.

Skipped unless DIGIT_INTEGRATION=1 and the scratch project exists.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from digit_loader import load_digit_module

image_saver_node = load_digit_module("image_saver_node")
image_loader_node = load_digit_module("image_loader_node")

SCRATCH_ROOT = Path("/mnt/lucid/PROJEKTS")
SCRATCH_PROJECT = "25999_comfy_corner"


class FakeTensorImage:
    def __init__(self):
        self._np = np.zeros((4, 4, 3), dtype=np.float32)
        self.shape = (1, 4, 4, 3)

    def __getitem__(self, idx):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._np


@pytest.mark.integration
def test_save_load_roundtrip_on_comfy_corner():
    if os.environ.get("DIGIT_INTEGRATION") != "1":
        pytest.skip("Set DIGIT_INTEGRATION=1 to run live PROJEKTS roundtrip")
    root = SCRATCH_ROOT
    if not (root / SCRATCH_PROJECT / "shots").is_dir():
        pytest.skip(f"{root / SCRATCH_PROJECT} is not mounted")

    saver = image_saver_node.DigitImageSaver()
    saved = saver.save_image(
        FakeTensorImage(), str(root), SCRATCH_PROJECT, "digit_ci",
        "comfy", "comp", "png", "linear", 95, 1001, 4, False, "none",
    )
    path = Path(saved["result"][0])
    assert path.is_file()
    assert str(path).startswith(str(root))

    loader = image_loader_node.DigitImageLoader()
    loaded = loader.load_latest(
        str(root), SCRATCH_PROJECT, "digit_ci", "comfy", "comp", "png",
        frame_mode="latest",
    )
    assert Path(loaded["result"][1]) == path
