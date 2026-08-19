from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import media_sanitize


class FileVideo:
    def __init__(self, path):
        self.path = str(path)

    def get_stream_source(self):
        return self.path


def test_image_clamps_8334_by_4748_and_stays_under_cap():
    tensor = np.full((1, 4748, 8334, 3), 0.5, dtype=np.float16)
    result = media_sanitize.sanitize_image_batch(
        tensor,
        max_edge=6000,
        max_bytes=9_000_000,
        route="test:seedance",
    )
    assert (result.original_width, result.original_height) == (8334, 4748)
    assert result.final_width == 6000
    assert max(result.final_width, result.final_height) == 6000
    assert result.byte_count <= 9_000_000
    assert "resize_max_edge:6000" in result.transformations


def test_rgba_is_converted_to_rgb():
    tensor = np.zeros((1, 8, 12, 4), dtype=np.float32)
    tensor[..., 0] = 1
    tensor[..., 3] = 0.25
    result = media_sanitize.sanitize_image_batch(
        tensor,
        max_edge=None,
        max_bytes=None,
        route="test:no-limits",
    )
    decoded = Image.open(io.BytesIO(result.data))
    assert decoded.mode == "RGB"
    assert result.transformations == ("rgba_to_rgb",)


def test_noisy_png_falls_back_to_jpeg_under_cap():
    rng = np.random.default_rng(4)
    tensor = rng.random((1, 1600, 1600, 3), dtype=np.float32)
    result = media_sanitize.sanitize_image_batch(
        tensor,
        max_edge=None,
        max_bytes=1_000_000,
        route="test:small-cap",
    )
    assert result.content_type == "image/jpeg"
    assert result.extension == ".jpg"
    assert result.byte_count <= 1_000_000
    assert any(item.startswith("resize_for_bytes:") for item in result.transformations)


def test_minimum_even_dimensions_preserves_aspect_and_pixel_floor():
    width, height = media_sanitize.minimum_even_dimensions(320, 180, 407_696)
    assert width % 2 == height % 2 == 0
    assert width * height >= 407_696
    assert width / height == pytest.approx(16 / 9, rel=0.01)


def test_compliant_video_is_noop(tmp_path):
    path = tmp_path / "source.mp4"
    path.write_bytes(b"x" * 100)

    def probe(_path):
        return {
            "width": 1280,
            "height": 720,
            "duration": 5.0,
            "codec": "h264",
            "bytes": 100,
        }

    result = media_sanitize.sanitize_reference_video(
        FileVideo(path),
        str(tmp_path),
        min_pixels=407_696,
        max_bytes=50_000_000,
        probe_fn=probe,
    )
    assert result.path == str(path)
    assert result.transformations == ()
    result.cleanup()
    assert path.exists()


def test_small_video_is_upscaled_and_temp_is_cleaned(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    def probe(path):
        if path == str(source):
            width, height = 320, 180
        else:
            width, height = media_sanitize.minimum_even_dimensions(
                320, 180, 407_696
            )
        return {
            "width": width,
            "height": height,
            "duration": 4.5,
            "codec": "h264",
            "bytes": os.path.getsize(path),
        }

    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        with open(command[-1], "wb") as file:
            file.write(b"upscaled")

    result = media_sanitize.sanitize_reference_video(
        FileVideo(source),
        str(tmp_path),
        min_pixels=407_696,
        max_bytes=50_000_000,
        probe_fn=probe,
        runner=runner,
    )
    assert result.final_width % 2 == result.final_height % 2 == 0
    assert result.final_width * result.final_height >= 407_696
    assert commands[0][0] == "ffmpeg"
    output = result.path
    result.cleanup()
    assert not os.path.exists(output)
    assert source.exists()


def test_oversized_video_is_rejected(tmp_path):
    path = tmp_path / "large.mp4"
    path.write_bytes(b"x")

    def probe(_path):
        return {
            "width": 1280,
            "height": 720,
            "duration": 5.0,
            "codec": "h264",
            "bytes": 50_000_001,
        }

    with pytest.raises(ValueError, match="accepts at most 50000000 bytes"):
        media_sanitize.sanitize_reference_video(
            FileVideo(path),
            str(tmp_path),
            min_pixels=407_696,
            max_bytes=50_000_000,
            route="fal:seedance-r2v",
            probe_fn=probe,
        )
