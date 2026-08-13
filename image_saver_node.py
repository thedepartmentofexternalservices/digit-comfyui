import json
import logging
import os

os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"

import folder_paths
import numpy as np

logger = logging.getLogger("DigitImageSaver")
from aiohttp import web
from PIL import Image, PngImagePlugin
from server import PromptServer

from .projekts_utils import (
    SENTINEL_NO_SHOTS,
    combo_choices,
    create_shot_dir,
    file_stem,
    get_available_projekts_roots,
    health_payload,
    is_placeholder,
    is_storage_unavailable,
    is_within_roots,
    next_frame,
    resolve_pipeline_dir,
    scan_child_folders,
    scan_projects,
    scan_shots,
)


def sRGBtoLinear(npArray):
    """Convert sRGB gamma-encoded values to linear light."""
    less = npArray <= 0.04045
    result = np.where(less, npArray / 12.92, ((npArray + 0.055) / 1.055) ** 2.4)
    return result.astype(np.float32)


def _json_scan(items):
    """Return a list payload; 503 when the scan hit a storage error."""
    if is_storage_unavailable(items):
        return web.json_response(items, status=503)
    return web.json_response(items)


def _constrained_root(raw_root):
    """Return root if it is inside configured PROJEKTS roots, else empty string."""
    if not raw_root:
        roots = get_available_projekts_roots()
        return roots[0] if roots else ""
    if is_within_roots(raw_root):
        return raw_root
    return ""


@PromptServer.instance.routes.get("/digit/roots")
async def get_roots(request):
    return web.json_response(get_available_projekts_roots())


@PromptServer.instance.routes.get("/digit/projects")
async def get_projects(request):
    root = _constrained_root(request.rel_url.query.get("root", ""))
    if not root:
        return web.json_response([], status=403)
    return _json_scan(scan_projects(root))


@PromptServer.instance.routes.get("/digit/shots")
async def get_shots(request):
    root = _constrained_root(request.rel_url.query.get("root", ""))
    if not root:
        return web.json_response([], status=403)
    project = request.rel_url.query.get("project", "")
    if not project:
        return web.json_response([SENTINEL_NO_SHOTS], status=400)
    return _json_scan(scan_shots(root, project))


