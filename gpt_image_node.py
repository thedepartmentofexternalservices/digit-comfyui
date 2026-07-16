"""DIGIT GPT Image node — OpenAI GPT Image generation via fal.ai.

Auto-detects mode based on connected inputs:
- No image inputs connected → text-to-image (openai/gpt-image-2)
- Any image1..image16 connected → edit (openai/gpt-image-2/edit), with
  optional mask for inpainting.

Batch generation submits all jobs to the fal queue up front and polls,
matching the DIGIT Seedance Video node. Uses FAL_KEY environment variable
for authentication.
"""

import io
import logging
import os
import time

import comfy.utils
import numpy as np
import requests as http_requests
import torch
from PIL import Image

from .gpt_image_models import (
    CUSTOM_DIM_MAX,
    CUSTOM_DIM_MIN,
    CUSTOM_DIM_STEP,
    DEFAULT_GPT_IMAGE_MODEL,
    GPT_IMAGE_APPS,
    GPT_IMAGE_MODELS,
    IMAGE_SIZES,
    MAX_REFERENCE_IMAGES,
    OUTPUT_FORMATS,
    QUALITIES,
)

logger = logging.getLogger("DigitGptImage")

MAX_BATCH_COUNT = 128
MAX_NUM_IMAGES = 4
MAX_AUTOMATIC_RETRIES = 3
POLL_INTERVAL_SECONDS = 2.0
MAX_SEED = 2147483647
DOWNLOAD_TIMEOUT_SECONDS = 120

# fal's fine-grained retry header is ignored on public model endpoints. Disable
# platform retries and coordinate at most three retries here so the limit is real.
FAL_NO_RETRY_HEADERS = {"X-Fal-No-Retry": "1"}


def _is_content_policy_error(error):
    """True when fal rejected the request on content policy (422), not transient."""
    text = str(error).lower()
    return (
        "content_policy_violation" in text
        or "content policy" in text
        or "moderation" in text
        or "likenesses of real people" in text
    )


def _tensor_to_png_bytes(tensor):
    """Convert a single (H,W,C) float32 0-1 image tensor to PNG bytes."""
    img_np = tensor.cpu().numpy()
    img_np = (img_np * 255).clip(0, 255).astype(np.uint8)
    img = Image.fromarray(img_np)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_image_tensor(fal_client, image_tensor):
    """Upload the first frame of a ComfyUI IMAGE batch to fal storage, return URL."""
    png_bytes = _tensor_to_png_bytes(image_tensor[0])
    return fal_client.upload(png_bytes, content_type="image/png")


