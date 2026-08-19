"""Live integration helpers for MiniMax H3 fal and MUAPI endpoints.

Used by scripts/manual/h3_integration_test.py and tests/test_h3_integration_live.py.
Requires FAL_KEY and/or MUAPIAPP_API_KEY for live calls.
"""

from __future__ import annotations

import io
import logging
import os
import time
from dataclasses import dataclass, field

try:
    from . import digit_video_common, h3_models, h3_payloads, muapi_client
except ImportError:
    import digit_video_common
    import h3_models
    import h3_payloads
    import muapi_client

logger = logging.getLogger("DigitH3Integration")

T2V_PROMPT = (
    "A macro shot of a red apple on a wooden table, soft window light, "
    "subtle camera push-in, photorealistic."
)
I2V_PROMPT = (
    "The camera slowly pushes in as light shifts across the apple and "
    "a gentle breeze moves the stem."
)
R2V_PROMPT = (
    "Image 1 is the subject. Keep the apple consistent with the reference "
    "while the camera orbits slowly."
)

MIN_LIVE_DURATION = 4


@dataclass
class LiveTestResult:
    provider: str
    mode: str
    endpoint: str
    success: bool
    elapsed_seconds: float
    video_url: str | None = None
    local_path: str | None = None
    error: str | None = None
    payload_keys: list[str] = field(default_factory=list)


def has_fal_key() -> bool:
    return bool(os.environ.get("FAL_KEY", "").strip())


def has_muapi_key() -> bool:
    return bool(os.environ.get("MUAPIAPP_API_KEY", "").strip())


