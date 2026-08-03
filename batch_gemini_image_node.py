"""DIGIT Batch Gemini Image — run Gemini image generation across a folder with LLM-driven prompt variation."""

import base64
import io
import logging
import os
import random
import time

import comfy.utils
import numpy as np
import requests as http_requests
import torch
from PIL import Image

from .gcp_config import resolve_gcp_config, get_gcp_access_token, build_vertex_url, default_project, default_region
from .gemini_image_models import (
    GEMINI_IMAGE_MODELS,
    DEFAULT_GEMINI_IMAGE_MODEL,
    MODELS_1K_ONLY,
    RESOLUTIONS,
    resolve_gemini_image_model,
    apply_image_thinking_config,
)

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}

DEFAULT_IMAGE_SYSTEM_PROMPT = (
    "You are an expert image-generation engine. You must ALWAYS produce an image.\n"
    "Interpret all user input—regardless of format, intent, or abstraction—as literal visual directives for image composition.\n"
    "If a prompt is conversational or lacks specific visual details, you must creatively invent a concrete visual scenario that depicts the concept.\n"
    "Prioritize generating the visual representation above any text, formatting, or conversational requests."
)

DEFAULT_VARIATION_SYSTEM_PROMPT = (
    "You are a creative prompt engineer for image generation. "
    "Given a base prompt, produce a variation that preserves the core intent and subject "
    "but introduces creative differences in style, mood, lighting, composition, color palette, "
    "or artistic approach. Output ONLY the varied prompt text — no preamble, numbering, or commentary."
)

SAFETY_THRESHOLD_OPTIONS = [
    "BLOCK_NONE",
    "BLOCK_ONLY_HIGH",
    "BLOCK_MEDIUM_AND_ABOVE",
    "BLOCK_LOW_AND_ABOVE",
]

IMAGE_HARM_CATEGORIES = [
    "HARM_CATEGORY_IMAGE_HATE",
    "HARM_CATEGORY_IMAGE_DANGEROUS_CONTENT",
    "HARM_CATEGORY_IMAGE_HARASSMENT",
    "HARM_CATEGORY_IMAGE_SEXUALLY_EXPLICIT",
]


