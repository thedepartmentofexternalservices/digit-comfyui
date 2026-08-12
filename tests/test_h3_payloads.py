"""Unit tests for MiniMax H3 payload validation and builders."""

from __future__ import annotations

import pytest
from digit_loader import load_digit_module

payloads = load_digit_module("h3_payloads")
models = load_digit_module("h3_models")


def test_validate_t2v_ok():
    mode = payloads.validate_h3_request(
        prompt="A cat in a garden",
        provider="fal",
        resolution="2K",
        aspect_ratio="16:9",
        duration="5",
    )
    assert mode == "text_to_video"


def test_validate_rejects_adaptive_t2v():
    with pytest.raises(ValueError, match="adaptive"):
        payloads.validate_h3_request(
            prompt="test",
            provider="fal",
            resolution="2K",
            aspect_ratio="adaptive",
            duration="5",
        )


def test_validate_rejects_replicate_when_unavailable():
    with pytest.raises(ValueError, match="not published on Replicate"):
        payloads.validate_h3_request(
            prompt="test",
            provider="replicate",
            resolution="2K",
            aspect_ratio="16:9",
            duration="5",
        )


def test_validate_r2v_requires_visual_ref():
    with pytest.raises(ValueError, match="reference_audio requires"):
        payloads.validate_h3_request(
            prompt="test",
            provider="fal",
            resolution="2K",
            aspect_ratio="16:9",
            duration="5",
            ref_audios=["audio"],
        )


def test_validate_muapi_resolution():
    with pytest.raises(ValueError, match="does not support resolution"):
        payloads.validate_h3_request(
            prompt="test",
            provider="muapi",
            resolution="768P",
            aspect_ratio="16:9",
            duration="5",
        )


def test_validate_prompt_length():
    with pytest.raises(ValueError, match="7000"):
        payloads.validate_h3_request(
            prompt="x" * 7001,
            provider="fal",
            resolution="2K",
            aspect_ratio="16:9",
            duration="5",
        )


def test_build_fal_args_r2v():
    args = payloads.build_fal_args(
        prompt="Image 1 is the hero",
        mode="reference_to_video",
        resolution="2K",
        aspect_ratio="adaptive",
        duration=6,
        enable_prompt_expansion=True,
        enable_safety_checker=True,
        reference_image_urls=["https://example.com/a.jpg"],
    )
    assert args["reference_image_urls"] == ["https://example.com/a.jpg"]
    assert args["duration"] == 6


def test_build_muapi_payload_i2v():
    payload = payloads.build_muapi_payload(
        prompt="motion",
        mode="image_to_video",
        resolution="2K",
        aspect_ratio="16:9",
        duration=5,
        image_url="https://cdn.example.com/frame.png",
    )
    assert payload["image_url"] == "https://cdn.example.com/frame.png"
    assert payload["resolution"] == "2k"


def test_parse_estimate_request_valid():
    params = payloads.parse_estimate_request({
        "provider": "fal",
        "mode": "text_to_video",
        "resolution": "2K",
        "duration": "8",
        "batch_count": 2,
    })
    assert params["duration_seconds"] == 8
    assert params["batch_count"] == 2


def test_parse_estimate_request_invalid_duration():
    with pytest.raises(ValueError, match="between"):
        payloads.parse_estimate_request({"duration": "99"})


def test_available_providers_excludes_replicate_by_default():
    assert "replicate" not in payloads.available_providers()
    assert "fal" in payloads.available_providers()
