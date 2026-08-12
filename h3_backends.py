"""MiniMax H3 provider backends (fal, MUAPI, Replicate)."""

from __future__ import annotations

import io
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import folder_paths
import numpy as np
from PIL import Image as PILImage

try:
    from . import digit_video_common, h3_models, h3_payloads, muapi_client
except ImportError:
    import digit_video_common
    import h3_models
    import h3_payloads
    import muapi_client

logger = logging.getLogger("DigitH3Backends")

MAX_AUTOMATIC_RETRIES = 3
POLL_INTERVAL_SECONDS = 2.0
FAL_NO_RETRY_HEADERS = {"X-Fal-No-Retry": "1"}
LOG_PREFIX = "[DigitMiniMax]"


@dataclass
class H3GenerationContext:
    prompt: str
    mode: str
    resolution: str
    aspect_ratio: str
    duration: int
    batch_count: int
    enable_prompt_expansion: bool
    enable_safety_checker: bool
    first_frame: Any = None
    last_frame: Any = None
    ref_images: list = field(default_factory=list)
    ref_videos: list = field(default_factory=list)
    ref_audios: list = field(default_factory=list)


@dataclass
class H3GenerationResult:
    video_paths: list[str]
    jobs: list[dict]
    route: str
    payload_summary: dict


def _tensor_to_png_bytes(tensor):
    img_np = tensor.cpu().numpy()
    img_np = (img_np * 255).clip(0, 255).astype(np.uint8)
    img = PILImage.fromarray(img_np)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_image_tensor(fal_client, image_tensor):
    return fal_client.upload(_tensor_to_png_bytes(image_tensor[0]), content_type="image/png")


def _upload_video(fal_client, video_obj, temp_paths: list[str]):
    try:
        source = video_obj.get_stream_source()
        if isinstance(source, str) and os.path.isfile(source):
            return fal_client.upload_file(source)
    except Exception:
        pass
    temp_dir = folder_paths.get_temp_directory()
    os.makedirs(temp_dir, exist_ok=True)
    tmp_path = os.path.join(temp_dir, f"h3_upload_{uuid.uuid4().hex[:8]}.mp4")
    video_obj.save_to(tmp_path)
    temp_paths.append(tmp_path)
    return fal_client.upload_file(tmp_path)


def _upload_audio(fal_client, audio_obj, temp_paths: list[str]):
    try:
        import soundfile as sf
    except ImportError:
        raise ImportError(
            "soundfile is required for reference_audio inputs. "
            "Install with: pip install soundfile"
        )
    waveform = audio_obj["waveform"]
    sample_rate = audio_obj["sample_rate"]
    wav = waveform[0].cpu().numpy().T
    temp_dir = folder_paths.get_temp_directory()
    os.makedirs(temp_dir, exist_ok=True)
    tmp_path = os.path.join(temp_dir, f"h3_audio_{uuid.uuid4().hex[:8]}.wav")
    sf.write(tmp_path, wav, sample_rate)
    temp_paths.append(tmp_path)
    return fal_client.upload_file(tmp_path)


def _check_interrupted():
    from comfy.model_management import throw_exception_if_processing_interrupted
    throw_exception_if_processing_interrupted()


def _download_to_temp(url: str, batch_timestamp: int, batch_uuid: str, job_index: int) -> str:
    temp_dir = folder_paths.get_temp_directory()
    os.makedirs(temp_dir, exist_ok=True)
    local_path = os.path.join(temp_dir, f"h3_{batch_timestamp}_{batch_uuid}_{job_index}.mp4")
    digit_video_common.secure_download_video(url, local_path, log_prefix=LOG_PREFIX)
    return local_path