def make_test_image_png() -> bytes:
    """512x512 gradient PNG — small, fast to upload."""
    from PIL import Image

    image = Image.new("RGB", (512, 512))
    pixels = image.load()
    for y in range(512):
        for x in range(512):
            pixels[x, y] = (180 + x // 8, 60 + y // 6, 40)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _upload_fal_image(png_bytes: bytes) -> str:
    import fal_client

    return fal_client.upload(png_bytes, content_type="image/png")


def _upload_muapi_image(png_bytes: bytes) -> str:
    headers = muapi_client.auth_headers()
    return muapi_client.upload_image_bytes(headers, png_bytes, label="integration_test")


def _run_fal(app_id: str, arguments: dict, timeout_seconds: int = 600) -> dict:
    import fal_client

    handle = fal_client.submit(app_id, arguments=arguments)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = handle.status(with_logs=False)
        if isinstance(status, fal_client.Completed):
            return handle.get()
        status_label = type(status).__name__.lower()
        if "fail" in status_label or "error" in status_label:
            raise RuntimeError(f"fal job failed: {status}")
        time.sleep(2)
    raise TimeoutError(f"fal job timed out after {timeout_seconds}s ({app_id})")


def run_fal_mode(
    mode: str,
    *,
    duration: int = MIN_LIVE_DURATION,
    resolution: str = "2K",
    test_image: bytes | None = None,
    output_dir: str | None = None,
) -> LiveTestResult:
    if not has_fal_key():
        return LiveTestResult("fal", mode, "", False, 0.0, error="FAL_KEY not set")

    app_id = h3_models.fal_app_id(mode)
    start = time.monotonic()
    try:
        h3_payloads.validate_h3_request(
            prompt=T2V_PROMPT if mode == "text_to_video" else I2V_PROMPT,
            provider="fal",
            resolution=resolution,
            aspect_ratio="16:9" if mode != "reference_to_video" else "adaptive",
            duration=str(duration),
        )

        prompt = T2V_PROMPT
        if mode == "text_to_video":
            args = h3_payloads.build_fal_args(
                prompt=prompt,
                mode=mode,
                resolution=resolution,
                aspect_ratio="16:9",
                duration=duration,
                enable_prompt_expansion=False,
                enable_safety_checker=True,
            )
        elif mode in ("image_to_video", "first_last_frame"):
            png = test_image or make_test_image_png()
            args = h3_payloads.build_fal_args(
                prompt=I2V_PROMPT,
                mode=mode,
                resolution=resolution,
                aspect_ratio="16:9",
                duration=duration,
                enable_prompt_expansion=False,
                enable_safety_checker=True,
                image_url=_upload_fal_image(png),
            )
        elif mode == "reference_to_video":
            png = test_image or make_test_image_png()
            args = h3_payloads.build_fal_args(
                prompt=R2V_PROMPT,
                mode=mode,
                resolution=resolution,
                aspect_ratio="adaptive",
                duration=duration,
                enable_prompt_expansion=False,
                enable_safety_checker=True,
                reference_image_urls=[_upload_fal_image(png)],
            )
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        result = _run_fal(app_id, args)
        urls = digit_video_common.extract_fal_video_urls(result)
        if not urls:
            raise RuntimeError(f"No video URL in fal response: {result}")

        local_path = None
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            local_path = os.path.join(output_dir, f"fal_{mode}.mp4")
            digit_video_common.secure_download_video(urls[0], local_path)

        return LiveTestResult(
            provider="fal",
            mode=mode,
            endpoint=app_id,
            success=True,
            elapsed_seconds=time.monotonic() - start,
            video_url=urls[0],
            local_path=local_path,
            payload_keys=sorted(args.keys()),
        )
    except Exception as error:
        return LiveTestResult(
            provider="fal",
            mode=mode,
            endpoint=app_id,
            success=False,
            elapsed_seconds=time.monotonic() - start,
            error=digit_video_common.format_api_error(error, provider="fal"),
        )


def run_muapi_mode(
    mode: str,
    *,
    duration: int = MIN_LIVE_DURATION,
    resolution: str = "2K",
    test_image: bytes | None = None,
    output_dir: str | None = None,
) -> LiveTestResult:
    if not has_muapi_key():
        return LiveTestResult("muapi", mode, "", False, 0.0, error="MUAPIAPP_API_KEY not set")

    endpoint = h3_models.muapi_endpoint(mode)
    start = time.monotonic()
    try:
        aspect = "16:9" if mode != "reference_to_video" else "adaptive"
        h3_payloads.validate_h3_request(
            prompt=T2V_PROMPT,
            provider="muapi",
            resolution=resolution,
            aspect_ratio=aspect,
            duration=str(duration),
        )

        png = test_image or make_test_image_png()
        image_url = None
        reference_images = None

        if mode in ("image_to_video", "first_last_frame"):
            image_url = _upload_muapi_image(png)
            payload = h3_payloads.build_muapi_payload(
                prompt=I2V_PROMPT,
                mode=mode,
                resolution=resolution,
                aspect_ratio="16:9",
                duration=duration,
                image_url=image_url,
            )
        elif mode == "reference_to_video":
            reference_images = [_upload_muapi_image(png)]
            payload = h3_payloads.build_muapi_payload(
                prompt=R2V_PROMPT,
                mode=mode,
                resolution=resolution,
                aspect_ratio=aspect,
                duration=duration,
                reference_images=reference_images,
            )
        else:
            payload = h3_payloads.build_muapi_payload(
                prompt=T2V_PROMPT,
                mode=mode,
                resolution=resolution,
                aspect_ratio=aspect,
                duration=duration,
            )

        headers = muapi_client.auth_headers()
        request_id = muapi_client.submit(headers, endpoint, payload)
        result = muapi_client.poll_until_done(headers, request_id)
        urls = muapi_client.extract_output_urls(result)
        if not urls:
            raise RuntimeError(f"No video URL in MUAPI response: {result}")

        local_path = None
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            local_path = os.path.join(output_dir, f"muapi_{mode}.mp4")
            digit_video_common.secure_download_video(urls[0], local_path)

        return LiveTestResult(
            provider="muapi",
            mode=mode,
            endpoint=endpoint,
            success=True,
            elapsed_seconds=time.monotonic() - start,
            video_url=urls[0],
            local_path=local_path,
            payload_keys=sorted(payload.keys()),
        )
    except Exception as error:
        return LiveTestResult(
            provider="muapi",
            mode=mode,
            endpoint=endpoint,
            success=False,
            elapsed_seconds=time.monotonic() - start,
            error=digit_video_common.format_api_error(error, provider="muapi"),
        )


def run_live_suite(
    *,
    providers: list[str] | None = None,
    modes: list[str] | None = None,
    duration: int = MIN_LIVE_DURATION,
    output_dir: str | None = None,
) -> list[LiveTestResult]:
    providers = providers or ["fal", "muapi"]
    modes = modes or ["text_to_video", "image_to_video", "reference_to_video"]

    png = make_test_image_png()
    results: list[LiveTestResult] = []

    for provider in providers:
        for mode in modes:
            if provider == "fal":
                results.append(
                    run_fal_mode(
                        mode,
                        duration=duration,
                        test_image=png,
                        output_dir=output_dir,
                    )
                )
            elif provider == "muapi":
                results.append(
                    run_muapi_mode(
                        mode,
                        duration=duration,
                        test_image=png,
                        output_dir=output_dir,
                    )
                )
    return results


def format_results_report(results: list[LiveTestResult]) -> str:
    lines = ["MiniMax H3 live integration results", "=" * 40]
    for item in results:
        status = "PASS" if item.success else "FAIL"
        lines.append(f"[{status}] {item.provider} / {item.mode} ({item.endpoint})")
        lines.append(f"       elapsed: {item.elapsed_seconds:.1f}s")
        if item.payload_keys:
            lines.append(f"       payload: {', '.join(item.payload_keys)}")
        if item.video_url:
            lines.append(f"       url: {item.video_url[:80]}…")
        if item.local_path:
            lines.append(f"       saved: {item.local_path}")
        if item.error:
            lines.append(f"       error: {item.error}")
    passed = sum(1 for r in results if r.success)
    lines.append("=" * 40)
    lines.append(f"{passed}/{len(results)} passed")
    return "\n".join(lines)
