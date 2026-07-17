"""DIGIT MU Seedance 2 Character node.

Builds a reusable character sheet from one to three ComfyUI IMAGE inputs via
MUAPI. Authentication is read from the MUAPIAPP_API_KEY environment variable.
"""

import io
import logging

import numpy as np
import torch
from PIL import Image, ImageOps

from . import muapi_client


logger = logging.getLogger("DigitMuSeedanceCharacter")

CHARACTER_ENDPOINT = "seedance-2-character"


def _image_bytes_to_tensor(image_bytes):
    """Convert downloaded image bytes to a ComfyUI IMAGE tensor."""
    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image).convert("RGB")
    image_array = np.asarray(image, dtype=np.float32) / 255.0
    return torch.from_numpy(image_array.copy()).unsqueeze(0)


class DigitMuSeedanceCharacter:
    CATEGORY = "DIGIT"
    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("character_sheet", "sheet_url", "request_id", "status")
    FUNCTION = "generate"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": (
                    "STRING",
                    {
                        "default": "A red leather jacket with black jeans and white sneakers",
                        "multiline": True,
                        "tooltip": "Describe the character's outfit or costume.",
                    },
                ),
                "character_name": (
                    "STRING",
                    {
                        "default": "A Hero",
                        "tooltip": "Optional label stored with the MUAPI character.",
                    },
                ),
                "image1": (
                    "IMAGE",
                    {"tooltip": "Primary character reference. Clear frontal or 3/4 photos work best."},
                ),
            },
            "optional": {
                "image2": ("IMAGE", {"tooltip": "Optional second character reference."}),
                "image3": ("IMAGE", {"tooltip": "Optional third character reference."}),
            },
        }

    def generate(self, prompt, character_name, image1, image2=None, image3=None):
        if not prompt or not prompt.strip():
            raise ValueError("Prompt is required.")

        headers = muapi_client.auth_headers()
        images = [image for image in (image1, image2, image3) if image is not None]

        logger.info("[DIGIT MU Character] Uploading %d reference image(s)...", len(images))
        image_urls = [
            muapi_client.upload_image_tensor(headers, image, label=f"character_ref{index}")
            for index, image in enumerate(images, start=1)
        ]

        payload = {
            "prompt": prompt.strip(),
            "images_list": image_urls,
        }
        if character_name and character_name.strip():
            payload["character_name"] = character_name.strip()

        request_id = muapi_client.submit(
            headers, CHARACTER_ENDPOINT, payload, log_prefix="[DIGIT MU Character]"
        )
        logger.info("[DIGIT MU Character] Request ID: %s", request_id)
        result = muapi_client.poll_until_done(
            headers, request_id, log_prefix="[DIGIT MU Character]"
        )
        sheet_url = self._extract_sheet_url(result)
        if not sheet_url:
            raise RuntimeError(
                "MUAPI completed without a character-sheet URL. "
                f"Request ID: {request_id}; result: {result}"
            )
        logger.info("[DIGIT MU Character] Downloading completed character sheet...")
        download = muapi_client.request_with_retry("get", sheet_url, timeout=180)
        character_sheet = _image_bytes_to_tensor(download.content)

        status = "\n".join(
            [
                "Provider: MUAPI",
                "Model: seedance-2-character",
                f"Character: {payload.get('character_name', '(unnamed)')}",
                f"References: {len(image_urls)}",
                f"Request ID: {request_id}",
                f"Sheet URL: {sheet_url}",
            ]
        )
        return (character_sheet, sheet_url, request_id, status)

    @staticmethod
    def _extract_sheet_url(result):
        """Read the character sheet URL from MUAPI's endpoint-specific response."""
        output_data = result.get("output_data") or {}
        if isinstance(output_data, dict):
            for key in ("sheet_url", "image_url", "output_url", "url"):
                value = output_data.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return value

        for key in ("sheet_url", "image_url", "output_url", "url"):
            value = result.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value

        for value in result.get("outputs") or []:
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value

        return ""
