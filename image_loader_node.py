import logging
import os
import re

import folder_paths
import numpy as np
from aiohttp import web

logger = logging.getLogger("DigitImageLoader")
from PIL import Image
from server import PromptServer

from .projekts_utils import PROJEKTS_ROOTS, PROJECT_RE, FRAME_RE, scan_projects, scan_shots

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff", ".bmp", ".webp"}

FILTER_PRESETS = {
    "images": IMAGE_EXTENSIONS,
    "loras": {".safetensors", ".pt", ".ckpt"},
    "videos": {".mp4", ".mov", ".qt", ".m4v", ".mkv", ".avi", ".mxf"},
    "all": None,  # show all files
}


@PromptServer.instance.routes.get("/digit/browse")
async def browse_filesystem(request):
    """List directories and files at a given path.

    Query params:
        path: directory to list
        filter: "images" (default), "loras", or "all"
    """
    path = request.rel_url.query.get("path", "")
    if not path or not os.path.isdir(path):
        return web.json_response({"error": "Invalid path", "dirs": [], "files": []}, status=400)

    filter_name = request.rel_url.query.get("filter", "images")
    allowed_exts = FILTER_PRESETS.get(filter_name, IMAGE_EXTENSIONS)

    dirs = []
    files = []
    try:
        for entry in sorted(os.listdir(path)):
            full = os.path.join(path, entry)
            if entry.startswith("."):
                continue
            if os.path.isdir(full):
                dirs.append(entry)
            elif os.path.isfile(full):
                if allowed_exts is None:
                    files.append(entry)
                else:
                    ext = os.path.splitext(entry)[1].lower()
                    if ext in allowed_exts:
                        files.append(entry)
    except PermissionError:
        return web.json_response({"error": "Permission denied", "dirs": [], "files": []}, status=403)

    return web.json_response({"path": path, "dirs": dirs, "files": files})


