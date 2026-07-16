"""Shared Seedream image model/endpoint IDs for DIGIT image generation nodes."""

# Each model maps to its fal.ai endpoint pair. Adding a future model
# (seedream 5.5, ...) is a one-entry change here.
SEEDREAM_IMAGE_APPS = {
    "seedream-5.0-pro": {
        "text_to_image": "bytedance/seedream/v5/pro/text-to-image",
        "edit": "bytedance/seedream/v5/pro/edit",
    },
    "seedream-5.0-lite": {
        "text_to_image": "bytedance/seedream/v5/lite/text-to-image",
        "edit": "bytedance/seedream/v5/lite/edit",
    },
}

SEEDREAM_IMAGE_MODELS = list(SEEDREAM_IMAGE_APPS.keys())
DEFAULT_SEEDREAM_IMAGE_MODEL = "seedream-5.0-pro"

# Union of pro and lite presets plus "custom" which sends {width, height}.
# Pro supports auto_1K/auto_2K; lite supports auto_2K/auto_3K/auto_4K.
IMAGE_SIZES = [
    "auto_1K",
    "auto_2K",
    "auto_3K",
    "auto_4K",
    "square_hd",
    "square",
    "portrait_4_3",
    "portrait_16_9",
    "landscape_4_3",
    "landscape_16_9",
    "custom",
]

SIZES_PRO_ONLY = frozenset({"auto_1K"})
SIZES_LITE_ONLY = frozenset({"auto_3K", "auto_4K"})

# Only the pro endpoints accept output_format; lite always returns jpeg.
OUTPUT_FORMATS = ["jpeg", "png"]
MODELS_WITH_OUTPUT_FORMAT = frozenset({"seedream-5.0-pro"})
# Only the lite endpoints accept max_images (up to max_images per generation).
MODELS_WITH_MAX_IMAGES = frozenset({"seedream-5.0-lite"})

# Custom dimensions: pro wants total pixels 1024^2-2048^2 (aspect 1/16-16),
# lite wants 2560x1440-4096x4096. The API validates totals; widgets allow the
# full union range.
CUSTOM_DIM_MIN = 256
CUSTOM_DIM_MAX = 4096
CUSTOM_DIM_STEP = 16

# Seedream edit endpoints accept up to 10 reference images.
MAX_REFERENCE_IMAGES = 10
MAX_NUM_IMAGES = 6
