import base64
import copy
import io
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests as http_requests
import torch
from PIL import Image

from .gcp_config import resolve_gcp_config, get_gcp_access_token, build_vertex_url, default_project, default_region
from .gemini_image_models import (
    GEMINI_IMAGE_MODELS,
    DEFAULT_GEMINI_IMAGE_MODEL,
    MODELS_1K_ONLY,
    MODELS_NO_THINKING,
    RESOLUTIONS,
)

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_SYSTEM_PROMPT = (
    "You are an expert image-generation engine. You must ALWAYS produce an image.\n"
    "Interpret all user input—regardless of format, intent, or abstraction—as literal visual directives for image composition.\n"
    "If a prompt is conversational or lacks specific visual details, you must creatively invent a concrete visual scenario that depicts the concept.\n"
    "Prioritize generating the visual representation above any text, formatting, or conversational requests."
)

SAFETY_THRESHOLD_OPTIONS = [
    "BLOCK_NONE",
    "BLOCK_ONLY_HIGH",
    "BLOCK_MEDIUM_AND_ABOVE",
    "BLOCK_LOW_AND_ABOVE",
]

HARM_CATEGORIES = [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_CIVIC_INTEGRITY",
]

# Image-specific safety categories (used by Nano Banana 2 / Google's nodes)
IMAGE_HARM_CATEGORIES = [
    "HARM_CATEGORY_IMAGE_HATE",
    "HARM_CATEGORY_IMAGE_DANGEROUS_CONTENT",
    "HARM_CATEGORY_IMAGE_HARASSMENT",
    "HARM_CATEGORY_IMAGE_SEXUALLY_EXPLICIT",
]


def _image_tensor_to_png_bytes(image_tensor):
    """Convert a single ComfyUI IMAGE tensor (H,W,C float32 0-1) to PNG bytes."""
    img_np = image_tensor.cpu().numpy()
    img_np = (img_np * 255).clip(0, 255).astype(np.uint8)
    img = Image.fromarray(img_np)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _png_bytes_to_tensor(png_bytes):
    """Convert PNG bytes to a ComfyUI IMAGE tensor (1,H,W,3 float32 0-1)."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    img_np = np.array(img).astype(np.float32) / 255.0
    return torch.from_numpy(img_np).unsqueeze(0)


def _stack_image_batch(tensors):
    """Concatenate (1,H,W,3) tensors into one (N,H,W,3) batch, resizing mismatches to the first image's size."""
    if len(tensors) == 1:
        return tensors[0]
    target_h, target_w = tensors[0].shape[1], tensors[0].shape[2]
    resized = []
    for t in tensors:
        if t.shape[1] != target_h or t.shape[2] != target_w:
            img_np = (t[0].cpu().numpy() * 255).clip(0, 255).astype(np.uint8)
            img = Image.fromarray(img_np).resize((target_w, target_h), Image.LANCZOS)
            t = torch.from_numpy(np.array(img).astype(np.float32) / 255.0).unsqueeze(0)
        resized.append(t)
    return torch.cat(resized, dim=0)