class DigitImageLoader:
    """Loads images from upload, filepath connection, or PROJEKTS pipeline.

    Priority order:
    1. upload_image — drag-and-drop or pick from ComfyUI's input folder
    2. filepath — connected from another node (e.g. Image Saver)
    3. Pipeline scan — finds latest frame by shot/task in PROJEKTS
    """

    CATEGORY = "DIGIT"
    RETURN_TYPES = ("IMAGE", "STRING", "INT")
    RETURN_NAMES = ("image", "filepath", "frame")
    FUNCTION = "load_latest"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        input_dir = folder_paths.get_input_directory()
        try:
            files = [f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f))]
            files = folder_paths.filter_files_content_types(files, ["image"])
        except (FileNotFoundError, OSError):
            files = []

        available_roots = [r for r in PROJEKTS_ROOTS if os.path.isdir(r)]
        if not available_roots:
            available_roots = PROJEKTS_ROOTS

        first_root = available_roots[0]
        projects = scan_projects(first_root)
        first_project = projects[0] if projects else ""
        shots = scan_shots(first_root, first_project)

        return {
            "required": {
                "projekts_root": (available_roots,),
                "project": (projects,),
                "shot": (shots,),
                "subfolder": ("STRING", {"default": "comfy"}),
                "task": ("STRING", {"default": "comp"}),
                "format": (["png", "jpg", "exr"],),
            },
            "optional": {
                "browse_path": ("STRING", {"default": "", "multiline": False, "tooltip": "Absolute path to an image file on the filesystem. Highest priority."}),
                "upload_image": (sorted(files), {"image_upload": True, "tooltip": "Upload or select an image from ComfyUI's input folder."}),
                "filepath": ("STRING", {"forceInput": True}),
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Always re-execute so we pick up the latest file.
        return float("nan")

    def load_latest(self, projekts_root, project, shot, subfolder, task, format,
                    browse_path=None, upload_image=None, filepath=None):
        import torch

        # Priority 0: browse_path — absolute filesystem path typed by the user
        if browse_path and browse_path.strip():
            bp = browse_path.strip()
            if os.path.isfile(bp):
                ext = os.path.splitext(bp)[1].lstrip(".")
                m = FRAME_RE.search(os.path.basename(bp))
                frame_num = int(m.group(1)) if m else 0
                img_tensor = self._load_image(bp, ext)
                preview_info = self._save_preview(img_tensor, bp)
                return {"ui": {"images": [preview_info], "filepath_text": [bp]},
                        "result": (img_tensor, bp, frame_num)}
            else:
                logger.warning(f"browse_path not found: {bp}")

        # Priority 1: uploaded/selected image from ComfyUI's input folder
        if upload_image is not None:
            image_path = folder_paths.get_annotated_filepath(upload_image)
            if os.path.isfile(image_path):
                ext = os.path.splitext(image_path)[1].lstrip(".")
                img_tensor = self._load_image(image_path, ext)
                preview_info = self._save_preview(img_tensor, image_path)
                return {"ui": {"images": [preview_info], "filepath_text": [image_path]},
                        "result": (img_tensor, image_path, 0)}

        # Priority 2: filepath connected from another node (e.g. Image Saver)
        if filepath and os.path.isfile(filepath):
            ext = os.path.splitext(filepath)[1].lstrip(".")
            # Extract frame number from filename if possible
            m = FRAME_RE.search(os.path.basename(filepath))
            frame_num = int(m.group(1)) if m else 0
            img_tensor = self._load_image(filepath, ext)
            preview_info = self._save_preview(img_tensor, filepath)
            return {"ui": {"images": [preview_info], "filepath_text": [filepath]},
                    "result": (img_tensor, filepath, frame_num)}

        # Otherwise scan the directory for the latest frame
        prefix = project[:5]
        target_dir = os.path.join(projekts_root, project, "shots", shot, subfolder, task)

        found_path, frame_num = self._find_latest(target_dir, prefix, shot, task, format)

        if found_path is None:
            empty = np.zeros((1, 1, 3), dtype=np.float32)
            return {"ui": {"images": [], "filepath_text": ["(no frames found)"]},
                    "result": (torch.from_numpy(empty).unsqueeze(0), "", 0)}

        img_tensor = self._load_image(found_path, format)
        preview_info = self._save_preview(img_tensor, found_path)

        return {"ui": {"images": [preview_info], "filepath_text": [found_path]},
                "result": (img_tensor, found_path, frame_num)}

    def _save_preview(self, img_tensor, original_path):
        """Save an 8-bit PNG preview to ComfyUI's temp folder."""
        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)
        basename = os.path.splitext(os.path.basename(original_path))[0]
        preview_name = f"digit_preview_{basename}.png"
        preview_path = os.path.join(temp_dir, preview_name)

        img_np = img_tensor[0].cpu().numpy()
        img_8bit = np.clip(255.0 * img_np[:, :, :3], 0, 255).astype(np.uint8)
        pil_img = Image.fromarray(img_8bit, mode="RGB")
        pil_img.save(preview_path, format="PNG")

        return {"filename": preview_name, "subfolder": "", "type": "temp"}

    def _find_latest(self, target_dir, prefix, shot, task, ext):
        """Return (filepath, frame_number) for the highest-numbered frame, or (None, 0)."""
        if not os.path.isdir(target_dir):
            return None, 0

        pat = re.compile(
            rf"^{re.escape(prefix)}_{re.escape(shot)}_{re.escape(task)}\.(\d+)\.{re.escape(ext)}$"
        )
        best_frame = -1
        best_file = None
        for f in os.listdir(target_dir):
            m = pat.match(f)
            if m:
                n = int(m.group(1))
                if n > best_frame:
                    best_frame = n
                    best_file = f
        if best_file is None:
            return None, 0
        return os.path.join(target_dir, best_file), best_frame

    def _load_image(self, filepath, format):
        import torch

        if format == "exr":
            return self._load_exr(filepath)

        pil_img = Image.open(filepath)
        if pil_img.mode == "RGBA":
            img_np = np.array(pil_img).astype(np.float32) / 255.0
        else:
            pil_img = pil_img.convert("RGB")
            img_np = np.array(pil_img).astype(np.float32) / 255.0

        # (H, W, C) -> (1, H, W, C)
        return torch.from_numpy(img_np).unsqueeze(0)

    def _load_exr(self, filepath):
        import torch

        try:
            import cv2
        except ImportError:
            raise ImportError("opencv-python (cv2) is required for EXR loading. "
                              "Install with: pip install opencv-python")

        img = cv2.imread(filepath, cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
        if img is None:
            raise RuntimeError(f"cv2.imread failed to read EXR: {filepath}")

        if img.ndim == 2:
            img = np.stack([img, img, img], axis=2)

        if img.shape[2] == 4:
            # BGRA -> RGBA, un-invert alpha
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA)
            img[:, :, 3] = 1.0 - img[:, :, 3]
        else:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        img = img.astype(np.float32)
        return torch.from_numpy(img).unsqueeze(0)
