"""DIGIT MiniMax Video node — H3 across fal, MUAPI, and Replicate.

One node, one provider dropdown. Mode auto-detects from connected inputs:
- No image/reference inputs connected → text-to-video
- first_frame connected               → image-to-video
- first_frame + last_frame            → first/last-frame interpolation
- Any reference_image/video/audio     → reference-to-video

Providers:
- fal        (FAL_KEY)            — minimax/h3/* endpoints.
- muapi      (MUAPIAPP_API_KEY)   — minimax-h3-* endpoints (2K today).
- replicate  (REPLICATE_API_TOKEN) — not published yet (runtime error).

Cost estimates surface on the node via web/digit_h3_cost.js and the
/digit/h3/estimate route registered below.
"""

import io
import logging
import os
import time
import urllib.request
import uuid

import comfy.utils
import numpy as np
from PIL import Image as PILImage

import folder_paths

try:
    from . import h3_models, h3_pricing, muapi_client
except ImportError:  # standalone import (tests, linting)
    import h3_models
    import h3_pricing
    import muapi_client

logger = logging.getLogger("DigitH3Video")

PROVIDERS = h3_pricing.PROVIDERS
RESOLUTIONS = h3_models.RESOLUTIONS
ASPECT_RATIOS = h3_models.ASPECT_RATIOS
DURATIONS = h3_models.DURATIONS

MAX_REFERENCE_IMAGES = h3_models.MAX_REFERENCE_IMAGES
MAX_REFERENCE_VIDEOS = h3_models.MAX_REFERENCE_VIDEOS
MAX_REFERENCE_AUDIOS = h3_models.MAX_REFERENCE_AUDIOS
MAX_REFERENCE_FILES = h3_models.MAX_REFERENCE_FILES
MAX_BATCH_COUNT = h3_models.MAX_BATCH_COUNT
MAX_AUTOMATIC_RETRIES = 3
POLL_INTERVAL_SECONDS = 2.0

FAL_NO_RETRY_HEADERS = {"X-Fal-No-Retry": "1"}
PROVIDER_TOOLTIP = "\n".join(h3_pricing.PROVIDER_BLURBS.values())


def _is_content_policy_error(error):
    text = str(error).lower()
    return (
        "content_policy_violation" in text
        or "content policy" in text
        or "likenesses of real people" in text
    )


def _tensor_to_png_bytes(tensor):
    img_np = tensor.cpu().numpy()
    img_np = (img_np * 255).clip(0, 255).astype(np.uint8)
    img = PILImage.fromarray(img_np)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_image_tensor(fal_client, image_tensor):
    png_bytes = _tensor_to_png_bytes(image_tensor[0])
    return fal_client.upload(png_bytes, content_type="image/png")


def _upload_video(fal_client, video_obj):
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
    return fal_client.upload_file(tmp_path)


def _upload_audio(fal_client, audio_obj):
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
    return fal_client.upload_file(tmp_path)


