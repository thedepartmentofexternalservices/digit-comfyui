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
import time
import urllib.request
import uuid

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
RESOLUTIONS = ["480p", "720p"]
ASPECT_RATIOS = ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]

MAX_REFERENCE_IMAGES = 9
MAX_REFERENCE_VIDEOS = 3
MAX_REFERENCE_AUDIOS = 3


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
    RETURN_TYPES = ("VIDEO", "VEO_PATHS", "STRING")
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
                    "placeholder": "Describe the video. In reference mode, use @image1, @video1, @audio1 to cite inputs.",
                }),
                "model": (MODELS, {"default": "seedance-2.0"}),
                "resolution": (RESOLUTIONS, {"default": "720p"}),
                "aspect_ratio": (ASPECT_RATIOS, {"default": "16:9"}),
                "duration_seconds": ("INT", {
                    "default": 5, "min": -1, "max": 15,
                    "tooltip": "Video length in seconds (4-15). Use -1 for smart duration.",
                }),
                "generate_audio": ("BOOLEAN", {"default": True}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2147483647}),
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

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        seed = kwargs.get("seed", 0)
        if seed == 0:
            return float("nan")
        return seed

    def generate(self, prompt, model, resolution, aspect_ratio,
                 duration_seconds, generate_audio, seed,
                 first_frame=None, last_frame=None, negative_prompt="",
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
                "Export FAL_KEY=<your-key> on your GCP instance before starting ComfyUI."
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
        if has_last_frame and not has_first_frame:
            raise ValueError("last_frame requires first_frame to be connected.")

        # Detect mode
        if has_refs:
            mode = "reference_to_video"
        elif has_first_frame:
            mode = "image_to_video"
        else:
            mode = "text_to_video"

        app_id = SEEDANCE_APPS[model][mode]
        logger.info(f"[DigitDance] Mode: {mode} | App: {app_id}")

        # Build arguments
        args = {
            "prompt": prompt.strip(),
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "duration": duration_seconds,
            "generate_audio": generate_audio,
        }
        if seed > 0:
            args["seed"] = seed
        if negative_prompt and negative_prompt.strip():
            args["negative_prompt"] = negative_prompt.strip()

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

        logger.info(f"[DigitDance] Submitting request to {app_id}...")
        result = self._submit_with_retry(fal_client, app_id, args)

        video_paths = self._download_results(result)
        if not video_paths:
            raise RuntimeError(
                "Seedance returned no videos. The content may have been filtered."
            )

        from comfy_api.latest._input_impl.video_types import VideoFromFile
        video_output = VideoFromFile(video_paths[0])

        status = self._format_status(mode, model, args, result, video_paths)
        return (video_output, video_paths, status)

    def _submit_with_retry(self, fal_client, app_id, args, max_retries=3, base_delay=5.0):
        """Call fal_client.subscribe with exponential backoff on rate limits."""
        last_error = None
        for attempt in range(max_retries):
            try:
                return fal_client.subscribe(
                    app_id,
                    arguments=args,
                    with_logs=True,
                    on_queue_update=lambda s: logger.info(f"[DigitDance] queue: {s}"),
                )
            except Exception as e:
                last_error = e
                error_str = str(e)
                if "429" in error_str or "503" in error_str or "rate" in error_str.lower():
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"[DigitDance] Rate limited (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_error

    def _download_results(self, result):
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

        timestamp = int(time.time())
        unique_id = uuid.uuid4().hex[:8]
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
                temp_dir, f"dance_{timestamp}_{unique_id}_{i}.mp4"
            )
            try:
                urllib.request.urlretrieve(url, local_path)
                paths.append(local_path)
                logger.info(f"[DigitDance] Downloaded video {i}: {local_path}")
            except Exception as e:
                logger.error(f"[DigitDance] Failed to download {url}: {e}")

        return paths

    def _format_status(self, mode, model, args, result, video_paths):
        lines = [
            f"Model: {model}",
            f"Mode: {mode}",
            f"Resolution: {args.get('resolution')}",
            f"Aspect: {args.get('aspect_ratio')}",
            f"Duration: {args.get('duration')}s",
            f"Audio: {args.get('generate_audio')}",
            f"Videos generated: {len(video_paths)}",
        ]
        if isinstance(result, dict):
            if "seed" in result:
                lines.append(f"Seed: {result['seed']}")
            if "request_id" in result:
                lines.append(f"Request ID: {result['request_id']}")
        for i, path in enumerate(video_paths):
            lines.append(f"Video {i + 1}: {path}")
        return "\n".join(lines)
