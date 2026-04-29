"""Shade.inc PROJEKTS nodes — project picker and output saver.

Each Shade project is a separately-mounted filespace that appears as a
subfolder under the standard PROJEKTS root (e.g. /Volumes/saint/goose/PROJEKTS/).
This node reuses the same PROJEKTS_ROOTS detection as all other DIGIT nodes.
"""

import logging
import os
import re
import shutil

import numpy as np
from PIL import Image

from .projekts_utils import PROJEKTS_ROOTS, scan_projects

logger = logging.getLogger("DigitShade")


def _next_index(output_dir, prefix, ext):
    """Return the next available 4-digit index for files matching prefix_NNNN.ext."""
    pat = re.compile(rf"^{re.escape(prefix)}_(\d{{4}})\.{re.escape(ext)}$")
    max_idx = 0
    if os.path.isdir(output_dir):
        for f in os.listdir(output_dir):
            m = pat.match(f)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
    return max_idx + 1


class ShadeProjects:
    """Pick a Shade PROJEKTS project and output its path as a string.

    Each project is a separately-mounted Shade filespace appearing under
    the standard PROJEKTS root (e.g. /Volumes/saint/goose/PROJEKTS/).
    """

    CATEGORY = "DIGIT/Shade"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("project_path",)
    FUNCTION = "get_project_path"

    @classmethod
    def INPUT_TYPES(cls):
        available_roots = [r for r in PROJEKTS_ROOTS if os.path.isdir(r)]
        if not available_roots:
            available_roots = PROJEKTS_ROOTS
        first_root = available_roots[0]
        projects = scan_projects(first_root)
        return {
            "required": {
                "projekts_root": (available_roots,),
                "project": (projects,),
            }
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def get_project_path(self, projekts_root, project):
        project_path = os.path.join(projekts_root, project)
        return (project_path,)


class ShadeSave:
    """Save images or video clips to PROJEKTS/<project>/output/ on Shade.inc.

    Connect a project_path string from ShadeProjects, then plug in an IMAGE
    batch and/or video_paths list. Files are written as:
        <output_dir>/<filename_prefix>_NNNN.<format>
    where NNNN auto-increments from the highest existing index.
    """

    CATEGORY = "DIGIT/Shade"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("saved_paths",)
    FUNCTION = "save_to_shade"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_path": ("STRING", {"forceInput": True}),
                "filename_prefix": ("STRING", {"default": "output"}),
                "format": (["png", "jpg"],),
                "quality": ("INT", {"default": 95, "min": 1, "max": 100, "step": 1}),
            },
            "optional": {
                "image": ("IMAGE",),
                "video_paths": ("VEO_PATHS",),
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def save_to_shade(self, project_path, filename_prefix, format, quality,
                      image=None, video_paths=None):
        output_dir = os.path.join(project_path, "output")
        os.makedirs(output_dir, exist_ok=True)

        saved = []

        # --- Images ---
        if image is not None:
            for i in range(image.shape[0]):
                idx = _next_index(output_dir, filename_prefix, format)
                filename = f"{filename_prefix}_{idx:04d}.{format}"
                filepath = os.path.join(output_dir, filename)

                img_np = image[i].cpu().numpy()
                img_8bit = np.clip(255.0 * img_np[:, :, :3], 0, 255).astype(np.uint8)
                pil_img = Image.fromarray(img_8bit, mode="RGB")

                if format == "jpg":
                    pil_img.save(filepath, format="JPEG", quality=quality)
                else:
                    pil_img.save(filepath, format="PNG")

                logger.info(f"[ShadeSave] Saved image: {filepath}")
                saved.append(filepath)

        # --- Video ---
        if video_paths and isinstance(video_paths, list):
            for src in video_paths:
                if not os.path.isfile(src):
                    logger.warning(f"[ShadeSave] Source not found: {src}")
                    continue
                ext = os.path.splitext(src)[1].lstrip(".") or "mp4"
                idx = _next_index(output_dir, filename_prefix, ext)
                filename = f"{filename_prefix}_{idx:04d}.{ext}"
                filepath = os.path.join(output_dir, filename)
                shutil.copy2(src, filepath)
                logger.info(f"[ShadeSave] Saved video: {filepath}")
                saved.append(filepath)

        if not saved:
            raise ValueError("[ShadeSave] No image or video_paths connected.")

        result_text = "\n".join(saved)
        return {"ui": {"filepath_text": saved}, "result": (result_text,)}
