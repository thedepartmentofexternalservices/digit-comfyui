"""One destination-first saver for DIGIT images and videos."""

from .image_saver_node import DigitImageSaver
from .projekts_utils import combo_choices, get_available_projekts_roots, scan_projects
from .video_saver_node import DigitVideoSaver


class DigitUberSaver:
    """Save one IMAGE batch or VIDEO input through the same pipeline controls."""

    CATEGORY = "DIGIT"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("filepath",)
    FUNCTION = "save"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        available_roots = get_available_projekts_roots() or [""]
        first_root = available_roots[0]
        projects = combo_choices(scan_projects(first_root)) if first_root else [""]

        return {
            "required": {
                "projekts_root": (available_roots,),
                "project": (projects,),
                "shot": ([""],),
                "folder": (["comfy/comp"],),
                "name": ("STRING", {
                    "default": "",
                    "tooltip": (
                        "What to name the file. Leave empty for PREFIX_SHOT_FOLDER. "
                        "Frame number and extension are added."
                    ),
                }),
                "format": (["png", "jpg", "exr"],),
                "tonemap": (["linear", "sRGB", "Reinhard"],),
                "quality": ("INT", {
                    "default": 95, "min": 1, "max": 100, "step": 1,
                }),
                "start_frame": ("INT", {
                    "default": 1001, "min": 0, "max": 99999999, "step": 1,
                }),
                "frame_pad": ("INT", {
                    "default": 4, "min": 1, "max": 8, "step": 1,
                }),
                "show_preview": ("BOOLEAN", {"default": True}),
                "save_workflow": (["ui", "api", "ui + api", "none"],),
            },
            "optional": {
                "image": ("IMAGE",),
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

    def save(self, projekts_root, project, shot, folder, name,
             format, tonemap, quality, start_frame, frame_pad,
             show_preview, save_workflow, image=None, video=None,
             video_paths=None, prompt=None, extra_pnginfo=None,
             unique_id=None):
        has_image = image is not None
        has_video = video is not None or video_paths is not None

        if has_image and has_video:
            raise ValueError(
                "DIGIT Uber Saver accepts one media type at a time. "
                "Disconnect either image or video."
            )
        if not has_image and not has_video:
            raise ValueError(
                "DIGIT Uber Saver needs an image or video input."
            )

        if has_image:
            return DigitImageSaver().save_image(
                image=image,
                projekts_root=projekts_root,
                project=project,
                shot=shot,
                folder=folder,
                filename=name,
                format=format,
                tonemap=tonemap,
                quality=quality,
                start_frame=start_frame,
                frame_pad=frame_pad,
                show_preview=show_preview,
                save_workflow=save_workflow,
                prompt=prompt,
                extra_pnginfo=extra_pnginfo,
            )

        return DigitVideoSaver().save_video(
            projekts_root=projekts_root,
            project=project,
            shot=shot,
            folder=folder,
            filename=name,
            start_frame=start_frame,
            frame_pad=frame_pad,
            save_workflow=save_workflow,
            video=video,
            video_paths=video_paths,
            prompt=prompt,
            extra_pnginfo=extra_pnginfo,
            unique_id=unique_id,
        )
