"""Shared GPT Image model/endpoint IDs for DIGIT image generation nodes."""

# Each model maps to its fal.ai endpoint pair. Adding a future model
# (gpt-image-3, ...) is a one-entry change here.
GPT_IMAGE_APPS = {
    "gpt-image-2": {
        "text_to_image": "openai/gpt-image-2",
        "edit": "openai/gpt-image-2/edit",
    },
}

GPT_IMAGE_MODELS = list(GPT_IMAGE_APPS.keys())
DEFAULT_GPT_IMAGE_MODEL = "gpt-image-2"

# fal presets plus "custom" which sends an explicit {width, height} object.
IMAGE_SIZES = [
    "auto",
    "square_hd",
    "square",
    "portrait_4_3",
    "portrait_16_9",
    "landscape_4_3",
    "landscape_16_9",
    "custom",
]

QUALITIES = ["auto", "low", "medium", "high"]
OUTPUT_FORMATS = ["png", "jpeg", "webp"]

# Custom dimensions must be multiples of 16, max edge 3840, ratio <= 3:1,
# total pixels 655,360 - 8,294,400 (fal GPT Image 2 schema).
CUSTOM_DIM_MIN = 320
CUSTOM_DIM_MAX = 3840
CUSTOM_DIM_STEP = 16

# GPT Image 2 accepts up to 16 reference images per edit call.
MAX_REFERENCE_IMAGES = 16