class DigitH3Video:
    CATEGORY = "DIGIT"
    RETURN_TYPES = ("VIDEO", "VIDEO_PATHS", "STRING")
    RETURN_NAMES = ("video", "video_paths", "status")
    FUNCTION = "generate"

    @classmethod
    def INPUT_TYPES(cls):
        ref_image_sockets = {
            f"reference_image{i}": ("IMAGE",) for i in range(1, MAX_REFERENCE_IMAGES + 1)
        }
        ref_video_sockets = {
            f"reference_video{i}": ("VIDEO",) for i in range(1, MAX_REFERENCE_VIDEOS + 1)
        }
        ref_audio_sockets = {
            f"reference_audio{i}": ("AUDIO",) for i in range(1, MAX_REFERENCE_AUDIOS + 1)
        }

        return {
            "required": {
                "prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": (
                        "Describe the video. In reference mode, cite Image 1, Video 1, Audio 1."
                    ),
                }),
                "provider": (PROVIDERS, {
                    "default": "fal",
                    "tooltip": PROVIDER_TOOLTIP,
                }),
                "resolution": (RESOLUTIONS, {
                    "default": "2K",
                    "tooltip": "MUAPI currently supports 2K only. fal supports 768P, 2K, and 4K.",
                }),
                "aspect_ratio": (ASPECT_RATIOS, {
                    "default": "16:9",
                    "tooltip": (
                        "T2V requires a fixed ratio (no adaptive). "
                        "I2V/FLF follow the source image. R2V supports adaptive."
                    ),
                }),
                "duration": (DURATIONS, {
                    "default": "5",
                    "tooltip": "Output length in seconds (4-15). Billed per second on fal.",
                }),
                "batch_count": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": MAX_BATCH_COUNT,
                    "tooltip": "Submits this many generations before polling.",
                }),
                "enable_prompt_expansion": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "fal only. Expand the prompt with a vision language model.",
                }),
                "enable_safety_checker": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "fal only. Enable fal's content safety checker.",
                }),
            },
            "optional": {
                "first_frame": ("IMAGE", {
                    "tooltip": "Image-to-video mode. Mutually exclusive with reference inputs.",
                }),
                "last_frame": ("IMAGE", {
                    "tooltip": "Optional end frame for first-to-last interpolation.",
                }),
                **ref_image_sockets,
                **ref_video_sockets,
                **ref_audio_sockets,
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def generate(
        self,
        prompt,
        provider,
        resolution,
        aspect_ratio,
        duration,
        batch_count,
        enable_prompt_expansion,
        enable_safety_checker,
        first_frame=None,
        last_frame=None,
        **kwargs,
    ):
        if not prompt or not prompt.strip():
            raise ValueError("Prompt is required.")

        ref_images = [kwargs.get(f"reference_image{i}") for i in range(1, MAX_REFERENCE_IMAGES + 1)]
        ref_images = [img for img in ref_images if img is not None]

        ref_videos = [kwargs.get(f"reference_video{i}") for i in range(1, MAX_REFERENCE_VIDEOS + 1)]
        ref_videos = [video for video in ref_videos if video is not None]

        ref_audios = [kwargs.get(f"reference_audio{i}") for i in range(1, MAX_REFERENCE_AUDIOS + 1)]
        ref_audios = [audio for audio in ref_audios if audio is not None]

        has_refs = bool(ref_images or ref_videos or ref_audios)
        has_first_frame = first_frame is not None
        has_last_frame = last_frame is not None

        if has_refs and (has_first_frame or has_last_frame):
            raise ValueError(
                "Cannot combine first_frame/last_frame with reference inputs. "
                "Use image-to-video mode OR reference-to-video mode, not both."
            )
        if ref_audios and not (ref_images or ref_videos):
            raise ValueError(
                "reference_audio requires at least one reference_image or reference_video."
            )
        reference_count = len(ref_images) + len(ref_videos) + len(ref_audios)
        if reference_count > MAX_REFERENCE_FILES:
            raise ValueError(
                f"MiniMax H3 accepts at most {MAX_REFERENCE_FILES} reference files total; "
                f"{reference_count} are connected."
            )
        if has_last_frame and not has_first_frame:
            raise ValueError("last_frame requires first_frame to be connected.")

        if has_refs:
            mode = "reference_to_video"
        elif has_first_frame and has_last_frame:
            mode = "first_last_frame"
        elif has_first_frame:
            mode = "image_to_video"
        else:
            mode = "text_to_video"

        if not h3_models.provider_supports_resolution(provider, resolution):
            if provider == "muapi":
                raise ValueError(
                    f"MUAPI H3 currently supports {', '.join(sorted(h3_models.MUAPI_RESOLUTIONS))} only. "
                    f"Got resolution '{resolution}'."
                )
            raise ValueError(
                f"Provider '{provider}' does not support resolution '{resolution}'."
            )

        if mode == "text_to_video" and aspect_ratio == "adaptive":
            raise ValueError(
                "aspect_ratio 'adaptive' is not supported for text-to-video. "
                "Pick a fixed ratio such as 16:9."
            )
        if mode in ("image_to_video", "first_last_frame") and aspect_ratio != "16:9":
            logger.info(
                "[DigitH3] aspect_ratio '%s' ignored in %s; output follows the source image.",
                aspect_ratio,
                mode,
            )

        duration_seconds = self._duration_int(duration)

        common = {
            "prompt": prompt,
            "mode": mode,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "duration": duration_seconds,
            "batch_count": int(batch_count),
            "enable_prompt_expansion": enable_prompt_expansion,
            "enable_safety_checker": enable_safety_checker,
            "first_frame": first_frame,
            "last_frame": last_frame,
            "ref_images": ref_images,
            "ref_videos": ref_videos,
            "ref_audios": ref_audios,
        }

        if provider == "fal":
            return self._generate_fal(**common)
        if provider == "muapi":
            return self._generate_muapi(**common)
        if provider == "replicate":
            return self._generate_replicate(**common)
        raise ValueError(f"Unknown provider: {provider}")

    @staticmethod
    def _duration_int(duration, default=5):
        try:
            value = int(duration)
            return value if value > 0 else default
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # fal backend
    # ------------------------------------------------------------------

    def _generate_fal(
        self,
        prompt,
        mode,
        resolution,
        aspect_ratio,
        duration,
        batch_count,
        enable_prompt_expansion,
        enable_safety_checker,
        first_frame,
        last_frame,
        ref_images,
        ref_videos,
        ref_audios,
    ):
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

        app_id = h3_models.fal_app_id(mode)
        logger.info("[DigitH3] Provider: fal | Mode: %s | App: %s", mode, app_id)

        args = {
            "prompt": prompt.strip(),
            "duration": int(duration),
            "resolution": resolution,
            "enable_prompt_expansion": bool(enable_prompt_expansion),
            "enable_safety_checker": bool(enable_safety_checker),
        }

        if mode == "text_to_video":
            args["aspect_ratio"] = aspect_ratio
        elif mode in ("image_to_video", "first_last_frame"):
            args["image_url"] = _upload_image_tensor(fal_client, first_frame)
            if mode == "first_last_frame":
                args["end_image_url"] = _upload_image_tensor(fal_client, last_frame)
        elif mode == "reference_to_video":
            args["aspect_ratio"] = aspect_ratio
            if ref_images:
                args["reference_image_urls"] = [
                    _upload_image_tensor(fal_client, img) for img in ref_images
                ]
            if ref_videos:
                args["reference_video_urls"] = [
                    _upload_video(fal_client, video) for video in ref_videos
                ]
            if ref_audios:
                args["reference_audio_urls"] = [
                    _upload_audio(fal_client, audio) for audio in ref_audios
                ]

        jobs = self._run_fal_batch(fal_client, app_id, args, int(batch_count))

        batch_timestamp = int(time.time())
        batch_uuid = uuid.uuid4().hex[:8]
        video_paths = []
        for job in jobs:
            if job.get("result") is None:
                continue
            paths = self._download_results(
                job["result"],
                batch_timestamp,
                batch_uuid,
                job["index"],
            )
            if paths:
                job["path"] = paths[0]
                video_paths.append(paths[0])
            else:
                job["error"] = "Completed request returned no downloadable video."

        if not video_paths:
            details = "; ".join(
                f"job {job['index'] + 1}: {job.get('error', 'unknown failure')}"
                for job in jobs
            )
            raise RuntimeError(f"All H3 batch generations failed. {details}")

        from comfy_api.latest._input_impl.video_types import VideoFromFile

        video_output = VideoFromFile(video_paths[0])
        status = self._format_fal_status(mode, app_id, args, jobs, video_paths)
        cost_summary = h3_pricing.estimate(
            "fal",
            mode,
            resolution,
            duration,
            len(video_paths),
            has_video_refs=bool(ref_videos),
            ref_image_count=len(ref_images),
            use_live=False,
        )
        status = "\n".join(h3_pricing.format_status_lines(cost_summary) + [status])
        return (video_output, video_paths, status)

    def _run_fal_batch(self, fal_client, app_id, shared_args, batch_count):
        jobs = []
        pending = set()
        try:
            for index in range(batch_count):
                job = {
                    "index": index,
                    "attempt": 0,
                    "request_ids": [],
                    "result": None,
                    "error": "",
                }
                jobs.append(job)
                if self._submit_fal_job_with_retries(fal_client, app_id, shared_args, job):
                    pending.add(index)

            pbar = comfy.utils.ProgressBar(len(jobs))
            completed_count = len(jobs) - len(pending)
            if completed_count:
                pbar.update_absolute(completed_count)

            while pending:
                from comfy.model_management import throw_exception_if_processing_interrupted
                throw_exception_if_processing_interrupted()

                for index in list(pending):
                    job = jobs[index]
                    try:
                        status = job["handle"].status(with_logs=False)
                    except Exception as error:
                        logger.warning(
                            "[DigitH3] Status check failed for job %d: %s",
                            index + 1,
                            error,
                        )
                        continue

                    if not isinstance(status, fal_client.Completed):
                        continue

                    try:
                        job["result"] = job["handle"].get()
                        pending.remove(index)
                    except Exception as error:
                        if self._should_retry(error) and job["attempt"] <= MAX_AUTOMATIC_RETRIES:
                            delay = 2 ** (job["attempt"] - 1)
                            logger.warning(
                                "[DigitH3] Job %d failed on attempt %d; retrying in %ds: %s",
                                index + 1,
                                job["attempt"],
                                delay,
                                error,
                            )
                            time.sleep(delay)
                            if self._submit_fal_job_with_retries(
                                fal_client, app_id, shared_args, job
                            ):
                                continue

                        job["error"] = self._format_error(error)
                        pending.remove(index)

                    completed_count += 1
                    pbar.update_absolute(completed_count)

                if pending:
                    time.sleep(POLL_INTERVAL_SECONDS)
        except BaseException:
            self._cancel_fal_jobs(jobs, pending)
            raise

        return jobs

    def _submit_fal_job_with_retries(self, fal_client, app_id, shared_args, job):
        while job["attempt"] <= MAX_AUTOMATIC_RETRIES:
            try:
                self._submit_fal_job(fal_client, app_id, shared_args, job)
                job["error"] = ""
                return True
            except Exception as error:
                job.pop("handle", None)
                job["error"] = self._format_error(error)
                if (
                    not self._should_retry(error)
                    or job["attempt"] > MAX_AUTOMATIC_RETRIES
                ):
                    logger.error(
                        "[DigitH3] Job %d submission failed after %d attempt(s): %s",
                        job["index"] + 1,
                        job["attempt"],
                        error,
                    )
                    return False

                delay = 2 ** (job["attempt"] - 1)
                logger.warning(
                    "[DigitH3] Job %d submission failed on attempt %d; retrying in %ds: %s",
                    job["index"] + 1,
                    job["attempt"],
                    delay,
                    error,
                )
                time.sleep(delay)

        return False

    @staticmethod
    def _submit_fal_job(fal_client, app_id, shared_args, job):
        job["attempt"] += 1
        logger.info(
            "[DigitH3] Submitting job %d, attempt %d to %s...",
            job["index"] + 1,
            job["attempt"],
            app_id,
        )
        handle = fal_client.submit(
            app_id,
            arguments=dict(shared_args),
            headers=FAL_NO_RETRY_HEADERS,
        )
        job["handle"] = handle
        job["request_ids"].append(handle.request_id)

    @staticmethod
    def _should_retry(error):
        if _is_content_policy_error(error):
            return False
        text = str(error).lower()
        status_code = getattr(error, "status_code", None)
        if status_code in {429, 500, 502, 503, 504}:
            return True
        if isinstance(status_code, int) and 400 <= status_code < 500:
            return False
        non_retryable = ("400", "401", "403", "404", "422", "invalid", "validation")
        if any(marker in text for marker in non_retryable):
            return False
        retryable = (
            "429", "500", "502", "503", "504", "rate", "timeout",
            "connection", "temporarily", "unavailable", "internal server",
            "server error", "gateway",
        )
        return any(marker in text for marker in retryable)

    @staticmethod
    def _format_error(error):
        if _is_content_policy_error(error):
            return f"Blocked by fal content policy: {error}"
        return str(error)

    @staticmethod
    def _cancel_fal_jobs(jobs, pending):
        for index in pending:
            handle = jobs[index].get("handle")
            if handle is None:
                continue
            try:
                handle.cancel()
                logger.info("[DigitH3] Cancelled request %s", handle.request_id)
            except Exception as error:
                logger.warning(
                    "[DigitH3] Could not cancel request %s: %s",
                    getattr(handle, "request_id", "unknown"),
                    error,
                )

    def _download_results(self, result, batch_timestamp, batch_uuid, job_index):
        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)

        video_items = []
        if isinstance(result, dict):
            if "videos" in result and isinstance(result["videos"], list):
                video_items = result["videos"]
            elif "video" in result:
                video_items = [result["video"]]

        if not video_items:
            logger.error("[DigitH3] Could not extract video URLs from result: %s", result)
            return []

        paths = []
        for item in video_items:
            url = item.get("url") if isinstance(item, dict) else item
            if not url:
                continue
            local_path = os.path.join(
                temp_dir,
                f"h3_{batch_timestamp}_{batch_uuid}_{job_index}.mp4",
            )
            try:
                urllib.request.urlretrieve(url, local_path)
                paths.append(local_path)
                logger.info("[DigitH3] Downloaded video: %s", local_path)
            except Exception as error:
                logger.error("[DigitH3] Failed to download %s: %s", url, error)
        return paths

    def _format_fal_status(self, mode, app_id, args, jobs, video_paths):
        lines = [
            f"Model: MiniMax H3",
            f"Mode: {mode}",
            f"App: {app_id}",
            f"Resolution: {args.get('resolution')}",
            f"Duration: {args.get('duration')}s",
            f"Videos generated: {len(video_paths)}/{len(jobs)}",
            f"Automatic retries: up to {MAX_AUTOMATIC_RETRIES} per job",
        ]
        if "aspect_ratio" in args:
            lines.insert(4, f"Aspect: {args.get('aspect_ratio')}")
        for job in jobs:
            summary = [
                f"Job {job['index'] + 1}",
                f"attempts={job['attempt']}",
                f"request_ids={','.join(job['request_ids'])}",
            ]
            if job.get("path"):
                summary.append(f"path={job['path']}")
            else:
                summary.append(f"error={job.get('error') or 'unknown failure'}")
            lines.append(" | ".join(summary))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # MUAPI backend
    # ------------------------------------------------------------------

    def _generate_muapi(
        self,
        prompt,
        mode,
        resolution,
        aspect_ratio,
        duration,
        batch_count,
        enable_prompt_expansion,
        enable_safety_checker,
        first_frame,
        last_frame,
        ref_images,
        ref_videos,
        ref_audios,
    ):
        del enable_prompt_expansion, enable_safety_checker

        headers = muapi_client.auth_headers()
        endpoint = h3_models.muapi_endpoint(mode)
        logger.info("[DigitH3] Provider: muapi | Mode: %s | Endpoint: %s", mode, endpoint)

        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)

        payload = {
            "prompt": prompt.strip(),
            "duration": int(duration),
            "resolution": h3_models.muapi_resolution(resolution),
        }

        if mode == "text_to_video":
            payload["aspect_ratio"] = aspect_ratio
        elif mode == "reference_to_video":
            payload["aspect_ratio"] = aspect_ratio

        if mode in ("image_to_video", "first_last_frame"):
            payload["image_url"] = muapi_client.upload_image_tensor(
                headers, first_frame, label="first_frame"
            )
            if mode == "first_last_frame":
                payload["last_image_url"] = muapi_client.upload_image_tensor(
                    headers, last_frame, label="last_frame"
                )
        elif mode == "reference_to_video":
            if ref_images:
                payload["reference_images"] = [
                    muapi_client.upload_image_tensor(headers, img, label=f"ref_image{i}")
                    for i, img in enumerate(ref_images, start=1)
                ]
            if ref_videos:
                payload["reference_videos"] = [
                    muapi_client.upload_video(headers, video, temp_dir, label=f"ref_video{i}")
                    for i, video in enumerate(ref_videos, start=1)
                ]
            if ref_audios:
                payload["reference_audios"] = [
                    muapi_client.upload_audio(headers, audio, temp_dir, label=f"ref_audio{i}")
                    for i, audio in enumerate(ref_audios, start=1)
                ]

        jobs = self._run_muapi_batch(headers, endpoint, payload, int(batch_count))

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
            local_path = os.path.join(
                temp_dir,
                f"h3_{batch_timestamp}_{batch_uuid}_{job['index']}.mp4",
            )
            try:
                urllib.request.urlretrieve(urls[0], local_path)
                job["path"] = local_path
                video_paths.append(local_path)
                logger.info("[DigitH3] Downloaded muapi video: %s", local_path)
            except Exception as error:
                job["error"] = f"Download failed: {error}"
                logger.error("[DigitH3] Failed to download %s: %s", urls[0], error)

        if not video_paths:
            details = "; ".join(
                f"job {job['index'] + 1}: {job.get('error', 'unknown failure')}"
                for job in jobs
            )
            raise RuntimeError(f"All MUAPI H3 generations failed. {details}")

        from comfy_api.latest._input_impl.video_types import VideoFromFile

        video_output = VideoFromFile(video_paths[0])
        cost_summary = h3_pricing.estimate(
            "muapi",
            mode,
            resolution,
            duration,
            len(video_paths),
            has_video_refs=bool(ref_videos),
            ref_image_count=len(ref_images),
            use_live=False,
        )
        lines = h3_pricing.format_status_lines(cost_summary)
        lines += [
            f"Mode: {mode}",
            f"Endpoint: {endpoint}",
            f"Resolution: {resolution}",
            f"Duration: {duration}s",
            f"Videos generated: {len(video_paths)}/{len(jobs)}",
        ]
        for job in jobs:
            summary = [
                f"Job {job['index'] + 1}",
                f"request_id={job.get('request_id', 'n/a')}",
            ]
            if job.get("path"):
                summary.append(f"path={job['path']}")
            else:
                summary.append(f"error={job.get('error') or 'unknown failure'}")
            lines.append(" | ".join(summary))

        return (video_output, video_paths, "\n".join(lines))

    def _run_muapi_batch(self, headers, endpoint, payload, batch_count):
        jobs = []
        pending = set()

        for index in range(batch_count):
            job = {"index": index, "request_id": None, "result": None, "error": ""}
            jobs.append(job)
            try:
                job["request_id"] = muapi_client.submit(headers, endpoint, payload)
                pending.add(index)
                logger.info(
                    "[DigitH3] Submitted MUAPI job %d: %s",
                    index + 1,
                    job["request_id"],
                )
            except Exception as error:
                job["error"] = str(error)
                logger.error("[DigitH3] MUAPI submission failed for job %d: %s", index + 1, error)

        pbar = comfy.utils.ProgressBar(len(jobs))
        completed_count = len(jobs) - len(pending)
        if completed_count:
            pbar.update_absolute(completed_count)

        while pending:
            from comfy.model_management import throw_exception_if_processing_interrupted
            throw_exception_if_processing_interrupted()

            for index in list(pending):
                job = jobs[index]
                try:
                    result = muapi_client.poll_status(headers, job["request_id"])
                except Exception as error:
                    logger.warning(
                        "[DigitH3] MUAPI poll failed for job %d: %s",
                        index + 1,
                        error,
                    )
                    continue

                status = str(result.get("status", "")).lower()
                if status == "completed":
                    job["result"] = result
                elif status in muapi_client.TERMINAL_FAILURE_STATES:
                    job["error"] = str(result.get("error") or f"Generation {status}.")
                else:
                    continue

                pending.remove(index)
                completed_count += 1
                pbar.update_absolute(completed_count)

            if pending:
                time.sleep(muapi_client.POLL_INTERVAL_SECONDS)

        return jobs

    # ------------------------------------------------------------------
    # Replicate backend (deferred)
    # ------------------------------------------------------------------

    def _generate_replicate(self, **_kwargs):
        raise RuntimeError(
            "MiniMax H3 is not published on Replicate yet. "
            "Use provider=fal or provider=muapi."
        )


# ---------------------------------------------------------------------------
# Cost-estimate route for the node's live summary strip (web/digit_h3_cost.js).
# ---------------------------------------------------------------------------
try:
    from server import PromptServer
    from aiohttp import web

    @PromptServer.instance.routes.post("/digit/h3/estimate")
    async def _digit_h3_estimate(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        provider = body.get("provider", "fal")
        mode = body.get("mode", "text_to_video")
        resolution = body.get("resolution", "2K")
        duration = body.get("duration", "5")
        batch_count = body.get("batch_count", 1)
        has_video_refs = bool(body.get("has_video_refs", False))
        ref_image_count = int(body.get("ref_image_count", 0) or 0)

        try:
            duration_seconds = max(1, int(duration))
        except (TypeError, ValueError):
            duration_seconds = 5

        import asyncio

        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(
            None,
            lambda: h3_pricing.estimate(
                provider,
                mode,
                resolution,
                duration_seconds,
                batch_count,
                has_video_refs=has_video_refs,
                ref_image_count=ref_image_count,
                use_live=True,
            ),
        )
        return web.json_response({"range": False, "summary": summary})

except Exception:
    pass