def _pack_version():
    import subprocess

    root = os.path.dirname(os.path.abspath(__file__))
    try:
        return subprocess.check_output(
            ["git", "-C", root, "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "4.0.1"


def _comfyui_version():
    try:
        import comfyui_version

        return getattr(comfyui_version, "__version__", None) or str(comfyui_version)
    except ImportError:
        return None


@PromptServer.instance.routes.get("/digit/health")
async def get_health(request):
    payload = health_payload(_pack_version(), _comfyui_version())
    return web.json_response(payload, status=200 if payload["ok"] else 503)


@PromptServer.instance.routes.get("/digit/subfolders")
async def get_subfolders(request):
    root = _constrained_root(request.rel_url.query.get("root", ""))
    if not root:
        return web.json_response([], status=403)
    project = request.rel_url.query.get("project", "")
    shot = request.rel_url.query.get("shot", "")
    if not project or not shot:
        return web.json_response([""], status=400)
    return _json_scan(scan_child_folders(root, project, shot))


@PromptServer.instance.routes.get("/digit/tasks")
async def get_tasks(request):
    root = _constrained_root(request.rel_url.query.get("root", ""))
    if not root:
        return web.json_response([], status=403)
    project = request.rel_url.query.get("project", "")
    shot = request.rel_url.query.get("shot", "")
    subfolder = request.rel_url.query.get("subfolder", "")
    if not project or not shot or not subfolder:
        return web.json_response([""], status=400)
    return _json_scan(scan_child_folders(root, project, shot, subfolder))


@PromptServer.instance.routes.post("/digit/create_shot")
async def create_shot(request):
    try:
        data = await request.json()
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid json"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "invalid json"}, status=400)
    root = _constrained_root(data.get("root", ""))
    if not root:
        return web.json_response({"error": "root not allowed"}, status=403)
    project = str(data.get("project", "")).strip()
    shot = str(data.get("shot", "")).strip()
    subfolder = data.get("subfolder") or None
    task = data.get("task") or None
    try:
        created = create_shot_dir(root, project, shot, subfolder, task)
    except FileNotFoundError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except OSError as exc:
        return web.json_response({"error": str(exc)}, status=503)
    shots = scan_shots(root, project)
    if is_storage_unavailable(shots):
        return web.json_response({"error": "storage unavailable", "shots": shots}, status=503)
    cleaned = [name for name in shots if name and not is_placeholder(name)]
    return web.json_response({
        "ok": True,
        "shot": shot,
        "path": created,
        "shots": cleaned,
    })


class DigitImageSaver:
    CATEGORY = "DIGIT"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("filepath",)
    FUNCTION = "save_image"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        available_roots = get_available_projekts_roots() or [""]
        first_root = available_roots[0]
        projects = combo_choices(scan_projects(first_root)) if first_root else [""]
        shots = [""]

        return {
            "required": {
                "image": ("IMAGE",),
                "projekts_root": (available_roots,),
                "project": (projects,),
                "shot": (shots,),
                "filename": ("STRING", {"default": "", "tooltip": "What to name the file. Leave empty for PREFIX_SHOT_TASK. Frame number and extension are added."}),
                "subfolder": ("STRING", {"default": "comfy"}),
                "task": ("STRING", {"default": "comp"}),
                "format": (["png", "jpg", "exr"],),
                "tonemap": (["linear", "sRGB", "Reinhard"],),
                "quality": ("INT", {"default": 95, "min": 1, "max": 100, "step": 1}),
                "start_frame": ("INT", {"default": 1001, "min": 0, "max": 99999999, "step": 1}),
                "frame_pad": ("INT", {"default": 4, "min": 1, "max": 8, "step": 1}),
                "show_preview": ("BOOLEAN", {"default": True}),
                "save_workflow": (["ui", "api", "ui + api", "none"],),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Always re-execute so frame numbering increments each run.
        return float("nan")

    def save_image(self, image, projekts_root, project, shot, subfolder, task,
                   format, tonemap, quality, start_frame, frame_pad, show_preview,
                   save_workflow, filename="", prompt=None, extra_pnginfo=None):
        stem = file_stem(project, shot, task, filename)
        target_dir = resolve_pipeline_dir(projekts_root, project, shot, subfolder, task)
        os.makedirs(target_dir, exist_ok=True)

        frame_num = next_frame(target_dir, stem, format, start_frame, frame_pad)

        metadata = {}
        if prompt is not None:
            metadata["prompt"] = prompt
        if extra_pnginfo is not None:
            for key in extra_pnginfo:
                metadata[key] = extra_pnginfo[key]

        ui_images = []
        last_filepath = ""
        batch_size = image.shape[0]

        for i in range(batch_size):
            current_frame = frame_num + i
            disk_name = f"{stem}.{current_frame:0{frame_pad}d}.{format}"
            filepath = os.path.join(target_dir, disk_name)

            img_np = image[i].cpu().numpy()

            try:
                if format == "png":
                    self._save_png(img_np, filepath, metadata)
                elif format == "jpg":
                    self._save_jpg(img_np, filepath, metadata, quality)
                elif format == "exr":
                    self._save_exr(img_np, filepath, tonemap)
                    # Save sidecar metadata for first frame only
                    if i == 0 and save_workflow != "none":
                        self._save_exr_sidecar(filepath, metadata, save_workflow)
            except Exception as e:
                logger.error(f"[DigitImageSaver] SAVE FAILED: {e}", exc_info=True)
                raise

            # Save a preview copy to ComfyUI's temp dir so the UI can display it
            if show_preview and format != "exr":
                temp_dir = folder_paths.get_temp_directory()
                os.makedirs(temp_dir, exist_ok=True)
                preview_name = f"digit_preview_{disk_name}"
                preview_path = os.path.join(temp_dir, preview_name)
                img_8bit = np.clip(255.0 * img_np[:, :, :3], 0, 255).astype(np.uint8)
                Image.fromarray(img_8bit, mode="RGB").save(preview_path, format="PNG")
                ui_images.append({"filename": preview_name, "subfolder": "", "type": "temp"})

            last_filepath = filepath

        return {"ui": {"images": ui_images, "filepath_text": [last_filepath]},
                "result": (last_filepath,)}

    def _save_png(self, img_np, filepath, metadata):
        channels = img_np.shape[2] if img_np.ndim == 3 else 1
        img_8bit = np.clip(255.0 * img_np, 0, 255).astype(np.uint8)
        if channels == 4:
            pil_img = Image.fromarray(img_8bit, mode="RGBA")
        else:
            pil_img = Image.fromarray(img_8bit[:, :, :3], mode="RGB")
        pnginfo = PngImagePlugin.PngInfo()
        for key, value in metadata.items():
            pnginfo.add_text(key, json.dumps(value))
        pil_img.save(filepath, format="PNG", pnginfo=pnginfo)

    def _save_jpg(self, img_np, filepath, metadata, quality):
        # JPEG doesn't support alpha — strip to RGB
        img_8bit = np.clip(255.0 * img_np[:, :, :3], 0, 255).astype(np.uint8)
        pil_img = Image.fromarray(img_8bit, mode="RGB")

        exif_bytes = None
        if metadata:
            try:
                import piexif
                exif_dict = {"Exif": {piexif.ExifIFD.UserComment: piexif.helper.UserComment.dump(
                    json.dumps(metadata), encoding="unicode")}}
                exif_bytes = piexif.dump(exif_dict)
            except ImportError:
                pass

        save_kwargs = {"format": "JPEG", "quality": quality}
        if exif_bytes:
            save_kwargs["exif"] = exif_bytes
        pil_img.save(filepath, **save_kwargs)

    def _save_exr(self, img_np, filepath, tonemap):
        try:
            import cv2
        except ImportError:
            raise ImportError("opencv-python (cv2) is required for EXR saving. "
                              "Install with: pip install opencv-python")

        img_float32 = img_np.astype(np.float32)
        channels = img_float32.shape[2] if img_float32.ndim == 3 else 1

        # Apply tone mapping to RGB channels only
        if tonemap == "sRGB":
            rgb = sRGBtoLinear(img_float32[:, :, :3])
        elif tonemap == "Reinhard":
            rgb = img_float32[:, :, :3]
            rgb = rgb / (1.0 + rgb)
        else:
            # linear — no transform
            rgb = img_float32[:, :, :3]

        if channels == 4:
            # Invert alpha per HQ convention
            alpha = 1.0 - img_float32[:, :, 3:4]
            rgba = np.concatenate([rgb, alpha], axis=2)
            img_bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
            out = img_bgra
        else:
            out = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

        success = cv2.imwrite(filepath, out,
                              [int(cv2.IMWRITE_EXR_TYPE), int(cv2.IMWRITE_EXR_TYPE_FLOAT)])
        if not success:
            raise RuntimeError(f"cv2.imwrite failed to write EXR: {filepath}")

    def _save_exr_sidecar(self, filepath, metadata, save_workflow):
        """Save EXR metadata as JSON sidecar file(s)."""
        base = os.path.splitext(filepath)[0]

        prompt_data = metadata.get("prompt")
        workflow_data = metadata.get("workflow")

        if save_workflow in ("api", "ui + api") and prompt_data is not None:
            api_path = base + "_api.json"
            with open(api_path, "w") as f:
                json.dump(prompt_data, f, indent=2)

        if save_workflow in ("ui", "ui + api") and workflow_data is not None:
            ui_path = base + "_ui.json"
            with open(ui_path, "w") as f:
                json.dump(workflow_data, f, indent=2)
