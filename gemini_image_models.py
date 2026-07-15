"""Shared Gemini image model IDs for DIGIT image generation nodes."""

GEMINI_IMAGE_MODELS = [
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite-image",
    "gemini-3-pro-image-preview",
    "gemini-2.5-flash-image",
]

DEFAULT_GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"

MODELS_1K_ONLY = frozenset({"gemini-3.1-flash-lite-image"})

# Models that reject generationConfig.thinkingConfig with HTTP 400
# ("thinking_level is not supported by this model"). Verified against Vertex.
MODELS_NO_THINKING = frozenset({
    "gemini-3-pro-image-preview",
    "gemini-2.5-flash-image",
})

RESOLUTIONS = ["1K", "2K", "4K"]
RESOLUTIONS_1K_ONLY = ["1K"]
