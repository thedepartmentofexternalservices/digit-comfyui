"""MUAPI image upload limits — Seedance 6000px edge and 10 MB cap."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import muapi_client as muapi


def _solid_tensor(width, height):
    rgb = np.full((height, width, 3), 0.5, dtype=np.float32)
    return rgb[np.newaxis, ...]


def test_fit_image_scales_down_8334_wide_frame():
    image = Image.new("RGB", (8334, 4748), color=(128, 64, 32))
    fitted, changed = muapi._fit_image_within_edge(image)
    assert changed is True
    assert fitted.size[0] == 6000
    assert max(fitted.size) == 6000


def test_fit_image_leaves_small_images_alone():
    image = Image.new("RGB", (1920, 1080), color=(255, 255, 255))
    fitted, changed = muapi._fit_image_within_edge(image)
    assert changed is False
    assert fitted.size == (1920, 1080)


def test_encode_image_falls_back_to_jpeg_for_large_png():
    # Random noise PNGs compress poorly and exceed MUAPI's 10 MB upload cap.
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 256, size=(4000, 4000, 3), dtype=np.uint8)
    image = Image.fromarray(noise, mode="RGB")
    file_bytes, content_type, ext = muapi._encode_image_bytes(image)
    assert content_type == "image/jpeg"
    assert ext == ".jpg"
    assert len(file_bytes) <= muapi.MAX_UPLOAD_BYTES


def test_tensor_upload_bytes_respects_edge_limit():
    tensor = _solid_tensor(8334, 4748)
    raw = muapi._tensor_to_png_bytes(tensor)
    uploaded = Image.open(io.BytesIO(raw))
    assert max(uploaded.size) == 6000
