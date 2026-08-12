"""MiniMax H3 endpoint registry and shared constants for DIGIT nodes."""

MODES = ("text_to_video", "image_to_video", "first_last_frame", "reference_to_video")

PROVIDERS = ["fal", "muapi", "replicate"]

RESOLUTIONS = ["768P", "2K", "4K"]
MUAPI_RESOLUTIONS = {"2K"}

ASPECT_RATIOS = ["16:9", "21:9", "4:3", "1:1", "3:4", "9:16", "adaptive"]
T2V_ASPECT_RATIOS = ["16:9", "21:9", "4:3", "1:1", "3:4", "9:16"]
R2V_ASPECT_RATIOS = ASPECT_RATIOS

DURATIONS = [str(seconds) for seconds in range(4, 16)]

MAX_REFERENCE_IMAGES = 9
MAX_REFERENCE_VIDEOS = 3
MAX_REFERENCE_AUDIOS = 3
MAX_REFERENCE_FILES = 12
MAX_BATCH_COUNT = 8

FAL_APPS = {
    "text_to_video": "minimax/h3/text-to-video",
    "image_to_video": "minimax/h3/image-to-video",
    "first_last_frame": "minimax/h3/image-to-video",
    "reference_to_video": "minimax/h3/reference-to-video",
}

MUAPI_ENDPOINTS = {
    "text_to_video": "minimax-h3-text-to-video",
    "image_to_video": "minimax-h3-image-to-video",
    "first_last_frame": "minimax-h3-image-to-video",
    "reference_to_video": "minimax-h3-reference-to-video",
}

REPLICATE_MODEL = None  # Not published on Replicate yet.


def fal_app_id(mode):
    """Return the fal app ID for a generation mode."""
    app_id = FAL_APPS.get(mode)
    if app_id is None:
        raise ValueError(f"Unsupported H3 mode for fal: {mode}")
    return app_id


def muapi_endpoint(mode):
    """Return the MUAPI endpoint slug for a generation mode."""
    endpoint = MUAPI_ENDPOINTS.get(mode)
    if endpoint is None:
        raise ValueError(f"Unsupported H3 mode for muapi: {mode}")
    return endpoint


def muapi_resolution(resolution):
    """Map node resolution enum to MUAPI payload value."""
    mapping = {"768P": "768p", "2K": "2k", "4K": "4k"}
    return mapping.get(resolution, resolution.lower())


def provider_supports_resolution(provider, resolution):
    if provider == "muapi":
        return resolution in MUAPI_RESOLUTIONS
    if provider == "fal":
        return resolution in RESOLUTIONS
    if provider == "replicate":
        return False
    return False
