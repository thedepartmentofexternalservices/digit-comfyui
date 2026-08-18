"""Unit tests for MUAPI image-upload encoding and the 10MB size fallback."""

from __future__ import annotations

import io

import numpy as np
import pytest
from digit_loader import load_digit_module
from PIL import Image

muapi_client = load_digit_module("muapi_client")


class FakeTensor:
    """Minimal stand-in for a torch tensor backed by a numpy array."""

    def __init__(self, array):
        self._array = np.asarray(array)

    @property
    def ndim(self):
        return self._array.ndim

    @property
    def shape(self):
        return self._array.shape

    def __getitem__(self, index):
        return FakeTensor(self._array[index])

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._array


def _noise_batch(height, width, channels=3, seed=0):
    rng = np.random.default_rng(seed)
    return FakeTensor(rng.random((1, height, width, channels), dtype=np.float32))


def _decode(file_bytes):
    return Image.open(io.BytesIO(file_bytes))


def test_small_image_stays_png():
    tensor = _noise_batch(64, 64)
    file_bytes, extension, content_type = muapi_client._tensor_to_upload_file(tensor)
    assert file_bytes[:4] == b"\x89PNG"
    assert extension == "png"
    assert content_type == "image/png"


def test_rgba_input_converts_to_rgb():
    tensor = _noise_batch(32, 32, channels=4)
    image = muapi_client._tensor_to_pil_image(tensor)
    assert image.mode == "RGB"
    file_bytes, _, _ = muapi_client._tensor_to_upload_file(tensor)
    assert _decode(file_bytes).mode == "RGB"


def test_non_batch_input_raises():
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError, match="IMAGE batch"):
        muapi_client._tensor_to_pil_image(
            FakeTensor(rng.random((64, 64, 3), dtype=np.float32))
        )
    with pytest.raises(ValueError, match="IMAGE batch"):
        muapi_client._tensor_to_pil_image(None)


def test_oversize_png_falls_back_to_full_size_jpeg(monkeypatch):
    tensor = _noise_batch(512, 512)
    image = muapi_client._tensor_to_pil_image(tensor)
    png_size = len(muapi_client._encode_image(image, "PNG"))
    jpeg_size = len(
        muapi_client._encode_image(
            image, "JPEG", quality=muapi_client.JPEG_FALLBACK_QUALITY
        )
    )
    assert jpeg_size < png_size

    limit = (png_size + jpeg_size) // 2
    monkeypatch.setattr(muapi_client, "MAX_UPLOAD_BYTES", limit)

    file_bytes, extension, content_type = muapi_client._tensor_to_upload_file(tensor)
    assert extension == "jpg"
    assert content_type == "image/jpeg"
    assert len(file_bytes) <= limit
    assert _decode(file_bytes).size == (512, 512)


def test_oversize_jpeg_downscales_until_under_limit(monkeypatch):
    monkeypatch.setattr(muapi_client, "MAX_UPLOAD_BYTES", 15_000)
    tensor = _noise_batch(512, 512)

    file_bytes, extension, content_type = muapi_client._tensor_to_upload_file(tensor)
    assert extension == "jpg"
    assert content_type == "image/jpeg"
    assert len(file_bytes) <= 15_000
    width, height = _decode(file_bytes).size
    assert width < 512 and height < 512


def test_upload_image_tensor_uses_matching_name_and_content_type(monkeypatch):
    captured = {}

    def fake_upload_bytes(headers, file_bytes, filename, content_type):
        captured.update(
            {"bytes": file_bytes, "filename": filename, "content_type": content_type}
        )
        return "https://example.com/uploaded"

    monkeypatch.setattr(muapi_client, "_upload_bytes", fake_upload_bytes)
    tensor = _noise_batch(64, 64)

    url = muapi_client.upload_image_tensor({}, tensor, label="first_frame")
    assert url == "https://example.com/uploaded"
    assert captured["filename"].endswith(".png")
    assert captured["content_type"] == "image/png"
    assert captured["bytes"][:4] == b"\x89PNG"

    monkeypatch.setattr(muapi_client, "MAX_UPLOAD_BYTES", 1)
    muapi_client.upload_image_tensor({}, tensor, label="first_frame")
    assert captured["filename"].endswith(".jpg")
    assert captured["content_type"] == "image/jpeg"
    assert captured["bytes"][:3] == b"\xff\xd8\xff"
