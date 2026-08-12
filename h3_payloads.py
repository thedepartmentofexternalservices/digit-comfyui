"""Pure MiniMax H3 validation and API payload builders (no network I/O)."""

from __future__ import annotations

try:
    from . import digit_video_common, h3_models, h3_pricing
except ImportError:
    import digit_video_common
    import h3_models
    import h3_pricing

MAX_PROMPT_LENGTH = 7000
MODEL_LABEL = "MiniMax H3"


def available_providers() -> list[str]:
    return h3_models.available_providers()


def collect_reference_inputs(kwargs, *, max_images: int, max_videos: int, max_audios: int):
    ref_images = [
        kwargs.get(f"reference_image{i}")
        for i in range(1, max_images + 1)
        if kwargs.get(f"reference_image{i}") is not None
    ]
    ref_videos = [
        kwargs.get(f"reference_video{i}")
        for i in range(1, max_videos + 1)
        if kwargs.get(f"reference_video{i}") is not None
    ]
    ref_audios = [
        kwargs.get(f"reference_audio{i}")
        for i in range(1, max_audios + 1)
        if kwargs.get(f"reference_audio{i}") is not None
    ]
    return ref_images, ref_videos, ref_audios


def validate_h3_request(
    *,
    prompt: str,
    provider: str,
    resolution: str,
    aspect_ratio: str,
    duration,
    first_frame=None,
    last_frame=None,
    ref_images=None,
    ref_videos=None,
    ref_audios=None,
) -> str:
    if not prompt or not prompt.strip():
        raise ValueError("Prompt is required.")
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ValueError(
            f"Prompt exceeds {MAX_PROMPT_LENGTH} characters ({len(prompt)} provided)."
        )

    if provider not in available_providers():
        if provider == "replicate":
            raise ValueError(
                "MiniMax H3 is not published on Replicate yet. Use provider=fal or provider=muapi."
            )
        raise ValueError(f"Unknown provider: {provider}")

    ref_images = ref_images or []
    ref_videos = ref_videos or []
    ref_audios = ref_audios or []

    has_refs = bool(ref_images or ref_videos or ref_audios)
    has_first_frame = first_frame is not None
    has_last_frame = last_frame is not None

    digit_video_common.validate_mode_input_conflicts(
        has_refs=has_refs,
        has_first_frame=has_first_frame,
        has_last_frame=has_last_frame,
        ref_audios_without_visual=bool(ref_audios and not (ref_images or ref_videos)),
        reference_count=len(ref_images) + len(ref_videos) + len(ref_audios),
        max_reference_files=h3_models.MAX_REFERENCE_FILES,
        model_label=MODEL_LABEL,
    )

    mode = digit_video_common.detect_video_mode(
        has_refs=has_refs,
        has_first_frame=has_first_frame,
        has_last_frame=has_last_frame,
    )

    if mode in ("image_to_video", "first_last_frame") and first_frame is None:
        raise ValueError("first_frame is required for image-to-video mode.")
    if mode == "first_last_frame" and last_frame is None:
        raise ValueError("last_frame is required for first/last-frame mode.")
    if mode == "reference_to_video" and not (ref_images or ref_videos):
        raise ValueError(
            "Reference-to-video requires at least one reference_image or reference_video."
        )

    if not h3_models.provider_supports_resolution(provider, resolution):
        caps = h3_models.provider_capabilities(provider)
        supported = ", ".join(sorted(caps.get("resolutions") or ()))
        raise ValueError(
            f"Provider '{provider}' does not support resolution '{resolution}'. "
            f"Supported: {supported or 'none'}."
        )

    if mode == "text_to_video" and aspect_ratio == "adaptive":
        raise ValueError(
            "aspect_ratio 'adaptive' is not supported for text-to-video. "
            "Pick a fixed ratio such as 16:9."
        )

    digit_video_common.parse_duration_seconds(
        duration,
        minimum=h3_models.MIN_DURATION,
        maximum=h3_models.MAX_DURATION,
    )

    unsupported = h3_models.unsupported_aspect_ratio(mode, aspect_ratio)
    if unsupported:
        raise ValueError(unsupported)

    return mode


def build_fal_args(
    *,
    prompt: str,
    mode: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    enable_prompt_expansion: bool,
    enable_safety_checker: bool,
    image_url: str | None = None,
    end_image_url: str | None = None,
    reference_image_urls: list[str] | None = None,
    reference_video_urls: list[str] | None = None,
    reference_audio_urls: list[str] | None = None,
) -> dict:
    args = {
        "prompt": prompt.strip(),
        "duration": int(duration),
        "resolution": resolution,
        "enable_prompt_expansion": bool(enable_prompt_expansion),
        "enable_safety_checker": bool(enable_safety_checker),
    }

    if mode == "text_to_video":
        args["aspect_ratio"] = aspect_ratio
    elif mode in ("image_to_video", "first_last_frame"):
        args["image_url"] = image_url
        if mode == "first_last_frame":
            args["end_image_url"] = end_image_url
    elif mode == "reference_to_video":
        args["aspect_ratio"] = aspect_ratio
        if reference_image_urls:
            args["reference_image_urls"] = reference_image_urls
        if reference_video_urls:
            args["reference_video_urls"] = reference_video_urls
        if reference_audio_urls:
            args["reference_audio_urls"] = reference_audio_urls

    return args


def build_muapi_payload(
    *,
    prompt: str,
    mode: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    image_url: str | None = None,
    last_image_url: str | None = None,
    reference_images: list[str] | None = None,
    reference_videos: list[str] | None = None,
    reference_audios: list[str] | None = None,
) -> dict:
    payload = {
        "prompt": prompt.strip(),
        "duration": int(duration),
        "resolution": h3_models.muapi_resolution(resolution),
    }

    if mode in ("text_to_video", "reference_to_video"):
        payload["aspect_ratio"] = aspect_ratio

    if mode in ("image_to_video", "first_last_frame"):
        payload["image_url"] = image_url
        if mode == "first_last_frame":
            payload["last_image_url"] = last_image_url
    elif mode == "reference_to_video":
        if reference_images:
            payload["reference_images"] = reference_images
        if reference_videos:
            payload["reference_videos"] = reference_videos
        if reference_audios:
            payload["reference_audios"] = reference_audios

    return payload


def parse_estimate_request(body: dict) -> dict:
    """Validate /digit/h3/estimate JSON body."""
    provider = str(body.get("provider") or "fal").strip().lower()
    if provider not in h3_pricing.PROVIDERS:
        raise ValueError(f"Invalid provider: {provider}")

    mode = str(body.get("mode") or "text_to_video")
    if mode not in h3_models.MODES:
        raise ValueError(f"Invalid mode: {mode}")

    resolution = str(body.get("resolution") or "2K")
    if resolution not in h3_models.RESOLUTIONS:
        raise ValueError(f"Invalid resolution: {resolution}")

    try:
        batch_count = int(body.get("batch_count") or 1)
    except (TypeError, ValueError):
        batch_count = 1
    batch_count = max(1, min(batch_count, h3_models.MAX_BATCH_COUNT))

    duration_seconds = digit_video_common.parse_duration_seconds(
        body.get("duration", 5),
        minimum=h3_models.MIN_DURATION,
        maximum=h3_models.MAX_DURATION,
    )

    return {
        "provider": provider,
        "mode": mode,
        "resolution": resolution,
        "duration_seconds": duration_seconds,
        "batch_count": batch_count,
        "has_video_refs": bool(body.get("has_video_refs", False)),
        "ref_image_count": max(0, int(body.get("ref_image_count") or 0)),
    }
