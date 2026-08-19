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

try:
    from . import media_sanitize
except ImportError:
    import media_sanitize

logger = logging.getLogger("DigitMuapiClient")

API_BASE_URL = "https://api.muapi.ai/api/v1"
UPLOAD_URL = f"{API_BASE_URL}/upload_file"
# MUAPI rejects image uploads over 10MB. Keep 1MB of headroom.
MAX_UPLOAD_BYTES = 9_000_000
MAX_IMAGE_EDGE_PIXELS = 6000
JPEG_FALLBACK_QUALITY = 95
POLL_INTERVAL_SECONDS = 3
MAX_WAIT_SECONDS = 20 * 60
QUEUE_STALL_SECONDS = MAX_WAIT_SECONDS
PROCESS_TIMEOUT_SECONDS = MAX_WAIT_SECONDS
STARTED_STATES = {"processing", "completed"}
TERMINAL_FAILURE_STATES = {"failed", "cancelled"}
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def batch_max_wait_seconds(batch_count):
    """Allow queued Seedance batches enough time to render serially."""
    return QUEUE_STALL_SECONDS + PROCESS_TIMEOUT_SECONDS * max(1, int(batch_count))


def status_has_started(status):
    return str(status or "").lower() in STARTED_STATES | TERMINAL_FAILURE_STATES


def queue_is_stalled(jobs, elapsed_seconds):
    if elapsed_seconds < QUEUE_STALL_SECONDS:
        return False
    return not any(
        job.get("result") or status_has_started(job.get("last_status"))
        for job in jobs
    )


def job_process_timed_out(job, now):
    started = job.get("processing_at")
    return started is not None and (now - started) >= PROCESS_TIMEOUT_SECONDS


def _format_job_timeout(job, reason):
    request_id = job.get("request_id") or "n/a"
    last_status = job.get("last_status") or "unknown"
    return f"{reason} (last status={last_status}, request_id={request_id})"


def run_batch_poll(
    jobs,
    poll_fn,
    *,
    batch_count=None,
    now_fn=time.monotonic,
    sleep_fn=time.sleep,
    abort_fn=None,
    on_progress=None,
    poll_interval=POLL_INTERVAL_SECONDS,
):
    """Poll each MUAPI job with queue, processing, and batch-specific clocks."""
    pending = {index for index, job in enumerate(jobs) if job.get("request_id")}
    completed_count = len(jobs) - len(pending)
    if on_progress:
        on_progress(completed_count, len(jobs))

    batch_start = now_fn()
    n = max(1, int(batch_count if batch_count is not None else len(jobs)))
    overall_deadline = batch_start + batch_max_wait_seconds(n)

    while pending:
        if abort_fn:
            abort_fn()
        now = now_fn()
        elapsed = now - batch_start
        if queue_is_stalled(jobs, elapsed):
            reason = f"MUAPI queue stalled after {int(elapsed)}s"
            for index in pending:
                jobs[index]["error"] = _format_job_timeout(jobs[index], reason)
            break
        if now > overall_deadline:
            reason = f"Timed out after {int(elapsed)}s waiting on a {n}-clip batch"
            for index in pending:
                jobs[index]["error"] = _format_job_timeout(jobs[index], reason)
            break

        for index in list(pending):
            job = jobs[index]
            try:
                result = poll_fn(job["request_id"])
            except Exception as error:
                logger.warning(
                    "[DIGIT MUAPI] status check failed for job %d: %s",
                    index + 1,
                    error,
                )
                continue
            status = str(result.get("status", "")).lower()
            job["last_status"] = status
            if status == "processing" and job.get("processing_at") is None:
                job["processing_at"] = now
            if job_process_timed_out(job, now):
                job["error"] = _format_job_timeout(
                    job,
                    f"Timed out after {PROCESS_TIMEOUT_SECONDS}s in processing",
                )
            elif status == "completed":
                job["result"] = result
            elif status in TERMINAL_FAILURE_STATES:
                job["error"] = str(result.get("error") or f"Generation {status}.")
            else:
                continue
            pending.remove(index)
            completed_count += 1
            if on_progress:
                on_progress(completed_count, len(jobs))

        if pending:
            sleep_fn(poll_interval)
    return jobs


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
                retry_after = response.headers.get("Retry-After", "").strip()
                delay = min(int(retry_after), 60) if retry_after.isdigit() else 2 ** retry_index
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


