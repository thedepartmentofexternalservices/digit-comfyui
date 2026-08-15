"""DIGIT MiniMax Video node — MiniMax H3 across fal and MUAPI.

One node, one provider dropdown. Mode auto-detects from connected inputs:
- No image/reference inputs connected → text-to-video
- first_frame connected               → image-to-video
- first_frame + last_frame            → first/last-frame interpolation
- Any reference_image/video/audio     → reference-to-video

Providers:
    fal     (FAL_KEY)          — strict filtering; 480P/768P native, 2K/4K upscale.
    muapi   (MUAPIAPP_API_KEY) — low/reduced filtering; hosted H3 is 2K only.

H3 always writes native stereo audio. Cite references in the prompt as
Image 1, Video 1, Audio 1.

Cost estimates surface on the node via web/digit_minimax_cost.js and the
/digit/minimax/estimate route registered below.
"""

import io
import logging
import os
import random
import time
import urllib.request
import uuid

import comfy.utils
import folder_paths
import numpy as np
from PIL import Image as PILImage

try:
    from . import minimax_pricing, muapi_client
except ImportError:  # standalone import (tests, linting)
    import minimax_pricing
    import muapi_client

logger = logging.getLogger("DigitMiniMaxVideo")


PROVIDERS = minimax_pricing.PROVIDERS
RESOLUTIONS = list(minimax_pricing.RESOLUTIONS)
ASPECT_RATIOS = ["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]
DEFAULT_ASPECT = "16:9"
DURATIONS = [str(seconds) for seconds in range(5, 16)]

MAX_REFERENCE_IMAGES = 9
MAX_REFERENCE_VIDEOS = 3
MAX_REFERENCE_AUDIOS = 3
MAX_REFERENCE_FILES = 12
MAX_BATCH_COUNT = 8
MAX_AUTOMATIC_RETRIES = 3
POLL_INTERVAL_SECONDS = 2.0
MAX_SEED = 2147483647

FAL_NO_RETRY_HEADERS = {"X-Fal-No-Retry": "1"}

PROVIDER_TOOLTIP = "\n".join(minimax_pricing.PROVIDER_BLURBS.values())


def detect_mode(has_refs, has_first_frame, has_last_frame):
    if has_refs:
        return "reference_to_video"
    if has_first_frame and has_last_frame:
        return "first_last_frame"
    if has_first_frame:
        return "image_to_video"
    return "text_to_video"


def validate_generation_inputs(
    prompt, first_frame, last_frame, ref_images, ref_videos, ref_audios,
):
    if not prompt or not prompt.strip():
        raise ValueError("Prompt is required.")

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

    return detect_mode(has_refs, has_first_frame, has_last_frame)


def aspect_for_payload(mode, aspect_ratio, provider):
    """Return the aspect string to send, or None to omit (I2V/FLF follow the frame)."""
    if mode in ("image_to_video", "first_last_frame"):
        return None
    ratio = aspect_ratio or DEFAULT_ASPECT
    if mode == "text_to_video" and ratio == "adaptive":
        return DEFAULT_ASPECT
    if mode == "reference_to_video" and provider == "muapi" and ratio == "adaptive":
        return DEFAULT_ASPECT
    return ratio


def duration_int(duration, default=5):
    try:
        value = int(duration)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def build_fal_arguments(
    prompt, mode, resolution, aspect_ratio, duration,
    enable_prompt_expansion, enable_safety_checker,
    image_url=None, end_image_url=None,
    reference_image_urls=None, reference_video_urls=None, reference_audio_urls=None,
):
    args = {
        "prompt": prompt.strip(),
        "resolution": resolution,
        "duration": duration_int(duration),
        "enable_prompt_expansion": bool(enable_prompt_expansion),
        "enable_safety_checker": bool(enable_safety_checker),
    }
    aspect = aspect_for_payload(mode, aspect_ratio, "fal")
    if aspect is not None:
        args["aspect_ratio"] = aspect
    if mode in ("image_to_video", "first_last_frame"):
        if image_url:
            args["image_url"] = image_url
        if end_image_url:
            args["end_image_url"] = end_image_url
    elif mode == "reference_to_video":
        if reference_image_urls:
            args["reference_image_urls"] = list(reference_image_urls)
        if reference_video_urls:
            args["reference_video_urls"] = list(reference_video_urls)
        if reference_audio_urls:
            args["reference_audio_urls"] = list(reference_audio_urls)
    return args


def build_muapi_payload(
    prompt, mode, aspect_ratio, duration,
    image_url=None, last_image_url=None,
    reference_images=None, reference_videos=None, reference_audios=None,
):
    payload = {
        "prompt": prompt.strip(),
        "duration": duration_int(duration),
        "resolution": minimax_pricing.MUAPI_RESOLUTION_PARAM,
    }
    aspect = aspect_for_payload(mode, aspect_ratio, "muapi")
    if aspect is not None:
        payload["aspect_ratio"] = aspect
    if mode in ("image_to_video", "first_last_frame"):
        if image_url:
            payload["image_url"] = image_url
        if last_image_url:
            payload["last_image_url"] = last_image_url
    elif mode == "reference_to_video":
        if reference_images:
            payload["reference_images"] = list(reference_images)
        if reference_videos:
            payload["reference_videos"] = list(reference_videos)
        if reference_audios:
            payload["reference_audios"] = list(reference_audios)
    return payload


def _is_content_policy_error(error):
    """True when fal rejected the request on content policy (422), not transient."""
    text = str(error).lower()
    return (
        "content_policy_violation" in text
        or "content policy" in text
        or "likenesses of real people" in text
    )


def _tensor_to_png_bytes(tensor):
    """Convert a single (H,W,C) float32 0-1 image tensor to PNG bytes."""
    img_np = tensor.cpu().numpy()
    img_np = (img_np * 255).clip(0, 255).astype(np.uint8)
    img = PILImage.fromarray(img_np)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_image_tensor(fal_client, image_tensor):
    """Upload the first frame of a ComfyUI IMAGE batch to fal storage, return URL."""
    png_bytes = _tensor_to_png_bytes(image_tensor[0])
    return fal_client.upload(png_bytes, content_type="image/png")


def _upload_video(fal_client, video_obj):
    """Upload a ComfyUI VIDEO object to fal storage and return its URL."""
    try:
        source = video_obj.get_stream_source()
        if isinstance(source, str) and os.path.isfile(source):
            return fal_client.upload_file(source)
    except Exception:
        pass

    temp_dir = folder_paths.get_temp_directory()
    os.makedirs(temp_dir, exist_ok=True)
    tmp_path = os.path.join(temp_dir, f"minimax_upload_{uuid.uuid4().hex[:8]}.mp4")
    video_obj.save_to(tmp_path)
    return fal_client.upload_file(tmp_path)


def _upload_audio(fal_client, audio_obj):
    """Upload ComfyUI AUDIO dict ({'waveform', 'sample_rate'}) to fal as WAV."""
    try:
        import soundfile as sf
    except ImportError:
        raise ImportError(
            "soundfile is required for reference_audio inputs. "
            "Install with: pip install soundfile"
        )

    waveform = audio_obj["waveform"]  # (B, C, N)
    sample_rate = audio_obj["sample_rate"]
    wav = waveform[0].cpu().numpy().T

    temp_dir = folder_paths.get_temp_directory()
    os.makedirs(temp_dir, exist_ok=True)
    tmp_path = os.path.join(temp_dir, f"minimax_audio_{uuid.uuid4().hex[:8]}.wav")
    sf.write(tmp_path, wav, sample_rate)
    return fal_client.upload_file(tmp_path)


class DigitMiniMaxVideo:
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
                        "Describe the video. In reference mode, cite Image 1, "
                        "Video 1, and Audio 1 by order."
                    ),
                }),
                "provider": (PROVIDERS, {
                    "default": "fal",
                    "tooltip": PROVIDER_TOOLTIP,
                }),
                "resolution": (RESOLUTIONS, {
                    "default": "2K",
                    "tooltip": (
                        "Cost driver #1. fal: 480P/768P native, 2K/4K upscale a 768P base. "
                        "muapi hosted H3 is 2K only."
                    ),
                }),
                "aspect_ratio": (ASPECT_RATIOS, {
                    "default": "16:9",
                    "tooltip": (
                        "T2V: pick a ratio (adaptive becomes 16:9). "
                        "I2V/FLF: ignored; output follows the first frame. "
                        "R2V: adaptive is fal-only; muapi maps it to 16:9."
                    ),
                }),
                "duration": (DURATIONS, {
                    "default": "5",
                    "tooltip": "Cost driver #2 — billed per second. 5–15s.",
                }),
                "enable_prompt_expansion": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "fal only. Expand the prompt with a vision language model before generation.",
                }),
                "enable_safety_checker": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "fal only. Leave on unless you know the prompt will trip the checker.",
                }),
                "batch_count": ("INT", {
                    "default": 4,
                    "min": 1,
                    "max": MAX_BATCH_COUNT,
                    "tooltip": "Cost driver #3 — you pay per clip. Submits this many generations before polling.",
                }),
                "seed": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": MAX_SEED,
                    "tooltip": (
                        "0 creates distinct random seeds. A positive value is the first "
                        "seed in a consecutive batch. (fal only; muapi has no seed input.)"
                    ),
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
        seed = kwargs.get("seed", 0)
        if seed == 0:
            return float("nan")
        return seed

    def generate(self, prompt, provider, resolution, aspect_ratio, duration,
                 enable_prompt_expansion, enable_safety_checker, batch_count, seed,
                 first_frame=None, last_frame=None, **kwargs):
        ref_images = [
            kwargs.get(f"reference_image{i}")
            for i in range(1, MAX_REFERENCE_IMAGES + 1)
        ]
        ref_images = [img for img in ref_images if img is not None]

        ref_videos = [
            kwargs.get(f"reference_video{i}")
            for i in range(1, MAX_REFERENCE_VIDEOS + 1)
        ]
        ref_videos = [v for v in ref_videos if v is not None]

        ref_audios = [
            kwargs.get(f"reference_audio{i}")
            for i in range(1, MAX_REFERENCE_AUDIOS + 1)
        ]
        ref_audios = [a for a in ref_audios if a is not None]

        mode = validate_generation_inputs(
            prompt, first_frame, last_frame, ref_images, ref_videos, ref_audios,
        )

        common = {
            "prompt": prompt,
            "mode": mode,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "duration": duration,
            "batch_count": int(batch_count),
            "seed": seed,
            "first_frame": first_frame,
            "last_frame": last_frame,
            "ref_images": ref_images,
            "ref_videos": ref_videos,
            "ref_audios": ref_audios,
        }

        if provider == "fal":
            return self._generate_fal(
                enable_prompt_expansion=enable_prompt_expansion,
                enable_safety_checker=enable_safety_checker,
                **common,
            )
        if provider == "muapi":
            return self._generate_muapi(**common)
        raise ValueError(f"Unknown provider: {provider}")

    # ------------------------------------------------------------------
    # fal backend
    # ------------------------------------------------------------------

    def _generate_fal(self, prompt, mode, resolution, aspect_ratio, duration,
                      batch_count, seed, first_frame, last_frame,
                      ref_images, ref_videos, ref_audios,
                      enable_prompt_expansion, enable_safety_checker):
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
                "Export FAL_KEY=<your-key> in the environment before starting ComfyUI."
            )

        app_id = minimax_pricing.fal_app_for_mode(mode)
        logger.info("[DigitMiniMax] Provider: fal | Mode: %s | App: %s", mode, app_id)

        image_url = None
        end_image_url = None
        reference_image_urls = None
        reference_video_urls = None
        reference_audio_urls = None

        if mode in ("image_to_video", "first_last_frame"):
            image_url = _upload_image_tensor(fal_client, first_frame)
            if last_frame is not None:
                end_image_url = _upload_image_tensor(fal_client, last_frame)
        elif mode == "reference_to_video":
            if ref_images:
                reference_image_urls = [
                    _upload_image_tensor(fal_client, img) for img in ref_images
                ]
            if ref_videos:
                reference_video_urls = [
                    _upload_video(fal_client, v) for v in ref_videos
                ]
            if ref_audios:
                reference_audio_urls = [
                    _upload_audio(fal_client, a) for a in ref_audios
                ]

        args = build_fal_arguments(
            prompt, mode, resolution, aspect_ratio, duration,
            enable_prompt_expansion, enable_safety_checker,
            image_url=image_url, end_image_url=end_image_url,
            reference_image_urls=reference_image_urls,
            reference_video_urls=reference_video_urls,
            reference_audio_urls=reference_audio_urls,
        )

        seeds = self._build_seeds(seed, int(batch_count))
        jobs = self._run_batch(fal_client, app_id, args, seeds)

        batch_timestamp = int(time.time())
        batch_uuid = uuid.uuid4().hex[:8]
        video_paths = []
        for job in jobs:
            if job.get("result") is None:
                continue
            paths = self._download_results(
                job["result"], batch_timestamp, batch_uuid, job["index"],
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
            raise RuntimeError(f"All MiniMax H3 batch generations failed. {details}")

        from comfy_api.latest._input_impl.video_types import VideoFromFile
        video_output = VideoFromFile(video_paths[0])

        status = self._format_batch_status(mode, args, jobs, video_paths)
        cost_summary = minimax_pricing.estimate(
            "fal", mode, resolution, duration_int(duration),
            len(video_paths), use_live=False,
        )
        status = "\n".join(
            minimax_pricing.format_status_lines(cost_summary) + [status]
        )
        return (video_output, video_paths, status)

    @staticmethod
    def _build_seeds(base_seed, batch_count):
        if base_seed > 0:
            return [((base_seed - 1 + index) % MAX_SEED) + 1 for index in range(batch_count)]
        return random.SystemRandom().sample(range(1, MAX_SEED + 1), batch_count)

    def _run_batch(self, fal_client, app_id, shared_args, seeds):
        jobs = []
        pending = set()
        try:
            for index, job_seed in enumerate(seeds):
                job = {
                    "index": index,
                    "seed": job_seed,
                    "attempt": 0,
                    "request_ids": [],
                    "result": None,
                    "error": "",
                }
                jobs.append(job)
                if self._submit_job_with_retries(
                    fal_client, app_id, shared_args, job
                ):
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
                            "[DigitMiniMax] Status check failed for job %d: %s",
                            index + 1, error,
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
                                "[DigitMiniMax] Job %d failed on attempt %d; retrying in %ds: %s",
                                index + 1, job["attempt"], delay, error,
                            )
                            time.sleep(delay)
                            if self._submit_job_with_retries(
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
            self._cancel_jobs(jobs, pending)
            raise

        return jobs

    def _submit_job_with_retries(self, fal_client, app_id, shared_args, job):
        while job["attempt"] <= MAX_AUTOMATIC_RETRIES:
            try:
                self._submit_job(fal_client, app_id, shared_args, job)
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
                        "[DigitMiniMax] Job %d submission failed after %d attempt(s): %s",
                        job["index"] + 1, job["attempt"], error,
                    )
                    return False

                delay = 2 ** (job["attempt"] - 1)
                logger.warning(
                    "[DigitMiniMax] Job %d submission failed on attempt %d; "
                    "retrying in %ds: %s",
                    job["index"] + 1, job["attempt"], delay, error,
                )
                time.sleep(delay)

        return False

    @staticmethod
    def _submit_job(fal_client, app_id, shared_args, job):
        job["attempt"] += 1
        arguments = dict(shared_args)
        arguments["seed"] = job["seed"]
        logger.info(
            "[DigitMiniMax] Submitting job %d, attempt %d to %s (seed %d)...",
            job["index"] + 1, job["attempt"], app_id, job["seed"],
        )
        handle = fal_client.submit(
            app_id,
            arguments=arguments,
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
    def _cancel_jobs(jobs, pending):
        for index in pending:
            handle = jobs[index].get("handle")
            if handle is None:
                continue
            try:
                handle.cancel()
                logger.info("[DigitMiniMax] Cancelled request %s", handle.request_id)
            except Exception as error:
                logger.warning(
                    "[DigitMiniMax] Could not cancel request %s: %s",
                    getattr(handle, "request_id", "unknown"),
                    error,
                )

    def _download_results(self, result, batch_timestamp, batch_uuid, job_index):
        """Extract video URLs from fal response and download to ComfyUI temp dir."""
        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)

        video_items = []
        if isinstance(result, dict):
            if "videos" in result and isinstance(result["videos"], list):
                video_items = result["videos"]
            elif "video" in result:
                video_items = [result["video"]]

        if not video_items:
            logger.error("[DigitMiniMax] Could not extract video URLs from result: %s", result)
            return []

        paths = []
        for i, item in enumerate(video_items):
            url = None
            if isinstance(item, dict):
                url = item.get("url")
            elif isinstance(item, str):
                url = item

            if not url:
                logger.warning("[DigitMiniMax] Skipping video %d: no URL found in %s", i, item)
                continue

            local_path = os.path.join(
                temp_dir,
                f"minimax_{batch_timestamp}_{batch_uuid}_{job_index}.mp4",
            )
            try:
                urllib.request.urlretrieve(url, local_path)
                paths.append(local_path)
                logger.info("[DigitMiniMax] Downloaded video %d: %s", i, local_path)
            except Exception as e:
                logger.error("[DigitMiniMax] Failed to download %s: %s", url, e)

        return paths

    def _format_batch_status(self, mode, args, jobs, video_paths):
        lines = [
            "Model: MiniMax H3",
            f"Mode: {mode}",
            f"Resolution: {args.get('resolution')}",
            f"Aspect: {args.get('aspect_ratio', 'from first frame')}",
            f"Duration: {args.get('duration')}s",
            "Audio: native stereo",
            f"Prompt expansion: {args.get('enable_prompt_expansion')}",
            f"Safety checker: {args.get('enable_safety_checker')}",
            f"Videos generated: {len(video_paths)}/{len(jobs)}",
            f"Automatic retries: up to {MAX_AUTOMATIC_RETRIES} per job",
        ]
        for job in jobs:
            result_seed = (
                job["result"].get("seed")
                if isinstance(job.get("result"), dict)
                else None
            )
            summary = [
                f"Job {job['index'] + 1}",
                f"seed={result_seed or job['seed']}",
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

    def _generate_muapi(self, prompt, mode, resolution, aspect_ratio, duration,
                        batch_count, seed, first_frame, last_frame,
                        ref_images, ref_videos, ref_audios):
        headers = muapi_client.auth_headers()
        minimax_pricing.require_muapi_resolution(resolution)
        endpoint = minimax_pricing.muapi_endpoint_for_mode(mode)
        duration_seconds = duration_int(duration)

        logger.info(
            "[DigitMiniMax] Provider: muapi | Mode: %s | Endpoint: %s", mode, endpoint
        )

        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)

        image_url = None
        last_image_url = None
        reference_images = None
        reference_videos = None
        reference_audios = None

        if mode in ("image_to_video", "first_last_frame"):
            image_url = muapi_client.upload_image_tensor(
                headers, first_frame, label="first_frame",
            )
            if last_frame is not None:
                last_image_url = muapi_client.upload_image_tensor(
                    headers, last_frame, label="last_frame",
                )
        elif mode == "reference_to_video":
            if ref_images:
                reference_images = [
                    muapi_client.upload_image_tensor(
                        headers, img, label=f"ref_image{i}",
                    )
                    for i, img in enumerate(ref_images, start=1)
                ]
            if ref_videos:
                reference_videos = [
                    muapi_client.upload_video(
                        headers, v, temp_dir, label=f"ref_video{i}",
                    )
                    for i, v in enumerate(ref_videos, start=1)
                ]
            if ref_audios:
                reference_audios = [
                    muapi_client.upload_audio(
                        headers, a, temp_dir, label=f"ref_audio{i}",
                    )
                    for i, a in enumerate(ref_audios, start=1)
                ]

        payload = build_muapi_payload(
            prompt, mode, aspect_ratio, duration_seconds,
            image_url=image_url, last_image_url=last_image_url,
            reference_images=reference_images,
            reference_videos=reference_videos,
            reference_audios=reference_audios,
        )

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
                f"minimax_{batch_timestamp}_{batch_uuid}_{job['index']}.mp4",
            )
            try:
                urllib.request.urlretrieve(urls[0], local_path)
                job["path"] = local_path
                video_paths.append(local_path)
                logger.info("[DigitMiniMax] Downloaded muapi video: %s", local_path)
            except Exception as error:
                job["error"] = f"Download failed: {error}"
                logger.error("[DigitMiniMax] Failed to download %s: %s", urls[0], error)

        if not video_paths:
            details = "; ".join(
                f"job {job['index'] + 1}: {job.get('error', 'unknown failure')}"
                for job in jobs
            )
            raise RuntimeError(f"All MUAPI MiniMax H3 generations failed. {details}")

        from comfy_api.latest._input_impl.video_types import VideoFromFile
        video_output = VideoFromFile(video_paths[0])

        cost_summary = minimax_pricing.estimate(
            "muapi", mode, resolution, duration_seconds, len(video_paths),
            use_live=False,
        )
        lines = minimax_pricing.format_status_lines(cost_summary)
        lines += [
            "Model: MiniMax H3",
            f"Mode: {mode}",
            f"Resolution: {payload.get('resolution')}",
            f"Aspect: {payload.get('aspect_ratio', 'from first frame')}",
            f"Duration: {duration_seconds}s",
            "Audio: native stereo",
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
        """Submit batch_count identical requests, then poll them all to terminal."""
        jobs = []
        pending = set()
        for index in range(batch_count):
            job = {"index": index, "request_id": None, "result": None, "error": ""}
            jobs.append(job)
            try:
                job["request_id"] = muapi_client.submit(
                    headers, endpoint, payload, log_prefix="[DigitMiniMax:muapi]"
                )
                pending.add(index)
                logger.info(
                    "[DigitMiniMax] muapi job %d/%d submitted: %s",
                    index + 1, batch_count, job["request_id"],
                )
            except Exception as error:
                job["error"] = str(error)
                logger.error(
                    "[DigitMiniMax] muapi job %d submission failed: %s",
                    index + 1, error,
                )

        pbar = comfy.utils.ProgressBar(len(jobs))
        completed_count = len(jobs) - len(pending)
        if completed_count:
            pbar.update_absolute(completed_count)

        deadline = time.monotonic() + muapi_client.MAX_WAIT_SECONDS
        while pending:
            from comfy.model_management import throw_exception_if_processing_interrupted
            throw_exception_if_processing_interrupted()

            if time.monotonic() > deadline:
                for index in pending:
                    jobs[index]["error"] = (
                        f"Timed out after {muapi_client.MAX_WAIT_SECONDS}s "
                        f"(request_id={jobs[index]['request_id']})"
                    )
                break

            for index in list(pending):
                job = jobs[index]
                try:
                    result = muapi_client.poll_status(
                        headers, job["request_id"], log_prefix="[DigitMiniMax:muapi]"
                    )
                except Exception as error:
                    logger.warning(
                        "[DigitMiniMax] muapi status check failed for job %d: %s",
                        index + 1, error,
                    )
                    continue

                status = str(result.get("status", "")).lower()
                if status == "completed":
                    job["result"] = result
                elif status in muapi_client.TERMINAL_FAILURE_STATES:
                    job["error"] = str(
                        result.get("error") or f"Generation {status}."
                    )
                else:
                    continue

                pending.remove(index)
                completed_count += 1
                pbar.update_absolute(completed_count)

            if pending:
                time.sleep(muapi_client.POLL_INTERVAL_SECONDS)

        return jobs


# ---------------------------------------------------------------------------
# Cost-estimate route for the node's live summary strip
# (web/digit_minimax_cost.js). Registered only when running inside ComfyUI.
# ---------------------------------------------------------------------------
try:
    from aiohttp import web
    from server import PromptServer

    @PromptServer.instance.routes.post("/digit/minimax/estimate")
    async def _digit_minimax_estimate(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        provider = body.get("provider", "fal")
        mode = body.get("mode", "text_to_video")
        resolution = body.get("resolution", "2K")
        duration = body.get("duration", "5")
        batch_count = body.get("batch_count", 1)

        try:
            duration_seconds = max(1, int(duration))
        except (TypeError, ValueError):
            duration_seconds = 5

        import asyncio
        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(
            None,
            lambda: minimax_pricing.estimate(
                provider, mode, resolution, duration_seconds, batch_count,
                use_live=True,
            ),
        )
        return web.json_response({"range": False, "summary": summary})

except Exception:
    # Standalone import (tests) or an old ComfyUI without PromptServer.
    pass
