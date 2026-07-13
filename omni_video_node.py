"""DIGIT Gemini Omni Video node — conversational video generation via Interactions API.

Uses gemini-omni-flash-preview through Vertex AI's Interactions API.
Supports text-to-video, image-to-video, reference images, video editing,
and stateful follow-up edits via previous_interaction_id.
"""

import base64
import logging
import os
import time
import uuid

import folder_paths
from .gcp_config import resolve_gcp_config, resolve_gcs_uri, default_project, default_region, default_gcs_uri
from .veo_video_node import _tensor_to_png_bytes

logger = logging.getLogger(__name__)

MAX_REFERENCE_IMAGES = 7


class DigitOmniVideo:
    MODELS = [
        "gemini-omni-flash-preview",
    ]

    TASKS = [
        "auto",
        "text_to_video",
        "image_to_video",
        "reference_to_video",
        "edit",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        ref_sockets = {
            f"reference{i}": ("IMAGE",) for i in range(1, MAX_REFERENCE_IMAGES + 1)
        }
        return {
            "required": {
                "prompt": ("STRING", {"default": "", "multiline": True}),
                "model": (cls.MODELS, {"default": cls.MODELS[0]}),
                "aspect_ratio": (["16:9", "9:16"], {"default": "16:9"}),
                "duration_seconds": ("INT", {"default": 8, "min": 3, "max": 10, "step": 1}),
                "task": (cls.TASKS, {"default": "auto"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2147483647}),
                "gcp_project_id": ("STRING", {
                    "default": default_project(),
                    "tooltip": "GCP project ID. Auto-detected from DIGIT_GCP_PROJECT env var or GCP metadata.",
                }),
                "gcp_region": ("STRING", {
                    "default": default_region(),
                    "tooltip": "GCP region. Omni Flash uses 'global'. Auto-detected from DIGIT_GCP_REGION env var.",
                }),
            },
            "optional": {
                "first_frame": ("IMAGE",),
                "source_video": ("VIDEO",),
                "previous_interaction_id": ("STRING", {"default": ""}),
                "delivery": (["inline", "uri"], {"default": "inline"}),
                "output_gcs_uri": ("STRING", {
                    "default": default_gcs_uri(),
                    "tooltip": "GCS URI required for Vertex URI delivery, e.g. gs://my-bucket/output/",
                }),
                "store": ("BOOLEAN", {"default": True}),
                "background": ("BOOLEAN", {"default": False}),
                **ref_sockets,
            },
        }

    RETURN_TYPES = ("VIDEO", "VIDEO_PATHS", "STRING", "STRING")
    RETURN_NAMES = ("video", "video_paths", "status", "interaction_id")
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
        duration_seconds,
        task,
        seed,
        gcp_project_id="",
        gcp_region="global",
        first_frame=None,
        source_video=None,
        previous_interaction_id="",
        delivery="inline",
        output_gcs_uri="",
        store=True,
        background=False,
        **kwargs,
    ):
        from google import genai
        from comfy_api.latest._input_impl.video_types import VideoFromFile

        if not prompt and not previous_interaction_id:
            raise ValueError("Prompt is required unless continuing from previous_interaction_id")

        project, region = resolve_gcp_config(gcp_project_id, gcp_region, region_fallback="global")

        client = genai.Client(
            vertexai=True,
            project=project,
            location=region,
        )

        references = [
            kwargs.get(f"reference{i}")
            for i in range(1, MAX_REFERENCE_IMAGES + 1)
            if kwargs.get(f"reference{i}") is not None
        ]

        has_references = len(references) > 0
        has_first_frame = first_frame is not None
        has_source_video = source_video is not None
        has_previous = bool(previous_interaction_id and previous_interaction_id.strip())

        if has_references and has_first_frame:
            raise ValueError(
                "Cannot use reference images and first_frame at the same time. "
                "Use one mode or the other."
            )
        if has_source_video and has_previous:
            raise ValueError(
                "Use either source_video or previous_interaction_id for editing, not both."
            )
        if has_source_video and (has_first_frame or has_references):
            raise ValueError(
                "source_video editing cannot be combined with first_frame or reference images."
            )

        mode = self._detect_mode(
            has_previous=has_previous,
            has_source_video=has_source_video,
            has_references=has_references,
            has_first_frame=has_first_frame,
            task=task,
        )

        if has_previous:
            raise ValueError(
                "previous_interaction_id is not supported on Vertex AI for gemini-omni-flash-preview. "
                "Connect source_video and use an edit prompt instead."
            )

        input_payload = self._build_input(
            client=client,
            prompt=prompt,
            first_frame=first_frame,
            references=references,
            source_video=source_video,
        )

        is_edit = mode == "edit"

        response_format = {"type": "video"}
        if not is_edit:
            response_format["aspect_ratio"] = aspect_ratio

        if delivery == "uri":
            gcs_uri = resolve_gcs_uri(output_gcs_uri)
            if not gcs_uri:
                raise ValueError(
                    "URI delivery on Vertex AI requires output_gcs_uri "
                    "(or DIGIT_GCS_URI env var)."
                )
            response_format["delivery"] = "uri"
            response_format["gcs_uri"] = gcs_uri
        else:
            response_format["delivery"] = "inline"

        create_kwargs = {
            "model": model,
            "input": input_payload,
            "response_format": response_format,
            "store": store,
            "background": background,
        }

        generation_config = {}
        if seed > 0:
            generation_config["seed"] = seed

        resolved_task = task
        if task == "auto":
            resolved_task = self._task_from_mode(mode)
        if resolved_task != "auto" and not has_previous:
            generation_config["video_config"] = {"task": resolved_task}

        if generation_config:
            create_kwargs["generation_config"] = generation_config

        interaction = self._create_with_retry(client, create_kwargs)
        interaction = self._wait_for_completion(client, interaction)

        if interaction.status == "failed":
            raise RuntimeError(f"Omni video generation failed: {getattr(interaction, 'error', interaction.status)}")

        video_paths = self._save_output_video(client, interaction, delivery)
        if not video_paths:
            raise RuntimeError(
                "Omni returned no videos. The content may have been filtered by safety settings."
            )

        status_parts = [
            f"Model: {model}",
            f"Mode: {mode}",
            f"Task: {resolved_task}",
            f"Duration: {duration_seconds}s",
            f"Aspect ratio: {aspect_ratio}",
            f"Delivery: {delivery}",
            f"Interaction ID: {interaction.id}",
            f"Videos generated: {len(video_paths)}",
        ]
        for i, path in enumerate(video_paths):
            status_parts.append(f"Video {i + 1}: {path}")

        return (
            VideoFromFile(video_paths[0]),
            video_paths,
            "\n".join(status_parts),
            interaction.id,
        )

    def _detect_mode(self, has_previous, has_source_video, has_references, has_first_frame, task):
        if task == "edit" or has_previous or has_source_video:
            return "edit"
        if has_references:
            return "reference"
        if has_first_frame:
            return "image-to-video"
        return "text-to-video"

    def _task_from_mode(self, mode):
        return {
            "text-to-video": "text_to_video",
            "image-to-video": "image_to_video",
            "reference": "reference_to_video",
            "edit": "edit",
        }.get(mode, "text_to_video")

    def _build_input(self, client, prompt, first_frame, references, source_video):
        if source_video is not None:
            video_path = self._video_to_path(source_video)
            with open(video_path, "rb") as f:
                video_b64 = base64.b64encode(f.read()).decode("ascii")
            return [
                {"type": "video", "data": video_b64, "mime_type": "video/mp4"},
                {"type": "text", "text": prompt},
            ]

        parts = []
        for ref_tensor in references:
            parts.append(self._image_part(ref_tensor))

        if first_frame is not None:
            parts.append(self._image_part(first_frame))

        if prompt:
            parts.append({"type": "text", "text": prompt})

        if not parts:
            raise ValueError("No input content provided.")

        if len(parts) == 1 and parts[0]["type"] == "text":
            return parts[0]["text"]
        return parts

    def _image_part(self, image_tensor):
        png_bytes = _tensor_to_png_bytes(image_tensor[0])
        return {
            "type": "image",
            "data": base64.b64encode(png_bytes).decode("ascii"),
            "mime_type": "image/png",
        }

    def _video_to_path(self, video_obj):
        try:
            source = video_obj.get_stream_source()
            if isinstance(source, str) and os.path.isfile(source):
                return source
        except Exception:
            pass

        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)
        tmp_path = os.path.join(temp_dir, f"omni_upload_{uuid.uuid4().hex[:8]}.mp4")
        video_obj.save_to(tmp_path)
        return tmp_path

    def _create_with_retry(self, client, create_kwargs, max_retries=3, base_delay=5.0):
        last_error = None
        for attempt in range(max_retries):
            try:
                return client.interactions.create(**create_kwargs)
            except Exception as e:
                last_error = e
                error_str = str(e)
                if "429" in error_str or "503" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Omni API rate limited (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_error

    def _wait_for_completion(self, client, interaction, poll_interval=10):
        poll_count = 0
        while interaction.status not in ("completed", "failed", "cancelled", "incomplete", "budget_exceeded"):
            time.sleep(poll_interval)
            interaction = client.interactions.get(interaction.id)
            poll_count += 1
            logger.info(f"Polling Omni interaction (attempt {poll_count}, status={interaction.status})...")
        return interaction

    def _save_output_video(self, client, interaction, delivery):
        output_video = getattr(interaction, "output_video", None)
        if output_video is None:
            output_video = self._extract_video_from_steps(interaction)
        if output_video is None:
            logger.error("Could not extract video from Omni interaction response.")
            return []

        temp_dir = folder_paths.get_temp_directory()
        timestamp = int(time.time())
        unique_id = uuid.uuid4().hex[:8]
        video_path = os.path.join(temp_dir, f"omni_{timestamp}_{unique_id}.mp4")

        if getattr(output_video, "data", None):
            with open(video_path, "wb") as f:
                f.write(base64.b64decode(output_video.data))
            logger.info(f"Saved Omni video from inline data: {video_path}")
            return [video_path]

        uri = getattr(output_video, "uri", None)
        if not uri:
            logger.error("Omni video output has no data or uri.")
            return []

        if uri.startswith("gs://"):
            self._download_from_gcs(uri, video_path)
            logger.info(f"Downloaded Omni video from GCS: {video_path}")
            return [video_path]

        if delivery == "uri" or "files/" in uri:
            self._download_from_files_api(client, output_video, video_path)
            logger.info(f"Downloaded Omni video from Files API: {video_path}")
            return [video_path]

        raise RuntimeError(f"Unsupported Omni video URI format: {uri}")

    def _extract_video_from_steps(self, interaction):
        steps = getattr(interaction, "steps", None) or []
        for step in steps:
            step_type = getattr(step, "type", None)
            if step_type != "model_output":
                continue
            content = getattr(step, "content", None) or []
            for item in content:
                item_type = getattr(item, "type", None)
                if item_type == "video":
                    return item
        return None

    def _download_from_files_api(self, client, output_video, local_path):
        uri = output_video.uri
        file_name = uri.split("/")[-1].split(":")[0]
        if not file_name.startswith("files/"):
            file_name = f"files/{file_name}"

        logger.info("Waiting for Omni video file to become ACTIVE...")
        while True:
            file_info = client.files.get(name=file_name)
            state = getattr(file_info.state, "name", file_info.state)
            if state == "ACTIVE":
                break
            if state == "FAILED":
                raise RuntimeError("Omni video file processing failed.")
            time.sleep(5)

        video_bytes = client.files.download(file=output_video.uri)
        with open(local_path, "wb") as f:
            f.write(video_bytes)

    def _download_from_gcs(self, gcs_uri, local_path):
        from google.cloud import storage

        uri_parts = gcs_uri.replace("gs://", "").split("/", 1)
        bucket_name = uri_parts[0]
        blob_path = uri_parts[1] if len(uri_parts) > 1 else ""

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.download_to_filename(local_path)
        logger.info(f"Downloaded {gcs_uri} to {local_path}")