def generate_fal(ctx: H3GenerationContext) -> H3GenerationResult:
    try:
        import fal_client
    except ImportError:
        raise ImportError(
            "fal-client is required for the fal provider. "
            "Install with: pip install fal-client"
        )
    if not os.environ.get("FAL_KEY"):
        raise ValueError(
            "FAL_KEY environment variable is not set. "
            "Export FAL_KEY=<your-key> before starting ComfyUI."
        )

    app_id = h3_models.fal_app_id(ctx.mode)
    temp_paths: list[str] = []
    try:
        image_url = end_image_url = None
        reference_image_urls = reference_video_urls = reference_audio_urls = None

        if ctx.mode in ("image_to_video", "first_last_frame"):
            image_url = _upload_image_tensor(fal_client, ctx.first_frame)
            if ctx.mode == "first_last_frame":
                end_image_url = _upload_image_tensor(fal_client, ctx.last_frame)
        elif ctx.mode == "reference_to_video":
            if ctx.ref_images:
                reference_image_urls = [
                    _upload_image_tensor(fal_client, img) for img in ctx.ref_images
                ]
            if ctx.ref_videos:
                reference_video_urls = [
                    _upload_video(fal_client, video, temp_paths) for video in ctx.ref_videos
                ]
            if ctx.ref_audios:
                reference_audio_urls = [
                    _upload_audio(fal_client, audio, temp_paths) for audio in ctx.ref_audios
                ]

        args = h3_payloads.build_fal_args(
            prompt=ctx.prompt,
            mode=ctx.mode,
            resolution=ctx.resolution,
            aspect_ratio=ctx.aspect_ratio,
            duration=ctx.duration,
            enable_prompt_expansion=ctx.enable_prompt_expansion,
            enable_safety_checker=ctx.enable_safety_checker,
            image_url=image_url,
            end_image_url=end_image_url,
            reference_image_urls=reference_image_urls,
            reference_video_urls=reference_video_urls,
            reference_audio_urls=reference_audio_urls,
        )
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except OSError:
                pass

    logger.info("%s Provider: fal | Mode: %s | App: %s", LOG_PREFIX, ctx.mode, app_id)
    jobs = _run_fal_batch(fal_client, app_id, args, ctx.batch_count)
    video_paths = _collect_fal_paths(jobs)
    if not video_paths:
        _raise_batch_failure(jobs, "H3 fal")
    return H3GenerationResult(video_paths, jobs, app_id, args)


def _run_fal_batch(fal_client, app_id, shared_args, batch_count):
    jobs = []
    pending = set()
    try:
        for index in range(batch_count):
            job = {"index": index, "attempt": 0, "request_ids": [], "result": None, "error": ""}
            jobs.append(job)
            if _submit_fal_job_with_retries(fal_client, app_id, shared_args, job):
                pending.add(index)

        import comfy.utils
        pbar = comfy.utils.ProgressBar(len(jobs))
        completed_count = len(jobs) - len(pending)
        if completed_count:
            pbar.update_absolute(completed_count)

        while pending:
            _check_interrupted()
            for index in list(pending):
                job = jobs[index]
                try:
                    status = job["handle"].status(with_logs=False)
                except Exception as error:
                    logger.warning("%s Status check failed job %d: %s", LOG_PREFIX, index + 1, error)
                    continue
                if not isinstance(status, fal_client.Completed):
                    continue
                try:
                    job["result"] = job["handle"].get()
                    pending.remove(index)
                except Exception as error:
                    if (
                        digit_video_common.should_retry_api_error(error)
                        and job["attempt"] <= MAX_AUTOMATIC_RETRIES
                    ):
                        time.sleep(2 ** (job["attempt"] - 1))
                        if _submit_fal_job_with_retries(fal_client, app_id, shared_args, job):
                            continue
                    job["error"] = digit_video_common.format_api_error(error, provider="fal")
                    pending.remove(index)
                completed_count += 1
                pbar.update_absolute(completed_count)
            if pending:
                time.sleep(POLL_INTERVAL_SECONDS)
    except BaseException:
        _cancel_fal_jobs(jobs, pending)
        raise
    return jobs


def _submit_fal_job_with_retries(fal_client, app_id, shared_args, job):
    while job["attempt"] <= MAX_AUTOMATIC_RETRIES:
        try:
            job["attempt"] += 1
            handle = fal_client.submit(
                app_id,
                arguments=dict(shared_args),
                headers=FAL_NO_RETRY_HEADERS,
            )
            job["handle"] = handle
            job["request_ids"].append(handle.request_id)
            job["error"] = ""
            return True
        except Exception as error:
            job.pop("handle", None)
            job["error"] = digit_video_common.format_api_error(error, provider="fal")
            if (
                not digit_video_common.should_retry_api_error(error)
                or job["attempt"] > MAX_AUTOMATIC_RETRIES
            ):
                return False
            time.sleep(2 ** (job["attempt"] - 1))
    return False


