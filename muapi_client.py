"""Shared MUAPI client helpers for DIGIT nodes.

Used by the Seedance video node (muapi provider) and the MU Seedance
Character node. Auth comes from the MUAPIAPP_API_KEY environment variable.
"""

import io
import logging
import os
import time
import uuid

import numpy as np
import requests
from PIL import Image

logger = logging.getLogger("DigitMuapiClient")

API_BASE_URL = "https://api.muapi.ai/api/v1"
UPLOAD_URL = f"{API_BASE_URL}/upload_file"
POLL_INTERVAL_SECONDS = 3
MAX_WAIT_SECONDS = 20 * 60
TERMINAL_FAILURE_STATES = {"failed", "cancelled"}
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def require_api_key():
    api_key = os.environ.get("MUAPIAPP_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "MUAPIAPP_API_KEY environment variable is not set. "
            "Set it before starting ComfyUI."
        )
    return api_key


def auth_headers():
    return {"x-api-key": require_api_key()}


def request_with_retry(method, url, max_retries=3, log_prefix="[DIGIT MUAPI]", **kwargs):
    last_error = None
    for retry_index in range(max_retries):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code in RETRYABLE_STATUS_CODES:
                if retry_index == max_retries - 1:
                    response.raise_for_status()
                delay = 2 ** retry_index
                logger.warning(
                    "%s HTTP %d; retrying in %ds.",
                    log_prefix,
                    response.status_code,
                    delay,
                )
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                detail = response.text[:500].strip()
                try:
                    error_data = response.json()
                    detail = (
                        error_data.get("error")
                        or error_data.get("message")
                        or detail
                    )
                except ValueError:
                    pass
                raise RuntimeError(
                    f"MUAPI request failed with HTTP {response.status_code}: "
                    f"{detail or 'No error detail returned.'}"
                )
            return response
        except requests.RequestException as error:
            last_error = error
            if retry_index == max_retries - 1:
                raise RuntimeError(f"MUAPI request failed: {error}") from error
            delay = 2 ** retry_index
            logger.warning(
                "%s Request error; retrying in %ds: %s",
                log_prefix,
                delay,
                error,
            )
            time.sleep(delay)

    raise RuntimeError(f"MUAPI request failed: {last_error}")


def response_json(response, operation):
    try:
        return response.json()
    except ValueError as error:
        preview = response.text[:500]
        raise RuntimeError(f"{operation} returned invalid JSON: {preview}") from error