def _image_file_to_png_bytes(filepath, max_dimension=2048):
    """Load an image file and return PNG bytes, resizing if needed."""
    img = Image.open(filepath).convert("RGB")
    w, h = img.size
    if max(w, h) > max_dimension:
        scale = max_dimension / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _png_bytes_to_tensor(png_bytes):
    """Convert PNG bytes to a ComfyUI IMAGE tensor (1,H,W,3 float32 0-1)."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    img_np = np.array(img).astype(np.float32) / 255.0
    return torch.from_numpy(img_np).unsqueeze(0)


def _save_image_tensor(tensor, path):
    """Save a single image tensor (H,W,C) to disk as PNG."""
    img_np = tensor.cpu().numpy()
    img_np = (img_np * 255).clip(0, 255).astype(np.uint8)
    img = Image.fromarray(img_np)
    img.save(path, format="PNG")


class DigitBatchGeminiImage:
    """Batch Gemini image generation: iterate over a folder of source images,
    use an LLM to vary the prompt, and generate multiple outputs per image."""

    IMAGE_MODELS = GEMINI_IMAGE_MODELS

    LLM_MODELS = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
    ]

    ASPECT_RATIOS = [
        "auto", "1:1", "2:3", "3:2", "3:4", "4:1", "4:3",
        "4:5", "5:4", "8:1", "9:16", "16:9", "21:9",
    ]

    RESOLUTIONS = RESOLUTIONS

    THINKING_LEVELS = ["MINIMAL", "HIGH"]

    CATEGORY = "DIGIT"
    RETURN_TYPES = ("IMAGE", "STRING", "INT", "STRING")
    RETURN_NAMES = ("images", "log", "generated_count", "output_folder")
    FUNCTION = "generate_batch"
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Batch Gemini image generation. Scans a folder of source images, "
        "uses an LLM to create prompt variations, and generates multiple "
        "Gemini image outputs per source image. Results saved to output subfolder."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_folder": ("STRING", {
                    "default": "",
                    "tooltip": "Path to folder containing source images.",
                }),
                "prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "tooltip": "Base prompt for Gemini image generation. The LLM will create variations of this.",
                }),
                "variations_per_image": ("INT", {
                    "default": 3,
                    "min": 1,
                    "max": 50,
                    "tooltip": "How many prompt variations (and image generations) per source image.",
                }),
                "image_model": (cls.IMAGE_MODELS, {"default": DEFAULT_GEMINI_IMAGE_MODEL}),
                "llm_model": (cls.LLM_MODELS, {
                    "default": cls.LLM_MODELS[0],
                    "tooltip": "Gemini model used to generate prompt variations.",
                }),
                "aspect_ratio": (cls.ASPECT_RATIOS, {"default": "16:9"}),
                "resolution": (cls.RESOLUTIONS, {"default": "1K"}),
                "thinking_level": (cls.THINKING_LEVELS, {
                    "default": "MINIMAL",
                    "tooltip": "Thinking level for image generation. HIGH may improve quality.",
                }),
                "temperature": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.0,
                    "max": 2.0,
                    "step": 0.01,
                    "tooltip": "Temperature for image generation.",
                }),
                "variation_temperature": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.0,
                    "max": 2.0,
                    "step": 0.05,
                    "tooltip": "Temperature for LLM prompt variation. Higher = more creative variations.",
                }),
                "gcp_project_id": ("STRING", {
                    "default": default_project(),
                    "tooltip": "GCP project ID.",
                }),
                "gcp_region": ("STRING", {
                    "default": default_region(),
                    "tooltip": "GCP region.",
                }),
            },
            "optional": {
                "output_subfolder": ("STRING", {
                    "default": "gemini_output",
                    "tooltip": "Subfolder name inside image_folder for outputs. Created automatically.",
                }),
                "system_instruction": ("STRING", {
                    "default": DEFAULT_IMAGE_SYSTEM_PROMPT,
                    "multiline": True,
                    "tooltip": "System instruction for Gemini image generation.",
                }),
                "variation_system_prompt": ("STRING", {
                    "default": DEFAULT_VARIATION_SYSTEM_PROMPT,
                    "multiline": True,
                    "tooltip": "System prompt for the LLM that generates prompt variations.",
                }),
                "variation_instruction": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "tooltip": "Extra instructions appended to the variation request. E.g. 'Keep it photorealistic' or 'Vary the lighting dramatically'.",
                }),
                "seed": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 2147483647,
                    "tooltip": "Base seed. 0 = random. Each variation gets seed + variation_index.",
                }),
                "max_dimension": ("INT", {
                    "default": 2048,
                    "min": 512,
                    "max": 4096,
                    "step": 256,
                    "tooltip": "Resize source images to this max dimension before sending to API.",
                }),
                "delay_seconds": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.0,
                    "max": 30.0,
                    "step": 0.5,
                    "tooltip": "Delay between API calls to avoid rate limiting.",
                }),
                "harassment_threshold": (SAFETY_THRESHOLD_OPTIONS, {"default": "BLOCK_NONE"}),
                "hate_speech_threshold": (SAFETY_THRESHOLD_OPTIONS, {"default": "BLOCK_NONE"}),
                "sexually_explicit_threshold": (SAFETY_THRESHOLD_OPTIONS, {"default": "BLOCK_NONE"}),
                "dangerous_content_threshold": (SAFETY_THRESHOLD_OPTIONS, {"default": "BLOCK_NONE"}),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def _build_safety_settings(self, harassment, hate_speech, sexually_explicit, dangerous_content):
        """Build safety settings matching Nano Banana 2 format (text + image categories)."""
        return [
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": hate_speech},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": dangerous_content},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": sexually_explicit},
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": harassment},
            {"category": "HARM_CATEGORY_IMAGE_HATE", "threshold": hate_speech},
            {"category": "HARM_CATEGORY_IMAGE_DANGEROUS_CONTENT", "threshold": dangerous_content},
            {"category": "HARM_CATEGORY_IMAGE_HARASSMENT", "threshold": harassment},
            {"category": "HARM_CATEGORY_IMAGE_SEXUALLY_EXPLICIT", "threshold": sexually_explicit},
        ]

    def _vary_prompt(self, token, project, region, llm_model, base_prompt, variation_index,
                     variation_system_prompt, variation_instruction, variation_temperature,
                     source_filename):
        """Use Gemini LLM (direct REST) to create a prompt variation."""
        request_text = (
            f"Base prompt: {base_prompt}\n\n"
            f"Source image filename: {source_filename}\n"
            f"Variation number: {variation_index + 1}\n\n"
            f"Create a unique variation of this base prompt. "
            f"Make it distinctly different from what variations 1-{variation_index} might produce."
        )
        if variation_instruction.strip():
            request_text += f"\n\nAdditional guidance: {variation_instruction.strip()}"

        body = {
            "contents": [{"role": "user", "parts": [{"text": request_text}]}],
            "generationConfig": {
                "temperature": variation_temperature,
                "maxOutputTokens": 1024,
            },
        }
        if variation_system_prompt and variation_system_prompt.strip():
            body["systemInstruction"] = {"parts": [{"text": variation_system_prompt.strip()}]}

        url = build_vertex_url(project, region, llm_model)
        resp = http_requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        if "candidates" in data:
            for candidate in data["candidates"]:
                for part in candidate.get("content", {}).get("parts", []):
                    if "text" in part:
                        return part["text"].strip()
        return base_prompt  # Fallback to original

    def _call_image_api(self, url, token, body, max_retries=3, base_delay=5.0):
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

    def generate_batch(
        self,
        image_folder,
        prompt,
        variations_per_image,
        image_model,
        llm_model,
        aspect_ratio,
        resolution,
        thinking_level,
        temperature,
        variation_temperature,
        gcp_project_id="",
        gcp_region="",
        output_subfolder="gemini_output",
        system_instruction="",
        variation_system_prompt="",
        variation_instruction="",
        seed=0,
        max_dimension=2048,
        delay_seconds=1.0,
        harassment_threshold="BLOCK_NONE",
        hate_speech_threshold="BLOCK_NONE",
        sexually_explicit_threshold="BLOCK_NONE",
        dangerous_content_threshold="BLOCK_NONE",
    ):
        logger.warning("=== DIGIT Batch Gemini Image: generate_batch called ===")
        logger.warning("  image_folder=%r, prompt=%r, variations=%d, image_model=%s, llm_model=%s",
                        image_folder, prompt[:80], variations_per_image, image_model, llm_model)

        # Validate
        image_folder = image_folder.strip()
        image_model = resolve_gemini_image_model(image_model)
        if not os.path.isdir(image_folder):
            raise ValueError(f"Image folder not found: {image_folder}")
        if not prompt.strip():
            raise ValueError("Prompt is required.")
        if image_model in MODELS_1K_ONLY and resolution != "1K":
            raise ValueError(
                f"Model {image_model} (Nano Banana 2 Lite) only supports 1K resolution, got {resolution}."
            )

        project, region = resolve_gcp_config(gcp_project_id, gcp_region)
        token = get_gcp_access_token()

        # Create output folder
        output_dir = os.path.join(image_folder, output_subfolder.strip() or "gemini_output")
        os.makedirs(output_dir, exist_ok=True)

        # Find source images (recursive walk through subfolders)
        image_files = []
        for root, _dirs, files in os.walk(image_folder):
            if os.path.abspath(root).startswith(os.path.abspath(output_dir)):
                continue
            for f in files:
                if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                    image_files.append(os.path.join(root, f))
        image_files.sort()

        logger.warning("  Found %d source images in %s", len(image_files), image_folder)

        if not image_files:
            blank = torch.zeros((1, 512, 512, 3))
            return {"ui": {"log_text": ["No images found in folder."]},
                    "result": (blank, "No images found.", 0, output_dir)}

        output_tensors = []
        total_ops = len(image_files) * variations_per_image
        pbar = comfy.utils.ProgressBar(total_ops)

        safety_settings = self._build_safety_settings(
            harassment_threshold, hate_speech_threshold,
            sexually_explicit_threshold, dangerous_content_threshold,
        )

        # Build image URL
        image_url = build_vertex_url(project, region, image_model)

        log_lines = []
        generated = 0
        errors = 0
        op_idx = 0

        for img_idx, img_path in enumerate(image_files):
            img_name = os.path.basename(img_path)
            base_name = os.path.splitext(img_name)[0]

            # Load source image bytes
            source_png_bytes = None
            try:
                source_png_bytes = _image_file_to_png_bytes(img_path, max_dimension)
            except Exception as e:
                log_lines.append(f"[{img_name}] ERROR loading image: {e}")
                logger.error("Failed to load %s: %s", img_name, e)
                errors += variations_per_image
                op_idx += variations_per_image
                pbar.update_absolute(op_idx)
                continue

            source_b64 = base64.b64encode(source_png_bytes).decode("utf-8")

            for var_idx in range(variations_per_image):
                op_idx += 1
                try:
                    # Step 1: Generate prompt variation via LLM
                    if variations_per_image == 1:
                        varied_prompt = prompt.strip()
                    else:
                        varied_prompt = self._vary_prompt(
                            token, project, region, llm_model, prompt, var_idx,
                            variation_system_prompt, variation_instruction,
                            variation_temperature, img_name,
                        )

                    # Step 2: Build image generation request (direct REST API)
                    parts = [
                        {"text": varied_prompt},
                        {"inlineData": {"mimeType": "image/png", "data": source_b64}},
                    ]

                    image_config = {
                        "imageSize": resolution,
                        "imageOutputOptions": {"mimeType": "image/png"},
                    }
                    if aspect_ratio != "auto":
                        image_config["aspectRatio"] = aspect_ratio

                    generation_config = {
                        "responseModalities": ["TEXT", "IMAGE"],
                        "imageConfig": image_config,
                    }
                    apply_image_thinking_config(generation_config, image_model, thinking_level)

                    body = {
                        "contents": [{"role": "user", "parts": parts}],
                        "generationConfig": generation_config,
                        "safetySettings": safety_settings,
                    }

                    if system_instruction and system_instruction.strip():
                        body["systemInstruction"] = {"parts": [{"text": system_instruction.strip()}]}

                    # Step 3: Generate via direct REST API
                    response_data = self._call_image_api(image_url, token, body)

                    # Step 4: Extract and save output image (pick largest — HIGH thinking returns 1K draft + 4K final)
                    saved = False
                    response_images = []
                    if "candidates" in response_data:
                        for candidate in response_data["candidates"]:
                            content = candidate.get("content", {})
                            for part in content.get("parts", []):
                                if "inlineData" in part:
                                    mime = part["inlineData"].get("mimeType", "")
                                    if "image" in mime:
                                        img_bytes = base64.b64decode(part["inlineData"]["data"])
                                        tensor = _png_bytes_to_tensor(img_bytes)
                                        response_images.append(tensor)

                    if response_images:
                        tensor = max(response_images, key=lambda t: t.shape[1] * t.shape[2])
                        output_tensors.append(tensor)
                        out_name = f"{base_name}_v{var_idx + 1:03d}.png"
                        out_path = os.path.join(output_dir, out_name)
                        _save_image_tensor(tensor[0], out_path)

                        prompt_path = os.path.join(output_dir, f"{base_name}_v{var_idx + 1:03d}.txt")
                        with open(prompt_path, "w", encoding="utf-8") as f:
                            f.write(varied_prompt)

                        generated += 1
                        saved = True
                        status = f"[{op_idx}/{total_ops}] {img_name} v{var_idx + 1} -> {out_name}"
                        log_lines.append(status)
                        logger.info("Batch Gemini Image: %s", status)

                    if not saved:
                        errors += 1
                        status = f"[{op_idx}/{total_ops}] {img_name} v{var_idx + 1} -> NO IMAGE returned"
                        log_lines.append(status)
                        logger.warning("Batch Gemini Image: %s", status)

                except Exception as e:
                    errors += 1
                    status = f"[{op_idx}/{total_ops}] {img_name} v{var_idx + 1} -> ERROR: {e}"
                    log_lines.append(status)
                    logger.error("Batch Gemini Image: %s", status)

                pbar.update_absolute(op_idx)

                # Rate limit delay
                if delay_seconds > 0 and op_idx < total_ops:
                    time.sleep(delay_seconds)

        # Summary
        summary = (
            f"Done. Generated: {generated}, Errors: {errors}, "
            f"Source images: {len(image_files)}, Variations each: {variations_per_image}, "
            f"Total attempts: {total_ops}"
        )
        log_lines.append("")
        log_lines.append(summary)
        log_text = "\n".join(log_lines)

        logger.info("Batch Gemini Image: %s", summary)

        # Build image batch tensor
        if output_tensors:
            target_h, target_w = output_tensors[0].shape[1], output_tensors[0].shape[2]
            resized = []
            for t in output_tensors:
                if t.shape[1] != target_h or t.shape[2] != target_w:
                    img_np = (t[0].cpu().numpy() * 255).clip(0, 255).astype(np.uint8)
                    img = Image.fromarray(img_np).resize((target_w, target_h), Image.LANCZOS)
                    t = torch.from_numpy(np.array(img).astype(np.float32) / 255.0).unsqueeze(0)
                resized.append(t)
            image_batch = torch.cat(resized, dim=0)
        else:
            image_batch = torch.zeros((1, 512, 512, 3))

        return {"ui": {"log_text": [summary]},
                "result": (image_batch, log_text, generated, output_dir)}