class DigitGeminiImage:
    MODELS = GEMINI_IMAGE_MODELS

    ASPECT_RATIOS = [
        "auto", "1:1", "2:3", "3:2", "3:4", "4:1", "4:3",
        "4:5", "5:4", "8:1", "9:16", "16:9", "21:9",
    ]

    THINKING_LEVELS = ["MINIMAL", "HIGH"]

    RESOLUTIONS = RESOLUTIONS

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"default": "", "multiline": True}),
                "model": (cls.MODELS, {"default": DEFAULT_GEMINI_IMAGE_MODEL}),
                "aspect_ratio": (cls.ASPECT_RATIOS, {"default": "16:9"}),
                "resolution": (cls.RESOLUTIONS, {"default": "1K"}),
                "thinking_level": (cls.THINKING_LEVELS, {"default": "MINIMAL", "tooltip": "Thinking level for image generation. HIGH may improve quality."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2147483647}),
                "temperature": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.01}),
                "gcp_project_id": ("STRING", {"default": default_project(), "tooltip": "GCP project ID. Auto-detected from DIGIT_GCP_PROJECT env var or GCP metadata."}),
                "gcp_region": ("STRING", {"default": default_region(), "tooltip": "GCP region. Auto-detected from DIGIT_GCP_REGION env var or GCP metadata. Defaults to 'global'."}),
            },
            "optional": {
                "image1": ("IMAGE",),
                "image2": ("IMAGE",),
                "image3": ("IMAGE",),
                "image4": ("IMAGE",),
                "image5": ("IMAGE",),
                "image6": ("IMAGE",),
                "image7": ("IMAGE",),
                "image8": ("IMAGE",),
                "image9": ("IMAGE",),
                "system_instruction": ("STRING", {"default": DEFAULT_IMAGE_SYSTEM_PROMPT, "multiline": True}),
                "top_p": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "top_k": ("INT", {"default": 32, "min": 1, "max": 64}),
                "harassment_threshold": (SAFETY_THRESHOLD_OPTIONS, {"default": "BLOCK_NONE"}),
                "hate_speech_threshold": (SAFETY_THRESHOLD_OPTIONS, {"default": "BLOCK_NONE"}),
                "sexually_explicit_threshold": (SAFETY_THRESHOLD_OPTIONS, {"default": "BLOCK_NONE"}),
                "dangerous_content_threshold": (SAFETY_THRESHOLD_OPTIONS, {"default": "BLOCK_NONE"}),
                "batch_count": ("INT", {"default": 1, "min": 1, "max": 128, "tooltip": "Number of images to generate. Each is a separate API call fired in parallel; results return as one IMAGE batch."}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "text")
    FUNCTION = "generate"
    CATEGORY = "DIGIT"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        seed = kwargs.get("seed", 0)
        if seed == 0:
            return float("nan")
        return seed

    def _build_safety_settings(self, harassment, hate_speech, sexually_explicit, dangerous_content):
        """Build safety settings matching Nano Banana 2 format (text + image categories)."""
        settings = [
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": hate_speech},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": dangerous_content},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": sexually_explicit},
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": harassment},
            {"category": "HARM_CATEGORY_IMAGE_HATE", "threshold": hate_speech},
            {"category": "HARM_CATEGORY_IMAGE_DANGEROUS_CONTENT", "threshold": dangerous_content},
            {"category": "HARM_CATEGORY_IMAGE_HARASSMENT", "threshold": harassment},
            {"category": "HARM_CATEGORY_IMAGE_SEXUALLY_EXPLICIT", "threshold": sexually_explicit},
        ]
        return settings

    def generate(
        self,
        prompt,
        model,
        aspect_ratio,
        resolution,
        thinking_level,
        seed,
        temperature,
        image1=None,
        image2=None,
        image3=None,
        image4=None,
        image5=None,
        image6=None,
        image7=None,
        image8=None,
        image9=None,
        system_instruction="",
        top_p=1.0,
        top_k=32,
        harassment_threshold="BLOCK_NONE",
        hate_speech_threshold="BLOCK_NONE",
        sexually_explicit_threshold="BLOCK_NONE",
        dangerous_content_threshold="BLOCK_NONE",
        batch_count=1,
        gcp_project_id="",
        gcp_region="global",
    ):
        if not prompt:
            raise ValueError("Prompt is required")
        if model in MODELS_1K_ONLY and resolution != "1K":
            raise ValueError(
                f"Model {model} (Nano Banana 2 Lite) only supports 1K resolution, got {resolution}."
            )

        project, region = resolve_gcp_config(gcp_project_id, gcp_region)
        token = get_gcp_access_token()

        # Build content parts — text first, then images (matching Nano Banana 2 order)
        parts = [{"text": prompt}]

        for img_tensor in [image1, image2, image3, image4, image5, image6, image7, image8, image9]:
            if img_tensor is not None:
                for i in range(img_tensor.shape[0]):
                    png_bytes = _image_tensor_to_png_bytes(img_tensor[i])
                    b64 = base64.b64encode(png_bytes).decode("utf-8")
                    parts.append({"inlineData": {"mimeType": "image/png", "data": b64}})

        # Build imageConfig — matching Nano Banana 2 exactly
        image_config = {
            "imageSize": resolution,
            "imageOutputOptions": {"mimeType": "image/png"},
        }
        if aspect_ratio != "auto":
            image_config["aspectRatio"] = aspect_ratio

        # Build request body — matching Nano Banana 2 structure.
        # gemini-3-pro-image-preview and gemini-2.5-flash-image 400 on thinkingConfig.
        generation_config = {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": image_config,
        }
        if model not in MODELS_NO_THINKING:
            generation_config["thinkingConfig"] = {"thinkingLevel": thinking_level}

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation_config,
            "safetySettings": self._build_safety_settings(
                harassment_threshold, hate_speech_threshold,
                sexually_explicit_threshold, dangerous_content_threshold,
            ),
        }

        if system_instruction and system_instruction.strip():
            body["systemInstruction"] = {"parts": [{"text": system_instruction.strip()}]}

        url = build_vertex_url(project, region, model)

        # Per-item seeds: seed=0 rolls a fresh random seed per image,
        # a fixed seed gives item i seed+i so the batch is reproducible.
        if seed == 0:
            seeds = [random.randint(1, 2147483647) for _ in range(batch_count)]
        else:
            seeds = [(seed + i - 1) % 2147483647 + 1 for i in range(batch_count)]

        logger.warning("DIGIT Gemini Image config: model=%s, imageSize=%s, thinkingLevel=%s, aspect_ratio=%s, batch_count=%d, seeds=%s",
                        model, resolution, thinking_level, aspect_ratio, batch_count, seeds)
        logger.warning("DIGIT Gemini Image URL: %s", url)

        # Fire all batch items in parallel, one API call each with its own seed.
        # Seed is best-effort: Vertex accepts it on gemini-2.5-flash-image, but the
        # Gemini 3 image models (Nano Banana Pro family) reject unknown generationConfig
        # fields with HTTP 400. Those models are natively non-deterministic, so batch
        # variety survives without it — retry seedless instead of failing the item.
        def _one_call(item_seed):
            item_body = copy.deepcopy(body)
            item_body["generationConfig"]["seed"] = item_seed
            try:
                return self._call_with_retry(url, token, item_body)
            except http_requests.exceptions.HTTPError as e:
                resp = e.response
                if resp is not None and resp.status_code == 400 and "seed" in resp.text.lower():
                    logger.warning("Model %s rejected the seed field (HTTP 400); retrying without it. "
                                   "Batch variety comes from the model's own sampling.", model)
                    del item_body["generationConfig"]["seed"]
                    return self._call_with_retry(url, token, item_body)
                raise

        if batch_count == 1:
            responses = [_one_call(seeds[0])]
        else:
            with ThreadPoolExecutor(max_workers=batch_count) as pool:
                responses = list(pool.map(_one_call, seeds))

        batch_tensors = []
        batch_texts = []
        for batch_idx, response_data in enumerate(responses):
            tensor, texts = self._parse_response(response_data)
            if tensor is None:
                logger.warning("Gemini returned no image for batch item %d/%d (seed=%d). Using blank fallback.",
                               batch_idx + 1, batch_count, seeds[batch_idx])
                tensor = torch.zeros((1, 1024, 1024, 3))
            batch_tensors.append(tensor)
            batch_texts.extend(texts)
            logger.warning("DIGIT Gemini Image output %d/%d: seed=%d, shape=%s (H=%d, W=%d)",
                            batch_idx + 1, batch_count, seeds[batch_idx],
                            tensor.shape, tensor.shape[1], tensor.shape[2])

        output_image = _stack_image_batch(batch_tensors)
        output_text = "\n".join(batch_texts)

        return (output_image, output_text)

    def _parse_response(self, response_data):
        """Parse a Vertex response into (best image tensor or None, list of text parts)."""
        image_tensors = []
        text_parts = []

        if "candidates" in response_data:
            for candidate in response_data["candidates"]:
                content = candidate.get("content") or {}
                for part in content.get("parts", []):
                    if "inlineData" in part:
                        mime = part["inlineData"].get("mimeType", "")
                        if "image" in mime:
                            img_bytes = base64.b64decode(part["inlineData"]["data"])
                            tensor = _png_bytes_to_tensor(img_bytes)
                            image_tensors.append(tensor)
                    elif "text" in part:
                        text_parts.append(part["text"])

        if not image_tensors:
            if response_data.get("error"):
                logger.warning("DIGIT Gemini Image API error: %s", response_data["error"])
            if response_data.get("promptFeedback"):
                logger.warning("DIGIT Gemini Image promptFeedback: %s", response_data["promptFeedback"])
            for i, candidate in enumerate(response_data.get("candidates", [])):
                content = candidate.get("content") or {}
                parts = content.get("parts", [])
                logger.warning(
                    "DIGIT Gemini Image candidate[%d]: finishReason=%s, part_keys=%s",
                    i,
                    candidate.get("finishReason", "unknown"),
                    [list(p.keys()) for p in parts],
                )
                for part in parts:
                    if "text" in part:
                        logger.warning("DIGIT Gemini Image text: %s", part["text"][:500])
            return None, text_parts

        # Pick the largest image (HIGH thinking returns a 1K draft + 4K final)
        return max(image_tensors, key=lambda t: t.shape[1] * t.shape[2]), text_parts

    def _call_with_retry(self, url, token, body, max_retries=3, base_delay=5.0):
        """POST to Vertex AI with exponential backoff on rate limits."""
        last_error = None
        for attempt in range(max_retries):
            try:
                resp = http_requests.post(
                    url,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=body,
                    timeout=300,
                )
                if resp.status_code in (429, 503):
                    delay = base_delay * (2 ** attempt)
                    logger.warning("Rate limited (HTTP %d, attempt %d/%d), retrying in %ds...",
                                   resp.status_code, attempt + 1, max_retries, delay)
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp.json()
            except http_requests.exceptions.HTTPError:
                raise
            except Exception as e:
                last_error = e
                error_str = str(e)
                if "429" in error_str or "503" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    delay = base_delay * (2 ** attempt)
                    logger.warning("Rate limited (attempt %d/%d), retrying in %ds: %s",
                                   attempt + 1, max_retries, delay, e)
                    time.sleep(delay)
                else:
                    raise
        raise last_error
