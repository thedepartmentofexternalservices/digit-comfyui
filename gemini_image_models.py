"""Shared Gemini image model IDs for DIGIT image generation nodes."""

GEMINI_IMAGE_MODELS = [
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite-image",
    "gemini-3-pro-image",
    "gemini-2.5-flash-image",
]

DEFAULT_GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"

MODELS_1K_ONLY = frozenset({"gemini-3.1-flash-lite-image"})

# Models that reject generationConfig.thinkingConfig with HTTP 400
# ("thinking_level is not supported by this model"). Verified against Vertex.
MODELS_NO_THINKING = frozenset({
    "gemini-3-pro-image",
    "gemini-2.5-flash-image",
})

# Google shut down *-preview image endpoints 2026-06-25; remap saved workflows.
DEPRECATED_MODEL_ALIASES = {
    "gemini-3-pro-image-preview": "gemini-3-pro-image",
    "gemini-3.1-flash-image-preview": "gemini-3.1-flash-image",
}

RESOLUTIONS = ["1K", "2K", "4K"]
RESOLUTIONS_1K_ONLY = ["1K"]


def resolve_gemini_image_model(model: str) -> str:
    """Return the current Vertex model ID, upgrading retired preview names."""
    return DEPRECATED_MODEL_ALIASES.get(model, model)


def image_model_supports_thinking(model: str) -> bool:
    """Whether Vertex accepts thinkingConfig for this image model."""
    return resolve_gemini_image_model(model) not in MODELS_NO_THINKING


def apply_image_thinking_config(generation_config: dict, model: str, thinking_level: str) -> None:
    """Attach thinkingConfig when the resolved model supports it."""
    if image_model_supports_thinking(model):
        generation_config["thinkingConfig"] = {"thinkingLevel": thinking_level}