def _cancel_fal_jobs(jobs, pending):
    for index in pending:
        handle = jobs[index].get("handle")
        if handle is None:
            continue
        try:
            handle.cancel()
        except Exception as error:
            logger.warning("%s Cancel failed: %s", LOG_PREFIX, error)


def _collect_fal_paths(jobs):
    batch_timestamp = int(time.time())
    batch_uuid = uuid.uuid4().hex[:8]
    video_paths = []
    for job in jobs:
        result = job.get("result")
        if result is None:
            continue
        for url in digit_video_common.extract_fal_video_urls(result):
            try:
                path = _download_to_temp(url, batch_timestamp, batch_uuid, job["index"])
                job["path"] = path
                video_paths.append(path)
                break
            except Exception as error:
                job["error"] = digit_video_common.format_api_error(error, provider="download")
    return video_paths


def generate_muapi(ctx: H3GenerationContext) -> H3GenerationResult:
    headers = muapi_client.auth_headers()
    endpoint = h3_models.muapi_endpoint(ctx.mode)
    temp_dir = folder_paths.get_temp_directory()
    os.makedirs(temp_dir, exist_ok=True)

    image_url = last_image_url = None
    reference_images = reference_videos = reference_audios = None

    if ctx.mode in ("image_to_video", "first_last_frame"):
        image_url = muapi_client.upload_image_tensor(headers, ctx.first_frame, label="first_frame")
        if ctx.mode == "first_last_frame":
            last_image_url = muapi_client.upload_image_tensor(
                headers, ctx.last_frame, label="last_frame"
            )
    elif ctx.mode == "reference_to_video":
        if ctx.ref_images:
            reference_images = [
                muapi_client.upload_image_tensor(headers, img, label=f"ref_image{i}")
                for i, img in enumerate(ctx.ref_images, start=1)
            ]
        if ctx.ref_videos:
            reference_videos = [
                muapi_client.upload_video(headers, video, temp_dir, label=f"ref_video{i}")
                for i, video in enumerate(ctx.ref_videos, start=1)
            ]
        if ctx.ref_audios:
            reference_audios = [
                muapi_client.upload_audio(headers, audio, temp_dir, label=f"ref_audio{i}")
                for i, audio in enumerate(ctx.ref_audios, start=1)
            ]

    payload = h3_payloads.build_muapi_payload(
        prompt=ctx.prompt,
        mode=ctx.mode,
        resolution=ctx.resolution,
        aspect_ratio=ctx.aspect_ratio,
        duration=ctx.duration,
        image_url=image_url,
        last_image_url=last_image_url,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
    )

    logger.info("%s Provider: muapi | Mode: %s | Endpoint: %s", LOG_PREFIX, ctx.mode, endpoint)
    jobs = _run_muapi_batch(headers, endpoint, payload, ctx.batch_count)
    video_paths = _collect_muapi_paths(jobs)
    if not video_paths:
        _raise_batch_failure(jobs, "MUAPI H3")
    return H3GenerationResult(video_paths, jobs, endpoint, payload)


