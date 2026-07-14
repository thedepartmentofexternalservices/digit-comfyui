"""DIGIT Seedance Video node — unified fal.ai Seedance 2.0 video generation.

One mega node that auto-detects mode based on connected inputs:
- No image/reference inputs connected → text-to-video
- first_frame connected               → image-to-video (optional last_frame for interpolation)
- Any reference_image/video/audio     → reference-to-video

Supports both standard and fast Seedance 2.0 variants via the model dropdown.
Uses FAL_KEY environment variable for authentication.
"""

import io
import logging
import os
import random
import time
import urllib.request
import uuid

import comfy.utils
import numpy as np
from PIL import Image as PILImage

import folder_paths

logger = logging.getLogger("DigitDanceVideo")


SEEDANCE_APPS = {
    "seedance-2.0": {
        "text_to_video":      "bytedance/seedance-2.0/text-to-video",
        "image_to_video":     "bytedance/seedance-2.0/image-to-video",
        "reference_to_video": "bytedance/seedance-2.0/reference-to-video",
    },
    "seedance-2.0-fast": {
        "text_to_video":      "bytedance/seedance-2.0/fast/text-to-video",
        "image_to_video":     "bytedance/seedance-2.0/fast/image-to-video",
        "reference_to_video": "bytedance/seedance-2.0/fast/reference-to-video",
    },
}

MODELS = list(SEEDANCE_APPS.keys())
RESOLUTIONS = ["480p", "720p", "1080p", "4k"]
FAST_MAX_RESOLUTIONS = {"480p", "720p"}
ASPECT_RATIOS = ["auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]
DURATIONS = ["auto", *[str(seconds) for seconds in range(4, 16)]]
BITRATE_MODES = ["standard", "high"]

MAX_REFERENCE_IMAGES = 9
MAX_REFERENCE_VIDEOS = 3
MAX_REFERENCE_AUDIOS = 3
MAX_REFERENCE_FILES = 12
MAX_BATCH_COUNT = 8
MAX_AUTOMATIC_RETRIES = 3
POLL_INTERVAL_SECONDS = 2.0
MAX_SEED = 2147483647

# fal's fine-grained retry header is ignored on public model endpoints. Disable
# platform retries and coordinate at most three retries here so the limit is real.
FAL_NO_RETRY_HEADERS = {"X-Fal-No-Retry": "1"}


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
    tmp_path = os.path.join(temp_dir, f"dance_upload_{uuid.uuid4().hex[:8]}.mp4")
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

    # First item in batch, transposed to (N, C) for soundfile
    wav = waveform[0].cpu().numpy().T

    temp_dir = folder_paths.get_temp_directory()
    os.makedirs(temp_dir, exist_ok=True)
    tmp_path = os.path.join(temp_dir, f"dance_audio_{uuid.uuid4().hex[:8]}.wav")
    sf.write(tmp_path, wav, sample_rate)
    return fal_client.upload_file(tmp_path)


class DigitDanceVideo:
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
                    "placeholder": "Describe the video. In reference mode, use @Image1, @Video1, and @Audio1.",
                }),
                "model": (MODELS, {"default": "seedance-2.0"}),
                "resolution": (RESOLUTIONS, {
                    "default": "720p",
                    "tooltip": "1080p and 4k require seedance-2.0 (not Fast).",
                }),
                "aspect_ratio": (ASPECT_RATIOS, {"default": "16:9"}),
                "duration": (DURATIONS, {
                    "default": "5",
                    "tooltip": "Video length in seconds, or auto for model-selected duration.",
                }),
                "generate_audio": ("BOOLEAN", {"default": True}),
                "bitrate_mode": (BITRATE_MODES, {
                    "default": "standard",
                    "tooltip": "High requests a larger, higher-quality encode.",
                }),
                "batch_count": ("INT", {
                    "default": 4,
                    "min": 1,
                    "max": MAX_BATCH_COUNT,
                    "tooltip": "Submit this many generations to fal before polling.",
                }),
                "seed": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": MAX_SEED,
                    "tooltip": "0 creates distinct random seeds. A positive value is the first seed in a consecutive batch.",
                }),
            },
            "optional": {
                "first_frame": ("IMAGE", {"tooltip": "Image-to-video mode. Mutually exclusive with reference inputs."}),
                "last_frame": ("IMAGE", {"tooltip": "Optional end frame for first-to-last interpolation."}),
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

    def generate(self, prompt, model, resolution, aspect_ratio,
                 duration, generate_audio, bitrate_mode, batch_count, seed,
                 first_frame=None, last_frame=None,
                 **kwargs):
        try:
            import fal_client
        except ImportError:
            raise ImportError(
                "fal-client is required for DigitDanceVideo. "
                "Install with: pip install fal-client"
            )

        if not os.environ.get("FAL_KEY"):
            raise ValueError(
                "FAL_KEY environment variable is not set. "
                "Export FAL_KEY=<your-key> in the environment before starting ComfyUI."
            )
        if not prompt or not prompt.strip():
            raise ValueError("Prompt is required.")

        # Collect reference inputs from optional kwargs
        ref_images = [kwargs.get(f"reference_image{i}") for i in range(1, MAX_REFERENCE_IMAGES + 1)]
        ref_images = [img for img in ref_images if img is not None]

        ref_videos = [kwargs.get(f"reference_video{i}") for i in range(1, MAX_REFERENCE_VIDEOS + 1)]
        ref_videos = [v for v in ref_videos if v is not None]

        ref_audios = [kwargs.get(f"reference_audio{i}") for i in range(1, MAX_REFERENCE_AUDIOS + 1)]
        ref_audios = [a for a in ref_audios if a is not None]

        has_refs = bool(ref_images or ref_videos or ref_audios)
        has_first_frame = first_frame is not None
        has_last_frame = last_frame is not None

        # Validation
        if has_refs and (has_first_frame or has_last_frame):
            raise ValueError(
                "Cannot combine first_frame/last_frame with reference inputs. "
                "Use image-to-video mode OR reference-to-video mode, not both."
            )
        if ref_audios and not (ref_images or ref_videos):
            raise ValueError(
                "reference_audio requires at least one reference_image or reference_video "
                "(fal.ai Seedance requirement)."
            )
        reference_count = len(ref_images) + len(ref_videos) + len(ref_audios)
        if reference_count > MAX_REFERENCE_FILES:
            raise ValueError(
                f"Seedance accepts at most {MAX_REFERENCE_FILES} reference files total; "
                f"{reference_count} are connected."
            )
        if has_last_frame and not has_first_frame:
            raise ValueError("last_frame requires first_frame to be connected.")
        if model == "seedance-2.0-fast" and resolution not in FAST_MAX_RESOLUTIONS:
            raise ValueError(
                f"Resolution '{resolution}' is not supported by seedance-2.0-fast "
                f"(max {', '.join(sorted(FAST_MAX_RESOLUTIONS))}). "
                "Switch to seedance-2.0 for 1080p or 4k output."
            )

        # Detect mode
        if has_refs:
            mode = "reference_to_video"
        elif has_first_frame:
            mode = "image_to_video"
        else:
            mode = "text_to_video"

        app_id = SEEDANCE_APPS[model][mode]
        logger.info(f"[DigitDance] Mode: {mode} | App: {app_id}")

        # Build the shared payload. Media uploads happen once and their URLs are
        # reused by every generation in this batch.
        args = {
            "prompt": prompt.strip(),
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "duration": str(duration),
            "generate_audio": generate_audio,
            "bitrate_mode": bitrate_mode,
        }

        # Mode-specific payload
        if mode == "image_to_video":
            args["image_url"] = _upload_image_tensor(fal_client, first_frame)
            if has_last_frame:
                args["end_image_url"] = _upload_image_tensor(fal_client, last_frame)

        elif mode == "reference_to_video":
            if ref_images:
                args["image_urls"] = [
                    _upload_image_tensor(fal_client, img) for img in ref_images
                ]
            if ref_videos:
                args["video_urls"] = [
                    _upload_video(fal_client, v) for v in ref_videos
                ]
            if ref_audios:
                args["audio_urls"] = [
                    _upload_audio(fal_client, a) for a in ref_audios
                ]

        seeds = self._build_seeds(seed, int(batch_count))
        jobs = self._run_batch(fal_client, app_id, args, seeds)

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
            raise RuntimeError(f"All Seedance batch generations failed. {details}")

        from comfy_api.latest._input_impl.video_types import VideoFromFile
        video_output = VideoFromFile(video_paths[0])

        status = self._format_batch_status(mode, model, args, jobs, video_paths)
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
                            "[DigitDance] Status check failed for job %d: %s",
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
                                "[DigitDance] Job %d failed on attempt %d; retrying in %ds: %s",
                                index + 1,
                                job["attempt"],
                                delay,
                                error,
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
                        "[DigitDance] Job %d submission failed after %d attempt(s): %s",
                        job["index"] + 1,
                        job["attempt"],
                        error,
                    )
                    return False

                delay = 2 ** (job["attempt"] - 1)
                logger.warning(
                    "[DigitDance] Job %d submission failed on attempt %d; "
                    "retrying in %ds: %s",
                    job["index"] + 1,
                    job["attempt"],
                    delay,
                    error,
                )
                time.sleep(delay)

        return False

    @staticmethod
    def _submit_job(fal_client, app_id, shared_args, job):
        job["attempt"] += 1
        arguments = dict(shared_args)
        arguments["seed"] = job["seed"]
        logger.info(
            "[DigitDance] Submitting job %d, attempt %d to %s (seed %d)...",
            job["index"] + 1,
            job["attempt"],
            app_id,
            job["seed"],
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
                logger.info("[DigitDance] Cancelled request %s", handle.request_id)
            except Exception as error:
                logger.warning(
                    "[DigitDance] Could not cancel request %s: %s",
                    getattr(handle, "request_id", "unknown"),
                    error,
                )

    def _download_results(self, result, batch_timestamp, batch_uuid, job_index):
        """Extract video URLs from fal response and download to ComfyUI temp dir."""
        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)

        # fal response schema varies: either {"video": {"url": ...}} or {"videos": [...]}
        video_items = []
        if isinstance(result, dict):
            if "videos" in result and isinstance(result["videos"], list):
                video_items = result["videos"]
            elif "video" in result:
                video_items = [result["video"]]

        if not video_items:
            logger.error(f"[DigitDance] Could not extract video URLs from result: {result}")
            return []

        paths = []

        for i, item in enumerate(video_items):
            url = None
            if isinstance(item, dict):
                url = item.get("url")
            elif isinstance(item, str):
                url = item

            if not url:
                logger.warning(f"[DigitDance] Skipping video {i}: no URL found in {item}")
                continue

            local_path = os.path.join(
                temp_dir,
                f"dance_{batch_timestamp}_{batch_uuid}_{job_index}.mp4",
            )
            try:
                urllib.request.urlretrieve(url, local_path)
                paths.append(local_path)
                logger.info(f"[DigitDance] Downloaded video {i}: {local_path}")
            except Exception as e:
                logger.error(f"[DigitDance] Failed to download {url}: {e}")

        return paths

    def _format_batch_status(self, mode, model, args, jobs, video_paths):
        lines = [
            f"Model: {model}",
            f"Mode: {mode}",
            f"Resolution: {args.get('resolution')}",
            f"Aspect: {args.get('aspect_ratio')}",
            f"Duration: {args.get('duration')}" + ("" if args.get('duration') == "auto" else "s"),
            f"Audio: {args.get('generate_audio')}",
            f"Bitrate: {args.get('bitrate_mode')}",
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
