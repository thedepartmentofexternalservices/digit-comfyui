"""Unit tests for Gemini image model ID resolution."""

from __future__ import annotations

from digit_loader import load_digit_module

gemini_image_models = load_digit_module("gemini_image_models")


def test_ga_models_listed():
    assert "gemini-3-pro-image" in gemini_image_models.GEMINI_IMAGE_MODELS
    assert "gemini-3-pro-image-preview" not in gemini_image_models.GEMINI_IMAGE_MODELS


def test_preview_aliases_upgrade():
    for preview, ga in gemini_image_models.DEPRECATED_MODEL_ALIASES.items():
        assert gemini_image_models.resolve_gemini_image_model(preview) == ga


def test_pro_rejects_thinking():
    assert not gemini_image_models.image_model_supports_thinking("gemini-3-pro-image")
    assert not gemini_image_models.image_model_supports_thinking("gemini-3-pro-image-preview")


def test_apply_thinking_config_skips_pro():
    config = {"responseModalities": ["TEXT", "IMAGE"]}
    gemini_image_models.apply_image_thinking_config(config, "gemini-3-pro-image", "HIGH")
    assert "thinkingConfig" not in config


def test_apply_thinking_config_adds_flash():
    config = {"responseModalities": ["TEXT", "IMAGE"]}
    gemini_image_models.apply_image_thinking_config(config, "gemini-3.1-flash-image", "MINIMAL")
    assert config["thinkingConfig"] == {"thinkingLevel": "MINIMAL"}