def _run_muapi_batch(headers, endpoint, payload, batch_count):
    jobs = []
    pending = set()

    for index in range(batch_count):
        job = {"index": index, "attempt": 0, "request_id": None, "result": None, "error": ""}
        jobs.append(job)
        if _submit_muapi_job_with_retries(headers, endpoint, payload, job):
            pending.add(index)

    import comfy.utils
    pbar = comfy.utils.ProgressBar(len(jobs))
    completed_count = len(jobs) - len(pending)
    if completed_count:
        pbar.update_absolute(completed_count)

    while pending:
        _check_interrupted()
        for index in list(pending):
            job = jobs[index]
            try:
                result = muapi_client.poll_status(headers, job["request_id"])
            except Exception as error:
                if (
                    digit_video_common.should_retry_api_error(error)
                    and job["attempt"] <= MAX_AUTOMATIC_RETRIES
                ):
                    time.sleep(2 ** job["attempt"])
                    if _submit_muapi_job_with_retries(headers, endpoint, payload, job):
                        continue
                logger.warning("%s MUAPI poll failed job %d: %s", LOG_PREFIX, index + 1, error)
                continue

            status = str(result.get("status", "")).lower()
            if status == "completed":
                job["result"] = result
                pending.remove(index)
            elif status in muapi_client.TERMINAL_FAILURE_STATES:
                job["error"] = digit_video_common.format_api_error(
                    result.get("error") or f"Generation {status}.",
                    provider="muapi",
                )
                pending.remove(index)
            else:
                continue
            completed_count += 1
            pbar.update_absolute(completed_count)

        if pending:
            time.sleep(muapi_client.POLL_INTERVAL_SECONDS)

    return jobs


def _submit_muapi_job_with_retries(headers, endpoint, payload, job):
    while job["attempt"] <= MAX_AUTOMATIC_RETRIES:
        try:
            job["attempt"] += 1
            job["request_id"] = muapi_client.submit(headers, endpoint, payload)
            job["error"] = ""
            return True
        except Exception as error:
            job["error"] = digit_video_common.format_api_error(error, provider="muapi")
            if (
                not digit_video_common.should_retry_api_error(error)
                or job["attempt"] > MAX_AUTOMATIC_RETRIES
            ):
                return False
            time.sleep(2 ** (job["attempt"] - 1))
    return False


def _collect_muapi_paths(jobs):
    batch_timestamp = int(time.time())
    batch_uuid = uuid.uuid4().hex[:8]
    video_paths = []
    for job in jobs:
        result = job.get("result")
        if result is None:
            continue
        urls = muapi_client.extract_output_urls(result)
        if not urls:
            job["error"] = "Completed request returned no downloadable video."
            continue
        try:
            path = _download_to_temp(urls[0], batch_timestamp, batch_uuid, job["index"])
            job["path"] = path
            video_paths.append(path)
        except Exception as error:
            job["error"] = digit_video_common.format_api_error(error, provider="download")
    return video_paths


def generate_replicate(ctx: H3GenerationContext) -> H3GenerationResult:
    if not h3_models.REPLICATE_MODEL:
        raise RuntimeError(
            "MiniMax H3 is not published on Replicate yet. "
            "Use provider=fal or provider=muapi."
        )
    raise RuntimeError(
        f"Replicate model {h3_models.REPLICATE_MODEL} is configured but backend wiring "
        "is not implemented yet."
    )


def _raise_batch_failure(jobs, label):
    details = "; ".join(
        f"job {job['index'] + 1}: {job.get('error') or 'unknown failure'}" for job in jobs
    )
    raise RuntimeError(f"All {label} generations failed. {details}")


def format_job_status_lines(result: H3GenerationResult, ctx: H3GenerationContext) -> list[str]:
    lines = [
        "Model: MiniMax H3",
        f"Mode: {ctx.mode}",
        f"Route: {result.route}",
        f"Resolution: {ctx.resolution}",
        f"Duration: {ctx.duration}s",
        f"Videos generated: {len(result.video_paths)}/{len(result.jobs)}",
        f"Automatic retries: up to {MAX_AUTOMATIC_RETRIES} per job",
    ]
    if ctx.aspect_ratio and ctx.mode in ("text_to_video", "reference_to_video"):
        lines.insert(4, f"Aspect: {ctx.aspect_ratio}")
    for job in result.jobs:
        parts = [f"Job {job['index'] + 1}"]
        if job.get("request_ids"):
            parts.append(f"request_ids={','.join(job['request_ids'])}")
        if job.get("request_id"):
            parts.append(f"request_id={job['request_id']}")
        parts.append(f"attempts={job.get('attempt', 1)}")
        if job.get("path"):
            parts.append(f"path={job['path']}")
        else:
            parts.append(f"error={job.get('error') or 'unknown failure'}")
        lines.append(" | ".join(parts))
    return lines
