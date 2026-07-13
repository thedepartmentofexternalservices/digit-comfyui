import io
import logging
import os
import time
import uuid

import numpy as np
from PIL import Image as PILImage

import folder_paths
from .gcp_config import resolve_gcp_config, resolve_gcs_uri, default_project, default_region, default_gcs_uri

logger = logging.getLogger(__name__)


def _tensor_to_png_bytes(tensor):
    """Convert a single ComfyUI IMAGE tensor (H,W,C float32 0-1) to PNG bytes."""
    img_np = tensor.cpu().numpy()
    img_np = (img_np * 255).clip(0, 255).astype(np.uint8)
    img = PILImage.fromarray(img_np)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _tensor_to_genai_image(tensor, types):
    """Convert a ComfyUI IMAGE tensor (B,H,W,C) to a google.genai Image (first frame only)."""
    png_bytes = _tensor_to_png_bytes(tensor[0])
    return types.Image(image_bytes=png_bytes, mime_type="image/png")


class DigitVeoVideo:
    MODELS = [
        "veo-3.1-generate-001",
        "veo-3.1-fast-generate-001",
        "veo-3.0-generate-001",
        "veo-3.0-fast-generate-001",
        "veo-2.0-generate-001",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"default": "", "multiline": True}),
                "model": (cls.MODELS, {"default": cls.MODELS[0]}),
                "aspect_ratio": (["16:9", "9:16"], {"default": "16:9"}),
                "resolution": (["720p", "1080p"], {"default": "720p"}),
                "duration_seconds": ("INT", {"default": 8, "min": 4, "max": 8, "step": 2}),
                "generate_audio": ("BOOLEAN", {"default": True}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2147483647}),
                "gcp_project_id": ("STRING", {"default": default_project(), "tooltip": "GCP project ID. Auto-detected from DIGIT_GCP_PROJECT env var or GCP metadata."}),
                "gcp_region": ("STRING", {"default": default_region(), "tooltip": "GCP region. Auto-detected from DIGIT_GCP_REGION env var or GCP metadata. Defaults to 'us-central1' for Veo."}),
            },
            "optional": {
                "first_frame": ("IMAGE",),
                "last_frame": ("IMAGE",),
                "reference1": ("IMAGE",),
                "reference2": ("IMAGE",),
                "reference3": ("IMAGE",),
                "negative_prompt": ("STRING", {"default": "", "multiline": True}),
                "person_generation": (["allow_adult", "dont_allow"], {"default": "allow_adult"}),
                "sample_count": ("INT", {"default": 1, "min": 1, "max": 4}),
                "compression_quality": (["lossless", "optimized"], {"default": "lossless"}),
                "output_gcs_uri": ("STRING", {"default": default_gcs_uri(), "tooltip": "GCS bucket URI for lossless output. Auto-detected from DIGIT_GCS_URI env var, e.g. gs://my-bucket/output/"}),
                "enhance_prompt": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("VIDEO", "VIDEO_PATHS", "STRING")
    RETURN_NAMES = ("video", "video_paths", "status")
    FUNCTION = "generate"
    CATEGORY = "DIGIT"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        seed = kwargs.get("seed", 0)
        if seed == 0:
            return float("nan")
        return seed

    def generate(
        self,
        prompt,
        model,
        aspect_ratio,
        resolution,
        duration_seconds,
        generate_audio,
        seed,
        first_frame=None,
        last_frame=None,
        reference1=None,
        reference2=None,
        reference3=None,
        negative_prompt="",
        person_generation="allow_adult",
        sample_count=1,
        compression_quality="optimized",
        output_gcs_uri="",
        enhance_prompt=True,
        gcp_project_id="",
        gcp_region="us-central1",
    ):
        from google import genai
        from google.genai import types
        from comfy_api.latest._input_impl.video_types import VideoFromFile

        if not prompt:
            raise ValueError("Prompt is required")

        project, region = resolve_gcp_config(gcp_project_id, gcp_region, region_fallback="us-central1")

        client = genai.Client(
            vertexai=True,
            project=project,
            location=region,
        )

        # Detect mode
        has_references = any(r is not None for r in [reference1, reference2, reference3])
        has_first_frame = first_frame is not None

        if has_references and has_first_frame:
            raise ValueError("Cannot use both reference images and first_frame simultaneously. Use one mode or the other.")

        # Build config
        config_kwargs = {
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "duration_seconds": duration_seconds,
            "generate_audio": generate_audio,
            "person_generation": person_generation,
            "number_of_videos": sample_count,
            "enhance_prompt": enhance_prompt,
            "seed": seed if seed > 0 else None,
        }

        if negative_prompt and negative_prompt.strip():
            config_kwargs["negative_prompt"] = negative_prompt.strip()

        # Compression / GCS output
        gcs_uri = resolve_gcs_uri(output_gcs_uri)
        if compression_quality == "lossless":
            if not gcs_uri:
                logger.warning("Lossless compression requires output_gcs_uri. Falling back to optimized.")
                config_kwargs["compression_quality"] = "optimized"
            else:
                config_kwargs["compression_quality"] = "lossless"
                config_kwargs["output_gcs_uri"] = gcs_uri
        else:
            config_kwargs["compression_quality"] = "optimized"

        # Reference images
        if has_references:
            reference_images = []
            for ref_tensor in [reference1, reference2, reference3]:
                if ref_tensor is not None:
                    ref_img = _tensor_to_genai_image(ref_tensor, types)
                    reference_images.append(types.VideoGenerationReferenceImage(
                        image=ref_img,
                        reference_type="asset",
                    ))
            config_kwargs["reference_images"] = reference_images

        # Last frame (for interpolation with first_frame)
        if last_frame is not None and has_first_frame:
            config_kwargs["last_frame"] = _tensor_to_genai_image(last_frame, types)

        config = types.GenerateVideosConfig(**config_kwargs)

        # Build generate_videos kwargs
        gen_kwargs = {
            "model": model,
            "prompt": prompt,
            "config": config,
        }

        # Image-to-video mode
        if has_first_frame:
            gen_kwargs["image"] = _tensor_to_genai_image(first_frame, types)

        # Determine mode string for status
        if has_references:
            mode = "reference"
        elif has_first_frame:
            mode = "image-to-video"
        else:
            mode = "text-to-video"

        # Generate with retry
        operation = self._generate_with_retry(client, gen_kwargs)

        # Poll for completion
        poll_count = 0
        while not operation.done:
            time.sleep(20)
            operation = client.operations.get(operation)
            poll_count += 1
            logger.info(f"Polling Veo operation (attempt {poll_count})...")

        # Check for errors
        if hasattr(operation, 'error') and operation.error:
            raise RuntimeError(f"Veo generation failed: {operation.error}")

        # Process response
        video_paths = self._process_response(operation)

        if not video_paths:
            raise RuntimeError("Veo returned no videos. The content may have been filtered by safety settings.")

        # Build status message
        status_parts = [
            f"Model: {model}",
            f"Mode: {mode}",
            f"Duration: {duration_seconds}s",
            f"Resolution: {resolution}",
            f"Audio: {generate_audio}",
            f"Videos generated: {len(video_paths)}",
        ]
        for i, path in enumerate(video_paths):
            status_parts.append(f"Video {i + 1}: {path}")
        status_text = "\n".join(status_parts)

        # Return first video as VIDEO type, all paths as VIDEO_PATHS
        video_output = VideoFromFile(video_paths[0])
        return (video_output, video_paths, status_text)

    def _generate_with_retry(self, client, gen_kwargs, max_retries=3, base_delay=5.0):
        """Call generate_videos with exponential backoff on 429/503."""
        last_error = None
        for attempt in range(max_retries):
            try:
                return client.models.generate_videos(**gen_kwargs)
            except Exception as e:
                last_error = e
                error_str = str(e)
                if "429" in error_str or "503" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Veo API rate limited (attempt {attempt + 1}/{max_retries}), retrying in {delay}s: {e}")
                    time.sleep(delay)
                else:
                    raise
        raise last_error

    def _process_response(self, operation):
        """Extract video files from the operation response."""
        temp_dir = folder_paths.get_temp_directory()
        timestamp = int(time.time())
        unique_id = uuid.uuid4().hex[:8]

        # Try multiple paths to find generated videos (SDK response structure varies)
        generated_videos = None
        possible_paths = [
            lambda op: getattr(op.response, "generated_videos", None) if hasattr(op, "response") else None,
            lambda op: getattr(op.result, "generated_videos", None) if hasattr(op, "result") else None,
            lambda op: op.response.get("generated_videos") if hasattr(op, "response") and isinstance(op.response, dict) else None,
            lambda op: op.response if hasattr(op, "response") and isinstance(op.response, list) else None,
            lambda op: [op.response] if hasattr(op, "response") and hasattr(op.response, "video") else None,
            lambda op: [op.result] if hasattr(op, "result") and hasattr(op.result, "video") else None,
        ]

        for path_fn in possible_paths:
            try:
                result = path_fn(operation)
                if result:
                    generated_videos = result
                    break
            except Exception:
                continue

        if not generated_videos:
            logger.error("Could not extract generated videos from operation response.")
            return []

        video_paths = []
        for i, video_item in enumerate(generated_videos):
            video_path = os.path.join(temp_dir, f"veo_{timestamp}_{unique_id}_{i}.mp4")

            try:
                # Method 1: video.save() for in-memory video data (optimized mode)
                if (hasattr(video_item, "video") and
                        hasattr(video_item.video, "save") and
                        not (hasattr(video_item.video, "uri") and video_item.video.uri)):
                    video_item.video.save(video_path)
                    video_paths.append(video_path)
                    logger.info(f"Saved video via save(): {video_path}")

                # Method 2: GCS URI download (lossless mode)
                elif (hasattr(video_item, "video") and
                      hasattr(video_item.video, "uri") and
                      video_item.video.uri):
                    self._download_from_gcs(video_item.video.uri, video_path)
                    video_paths.append(video_path)
                    logger.info(f"Downloaded video from GCS: {video_path}")

                # Method 3: Direct video_bytes attribute
                elif hasattr(video_item, "video_bytes"):
                    with open(video_path, "wb") as f:
                        f.write(video_item.video_bytes)
                    video_paths.append(video_path)
                    logger.info(f"Saved video from bytes: {video_path}")

                else:
                    logger.warning(f"Could not extract video data from item {i}: {type(video_item)}")

            except Exception as e:
                logger.error(f"Error saving video {i}: {e}")

        return video_paths

    def _download_from_gcs(self, gcs_uri, local_path):
        """Download a file from Google Cloud Storage."""
        from google.cloud import storage

        # Parse gs://bucket-name/path/to/file
        uri_parts = gcs_uri.replace("gs://", "").split("/", 1)
        bucket_name = uri_parts[0]
        blob_path = uri_parts[1] if len(uri_parts) > 1 else ""

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.download_to_filename(local_path)
        logger.info(f"Downloaded {gcs_uri} to {local_path}")
