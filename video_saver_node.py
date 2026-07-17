import glob
import json
import logging
import os
import re
import shutil

import folder_paths

from .projekts_utils import get_available_projekts_roots, scan_projects, scan_shots, next_frame

logger = logging.getLogger("DigitVideoSaver")

# Video generators that emit batch siblings in ComfyUI temp with a shared timestamp.
BATCH_VIDEO_NODE_TYPES = frozenset({
    "DigitDanceVideo",
    "DigitReplicateSeedance",
    "DigitVeoVideo",
    "DigitOmniVideo",
})

# temp filename prefix -> glob pattern (batch timestamp + uuid inserted).
BATCH_TEMP_GLOBS = {
    "dance_": "dance_{ts}_{uid}_*.mp4",
    # Legacy Seedance provider prefixes (pre-unified naming).
    "dance_muapi_": "dance_muapi_{ts}_{uid}_*.mp4",
    "dance_replicate_": "dance_replicate_{ts}_{uid}_*.mp4",
    "replicate_seedance_": "replicate_seedance_{ts}_{uid}_*.mp4",
    "veo_": "veo_{ts}_{uid}_*.mp4",
    "omni_": "omni_{ts}_{uid}_*.mp4",
}


def _is_link(value):
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)


def _parse_batch_timestamp(filename):
    for prefix, pattern in BATCH_TEMP_GLOBS.items():
        if not filename.startswith(prefix):
            continue
        match = re.match(rf"^{re.escape(prefix)}(\d+)_([a-f0-9]{{8}})_", filename)
        if match:
            return pattern.format(ts=match.group(1), uid=match.group(2))
    return None


def _expand_digit_batch_paths(video_path):
    """Find sibling batch outputs sharing the same generator timestamp."""
    if not video_path or not os.path.isfile(video_path):
        return []

    directory = os.path.dirname(video_path)
    glob_pattern = _parse_batch_timestamp(os.path.basename(video_path))
    if not glob_pattern:
        return [video_path]

    matches = sorted(
        glob.glob(os.path.join(directory, glob_pattern)),
        key=lambda path: (os.path.getmtime(path), path),
    )
    if len(matches) <= 1:
        return [video_path]
    return matches


def _resolve_video_file_path(video):
    try:
        source = video.get_stream_source()
        if isinstance(source, str) and os.path.isfile(source):
            return source
    except Exception:
        pass

    temp_dir = folder_paths.get_temp_directory()
    os.makedirs(temp_dir, exist_ok=True)
    tmp_path = os.path.join(temp_dir, f"digit_video_saver_{os.getpid()}.mp4")
    video.save_to(tmp_path)
    return tmp_path


def _expected_batch_count(prompt, unique_id):
    if not prompt or unique_id is None:
        return None
    node = prompt.get(str(unique_id)) or prompt.get(unique_id)
    if not node:
        return None
    video_link = node.get("inputs", {}).get("video")
    if not _is_link(video_link):
        return None
    upstream = prompt.get(str(video_link[0])) or {}
    if upstream.get("class_type") not in BATCH_VIDEO_NODE_TYPES:
        return None
    batch_count = upstream.get("inputs", {}).get("batch_count")
    if batch_count is None:
        return None
    try:
        return max(1, int(batch_count))
    except (TypeError, ValueError):
        return None


def _resolve_source_paths(video_paths, video, prompt=None, unique_id=None):
    if video_paths and isinstance(video_paths, list):
        paths = [path for path in video_paths if isinstance(path, str) and os.path.isfile(path)]
        if paths:
            return paths

    if video is None:
        if video_paths is not None:
            logger.warning(
                "[DigitVideoSaver] video_paths was connected but empty; connect Seedance 'video' instead."
            )
        return []

    video_path = _resolve_video_file_path(video)
    paths = _expand_digit_batch_paths(video_path)
    expected = _expected_batch_count(prompt, unique_id)
    if expected and len(paths) > expected:
        paths = paths[:expected]

    if len(paths) > 1:
        logger.info(
            "[DigitVideoSaver] Resolved %d batch videos from upstream generator (expected=%s).",
            len(paths),
            expected,
        )
    return paths


class DigitVideoSaver:
    CATEGORY = "DIGIT"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("filepaths",)
    FUNCTION = "save_video"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        available_roots = get_available_projekts_roots()

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
                "start_frame": ("INT", {"default": 1001, "min": 0, "max": 99999999, "step": 1}),
                "frame_pad": ("INT", {"default": 4, "min": 1, "max": 8, "step": 1}),
                "save_workflow": (["ui", "api", "ui + api", "none"],),
            },
            "optional": {
                "video": ("VIDEO",),
                "video_paths": ("VIDEO_PATHS",),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
                "unique_id": "UNIQUE_ID",
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def save_video(self, projekts_root, project, shot, subfolder, task,
                   start_frame, frame_pad, save_workflow,
                   video=None, video_paths=None,
                   prompt=None, extra_pnginfo=None, unique_id=None):
        prefix = project[:5]
        ext = "mp4"
        target_dir = os.path.join(projekts_root, project, "shots", shot, subfolder, task)
        os.makedirs(target_dir, exist_ok=True)

        frame_num = next_frame(target_dir, prefix, shot, task, ext, start_frame, frame_pad)

        metadata = {}
        if prompt is not None:
            metadata["prompt"] = prompt
        if extra_pnginfo is not None:
            for key in extra_pnginfo:
                metadata[key] = extra_pnginfo[key]

        source_paths = _resolve_source_paths(video_paths, video, prompt, unique_id)
        if not source_paths:
            raise ValueError(
                "No video input connected. Wire Seedance 'video' to DigitVideoSaver 'video' "
                "(batch clips expand automatically)."
            )

        saved_paths = []
        for i, src_path in enumerate(source_paths):
            current_frame = frame_num + i
            filename = f"{prefix}_{shot}_{task}.{current_frame:0{frame_pad}d}.{ext}"
            filepath = os.path.join(target_dir, filename)

            if os.path.isfile(src_path):
                shutil.copy2(src_path, filepath)
                logger.info(f"[DigitVideoSaver] Saved: {filepath}")
                saved_paths.append(filepath)
            else:
                logger.warning(f"[DigitVideoSaver] Source not found: {src_path}")

        if not saved_paths:
            raise RuntimeError("No batch videos were saved to PROJEKTS.")

        if save_workflow != "none" and metadata and saved_paths:
            self._save_sidecar(saved_paths[0], metadata, save_workflow)

        result_text = "\n".join(saved_paths)

        return {"ui": {"filepath_text": saved_paths},
                "result": (result_text,)}

    def _save_sidecar(self, filepath, metadata, save_workflow):
        """Save workflow metadata as JSON sidecar file(s)."""
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
