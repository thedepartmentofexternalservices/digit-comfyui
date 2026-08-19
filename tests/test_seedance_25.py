"""Unit tests for Seedance 2.5 model wiring and validation."""

from __future__ import annotations

import pytest
from digit_loader import load_digit_module

seedance = load_digit_module("seedance_video_node")


def test_default_model_remains_seedance_20():
    required = seedance.DigitDanceVideo.INPUT_TYPES()["required"]
    assert required["model"][1]["default"] == "seedance-2.0"
    assert "seedance-2.5" in required["model"][0]


def test_seedance_25_fal_apps():
    apps = seedance.SEEDANCE_APPS["seedance-2.5"]
    assert apps["text_to_video"] == "bytedance/seedance-2.5/text-to-video"
    assert apps["image_to_video"] == "bytedance/seedance-2.5/image-to-video"
    assert apps["reference_to_video"] == "bytedance/seedance-2.5/reference-to-video"


def test_duration_and_reference_socket_caps():
    assert seedance.DURATIONS[0] == "auto"
    assert seedance.DURATIONS[-1] == "30"
    assert seedance.MAX_REFERENCE_IMAGES == 30
    assert seedance.MAX_REFERENCE_VIDEOS == 10
    assert seedance.MAX_REFERENCE_AUDIOS == 10
    optional = seedance.DigitDanceVideo.INPUT_TYPES()["optional"]
    assert "reference_image30" in optional
    assert "reference_video10" in optional
    assert "reference_audio10" in optional


def test_model_max_duration():
    assert seedance._model_max_duration("seedance-2.0") == 15
    assert seedance._model_max_duration("seedance-2.5") == 30


def test_validate_model_provider_fal_only():
    seedance._validate_model_provider("fal", "seedance-2.5")
    with pytest.raises(ValueError, match="fal-only"):
        seedance._validate_model_provider("muapi", "seedance-2.5")
    with pytest.raises(ValueError, match="fal-only"):
        seedance._validate_model_provider("replicate", "seedance-2.5")


def test_validate_duration_for_model():
    seedance._validate_duration_for_model("seedance-2.5", "30")
    seedance._validate_duration_for_model("seedance-2.0", "15")
    with pytest.raises(ValueError, match="seedance-2.0"):
        seedance._validate_duration_for_model("seedance-2.0", "16")
    with pytest.raises(ValueError, match="seedance-2.5"):
        seedance._validate_duration_for_model("seedance-2.5", "31")


def test_validate_references_for_model_20_vs_25():
    ten_images = [object()] * 10
    with pytest.raises(ValueError, match="at most 9 reference images"):
        seedance._validate_references_for_model("seedance-2.0", ten_images, [], [])
    seedance._validate_references_for_model("seedance-2.5", ten_images, [], [])


def test_models_with_bitrate():
    assert "seedance-2.0" in seedance.MODELS_WITH_BITRATE
    assert "seedance-2.5" not in seedance.MODELS_WITH_BITRATE