def _mask_tensor_to_png_bytes(mask_tensor):
    """Convert a ComfyUI MASK (B,H,W float32 0-1) to an RGBA PNG for GPT Image.

    GPT Image edits the transparent (alpha=0) regions of the mask, while
    ComfyUI masks mark the area of interest with 1.0 — so alpha = 1 - mask.
    """
    mask_np = mask_tensor[0].cpu().numpy()
    alpha = ((1.0 - mask_np) * 255).clip(0, 255).astype(np.uint8)
    h, w = alpha.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 3] = alpha
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _image_bytes_to_tensor(image_bytes):
    """Convert encoded image bytes to a ComfyUI IMAGE tensor (1,H,W,3 float32 0-1)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
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


class DigitGptImage:
    CATEGORY = "DIGIT"
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "text")
    FUNCTION = "generate"

    @classmethod
    def INPUT_TYPES(cls):
        image_sockets = {
            f"image{i}": ("IMAGE",) for i in range(1, MAX_REFERENCE_IMAGES + 1)
        }
        return {
            "required": {
                "prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": "Describe the image. Connect image inputs for edit mode.",
                }),
                "model": (GPT_IMAGE_MODELS, {"default": DEFAULT_GPT_IMAGE_MODEL}),
                "image_size": (IMAGE_SIZES, {
                    "default": "auto",
                    "tooltip": "Preset size, or custom to use custom_width/custom_height.",
                }),
                "quality": (QUALITIES, {
                    "default": "high",
                    "tooltip": "Higher quality and larger sizes cost more per image.",
                }),
                "output_format": (OUTPUT_FORMATS, {"default": "png"}),
                "num_images": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": MAX_NUM_IMAGES,
                    "tooltip": "Images per API request. Total output = num_images x batch_count.",
                }),
                "seed": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": MAX_SEED,
                    "tooltip": "Re-run control only — the GPT Image API has no seed. 0 regenerates on every queue.",
                }),
            },
            "optional": {
                **image_sockets,
                "mask": ("MASK", {
                    "tooltip": "Optional inpainting mask (edit mode only). White areas are edited.",
                }),
                "custom_width": ("INT", {
                    "default": 1024,
                    "min": CUSTOM_DIM_MIN,
                    "max": CUSTOM_DIM_MAX,
                    "step": CUSTOM_DIM_STEP,
                    "tooltip": "Used only when image_size is custom. Multiples of 16.",
                }),
                "custom_height": ("INT", {
                    "default": 1024,
                    "min": CUSTOM_DIM_MIN,
                    "max": CUSTOM_DIM_MAX,
                    "step": CUSTOM_DIM_STEP,
                    "tooltip": "Used only when image_size is custom. Multiples of 16.",
                }),
                "batch_count": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": MAX_BATCH_COUNT,
                    "tooltip": "Number of parallel fal jobs. Each is a separate API call; results return as one IMAGE batch.",
                }),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        seed = kwargs.get("seed", 0)
        if seed == 0:
            return float("nan")
        return seed

    def generate(self, prompt, model, image_size, quality, output_format,
                 num_images, seed, mask=None, custom_width=1024,
                 custom_height=1024, batch_count=1, **kwargs):
        try:
            import fal_client
        except ImportError:
            raise ImportError(
                "fal-client is required for DigitGptImage. "
                "Install with: pip install fal-client"
            )

        if not os.environ.get("FAL_KEY"):
            raise ValueError(
                "FAL_KEY environment variable is not set. "
                "Export FAL_KEY=<your-key> in the environment before starting ComfyUI."
            )
        if not prompt or not prompt.strip():
            raise ValueError("Prompt is required.")

        ref_images = [kwargs.get(f"image{i}") for i in range(1, MAX_REFERENCE_IMAGES + 1)]
        ref_images = [img for img in ref_images if img is not None]

        if mask is not None and not ref_images:
            raise ValueError("mask requires at least one image input (edit mode).")

        mode = "edit" if ref_images else "text_to_image"
        app_id = GPT_IMAGE_APPS[model][mode]
        logger.info(f"[DigitGptImage] Mode: {mode} | App: {app_id}")

        args = {
            "prompt": prompt.strip(),
            "quality": quality,
            "output_format": output_format,
            "num_images": int(num_images),
        }
        if image_size == "custom":
            args["image_size"] = {"width": int(custom_width), "height": int(custom_height)}
        else:
            args["image_size"] = image_size

        # Media uploads happen once; their URLs are reused by every job in the batch.
        if mode == "edit":
            args["image_urls"] = [
                _upload_image_tensor(fal_client, img) for img in ref_images
            ]
            if mask is not None:
                mask_bytes = _mask_tensor_to_png_bytes(mask)
                args["mask_url"] = fal_client.upload(mask_bytes, content_type="image/png")

        jobs = self._run_batch(fal_client, app_id, args, int(batch_count))

        batch_tensors = []
        for job in jobs:
            tensors = []
            if job.get("result") is not None:
                tensors = self._decode_results(job["result"], job["index"])
                if not tensors:
                    job["error"] = "Completed request returned no downloadable image."
            if tensors:
                job["image_count"] = len(tensors)
                batch_tensors.extend(tensors)
            else:
                # Blank fallback keeps batch positions stable instead of failing everything.
                batch_tensors.append(torch.zeros((1, 1024, 1024, 3), dtype=torch.float32))

        if not any(job.get("image_count") for job in jobs):
            details = "; ".join(
                f"job {job['index'] + 1}: {job.get('error', 'unknown failure')}"
                for job in jobs
            )
            raise RuntimeError(f"All GPT Image batch generations failed. {details}")

        output_image = _stack_image_batch(batch_tensors)
        status = self._format_batch_status(mode, model, args, jobs)
        return (output_image, status)

    def _run_batch(self, fal_client, app_id, shared_args, batch_count):
        jobs = []
        pending = set()
        try:
            for index in range(batch_count):
                job = {
                    "index": index,
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
                            "[DigitGptImage] Status check failed for job %d: %s",
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
                                "[DigitGptImage] Job %d failed on attempt %d; retrying in %ds: %s",
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
                        "[DigitGptImage] Job %d submission failed after %d attempt(s): %s",
                        job["index"] + 1,
                        job["attempt"],
                        error,
                    )
                    return False

                delay = 2 ** (job["attempt"] - 1)
                logger.warning(
                    "[DigitGptImage] Job %d submission failed on attempt %d; "
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
        logger.info(
            "[DigitGptImage] Submitting job %d, attempt %d to %s...",
            job["index"] + 1,
            job["attempt"],
            app_id,
        )
        handle = fal_client.submit(
            app_id,
            arguments=dict(shared_args),
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
                logger.info("[DigitGptImage] Cancelled request %s", handle.request_id)
            except Exception as error:
                logger.warning(
                    "[DigitGptImage] Could not cancel request %s: %s",
                    getattr(handle, "request_id", "unknown"),
                    error,
                )

    def _decode_results(self, result, job_index):
        """Extract image URLs from a fal response and decode into IMAGE tensors."""
        image_items = []
        if isinstance(result, dict):
            if isinstance(result.get("images"), list):
                image_items = result["images"]
            elif "image" in result:
                image_items = [result["image"]]

        if not image_items:
            logger.error(f"[DigitGptImage] Could not extract image URLs from result: {result}")
            return []

        tensors = []
        for i, item in enumerate(image_items):
            url = None
            if isinstance(item, dict):
                url = item.get("url")
            elif isinstance(item, str):
                url = item

            if not url:
                logger.warning(f"[DigitGptImage] Skipping image {i}: no URL found in {item}")
                continue

            try:
                response = http_requests.get(url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
                response.raise_for_status()
                tensors.append(_image_bytes_to_tensor(response.content))
                logger.info(
                    "[DigitGptImage] Downloaded image %d of job %d", i + 1, job_index + 1
                )
            except Exception as e:
                logger.error(f"[DigitGptImage] Failed to download {url}: {e}")

        return tensors

    def _format_batch_status(self, mode, model, args, jobs):
        image_size = args.get("image_size")
        if isinstance(image_size, dict):
            image_size = f"{image_size['width']}x{image_size['height']}"
        total_images = sum(job.get("image_count", 0) for job in jobs)
        lines = [
            f"Model: {model}",
            f"Mode: {mode}",
            f"Size: {image_size}",
            f"Quality: {args.get('quality')}",
            f"Format: {args.get('output_format')}",
            f"Reference images: {len(args.get('image_urls', []))}",
            f"Mask: {'yes' if args.get('mask_url') else 'no'}",
            f"Images generated: {total_images} across {len(jobs)} job(s)",
            f"Automatic retries: up to {MAX_AUTOMATIC_RETRIES} per job",
        ]
        for job in jobs:
            summary = [
                f"Job {job['index'] + 1}",
                f"attempts={job['attempt']}",
                f"request_ids={','.join(job['request_ids'])}",
            ]
            if job.get("image_count"):
                summary.append(f"images={job['image_count']}")
            else:
                summary.append(f"error={job.get('error') or 'unknown failure'}")
            lines.append(" | ".join(summary))
        return "\n".join(lines)
