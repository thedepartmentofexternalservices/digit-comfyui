"""MiniMax H3 endpoint registry, capability matrix, and shared constants."""

MODES = ("text_to_video", "image_to_video", "first_last_frame", "reference_to_video")

ALL_PROVIDERS = ["fal", "muapi", "replicate"]

RESOLUTIONS = ["768P", "2K", "4K"]
MUAPI_RESOLUTIONS = frozenset({"2K"})

ASPECT_RATIOS = ["16:9", "21:9", "4:3", "1:1", "3:4", "9:16", "adaptive"]
T2V_ASPECT_RATIOS = ["16:9", "21:9", "4:3", "1:1", "3:4", "9:16"]
R2V_ASPECT_RATIOS = ASPECT_RATIOS

MIN_DURATION = 4
MAX_DURATION = 15
DURATIONS = [str(seconds) for seconds in range(MIN_DURATION, MAX_DURATION + 1)]

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

# Set when Replicate publishes MiniMax H3 (e.g. minimax/hailuo-03).
REPLICATE_MODEL = None

PROVIDER_CAPABILITIES = {
    "fal": {
        "resolutions": frozenset(RESOLUTIONS),
        "modes": frozenset(MODES),
        "prompt_expansion": True,
        "safety_checker": True,
    },
    "muapi": {
        "resolutions": MUAPI_RESOLUTIONS,
        "modes": frozenset(MODES),
        "prompt_expansion": False,
        "safety_checker": False,
    },
    "replicate": {
        "resolutions": frozenset(),
        "modes": frozenset(),
        "prompt_expansion": False,
        "safety_checker": False,
    },
}


def available_providers():
    providers = ["fal", "muapi"]
    if REPLICATE_MODEL:
        providers.append("replicate")
    return providers


def provider_capabilities(provider: str) -> dict:
    return dict(PROVIDER_CAPABILITIES.get(provider, {}))


def fal_app_id(mode):
    app_id = FAL_APPS.get(mode)
    if app_id is None:
        raise ValueError(f"Unsupported H3 mode for fal: {mode}")
    return app_id


def muapi_endpoint(mode):
    endpoint = MUAPI_ENDPOINTS.get(mode)
    if endpoint is None:
        raise ValueError(f"Unsupported H3 mode for muapi: {mode}")
    return endpoint


def muapi_resolution(resolution):
    mapping = {"768P": "768p", "2K": "2k", "4K": "4k"}
    return mapping.get(resolution, resolution.lower())


def provider_supports_resolution(provider, resolution):
    caps = PROVIDER_CAPABILITIES.get(provider, {})
    resolutions = caps.get("resolutions") or frozenset()
    return resolution in resolutions


def unsupported_aspect_ratio(mode, aspect_ratio):
    if mode == "text_to_video" and aspect_ratio == "adaptive":
        return (
            "aspect_ratio 'adaptive' is not supported for text-to-video. "
            "Pick a fixed ratio such as 16:9."
        )
    if mode in ("image_to_video", "first_last_frame") and aspect_ratio not in T2V_ASPECT_RATIOS:
        return None  # ignored at runtime; follows source image
    if mode == "reference_to_video" and aspect_ratio not in R2V_ASPECT_RATIOS:
        return f"aspect_ratio '{aspect_ratio}' is not supported for reference-to-video."
    return None
