"""Route-scoped media sanitizers for provider uploads."""

from __future__ import annotations

import io
import json
import logging
import math
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger("DigitMediaSanitize")

JPEG_QUALITY = 95


def _ffmpeg_executable():
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as error:
        raise ValueError(
            "ffmpeg is required to sanitize reference videos. Install ffmpeg "
            "or the imageio-ffmpeg Python package."
        ) from error


def _probe_video_with_imageio(path):
    try:
        import imageio_ffmpeg
    except ImportError as error:
        raise ValueError(
            "ffprobe is unavailable and imageio-ffmpeg is not installed."
        ) from error
    reader = imageio_ffmpeg.read_frames(path, pix_fmt="rgb24")
    try:
        metadata = next(reader)
    except Exception as error:
        raise ValueError(f"Could not inspect reference video '{path}': {error}") from error
    finally:
        reader.close()
    width, height = metadata.get("source_size") or metadata.get("size") or (0, 0)
    return {
        "width": int(width),
        "height": int(height),
        "duration": float(metadata.get("duration") or 0),
        "codec": str(metadata.get("codec") or "unknown"),
        "bytes": os.path.getsize(path),
    }


@dataclass(frozen=True)
class SanitizedImage:
    data: bytes
    content_type: str
    extension: str
    original_width: int
    original_height: int
    final_width: int
    final_height: int
    transformations: tuple[str, ...]

    @property
    def byte_count(self):
        return len(self.data)


@dataclass
class SanitizedVideo:
    path: str
    original_width: int
    original_height: int
    final_width: int
    final_height: int
    duration: float
    codec: str
    byte_count: int
    transformations: tuple[str, ...]
    _owned_paths: tuple[str, ...] = ()

    def cleanup(self):
        for path in self._owned_paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def _to_numpy(value):
    if hasattr(value, "detach"):
        return value.detach().cpu().numpy()
    if hasattr(value, "cpu") and hasattr(value, "numpy"):
        return value.cpu().numpy()
    return np.asarray(value)


def _comfy_batch_to_image(image_batch):
    if image_batch is None:
        raise ValueError("Image input must be a non-empty ComfyUI IMAGE batch.")
    array = _to_numpy(image_batch)
    if array.ndim != 4 or array.shape[0] < 1:
        raise ValueError("Image input must be a non-empty ComfyUI IMAGE batch.")
    if array.shape[-1] not in (3, 4):
        raise ValueError("ComfyUI IMAGE input must have 3 RGB or 4 RGBA channels.")
    if array.shape[1] < 1 or array.shape[2] < 1:
        raise ValueError("ComfyUI IMAGE input dimensions must be positive.")
    if not np.issubdtype(array.dtype, np.number):
        raise ValueError("ComfyUI IMAGE input must contain numeric pixel values.")

    pixels = (array[0] * 255).clip(0, 255).astype(np.uint8)
    if pixels.shape[-1] == 4:
        return Image.fromarray(pixels, mode="RGBA").convert("RGB"), ["rgba_to_rgb"]
    return Image.fromarray(pixels, mode="RGB"), []


def _encode(image, fmt, **kwargs):
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def sanitize_image_batch(
    image_batch,
    *,
    max_edge: Optional[int],
    max_bytes: Optional[int],
    label="image",
    route="unspecified",
):
    """Validate and encode the first item in a Comfy IMAGE batch.

    Limits are mandatory keyword arguments so callers cannot accidentally apply
    one provider's constraints to unrelated models. ``None`` means no route
    limit.
    """
    image, transformations = _comfy_batch_to_image(image_batch)
    original_width, original_height = image.size
    if max_bytes is not None and max_bytes < 1:
        raise ValueError("max_bytes must be positive or None.")

    if max_edge is not None:
        if max_edge < 1:
            raise ValueError("max_edge must be positive or None.")
        longest = max(image.size)
        if longest > max_edge:
            scale = max_edge / float(longest)
            size = (
                max(1, int(image.width * scale)),
                max(1, int(image.height * scale)),
            )
            image = image.resize(size, Image.Resampling.LANCZOS)
            transformations.append(f"resize_max_edge:{max_edge}")

    png = _encode(image, "PNG")
    data = png
    content_type = "image/png"
    extension = ".png"

    if max_bytes is not None and len(data) > max_bytes:
        transformations.append(f"png_to_jpeg:q{JPEG_QUALITY}")
        while True:
            data = _encode(
                image,
                "JPEG",
                quality=JPEG_QUALITY,
                optimize=True,
            )
            content_type = "image/jpeg"
            extension = ".jpg"
            if len(data) <= max_bytes:
                break
            if image.width <= 1 and image.height <= 1:
                raise ValueError(
                    f"{label} remains over {max_bytes} bytes after reducing "
                    f"to {image.width}x{image.height}."
                )
            new_size = (
                max(1, int(image.width * 0.85)),
                max(1, int(image.height * 0.85)),
            )
            image = image.resize(new_size, Image.Resampling.LANCZOS)
            transformations.append(f"resize_for_bytes:{new_size[0]}x{new_size[1]}")

    result = SanitizedImage(
        data=data,
        content_type=content_type,
        extension=extension,
        original_width=original_width,
        original_height=original_height,
        final_width=image.width,
        final_height=image.height,
        transformations=tuple(transformations),
    )
    logger.info(
        "[DIGIT media] image label=%s route=%s original=%dx%d final=%dx%d "
        "bytes=%d format=%s transformations=%s",
        label,
        route,
        result.original_width,
        result.original_height,
        result.final_width,
        result.final_height,
        result.byte_count,
        result.content_type,
        list(result.transformations),
    )
    return result


