import json
import logging
import os
import shutil

from server import PromptServer

from .projekts_utils import get_available_projekts_roots, scan_projects, scan_shots, next_frame

logger = logging.getLogger("DigitVideoSaver")


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
                   prompt=None, extra_pnginfo=None):
        prefix = project[:5]
        ext = "mp4"
        target_dir = os.path.join(projekts_root, project, "shots", shot, subfolder, task)
        os.makedirs(target_dir, exist_ok=True)

        frame_num = next_frame(target_dir, prefix, shot, task, ext, start_frame, frame_pad)

        # Build workflow metadata
        metadata = {}
        if prompt is not None:
            metadata["prompt"] = prompt
        if extra_pnginfo is not None:
            for key in extra_pnginfo:
                metadata[key] = extra_pnginfo[key]

        saved_paths = []

        # If video_paths (VIDEO_PATHS) is connected, save all videos in the batch
        if video_paths and isinstance(video_paths, list) and len(video_paths) > 0:
            for i, src_path in enumerate(video_paths):
                current_frame = frame_num + i
                filename = f"{prefix}_{shot}_{task}.{current_frame:0{frame_pad}d}.{ext}"
                filepath = os.path.join(target_dir, filename)

                if os.path.isfile(src_path):
                    shutil.copy2(src_path, filepath)
                    logger.info(f"[DigitVideoSaver] Saved: {filepath}")
                    saved_paths.append(filepath)
                else:
                    logger.warning(f"[DigitVideoSaver] Source not found: {src_path}")

        # VIDEO can arrive as one item or a ComfyUI list output. Save every item.
        elif video is not None:
            videos = video if isinstance(video, (list, tuple)) else [video]
            for i, video_item in enumerate(videos):
                current_frame = frame_num + i
                filename = f"{prefix}_{shot}_{task}.{current_frame:0{frame_pad}d}.{ext}"
                filepath = os.path.join(target_dir, filename)

                try:
                    source = video_item.get_stream_source()
                    if isinstance(source, str) and os.path.isfile(source):
                        shutil.copy2(source, filepath)
                    else:
                        video_item.save_to(filepath)
                except Exception:
                    try:
                        video_item.save_to(filepath)
                    except Exception as e:
                        logger.error(f"[DigitVideoSaver] SAVE FAILED: {e}", exc_info=True)
                        raise

                logger.info(f"[DigitVideoSaver] Saved: {filepath}")
                saved_paths.append(filepath)

        else:
            raise ValueError("No video input connected. Connect either 'video' or 'video_paths'.")

        # Save workflow sidecar for the first video only
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
