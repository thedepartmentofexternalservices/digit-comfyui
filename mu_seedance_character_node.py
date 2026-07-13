"""DIGIT MU Seedance 2 Character node.

Builds a reusable character sheet from one to three ComfyUI IMAGE inputs via
MUAPI. Authentication is read from the MUAPIAPP_API_KEY environment variable.
"""

import io
import logging
import os
import time

import numpy as np
import requests
import torch
from PIL import Image, ImageOps


logger = logging.getLogger("DigitMuSeedanceCharacter")

API_BASE_URL = "https://api.muapi.ai/api/v1"
UPLOAD_URL = f"{API_BASE_URL}/upload_file"
CHARACTER_URL = f"{API_BASE_URL}/seedance-2-character"
POLL_INTERVAL_SECONDS = 3
MAX_WAIT_SECONDS = 20 * 60
TERMINAL_FAILURE_STATES = {"failed", "cancelled"}
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def _tensor_to_png_bytes(image_tensor):
    """Convert the first image in a ComfyUI IMAGE batch to PNG bytes."""
    if image_tensor is None or image_tensor.ndim != 4 or image_tensor.shape[0] < 1:
        raise ValueError("Each character reference must be a non-empty ComfyUI IMAGE.")

    image_array = image_tensor[0].detach().cpu().numpy()
    image_array = (image_array * 255).clip(0, 255).astype(np.uint8)

    if image_array.shape[-1] == 4:
        image = Image.fromarray(image_array, mode="RGBA").convert("RGB")
    else:
        image = Image.fromarray(image_array, mode="RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


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
        api_key = os.environ.get("MUAPIAPP_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "MUAPIAPP_API_KEY environment variable is not set. "
                "Set it before starting ComfyUI."
            )
        if not prompt or not prompt.strip():
            raise ValueError("Prompt is required.")

        headers = {"x-api-key": api_key}
        images = [image for image in (image1, image2, image3) if image is not None]

        logger.info("[DIGIT MU Character] Uploading %d reference image(s)...", len(images))
        image_urls = [
            self._upload_image(headers, image, index)
            for index, image in enumerate(images, start=1)
        ]

        payload = {
            "prompt": prompt.strip(),
            "images_list": image_urls,
        }
        if character_name and character_name.strip():
            payload["character_name"] = character_name.strip()

        logger.info("[DIGIT MU Character] Submitting character-sheet request...")
        response = self._request_with_retry(
            "post",
            CHARACTER_URL,
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        submission = self._response_json(response, "Character submission")
        request_id = submission.get("request_id")
        if not request_id:
            raise RuntimeError(f"MUAPI character submission returned no request_id: {submission}")

        logger.info("[DIGIT MU Character] Request ID: %s", request_id)
        result = self._poll_result(headers, request_id)
        sheet_url = self._extract_sheet_url(result)
        if not sheet_url:
            raise RuntimeError(
                "MUAPI completed without a character-sheet URL. "
                f"Request ID: {request_id}; result: {result}"
            )
        logger.info("[DIGIT MU Character] Downloading completed character sheet...")
        download = self._request_with_retry("get", sheet_url, timeout=180)
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

    def _upload_image(self, headers, image_tensor, index):
        png_bytes = _tensor_to_png_bytes(image_tensor)
        response = self._request_with_retry(
            "post",
            UPLOAD_URL,
            headers=headers,
            files={"file": (f"character_reference_{index}.png", png_bytes, "image/png")},
            timeout=120,
        )
        upload = self._response_json(response, f"Reference image {index} upload")
        image_url = upload.get("url") or upload.get("file_url") or upload.get("output")
        if not image_url:
            raise RuntimeError(f"MUAPI upload returned no URL for image {index}: {upload}")
        return str(image_url)

    def _poll_result(self, headers, request_id):
        poll_url = f"{API_BASE_URL}/predictions/{request_id}/result"
        deadline = time.monotonic() + MAX_WAIT_SECONDS
        last_status = "unknown"

        while time.monotonic() < deadline:
            response = self._request_with_retry(
                "get",
                poll_url,
                headers={**headers, "Content-Type": "application/json"},
                timeout=60,
            )
            result = self._response_json(response, "Character result polling")
            last_status = str(result.get("status", "unknown")).lower()

            if last_status == "completed":
                return result
            if last_status in TERMINAL_FAILURE_STATES:
                detail = result.get("error") or "No error detail returned."
                raise RuntimeError(
                    f"MUAPI character generation {last_status}: {detail}"
                )

            logger.info(
                "[DIGIT MU Character] Request %s status: %s",
                request_id,
                last_status,
            )
            time.sleep(POLL_INTERVAL_SECONDS)

        raise TimeoutError(
            f"MUAPI character generation timed out after {MAX_WAIT_SECONDS} seconds "
            f"(last status: {last_status}, request ID: {request_id})."
        )

    def _request_with_retry(self, method, url, max_retries=3, **kwargs):
        last_error = None
        for retry_index in range(max_retries):
            try:
                response = requests.request(method, url, **kwargs)
                if response.status_code in RETRYABLE_STATUS_CODES:
                    if retry_index == max_retries - 1:
                        response.raise_for_status()
                    delay = 2 ** retry_index
                    logger.warning(
                        "[DIGIT MU Character] HTTP %d; retrying in %ds.",
                        response.status_code,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                if response.status_code >= 400:
                    detail = response.text[:500].strip()
                    try:
                        error_data = response.json()
                        detail = (
                            error_data.get("error")
                            or error_data.get("message")
                            or detail
                        )
                    except ValueError:
                        pass
                    raise RuntimeError(
                        f"MUAPI request failed with HTTP {response.status_code}: "
                        f"{detail or 'No error detail returned.'}"
                    )
                return response
            except requests.RequestException as error:
                last_error = error
                if retry_index == max_retries - 1:
                    raise RuntimeError(f"MUAPI request failed: {error}") from error
                delay = 2 ** retry_index
                logger.warning(
                    "[DIGIT MU Character] Request error; retrying in %ds: %s",
                    delay,
                    error,
                )
                time.sleep(delay)

        raise RuntimeError(f"MUAPI request failed: {last_error}")

    @staticmethod
    def _response_json(response, operation):
        try:
            return response.json()
        except ValueError as error:
            preview = response.text[:500]
            raise RuntimeError(f"{operation} returned invalid JSON: {preview}") from error

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