def probe_video(path, *, runner=subprocess.run):
    if runner is subprocess.run and shutil.which("ffprobe") is None:
        result = _probe_video_with_imageio(path)
        if result["width"] < 1 or result["height"] < 1:
            raise ValueError(f"Reference video '{path}' has invalid dimensions.")
        return result
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,codec_name:format=duration,size",
        "-of",
        "json",
        path,
    ]
    try:
        completed = runner(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"Could not inspect reference video '{path}': {error}") from error
    try:
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        width = int(stream["width"])
        height = int(stream["height"])
        duration = float(payload.get("format", {}).get("duration") or 0)
        size = int(payload.get("format", {}).get("size") or os.path.getsize(path))
        codec = str(stream.get("codec_name") or "unknown")
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"ffprobe returned incomplete metadata for '{path}'.") from error
    if width < 1 or height < 1:
        raise ValueError(f"Reference video '{path}' has invalid dimensions.")
    return {
        "width": width,
        "height": height,
        "duration": duration,
        "codec": codec,
        "bytes": size,
    }


def minimum_even_dimensions(width, height, min_pixels):
    """Return aspect-preserving even dimensions meeting a pixel minimum."""
    if width * height >= min_pixels and width % 2 == 0 and height % 2 == 0:
        return width, height
    scale = max(1.0, math.sqrt(min_pixels / float(width * height)))
    scaled_width = max(2, int(math.ceil(width * scale / 2.0) * 2))
    scaled_height = max(2, int(math.ceil(height * scale / 2.0) * 2))
    return scaled_width, scaled_height


def _resolve_video_path(video_obj, temp_dir, label):
    try:
        source = video_obj.get_stream_source()
        if isinstance(source, str) and os.path.isfile(source):
            return source, ()
    except Exception:
        pass
    os.makedirs(temp_dir, exist_ok=True)
    path = os.path.join(temp_dir, f"digit_{label}_{uuid.uuid4().hex[:8]}.mp4")
    video_obj.save_to(path)
    return path, (path,)


def sanitize_reference_video(
    video_obj,
    temp_dir,
    *,
    min_pixels: Optional[int],
    max_bytes: Optional[int],
    label="video",
    route="unspecified",
    probe_fn=probe_video,
    runner=subprocess.run,
):
    """Resolve, inspect, and conditionally upscale a Comfy VIDEO object."""
    source_path, owned_paths = _resolve_video_path(video_obj, temp_dir, label)
    try:
        original = probe_fn(source_path)
        final_path = source_path
        transformations = []
        target = minimum_even_dimensions(
            original["width"],
            original["height"],
            min_pixels or 1,
        )
        needs_scale = target != (original["width"], original["height"])
        if needs_scale:
            os.makedirs(temp_dir, exist_ok=True)
            final_path = os.path.join(
                temp_dir,
                f"digit_{label}_sanitized_{uuid.uuid4().hex[:8]}.mp4",
            )
            command = [
                (
                    _ffmpeg_executable()
                    if runner is subprocess.run
                    else "ffmpeg"
                ),
                "-y",
                "-i",
                source_path,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-vf",
                f"scale={target[0]}:{target[1]}",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "medium",
                "-c:a",
                "aac",
                final_path,
            ]
            owned_paths = (*owned_paths, final_path)
            try:
                runner(command, check=True, capture_output=True, text=True)
            except (OSError, subprocess.CalledProcessError) as error:
                raise ValueError(
                    f"Could not upscale reference video '{source_path}': {error}"
                ) from error
            transformations.append(f"upscale_min_pixels:{target[0]}x{target[1]}")

        final = probe_fn(final_path)
        if max_bytes is not None and final["bytes"] > max_bytes:
            raise ValueError(
                f"{label} is {final['bytes']} bytes after sanitizing; "
                f"route '{route}' accepts at most {max_bytes} bytes."
            )
        result = SanitizedVideo(
            path=final_path,
            original_width=original["width"],
            original_height=original["height"],
            final_width=final["width"],
            final_height=final["height"],
            duration=final["duration"],
            codec=final["codec"],
            byte_count=final["bytes"],
            transformations=tuple(transformations),
            _owned_paths=tuple(owned_paths),
        )
        logger.info(
            "[DIGIT media] video label=%s route=%s source=%s original=%dx%d "
            "final=%dx%d bytes=%d format=%s duration=%.3f transformations=%s",
            label,
            route,
            source_path,
            result.original_width,
            result.original_height,
            result.final_width,
            result.final_height,
            result.byte_count,
            result.codec,
            result.duration,
            list(result.transformations),
        )
        return result
    except Exception:
        for path in owned_paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        raise
