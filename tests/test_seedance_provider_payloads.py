from __future__ import annotations

import pytest


def test_muapi_auto_omits_duration_and_adaptive_aspect(digit_module):
    seedance = digit_module("seedance_video_node")
    payload = seedance._build_muapi_payload(
        prompt="  camera pushes in  ",
        duration="auto",
        resolution="720p",
        aspect_ratio="auto",
        endpoint="seedance-2-mini-spicy-text-to-video",
        mode="text_to_video",
        generate_audio=True,
        bitrate_mode="high",
    )
    assert payload == {
        "prompt": "camera pushes in",
        "resolution": "720p",
        "generate_audio": True,
        "high_bitrate": True,
    }


def test_muapi_fixed_route_payload_is_exact(digit_module):
    seedance = digit_module("seedance_video_node")
    payload = seedance._build_muapi_payload(
        prompt="test",
        duration="7",
        resolution="1080p",
        aspect_ratio="adaptive",
        endpoint="seedance-2-vip-first-last-frame-1080p",
        mode="first_last_frame",
        generate_audio=True,
        bitrate_mode="high",
    )
    assert payload == {"prompt": "test", "duration": 7}
    assert -1 not in payload.values()


def test_fal_payload_is_exact(digit_module):
    seedance = digit_module("seedance_video_node")
    assert seedance._build_fal_payload(
        prompt="  orbit left ",
        resolution="720p",
        aspect_ratio="16:9",
        duration="5",
        generate_audio=False,
        bitrate_mode="standard",
    ) == {
        "prompt": "orbit left",
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "duration": "5",
        "generate_audio": False,
        "bitrate_mode": "standard",
    }


def test_replicate_payload_is_exact(digit_module):
    seedance = digit_module("seedance_video_node")
    assert seedance._build_replicate_payload(
        prompt="  dolly forward ",
        resolution="480p",
        aspect_ratio="auto",
        duration="auto",
        generate_audio=True,
        negative_prompt=" blur ",
    ) == {
        "prompt": "dolly forward",
        "resolution": "480p",
        "aspect_ratio": "adaptive",
        "duration": -1,
        "audio": True,
        "negative_prompt": "blur",
    }


def test_muapi_route_mode_is_validated_before_upload(digit_module):
    seedance = digit_module("seedance_video_node")
    with pytest.raises(ValueError, match="does not support mode"):
        seedance._validate_muapi_route_mode(
            "seedance-2-vip-text-to-video", "reference_to_video"
        )
