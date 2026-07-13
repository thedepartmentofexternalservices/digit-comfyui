"""DIGIT Replicate Seedance Video node — bytedance/seedance-2.0 via Replicate.

Mirror of the fal.ai Seedance node, routed through Replicate's single endpoint.
Mode auto-detects from connected inputs:
- No image/reference inputs        → text-to-video
- first_frame connected            → image-to-video (optional last_frame)
- Any reference_image/video/audio  → reference-to-video

Auth via REPLICATE_API_TOKEN environment variable.
"""

import logging
import os
import time
import urllib.request
import uuid

import numpy as np
from PIL import Image as PILImage

import folder_paths

logger = logging.getLogger("DigitReplicateSeedance")

REPLICATE_MODEL = "bytedance/seedance-2.0"

# Replicate's seedance-2.0 supports 480p through 4k; 4k is 10-bit H.265/HEVC.
RESOLUTIONS = ["480p", "720p", "1080p", "4k"]
ASPECT_RATIOS = ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "adaptive"]

MAX_REFERENCE_IMAGES = 9
MAX_REFERENCE_VIDEOS = 3
MAX_REFERENCE_AUDIOS = 3


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


class DigitReplicateSeedance:
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
                    "placeholder": "Describe the video. Use double quotes for spoken dialogue.",
                }),
                "resolution": (RESOLUTIONS, {
                    "default": "720p",
                    "tooltip": "4k output is 10-bit H.265/HEVC.",
                }),
                "aspect_ratio": (ASPECT_RATIOS, {"default": "16:9"}),
                "duration_seconds": ("INT", {
                    "default": 5, "min": -1, "max": 15,
                    "tooltip": "Video length in seconds (4-15). Use -1 for model-selected duration.",
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

    def generate(self, prompt, resolution, aspect_ratio, duration_seconds,
                 generate_audio, seed,
                 first_frame=None, last_frame=None, negative_prompt="",
                 **kwargs):
        try:
            import replicate
        except ImportError:
            raise ImportError(
                "replicate is required for DigitReplicateSeedance. "
                "Install with: pip install replicate"
            )

        if not os.environ.get("REPLICATE_API_TOKEN"):
            raise ValueError(
                "REPLICATE_API_TOKEN environment variable is not set. "
                "Export REPLICATE_API_TOKEN=<your-key> before starting ComfyUI."
            )
        if not prompt or not prompt.strip():
            raise ValueError("Prompt is required.")

        ref_images = [kwargs.get(f"reference_image{i}") for i in range(1, MAX_REFERENCE_IMAGES + 1)]
        ref_images = [img for img in ref_images if img is not None]
        ref_videos = [kwargs.get(f"reference_video{i}") for i in range(1, MAX_REFERENCE_VIDEOS + 1)]
        ref_videos = [v for v in ref_videos if v is not None]
        ref_audios = [kwargs.get(f"reference_audio{i}") for i in range(1, MAX_REFERENCE_AUDIOS + 1)]
        ref_audios = [a for a in ref_audios if a is not None]

        has_refs = bool(ref_images or ref_videos or ref_audios)
        has_first = first_frame is not None
        has_last = last_frame is not None

        if has_refs and (has_first or has_last):
            raise ValueError(
                "Cannot combine first_frame/last_frame with reference inputs. "
                "Use image-to-video mode OR reference-to-video mode, not both."
            )
        if ref_audios and not (ref_images or ref_videos):
            raise ValueError(
                "reference_audio requires at least one reference_image or reference_video."
            )
        if has_last and not has_first:
            raise ValueError("last_frame requires first_frame to be connected.")

        if has_refs:
            mode = "reference_to_video"
        elif has_first:
            mode = "image_to_video"
        else:
            mode = "text_to_video"

        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)

        replicate_input = {
            "prompt": prompt.strip(),
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "duration": int(duration_seconds),
            "audio": bool(generate_audio),
        }
        if seed > 0:
            replicate_input["seed"] = int(seed)
        if negative_prompt and negative_prompt.strip():
            replicate_input["negative_prompt"] = negative_prompt.strip()

        # Replicate's Python SDK accepts open file handles and auto-uploads them.
        # Track them so we can close after the call returns.
        open_files = []
        try:
            if mode == "image_to_video":
                fp = _tensor_to_png_path(first_frame[0], temp_dir)
                fh = open(fp, "rb"); open_files.append(fh)
                replicate_input["image"] = fh
                if has_last:
                    lp = _tensor_to_png_path(last_frame[0], temp_dir)
                    lh = open(lp, "rb"); open_files.append(lh)
                    replicate_input["last_frame"] = lh

            elif mode == "reference_to_video":
                if ref_images:
                    handles = []
                    for img in ref_images:
                        p = _tensor_to_png_path(img[0], temp_dir)
                        h = open(p, "rb"); open_files.append(h); handles.append(h)
                    replicate_input["reference_images"] = handles
                if ref_videos:
                    handles = []
                    for v in ref_videos:
                        p = _video_to_path(v, temp_dir)
                        h = open(p, "rb"); open_files.append(h); handles.append(h)
                    replicate_input["reference_videos"] = handles
                if ref_audios:
                    handles = []
                    for a in ref_audios:
                        p = _audio_to_path(a, temp_dir)
                        h = open(p, "rb"); open_files.append(h); handles.append(h)
                    # Replicate's field name is singular: reference_audio
                    replicate_input["reference_audio"] = handles

            logger.info(
                f"[DigitReplicateSeedance] Mode: {mode} | Submitting to {REPLICATE_MODEL}..."
            )
            try:
                output = self._run_with_retry(replicate, replicate_input)
            except Exception as run_err:
                err_str = str(run_err)
                if "E005" in err_str or "flagged as sensitive" in err_str.lower():
                    logger.warning(
                        f"[DigitReplicateSeedance] Safety filter blocked request: {err_str}"
                    )
                    status = "\n".join([
                        f"Model: {REPLICATE_MODEL}",
                        f"Mode: {mode}",
                        "BLOCKED: Replicate safety filter flagged the request.",
                        "Try a different prompt or remove potentially sensitive content.",
                        f"Detail: {err_str}",
                    ])
                    return (None, [], status)
                raise
        finally:
            for fh in open_files:
                try:
                    fh.close()
                except Exception:
                    pass

        video_paths = self._save_outputs(output)
        if not video_paths:
            raise RuntimeError(
                "Replicate Seedance returned no videos. "
                "The content may have been filtered or the request failed silently."
            )

        from comfy_api.latest._input_impl.video_types import VideoFromFile
        video_output = VideoFromFile(video_paths[0])

        status = self._format_status(mode, replicate_input, video_paths)
        return (video_output, video_paths, status)

    def _run_with_retry(self, replicate, input_args, max_retries=3, base_delay=5.0):
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
                        f"[DigitReplicateSeedance] Rate limited "
                        f"(attempt {attempt + 1}/{max_retries}), retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_error

    def _save_outputs(self, output):
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

        timestamp = int(time.time())
        unique_id = uuid.uuid4().hex[:8]
        paths = []
        for i, item in enumerate(items):
            if item is None:
                continue
            local_path = os.path.join(
                temp_dir, f"replicate_seedance_{timestamp}_{unique_id}_{i}.mp4"
            )
            try:
                if hasattr(item, "read"):
                    with open(local_path, "wb") as f:
                        f.write(item.read())
                elif isinstance(item, str):
                    urllib.request.urlretrieve(item, local_path)
                else:
                    logger.warning(
                        f"[DigitReplicateSeedance] Skipping unknown output type at index {i}: "
                        f"{type(item).__name__}"
                    )
                    continue
                paths.append(local_path)
                logger.info(f"[DigitReplicateSeedance] Saved video {i}: {local_path}")
            except Exception as e:
                logger.error(f"[DigitReplicateSeedance] Failed to save output {i}: {e}")

        return paths

    def _format_status(self, mode, input_args, video_paths):
        lines = [
            f"Model: {REPLICATE_MODEL}",
            f"Mode: {mode}",
            f"Resolution: {input_args.get('resolution')}",
            f"Aspect: {input_args.get('aspect_ratio')}",
            f"Duration: {input_args.get('duration')}s",
            f"Audio: {input_args.get('audio')}",
            f"Videos generated: {len(video_paths)}",
        ]
        if input_args.get("seed"):
            lines.append(f"Seed: {input_args['seed']}")
        for i, p in enumerate(video_paths):
            lines.append(f"Video {i + 1}: {p}")
        return "\n".join(lines)
