"""Shade.inc nodes — mount a project drive and save output to it.

Architecture:
  - Each Shade project is a separate drive (filespace) identified by a UUID.
  - shadefs daemon manages mounts. A drive must be configured in the local
    database before it can be mounted.
  - Drives mount at /Volumes/shade/<drive_name>.
  - ShadeMount lists available drives from the REST API, configures and mounts
    the selected one, and outputs the mount path as a STRING.
  - ShadeSave writes images or video clips to <mount_path>/output/.

Environment variables (set via systemd drop-in or /etc/profile.d/):
  SHADE_API_KEY       Shade API key (sk_...)
  SHADE_WORKSPACE_ID  Shade workspace UUID
  SHADE_DB_DIR        shadefs database dir (default: /root/.shade/fs)
  SHADE_MOUNT_BASE    Base dir for mounts (default: /Volumes/shade)
  SHADEFS_BIN         Path to shadefs binary
"""

import logging
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import json

import numpy as np
from PIL import Image

logger = logging.getLogger("DigitShade")

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

_SHADEFS_BIN_CANDIDATES = [
    os.environ.get("SHADEFS_BIN", ""),
    "/opt/Shade/resources/app.asar.unpacked/node_modules/shadefs/bin/shadefs",
]
SHADEFS_BIN = next((p for p in _SHADEFS_BIN_CANDIDATES if p and os.path.isfile(p)), "")

DB_DIR        = os.environ.get("SHADE_DB_DIR",        "/root/.shade/fs")
MOUNT_BASE    = os.environ.get("SHADE_MOUNT_BASE",    "/Volumes/shade")
API_URL       = "https://api.shade.inc"
WORKSPACE_ID  = os.environ.get("SHADE_WORKSPACE_ID", "65c31db7-3eb0-4ce5-991c-84a4fa2a94aa")

_TOKEN_SCRIPT = "/opt/shade-scripts/shade-token-refresh.sh"


def _api_key():
    """Read API key from env or the token refresh script."""
    key = os.environ.get("SHADE_API_KEY", "")
    if key:
        return key
    try:
        with open(_TOKEN_SCRIPT) as f:
            for line in f:
                line = line.strip()
                if line.startswith("API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _shade_request(path):
    """GET from api.shade.inc, return parsed JSON or []."""
    key = _api_key()
    if not key:
        logger.warning("[ShadeMount] No SHADE_API_KEY found.")
        return []
    url = f"{API_URL}{path}"
    req = urllib.request.Request(url, headers={"Authorization": key})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.error(f"[ShadeMount] API request failed: {e}")
        return []


def _list_drives():
    """Return list of {name, id} dicts from the Shade workspace."""
    drives = _shade_request(f"/workspaces/{WORKSPACE_ID}/drives")
    if not drives:
        return [{"name": "(no drives found)", "id": ""}]
    return [{"name": d["name"], "id": d["id"]} for d in drives if d.get("name")]


def _drive_number(uuid):
    """Stable 28-bit integer drive number derived from UUID."""
    return abs(hash(uuid)) % (2 ** 28)


def _is_configured(drive_number):
    """True if drive_number already exists in the shadefs database."""
    if not SHADEFS_BIN:
        return False
    result = subprocess.run(
        [SHADEFS_BIN, "-d", DB_DIR, "query"],
        capture_output=True, text=True
    )
    return f"DRIVE {drive_number}" in result.stdout


def _configure_drive(drive_number, drive_uuid):
    """Add a drive to the shadefs database."""
    subprocess.run(
        [SHADEFS_BIN, "-d", DB_DIR, "config:drive",
         str(drive_number), "--create",
         "--remote", f"https://fs.shade.inc/{drive_uuid}"],
        check=True, capture_output=True, text=True
    )
    logger.info(f"[ShadeMount] Configured drive {drive_number} → {drive_uuid}")


def _mount_drive(drive_number, mount_path):
    """Mount an already-configured drive. No-op if already mounted."""
    if os.path.ismount(mount_path):
        logger.info(f"[ShadeMount] Already mounted: {mount_path}")
        return
    os.makedirs(mount_path, exist_ok=True)
    subprocess.run(
        [SHADEFS_BIN, "-d", DB_DIR, "mount", str(drive_number), mount_path],
        check=True, capture_output=True, text=True
    )
    logger.info(f"[ShadeMount] Mounted drive {drive_number} at {mount_path}")


def _next_index(output_dir, prefix, ext):
    """Auto-increment 4-digit file index based on existing files."""
    pat = re.compile(rf"^{re.escape(prefix)}_(\d{{4}})\.{re.escape(ext)}$")
    max_idx = 0
    if os.path.isdir(output_dir):
        for f in os.listdir(output_dir):
            m = pat.match(f)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
    return max_idx + 1


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #

class ShadeMount:
    """Mount a Shade project drive and output its path.

    Lists all drives from the Shade REST API. When executed, configures the
    drive in shadefs (if not already present), mounts it at
    /Volumes/shade/<project_name>, and returns the mount path as a STRING
    for use in Save to Shade or other file-path nodes.
    """

    CATEGORY = "DIGIT/Shade"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("mount_path",)
    FUNCTION = "mount_drive"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        drives = _list_drives()
        drive_names = [d["name"] for d in drives]
        return {
            "required": {
                "project": (drive_names,),
            }
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def mount_drive(self, project):
        if not SHADEFS_BIN:
            raise RuntimeError("[ShadeMount] shadefs binary not found. "
                               "Is Shade installed at /opt/Shade?")

        # Look up UUID for the selected project name
        drives = _list_drives()
        drive_uuid = next((d["id"] for d in drives if d["name"] == project), "")
        if not drive_uuid:
            raise ValueError(f"[ShadeMount] Drive not found in API: {project}")

        drive_number = _drive_number(drive_uuid)
        mount_path = os.path.join(MOUNT_BASE, project)

        if not _is_configured(drive_number):
            _configure_drive(drive_number, drive_uuid)

        _mount_drive(drive_number, mount_path)

        return (mount_path,)


class ShadeSave:
    """Save images or video clips to <mount_path>/output/.

    Connect mount_path from ShadeMount (or any STRING with a valid path).
    Files are written as <filename_prefix>_NNNN.<format> with auto-increment.
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
                "mount_path":      ("STRING", {"forceInput": True}),
                "filename_prefix": ("STRING", {"default": "output"}),
                "format":          (["png", "jpg"],),
                "quality":         ("INT", {"default": 95, "min": 1, "max": 100, "step": 1}),
            },
            "optional": {
                "image":       ("IMAGE",),
                "video_paths": ("VIDEO_PATHS",),
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def save_to_shade(self, mount_path, filename_prefix, format, quality,
                      image=None, video_paths=None):
        output_dir = os.path.join(mount_path, "output")
        os.makedirs(output_dir, exist_ok=True)

        saved = []

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
                logger.info(f"[ShadeSave] Saved: {filepath}")
                saved.append(filepath)

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
                logger.info(f"[ShadeSave] Saved: {filepath}")
                saved.append(filepath)

        if not saved:
            raise ValueError("[ShadeSave] No image or video_paths connected.")

        result_text = "\n".join(saved)
        return {"ui": {"filepath_text": saved}, "result": (result_text,)}