def _tensor_to_png_bytes(image_tensor):
    """Convert the first image in a ComfyUI IMAGE batch to PNG bytes."""
    if image_tensor is None or image_tensor.ndim != 4 or image_tensor.shape[0] < 1:
        raise ValueError("Image input must be a non-empty ComfyUI IMAGE batch.")

    image_array = image_tensor[0].detach().cpu().numpy()
    image_array = (image_array * 255).clip(0, 255).astype(np.uint8)

    if image_array.shape[-1] == 4:
        image = Image.fromarray(image_array, mode="RGBA").convert("RGB")
    else:
        image = Image.fromarray(image_array, mode="RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _upload_bytes(headers, file_bytes, filename, content_type):
    response = request_with_retry(
        "post",
        UPLOAD_URL,
        headers=headers,
        files={"file": (filename, file_bytes, content_type)},
        timeout=300,
    )
    upload = response_json(response, f"Upload of {filename}")
    file_url = upload.get("url") or upload.get("file_url") or upload.get("output")
    if not file_url:
        raise RuntimeError(f"MUAPI upload returned no URL for {filename}: {upload}")
    return str(file_url)


def upload_image_tensor(headers, image_tensor, label="image"):
    """Upload the first frame of a ComfyUI IMAGE batch. Returns URL."""
    png_bytes = _tensor_to_png_bytes(image_tensor)
    name = f"digit_{label}_{uuid.uuid4().hex[:8]}.png"
    return _upload_bytes(headers, png_bytes, name, "image/png")


def upload_video(headers, video_obj, temp_dir, label="video"):
    """Upload a ComfyUI VIDEO object. Returns URL."""
    path = None
    try:
        source = video_obj.get_stream_source()
        if isinstance(source, str) and os.path.isfile(source):
            path = source
    except Exception:
        pass
    if path is None:
        os.makedirs(temp_dir, exist_ok=True)
        path = os.path.join(temp_dir, f"digit_{label}_{uuid.uuid4().hex[:8]}.mp4")
        video_obj.save_to(path)
    with open(path, "rb") as f:
        return _upload_bytes(headers, f.read(), os.path.basename(path), "video/mp4")


def upload_audio(headers, audio_obj, temp_dir, label="audio"):
    """Upload a ComfyUI AUDIO dict ({'waveform','sample_rate'}) as WAV. Returns URL."""
    try:
        import soundfile as sf
    except ImportError:
        raise ImportError(
            "soundfile is required for reference_audio inputs. "
            "Install with: pip install soundfile"
        )
    waveform = audio_obj["waveform"]  # (B, C, N)
    sample_rate = audio_obj["sample_rate"]
    wav = waveform[0].cpu().numpy().T  # (N, C) for soundfile
    os.makedirs(temp_dir, exist_ok=True)
    path = os.path.join(temp_dir, f"digit_{label}_{uuid.uuid4().hex[:8]}.wav")
    sf.write(path, wav, sample_rate)
    with open(path, "rb") as f:
        return _upload_bytes(headers, f.read(), os.path.basename(path), "audio/wav")


def submit(headers, endpoint, payload, log_prefix="[DIGIT MUAPI]"):
    """POST a generation job. Returns request_id."""
    url = f"{API_BASE_URL}/{endpoint.lstrip('/')}"
    logger.info("%s Submitting to %s...", log_prefix, url)
    response = request_with_retry(
        "post",
        url,
        headers={**headers, "Content-Type": "application/json"},
        json=payload,
        timeout=120,
        log_prefix=log_prefix,
    )
    submission = response_json(response, f"Submission to {endpoint}")
    request_id = submission.get("request_id")
    if not request_id:
        raise RuntimeError(f"MUAPI submission returned no request_id: {submission}")
    return request_id


def poll_status(headers, request_id, log_prefix="[DIGIT MUAPI]"):
    """One status check. Returns the result dict (contains 'status')."""
    poll_url = f"{API_BASE_URL}/predictions/{request_id}/result"
    response = request_with_retry(
        "get",
        poll_url,
        headers={**headers, "Content-Type": "application/json"},
        timeout=60,
        log_prefix=log_prefix,
    )
    return response_json(response, "Result polling")


def poll_until_done(headers, request_id, log_prefix="[DIGIT MUAPI]",
                    max_wait_seconds=MAX_WAIT_SECONDS):
    """Poll a request until terminal. Returns the completed result dict."""
    deadline = time.monotonic() + max_wait_seconds
    last_status = "unknown"

    while time.monotonic() < deadline:
        result = poll_status(headers, request_id, log_prefix=log_prefix)
        last_status = str(result.get("status", "unknown")).lower()

        if last_status == "completed":
            return result
        if last_status in TERMINAL_FAILURE_STATES:
            detail = result.get("error") or "No error detail returned."
            raise RuntimeError(f"MUAPI generation {last_status}: {detail}")

        logger.info("%s Request %s status: %s", log_prefix, request_id, last_status)
        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"MUAPI generation timed out after {max_wait_seconds} seconds "
        f"(last status: {last_status}, request ID: {request_id})."
    )


def extract_output_urls(result):
    """Pull output media URLs from a completed MUAPI result payload."""
    urls = []

    def _add(value):
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            urls.append(value)

    for value in result.get("outputs") or []:
        _add(value)

    output_data = result.get("output_data") or {}
    if isinstance(output_data, dict):
        for key in ("video_url", "url", "output_url", "sheet_url", "image_url"):
            _add(output_data.get(key))
        for value in output_data.get("outputs") or []:
            _add(value)

    for key in ("video_url", "url", "output_url"):
        _add(result.get(key))

    # De-dup, keep order
    seen = set()
    unique = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique
