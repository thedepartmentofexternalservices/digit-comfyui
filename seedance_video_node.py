"""DIGIT Seedance Video node — Seedance 2.0 / 2.5 across fal, MUAPI, and Replicate.

One node, one provider dropdown. Mode auto-detects from connected inputs:
- No image/reference inputs connected → text-to-video
- first_frame connected               → image-to-video
- first_frame + last_frame            → first/last-frame interpolation
- Any reference_image/video/audio     → reference-to-video
- source_video connected              → video_edit (2.5) or video_extend

Providers:
- fal        (FAL_KEY)            — strict filtering, fastest queue.
- muapi      (MUAPIAPP_API_KEY)   — low/reduced filtering; auto-routes to the
                                     cheapest low-censorship endpoint for the
                                     requested resolution (see seedance_pricing).
- replicate  (REPLICATE_API_TOKEN) — ByteDance stock filter, backup provider.

Cost estimates surface on the node via web/digit_seedance_cost.js and the
/digit/seedance/estimate route registered below.
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

try:
    from . import muapi_client, seedance_pricing
except ImportError:  # standalone import (tests, linting)
    import muapi_client
    import seedance_pricing

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
    "seedance-2.5": {
        "text_to_video":      "bytedance/seedance-2.5/text-to-video",
        "image_to_video":     "bytedance/seedance-2.5/image-to-video",
        "reference_to_video": "bytedance/seedance-2.5/reference-to-video",
        "video_edit":         "bytedance/seedance-2.5/reference-to-video",
        "video_extend":       "bytedance/seedance-2.5/reference-to-video",
    },
}

PROVIDERS = seedance_pricing.PROVIDERS
MODELS = list(SEEDANCE_APPS.keys())
RESOLUTIONS = ["480p", "720p", "1080p", "4k"]
FAST_MAX_RESOLUTIONS = {"480p", "720p"}
ASPECT_RATIOS = ["auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]
DURATIONS = ["auto", "same_as_input", *[str(seconds) for seconds in range(4, 31)]]
BITRATE_MODES = ["standard", "high"]
VIDEO_TASKS = ["auto", "edit", "extend"]
FAL_720P_MODELS = {"seedance-2.0-fast", "seedance-2.5"}

REPLICATE_MODEL = "bytedance/seedance-2.0"

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

PROVIDER_TOOLTIP = "\n".join(seedance_pricing.PROVIDER_BLURBS.values())


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


def _tensor_to_png_path(tensor, temp_dir):
    """Write a single (H,W,C) float32 0-1 image tensor to a PNG file. Returns path."""
    img_np = tensor.cpu().numpy()
    img_np = (img_np * 255).clip(0, 255).astype(np.uint8)
    img = PILImage.fromarray(img_np)
    path = os.path.join(temp_dir, f"replicate_seedance_img_{uuid.uuid4().hex[:8]}.png")
    img.save(path, format="PNG")
    return path


def _video_to_path(video_obj, temp_dir):
    """Resolve a ComfyUI VIDEO object to a local file path (saving if needed)."""
    try:
        source = video_obj.get_stream_source()
        if isinstance(source, str) and os.path.isfile(source):
            return source
    except Exception:
        pass
    path = os.path.join(temp_dir, f"replicate_seedance_vid_{uuid.uuid4().hex[:8]}.mp4")
    video_obj.save_to(path)
    return path


def _probe_video_duration(video_obj):
    """Return duration in seconds from a ComfyUI VIDEO object, or None."""
    if video_obj is None:
        return None
    try:
        duration = video_obj.get_duration()
        if duration is not None and float(duration) > 0:
            return float(duration)
    except Exception as error:
        logger.warning("[DigitDance] Could not probe video duration: %s", error)
    return None


def _with_role_prefix(prompt, prefixes):
    """Prefix the prompt unless it already names the source video role."""
    text = (prompt or "").strip()
    lowered = text.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix.lower().rstrip(". ").lower()):
            return text
    if "edit @video1" in lowered or "edit @video 1" in lowered:
        return text
    if "extend @video1" in lowered or "extend @video 1" in lowered:
        return text
    return prefixes[0] + text


def _audio_to_path(audio_obj, temp_dir):
    """Write a ComfyUI AUDIO dict ({'waveform','sample_rate'}) to a WAV file."""
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
    path = os.path.join(temp_dir, f"replicate_seedance_aud_{uuid.uuid4().hex[:8]}.wav")
    sf.write(path, wav, sample_rate)
    return path


class DigitDanceVideo:
    """DIGIT Dance / Seedance Video — Seedance 2.0 and Seedance 2.5."""

    CATEGORY = "DIGIT"
    DESCRIPTION = (
        "Digit Dance. Seedance 2.0 and Seedance 2.5 via fal, MUAPI, or Replicate. "
        "Pick seedance-2.5 in the model dropdown for 30s clips, video edit/extend, "
        "and MUAPI 4K."
    )
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
                "provider": (PROVIDERS, {
                    "default": "fal",
                    "tooltip": PROVIDER_TOOLTIP,
                }),
                "model": (MODELS, {
                    "default": "seedance-2.0",
                    "tooltip": "fal + muapi. seedance-2.5 unlocks 30s clips, MUAPI 4K (upscaled), and video edit/extend. Replicate is 2.0 only.",
                }),
                "resolution": (RESOLUTIONS, {
                    "default": "720p",
                    "tooltip": "Cost driver #1. On muapi 2.0, 1080p/4k auto-route to VIP. On muapi 2.5, 1080p/4k are upscaled from 720p. fal 2.5 tops out at 720p.",
                }),
                "aspect_ratio": (ASPECT_RATIOS, {"default": "16:9"}),
                "duration": (DURATIONS, {
                    "default": "5",
                    "tooltip": "Cost driver #2 — billed per second. 2.0 max 15s, 2.5 max 30s. same_as_input probes the first connected source/reference video. Video edit inherits source length (use same_as_input or auto).",
                }),
                "generate_audio": ("BOOLEAN", {"default": True}),
                "bitrate_mode": (BITRATE_MODES, {
                    "default": "standard",
                    "tooltip": "High requests a larger, higher-quality encode (fal + muapi).",
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
                    "tooltip": "0 creates distinct random seeds. A positive value is the first seed in a consecutive batch. fal + replicate always; MUAPI Seedance 2.5 also accepts seed.",
                }),
            },
            "optional": {
                "first_frame": ("IMAGE", {"tooltip": "Image-to-video mode. Mutually exclusive with reference inputs and source_video."}),
                "last_frame": ("IMAGE", {"tooltip": "Optional end frame for first-to-last interpolation, or MUAPI 2.5 video-extend target frame."}),
                "source_video": ("VIDEO", {
                    "tooltip": "Seedance 2.5 video edit (default) or extend. Mutually exclusive with first_frame and reference_video*.",
                }),
                "video_task": (VIDEO_TASKS, {
                    "default": "auto",
                    "tooltip": "Used when source_video is connected. auto = edit. extend continues from the last frame.",
                }),
                "muapi_route": (seedance_pricing.MUAPI_ROUTE_CHOICES, {
                    "default": "auto",
                    "tooltip": "muapi only. auto = cheapest low-censorship 2.0 endpoint, or the matching 2.5 endpoint when model is seedance-2.5.",
                }),
                "negative_prompt": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "replicate only.",
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

    def generate(self, prompt, provider, model, resolution, aspect_ratio,
                 duration, generate_audio, bitrate_mode, batch_count, seed,
                 first_frame=None, last_frame=None, source_video=None,
                 video_task="auto", muapi_route="auto", negative_prompt="",
                 **kwargs):
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
        has_ref_videos = bool(ref_videos)
        has_first_frame = first_frame is not None
        has_last_frame = last_frame is not None
        has_source = source_video is not None
        video_task = video_task or "auto"
        is_25 = seedance_pricing.is_seedance_25(model)

        if provider == "replicate" and is_25:
            raise ValueError(
                "Seedance 2.5 is not available on Replicate. "
                "Switch provider to fal or muapi."
            )
        if has_source and not is_25:
            raise ValueError(
                "source_video requires model=seedance-2.5 (video edit/extend)."
            )
        if video_task in ("edit", "extend") and not has_source:
            raise ValueError(
                f"video_task={video_task} requires a connected source_video."
            )
        if has_source and has_first_frame:
            raise ValueError(
                "Cannot combine source_video with first_frame. "
                "Use video edit/extend OR image-to-video, not both."
            )
        if has_source and has_ref_videos:
            raise ValueError(
                "Cannot combine source_video with reference_video*. "
                "source_video is the sole edit/extend master; use reference_image* "
                "or reference_audio* for extra guidance."
            )
        if has_refs and (has_first_frame or (has_last_frame and not has_source)):
            raise ValueError(
                "Cannot combine first_frame/last_frame with reference inputs. "
                "Use image-to-video mode OR reference-to-video mode, not both."
            )
        if ref_audios and not (ref_images or ref_videos or has_source):
            raise ValueError(
                "reference_audio requires at least one reference_image, "
                "reference_video, or source_video."
            )
        reference_count = len(ref_images) + len(ref_videos) + len(ref_audios)
        if reference_count > MAX_REFERENCE_FILES:
            raise ValueError(
                f"Seedance accepts at most {MAX_REFERENCE_FILES} reference files total; "
                f"{reference_count} are connected."
            )

        resolved_task = video_task
        if has_source and video_task == "auto":
            resolved_task = "edit"

        if has_last_frame and not has_first_frame and not (
            has_source and resolved_task == "extend"
        ):
            raise ValueError("last_frame requires first_frame to be connected.")

        # Detect mode. source_video wins so an edit clip is never a style ref.
        if has_source:
            mode = "video_extend" if resolved_task == "extend" else "video_edit"
        elif has_refs:
            mode = "reference_to_video"
        elif has_first_frame and has_last_frame:
            mode = "first_last_frame"
        elif has_first_frame:
            mode = "image_to_video"
        else:
            mode = "text_to_video"

        if mode == "video_edit" and duration not in ("auto", "same_as_input"):
            raise ValueError(
                "Video edit always matches the source length. "
                "Set duration to same_as_input or auto, or switch video_task to extend."
            )

        duration_token = str(duration)
        try:
            duration_number = int(duration_token)
        except (TypeError, ValueError):
            duration_number = None
        max_seconds = seedance_pricing.max_duration_seconds(model)
        if duration_number is not None and duration_number > max_seconds:
            raise ValueError(
                f"Duration {duration_number}s exceeds {model} max of {max_seconds}s. "
                f"{'Use seedance-2.5 for 16–30s clips.' if max_seconds == 15 else ''}"
            )

        probed_duration = None
        duration_note = ""
        duration_for_api = duration_token
        if duration_token == "same_as_input" or (
            duration_token == "auto" and mode == "video_edit"
        ):
            probe_video = source_video or (ref_videos[0] if ref_videos else None)
            if probe_video is None:
                raise ValueError(
                    "same_as_input requires a connected source_video or reference_video."
                )
            probed_duration = _probe_video_duration(probe_video)
            if mode == "video_edit":
                duration_for_api = "auto"
                if probed_duration is not None:
                    duration_note = f"inherited (source {probed_duration:.2f}s)"
                else:
                    duration_note = "inherited from source"
            else:
                if probed_duration is None:
                    raise ValueError(
                        "Could not read duration from the connected video. "
                        "Pick an explicit duration instead of same_as_input."
                    )
                clamped = seedance_pricing.clamp_duration_seconds(
                    probed_duration, model
                )
                duration_for_api = str(clamped)
                duration_note = f"same as input, probed {probed_duration:.2f}s"
                if clamped != int(round(probed_duration)):
                    duration_note += f", clamped to {clamped}s"

        common = {
            "prompt": prompt,
            "mode": mode,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "duration": duration_for_api,
            "generate_audio": generate_audio,
            "bitrate_mode": bitrate_mode,
            "batch_count": int(batch_count),
            "seed": seed,
            "first_frame": first_frame,
            "last_frame": last_frame,
            "ref_images": ref_images,
            "ref_videos": ref_videos,
            "ref_audios": ref_audios,
            "source_video": source_video,
            "probed_duration": probed_duration,
            "duration_note": duration_note,
        }

        if provider == "fal":
            return self._generate_fal(model=model, **common)
        if provider == "muapi":
            return self._generate_muapi(
                model=model, muapi_route=muapi_route, **common
            )
        if provider == "replicate":
            return self._generate_replicate(negative_prompt=negative_prompt, **common)
        raise ValueError(f"Unknown provider: {provider}")

    # ------------------------------------------------------------------
    # fal backend
    # ------------------------------------------------------------------

    def _generate_fal(self, model, prompt, mode, resolution, aspect_ratio,
                      duration, generate_audio, bitrate_mode, batch_count, seed,
                      first_frame, last_frame, ref_images, ref_videos, ref_audios,
                      source_video=None, probed_duration=None, duration_note=""):
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
        if model in FAL_720P_MODELS and resolution not in FAST_MAX_RESOLUTIONS:
            cap_name = "seedance-2.5" if seedance_pricing.is_seedance_25(model) else "seedance-2.0-fast"
            raise ValueError(
                f"Resolution '{resolution}' is not supported by {cap_name} "
                f"(max {', '.join(sorted(FAST_MAX_RESOLUTIONS))}). "
                "Switch to seedance-2.0 (fal) or muapi seedance-2.5 for 1080p/4k."
            )

        # fal serves first/last-frame through the image-to-video app.
        fal_mode = "image_to_video" if mode == "first_last_frame" else mode
        apps = SEEDANCE_APPS.get(model) or {}
        if fal_mode not in apps:
            raise ValueError(
                f"fal model '{model}' does not support mode '{mode}'."
            )
        app_id = apps[fal_mode]
        logger.info(f"[DigitDance] Provider: fal | Mode: {mode} | App: {app_id}")

        is_25 = seedance_pricing.is_seedance_25(model)
        inherit_duration = mode == "video_edit"
        inherit_aspect = mode in ("video_edit", "video_extend")

        fal_prompt = prompt.strip()
        if mode == "video_edit":
            fal_prompt = _with_role_prefix(fal_prompt, ["Edit @Video1. "])
        elif mode == "video_extend":
            fal_prompt = _with_role_prefix(fal_prompt, ["Extend @Video1 forward. "])

        args = {
            "prompt": fal_prompt,
            "resolution": resolution,
            "aspect_ratio": "auto" if inherit_aspect else aspect_ratio,
            "duration": "auto" if inherit_duration else str(duration),
            "generate_audio": generate_audio,
        }
        if not is_25:
            args["bitrate_mode"] = bitrate_mode

        if fal_mode == "image_to_video":
            args["image_url"] = _upload_image_tensor(fal_client, first_frame)
            if last_frame is not None:
                args["end_image_url"] = _upload_image_tensor(fal_client, last_frame)

        elif fal_mode in ("reference_to_video", "video_edit", "video_extend"):
            image_urls = [
                _upload_image_tensor(fal_client, img) for img in ref_images
            ]
            video_urls = []
            if source_video is not None:
                video_urls.append(_upload_video(fal_client, source_video))
            video_urls.extend(
                _upload_video(fal_client, v) for v in ref_videos
            )
            audio_urls = [
                _upload_audio(fal_client, a) for a in ref_audios
            ]
            if image_urls:
                args["image_urls"] = image_urls
            if video_urls:
                args["video_urls"] = video_urls
            if audio_urls:
                args["audio_urls"] = audio_urls

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
        cost_duration = probed_duration if probed_duration else self._duration_int(duration, default=5)
        cost_summary = seedance_pricing.estimate(
            "fal", mode, resolution, self._duration_int(cost_duration, default=5),
            len(video_paths), fal_model=model,
            has_video_refs=bool(ref_videos or source_video), use_live=False,
            source_duration_seconds=probed_duration,
        )
        extra = [status]
        if duration_note:
            extra.insert(0, f"Duration note: {duration_note}")
        status = "\n".join(
            seedance_pricing.format_status_lines(cost_summary) + extra
        )
        return (video_output, video_paths, status)

    @staticmethod
    def _build_seeds(base_seed, batch_count):
        if base_seed > 0:
            return [((base_seed - 1 + index) % MAX_SEED) + 1 for index in range(batch_count)]
        return random.SystemRandom().sample(range(1, MAX_SEED + 1), batch_count)

    @staticmethod
    def _duration_int(duration, default=5):
        try:
            value = int(duration)
            return value if value > 0 else default
        except (TypeError, ValueError):
            return default

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

    # ------------------------------------------------------------------
    # MUAPI backend
    # ------------------------------------------------------------------

    def _generate_muapi(self, muapi_route, prompt, mode, resolution, aspect_ratio,
                        duration, generate_audio, bitrate_mode, batch_count, seed,
                        first_frame, last_frame, ref_images, ref_videos, ref_audios,
                        model="seedance-2.0", source_video=None,
                        probed_duration=None, duration_note=""):
        headers = muapi_client.auth_headers()

        inherit_duration = mode == "video_edit"
        if duration == "auto" and not inherit_duration:
            raise ValueError(
                "MUAPI requires an explicit duration "
                f"(4-{seedance_pricing.max_duration_seconds(model)} seconds), "
                "same_as_input when a video is connected, or auto only for video edit. "
                "Pick a number instead of 'auto'."
            )
        duration_seconds = (
            -1 if inherit_duration else self._duration_int(duration)
        )

        endpoint, route_note = seedance_pricing.resolve_muapi_route(
            mode, resolution, muapi_route, model=model,
        )
        logger.info(
            "[DigitDance] Provider: muapi | Mode: %s | Endpoint: %s", mode, endpoint
        )

        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)

        payload = {
            "prompt": prompt.strip(),
            "duration": duration_seconds,
        }

        endpoint_is_25 = endpoint.startswith("seedance-2.5")
        # 2.5 bakes every resolution into the slug, including 480p/720p.
        # 2.0 1080p/4k VIP slugs also bake resolution in.
        endpoint_has_fixed_resolution = endpoint_is_25 or endpoint.endswith(
            ("-1080p", "-4k", "-480p")
        )
        if not endpoint_has_fixed_resolution:
            payload["resolution"] = resolution

        if inherit_duration or mode == "video_extend":
            payload["aspect_ratio"] = "adaptive"
        elif mode == "first_last_frame":
            payload["aspect_ratio"] = "adaptive" if aspect_ratio == "auto" else aspect_ratio
        elif aspect_ratio != "auto":
            payload["aspect_ratio"] = aspect_ratio

        if endpoint_is_25 and mode in ("video_edit", "video_extend"):
            payload["generate_audio"] = bool(generate_audio)
        elif not endpoint_is_25 and mode != "first_last_frame":
            payload["generate_audio"] = bool(generate_audio)
            payload["high_bitrate"] = bitrate_mode == "high"

        if endpoint_is_25:
            payload["seed"] = int(seed) if int(seed) > 0 else -1

        # Media uploads happen once; URLs are shared by every clip in the batch.
        if mode == "image_to_video":
            image_url = muapi_client.upload_image_tensor(
                headers, first_frame, label="first_frame"
            )
            if endpoint_is_25:
                payload["image_url"] = image_url
            else:
                payload["images_list"] = [image_url]
        elif mode == "first_last_frame":
            payload["images_list"] = [
                muapi_client.upload_image_tensor(headers, first_frame, label="first_frame"),
                muapi_client.upload_image_tensor(headers, last_frame, label="last_frame"),
            ]
        elif mode in ("video_edit", "video_extend"):
            payload["video"] = muapi_client.upload_video(
                headers, source_video, temp_dir, label="source_video"
            )
            if ref_images:
                payload["images_list"] = [
                    muapi_client.upload_image_tensor(headers, img, label=f"ref_image{i}")
                    for i, img in enumerate(ref_images, start=1)
                ]
            if ref_audios:
                payload["audios_list"] = [
                    muapi_client.upload_audio(headers, a, temp_dir, label=f"ref_audio{i}")
                    for i, a in enumerate(ref_audios, start=1)
                ]
            if mode == "video_extend" and last_frame is not None:
                payload["last_image"] = muapi_client.upload_image_tensor(
                    headers, last_frame, label="last_image"
                )
        elif mode == "reference_to_video":
            if ref_images:
                payload["images_list"] = [
                    muapi_client.upload_image_tensor(headers, img, label=f"ref_image{i}")
                    for i, img in enumerate(ref_images, start=1)
                ]
            if ref_videos:
                video_urls = [
                    muapi_client.upload_video(headers, v, temp_dir, label=f"ref_video{i}")
                    for i, v in enumerate(ref_videos, start=1)
                ]
                payload["videos_list" if endpoint_is_25 else "video_files"] = video_urls
            if ref_audios:
                audio_urls = [
                    muapi_client.upload_audio(headers, a, temp_dir, label=f"ref_audio{i}")
                    for i, a in enumerate(ref_audios, start=1)
                ]
                payload["audios_list" if endpoint_is_25 else "audio_files"] = audio_urls

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
                f"dance_{batch_timestamp}_{batch_uuid}_{job['index']}.mp4",
            )
            try:
                urllib.request.urlretrieve(urls[0], local_path)
                job["path"] = local_path
                video_paths.append(local_path)
                logger.info("[DigitDance] Downloaded muapi video: %s", local_path)
            except Exception as error:
                job["error"] = f"Download failed: {error}"
                logger.error("[DigitDance] Failed to download %s: %s", urls[0], error)

        if not video_paths:
            details = "; ".join(
                f"job {job['index'] + 1}: {job.get('error', 'unknown failure')}"
                for job in jobs
            )
            raise RuntimeError(f"All MUAPI Seedance generations failed. {details}")

        from comfy_api.latest._input_impl.video_types import VideoFromFile
        video_output = VideoFromFile(video_paths[0])

        cost_duration = probed_duration if probed_duration else (
            duration_seconds if duration_seconds and duration_seconds > 0 else 5
        )
        cost_summary = seedance_pricing.estimate(
            "muapi", mode, resolution,
            self._duration_int(cost_duration, default=5),
            len(video_paths),
            muapi_route=muapi_route, fal_model=model, use_live=False,
            source_duration_seconds=probed_duration,
        )
        lines = seedance_pricing.format_status_lines(cost_summary)
        if route_note:
            lines.append(f"Routing note: {route_note}")
        if duration_note:
            lines.append(f"Duration note: {duration_note}")
        duration_status = (
            duration_note or (
                f"{duration_seconds}s" if duration_seconds > 0 else "inherited"
            )
        )
        lines += [
            f"Mode: {mode}",
            f"Resolution: {resolution}",
            f"Aspect: {payload.get('aspect_ratio', 'auto')}",
            f"Duration: {duration_status}",
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
                    headers, endpoint, payload, log_prefix="[DigitDance:muapi]"
                )
                pending.add(index)
                logger.info(
                    "[DigitDance] muapi job %d/%d submitted: %s",
                    index + 1, batch_count, job["request_id"],
                )
            except Exception as error:
                job["error"] = str(error)
                logger.error(
                    "[DigitDance] muapi job %d submission failed: %s",
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
                        headers, job["request_id"], log_prefix="[DigitDance:muapi]"
                    )
                except Exception as error:
                    logger.warning(
                        "[DigitDance] muapi status check failed for job %d: %s",
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

    # ------------------------------------------------------------------
    # Replicate backend
    # ------------------------------------------------------------------

    def _generate_replicate(self, negative_prompt, prompt, mode, resolution,
                            aspect_ratio, duration, generate_audio, bitrate_mode,
                            batch_count, seed, first_frame, last_frame,
                            ref_images, ref_videos, ref_audios,
                            source_video=None, probed_duration=None, duration_note=""):
        try:
            import replicate
        except ImportError:
            raise ImportError(
                "replicate is required for the replicate provider. "
                "Install with: pip install replicate"
            )

        if mode in ("video_edit", "video_extend") or source_video is not None:
            raise ValueError(
                "Video edit/extend is not available on Replicate. "
                "Switch provider to fal or muapi with model=seedance-2.5."
            )

        if not os.environ.get("REPLICATE_API_TOKEN"):
            raise ValueError(
                "REPLICATE_API_TOKEN environment variable is not set. "
                "Export REPLICATE_API_TOKEN=<your-key> before starting ComfyUI."
            )

        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)

        duration_value = -1 if duration == "auto" else self._duration_int(duration)

        replicate_input = {
            "prompt": prompt.strip(),
            "resolution": resolution,
            "aspect_ratio": "adaptive" if aspect_ratio == "auto" else aspect_ratio,
            "duration": duration_value,
            "audio": bool(generate_audio),
        }
        if negative_prompt and negative_prompt.strip():
            replicate_input["negative_prompt"] = negative_prompt.strip()

        # Replicate's Python SDK accepts open file handles and auto-uploads them.
        # Track them so we can close after the calls return.
        open_files = []

        def _open(path):
            fh = open(path, "rb")
            open_files.append(fh)
            return fh

        jobs = []
        try:
            if mode in ("image_to_video", "first_last_frame"):
                replicate_input["image"] = _open(
                    _tensor_to_png_path(first_frame[0], temp_dir)
                )
                if mode == "first_last_frame":
                    replicate_input["last_frame"] = _open(
                        _tensor_to_png_path(last_frame[0], temp_dir)
                    )
            elif mode == "reference_to_video":
                if ref_images:
                    replicate_input["reference_images"] = [
                        _open(_tensor_to_png_path(img[0], temp_dir))
                        for img in ref_images
                    ]
                if ref_videos:
                    replicate_input["reference_videos"] = [
                        _open(_video_to_path(v, temp_dir)) for v in ref_videos
                    ]
                if ref_audios:
                    # Replicate's field name is singular: reference_audio
                    replicate_input["reference_audio"] = [
                        _open(_audio_to_path(a, temp_dir)) for a in ref_audios
                    ]

            seeds = self._build_seeds(seed, int(batch_count))
            logger.info(
                "[DigitDance] Provider: replicate | Mode: %s | Submitting %d job(s) to %s...",
                mode, len(seeds), REPLICATE_MODEL,
            )

            pbar = comfy.utils.ProgressBar(len(seeds))
            for index, job_seed in enumerate(seeds):
                from comfy.model_management import throw_exception_if_processing_interrupted
                throw_exception_if_processing_interrupted()

                job = {"index": index, "seed": job_seed, "result": None, "error": ""}
                jobs.append(job)
                job_input = dict(replicate_input)
                if job_seed > 0:
                    job_input["seed"] = int(job_seed)
                try:
                    job["result"] = self._run_replicate_with_retry(replicate, job_input)
                except Exception as run_err:
                    err_str = str(run_err)
                    if "E005" in err_str or "flagged as sensitive" in err_str.lower():
                        job["error"] = f"Blocked by Replicate safety filter: {err_str}"
                        logger.warning(
                            "[DigitDance] Replicate safety filter blocked job %d: %s",
                            index + 1, err_str,
                        )
                    else:
                        job["error"] = err_str
                        logger.error(
                            "[DigitDance] Replicate job %d failed: %s",
                            index + 1, err_str,
                        )
                pbar.update_absolute(index + 1)
        finally:
            for fh in open_files:
                try:
                    fh.close()
                except Exception:
                    pass

        batch_timestamp = int(time.time())
        batch_uuid = uuid.uuid4().hex[:8]
        video_paths = []
        for job in jobs:
            if job.get("result") is None:
                continue
            paths = self._save_replicate_outputs(
                job["result"], job["index"], batch_timestamp, batch_uuid
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
            raise RuntimeError(f"All Replicate Seedance generations failed. {details}")

        from comfy_api.latest._input_impl.video_types import VideoFromFile
        video_output = VideoFromFile(video_paths[0])

        cost_summary = seedance_pricing.estimate(
            "replicate", mode, resolution,
            self._duration_int(duration, default=5), len(video_paths),
            has_video_refs=bool(ref_videos), use_live=False,
        )
        lines = seedance_pricing.format_status_lines(cost_summary)
        if duration_note:
            lines.append(f"Duration note: {duration_note}")
        lines += [
            f"Mode: {mode}",
            f"Resolution: {resolution}",
            f"Duration: {duration_note or (duration_value if duration_value > 0 else 'auto')}",
            f"Videos generated: {len(video_paths)}/{len(jobs)}",
        ]
        for job in jobs:
            summary = [f"Job {job['index'] + 1}", f"seed={job['seed']}"]
            if job.get("path"):
                summary.append(f"path={job['path']}")
            else:
                summary.append(f"error={job.get('error') or 'unknown failure'}")
            lines.append(" | ".join(summary))

        return (video_output, video_paths, "\n".join(lines))

    def _run_replicate_with_retry(self, replicate, input_args, max_retries=3, base_delay=5.0):
        """Call replicate.run with exponential backoff on rate limits."""
        last_error = None
        for attempt in range(max_retries):
            try:
                return replicate.run(REPLICATE_MODEL, input=input_args)
            except Exception as e:
                last_error = e
                err = str(e)
                if "429" in err or "503" in err or "rate" in err.lower():
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"[DigitDance] Replicate rate limited "
                        f"(attempt {attempt + 1}/{max_retries}), retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_error

    def _save_replicate_outputs(self, output, job_index, batch_timestamp, batch_uuid):
        """Replicate may return FileOutput, list[FileOutput], dict, or a URL string."""
        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)

        items = []
        if isinstance(output, list):
            items = output
        elif isinstance(output, dict):
            for key in ("video", "videos", "output"):
                if key in output:
                    val = output[key]
                    items = val if isinstance(val, list) else [val]
                    break
            if not items:
                items = [v for v in output.values() if hasattr(v, "read") or isinstance(v, str)]
        else:
            items = [output]

        paths = []
        for i, item in enumerate(items):
            if item is None:
                continue
            suffix = f"_{i}" if i else ""
            local_path = os.path.join(
                temp_dir,
                f"dance_{batch_timestamp}_{batch_uuid}_{job_index}{suffix}.mp4",
            )
            try:
                if hasattr(item, "read"):
                    with open(local_path, "wb") as f:
                        f.write(item.read())
                elif isinstance(item, str):
                    urllib.request.urlretrieve(item, local_path)
                else:
                    logger.warning(
                        f"[DigitDance] Skipping unknown Replicate output type at index {i}: "
                        f"{type(item).__name__}"
                    )
                    continue
                paths.append(local_path)
                logger.info(f"[DigitDance] Saved Replicate video {i}: {local_path}")
            except Exception as e:
                logger.error(f"[DigitDance] Failed to save Replicate output {i}: {e}")

        return paths


class DigitReplicateSeedance(DigitDanceVideo):
    """Deprecated alias. Use DIGIT Seedance Video with provider=replicate.

    Keeps the old node's widget surface so saved workflows load untouched,
    then forwards to the unified Replicate backend.
    """

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
                    "placeholder": "Describe the video. Use double quotes for spoken dialogue.",
                }),
                "resolution": (RESOLUTIONS, {
                    "default": "720p",
                    "tooltip": "4k output is 10-bit H.265/HEVC.",
                }),
                "aspect_ratio": (["21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "adaptive"],
                                 {"default": "16:9"}),
                "duration_seconds": ("INT", {
                    "default": 5, "min": -1, "max": 15,
                    "tooltip": "Video length in seconds (4-15). Use -1 for model-selected duration.",
                }),
                "generate_audio": ("BOOLEAN", {"default": True}),
                "seed": ("INT", {"default": 0, "min": 0, "max": MAX_SEED}),
            },
            "optional": {
                "first_frame": ("IMAGE", {"tooltip": "Image-to-video mode. Mutually exclusive with reference inputs."}),
                "last_frame": ("IMAGE", {"tooltip": "Optional end frame for first-to-last interpolation."}),
                "negative_prompt": ("STRING", {"default": "", "multiline": True}),
                **ref_image_sockets,
                **ref_video_sockets,
                **ref_audio_sockets,
            },
        }

    def generate(self, prompt, resolution, aspect_ratio, duration_seconds,
                 generate_audio, seed,
                 first_frame=None, last_frame=None, negative_prompt="",
                 **kwargs):
        logger.warning(
            "[DigitReplicateSeedance] Deprecated node. "
            "Use DIGIT Seedance Video with provider=replicate."
        )
        duration = "auto" if int(duration_seconds) < 0 else str(int(duration_seconds))
        aspect = "auto" if aspect_ratio == "adaptive" else aspect_ratio
        return super().generate(
            prompt=prompt,
            provider="replicate",
            model="seedance-2.0",
            resolution=resolution,
            aspect_ratio=aspect,
            duration=duration,
            generate_audio=generate_audio,
            bitrate_mode="standard",
            batch_count=1,
            seed=seed,
            first_frame=first_frame,
            last_frame=last_frame,
            negative_prompt=negative_prompt,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Cost-estimate route for the node's live summary strip
# (web/digit_seedance_cost.js). Registered only when running inside ComfyUI.
# ---------------------------------------------------------------------------
try:
    from server import PromptServer
    from aiohttp import web

    @PromptServer.instance.routes.post("/digit/seedance/estimate")
    async def _digit_seedance_estimate(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        provider = body.get("provider", "fal")
        mode = body.get("mode", "text_to_video")
        resolution = body.get("resolution", "720p")
        duration = body.get("duration", "5")
        batch_count = body.get("batch_count", 1)
        muapi_route = body.get("muapi_route", "auto")
        fal_model = body.get("model", "seedance-2.0")
        has_video_refs = bool(body.get("has_video_refs", False))
        source_duration = body.get("source_duration_seconds")

        def _estimate_for(duration_seconds, use_live):
            return seedance_pricing.estimate(
                provider, mode, resolution, duration_seconds, batch_count,
                muapi_route=muapi_route, fal_model=fal_model,
                has_video_refs=has_video_refs, use_live=use_live,
                source_duration_seconds=source_duration,
            )

        import asyncio
        loop = asyncio.get_event_loop()
        max_seconds = seedance_pricing.max_duration_seconds(fal_model)
        if duration in ("auto", "same_as_input"):
            low = await loop.run_in_executor(None, _estimate_for, 4, False)
            high = await loop.run_in_executor(None, _estimate_for, max_seconds, False)
            return web.json_response({
                "range": True,
                "low": low,
                "high": high,
                "duration_span": f"4–{max_seconds}s",
            })

        try:
            duration_seconds = max(1, int(duration))
        except (TypeError, ValueError):
            duration_seconds = 5
        summary = await loop.run_in_executor(None, _estimate_for, duration_seconds, True)
        return web.json_response({"range": False, "summary": summary})

except Exception:
    # Standalone import (tests) or an old ComfyUI without PromptServer.
    pass