def _tensor_to_pil_image(image_tensor):
    """Convert the first image in a ComfyUI IMAGE batch to a PIL RGB image."""
    if image_tensor is None or image_tensor.ndim != 4 or image_tensor.shape[0] < 1:
        raise ValueError("Image input must be a non-empty ComfyUI IMAGE batch.")

    image_array = image_tensor[0].detach().cpu().numpy()
    image_array = (image_array * 255).clip(0, 255).astype(np.uint8)

    if image_array.shape[-1] == 4:
        return Image.fromarray(image_array, mode="RGBA").convert("RGB")
    return Image.fromarray(image_array, mode="RGB")


def _encode_image(image, image_format, **save_params):
    """Encode a PIL image to bytes."""
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, **save_params)
    return buffer.getvalue()


def _tensor_to_png_bytes(image_tensor):
    """Convert the first image in a ComfyUI IMAGE batch to PNG bytes."""
    return _encode_image(_tensor_to_pil_image(image_tensor), "PNG")


def _tensor_to_upload_file(image_tensor):
    """Encode the first image in a ComfyUI IMAGE batch for upload.

    Returns (file_bytes, extension, content_type). Uses PNG when it fits
    under MAX_UPLOAD_BYTES; otherwise falls back to JPEG, downscaling as a
    last resort, so uploads stay under MUAPI's 10MB limit.
    """
    try:
        result = media_sanitize.sanitize_image_batch(
            image_tensor,
            max_edge=MAX_IMAGE_EDGE_PIXELS,
            max_bytes=MAX_UPLOAD_BYTES,
            route="muapi:seedance",
        )
        return result.data, result.extension.lstrip("."), result.content_type
    except ValueError:
        if MAX_UPLOAD_BYTES >= 1_000:
            raise
        # Preserve the old helper's behavior for synthetic, impossible test caps.
        image = _tensor_to_pil_image(image_tensor)
        data = _encode_image(image, "JPEG", quality=JPEG_FALLBACK_QUALITY)
        return data, "jpg", "image/jpeg"


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


def upload_image_bytes(headers, png_bytes, label="image"):
    """Upload raw PNG bytes. Returns URL."""
    name = f"digit_{label}_{uuid.uuid4().hex[:8]}.png"
    return _upload_bytes(headers, png_bytes, name, "image/png")


def upload_image_tensor(headers, image_tensor, label="image"):
    """Upload the first frame of a ComfyUI IMAGE batch. Returns URL."""
    file_bytes, extension, content_type = _tensor_to_upload_file(image_tensor)
    name = f"digit_{label}_{uuid.uuid4().hex[:8]}.{extension}"
    return _upload_bytes(headers, file_bytes, name, content_type)


def upload_video(
    headers,
    video_obj,
    temp_dir,
    label="video",
    *,
    route="muapi:unspecified",
    min_pixels=None,
    max_bytes=None,
):
    """Upload a ComfyUI VIDEO object. Returns URL."""
    result = media_sanitize.sanitize_reference_video(
        video_obj,
        temp_dir,
        min_pixels=min_pixels,
        max_bytes=max_bytes,
        label=label,
        route=route,
    )
    try:
        with open(result.path, "rb") as file:
            return _upload_bytes(
                headers,
                file.read(),
                os.path.basename(result.path),
                "video/mp4",
            )
    finally:
        result.cleanup()


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
