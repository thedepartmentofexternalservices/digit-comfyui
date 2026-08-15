"""Unit tests for MiniMax H3 mode detection and payload mapping."""

from __future__ import annotations

import pytest
from digit_loader import load_digit_module

node = load_digit_module("minimax_video_node")


def test_detect_mode_text_to_video():
    assert node.detect_mode(False, False, False) == "text_to_video"


def test_detect_mode_image_to_video():
    assert node.detect_mode(False, True, False) == "image_to_video"


def test_detect_mode_first_last_frame():
    assert node.detect_mode(False, True, True) == "first_last_frame"


def test_detect_mode_reference():
    assert node.detect_mode(True, False, False) == "reference_to_video"


def test_validate_prompt_required():
    with pytest.raises(ValueError, match="Prompt is required"):
        node.validate_generation_inputs("", None, None, [], [], [])


def test_validate_refs_xor_frames():
    with pytest.raises(ValueError, match="Cannot combine"):
        node.validate_generation_inputs("go", object(), None, [object()], [], [])


def test_validate_last_frame_needs_first():
    with pytest.raises(ValueError, match="last_frame requires first_frame"):
        node.validate_generation_inputs("go", None, object(), [], [], [])


def test_validate_audio_needs_visual_ref():
    with pytest.raises(ValueError, match="reference_audio requires"):
        node.validate_generation_inputs("go", None, None, [], [], [object()])


def test_aspect_t2v_adaptive_coerces():
    assert node.aspect_for_payload("text_to_video", "adaptive", "fal") == "16:9"
    assert node.aspect_for_payload("text_to_video", "21:9", "fal") == "21:9"


def test_aspect_i2v_omitted():
    assert node.aspect_for_payload("image_to_video", "16:9", "fal") is None
    assert node.aspect_for_payload("first_last_frame", "adaptive", "muapi") is None


def test_aspect_r2v_fal_keeps_adaptive():
    assert node.aspect_for_payload("reference_to_video", "adaptive", "fal") == "adaptive"


def test_aspect_r2v_muapi_coerces_adaptive():
    assert node.aspect_for_payload("reference_to_video", "adaptive", "muapi") == "16:9"


def test_build_fal_t2v_payload():
    args = node.build_fal_arguments(
        "A kitten chases a butterfly",
        "text_to_video",
        "2K",
        "adaptive",
        "8",
        True,
        True,
    )
    assert args["prompt"] == "A kitten chases a butterfly"
    assert args["resolution"] == "2K"
    assert args["duration"] == 8
    assert args["aspect_ratio"] == "16:9"
    assert args["enable_prompt_expansion"] is True
    assert "image_url" not in args


def test_build_fal_i2v_omits_aspect_and_uses_end_image():
    args = node.build_fal_arguments(
        "Camera pulls back",
        "first_last_frame",
        "768P",
        "16:9",
        5,
        False,
        True,
        image_url="https://example.com/first.png",
        end_image_url="https://example.com/last.png",
    )
    assert args["image_url"] == "https://example.com/first.png"
    assert args["end_image_url"] == "https://example.com/last.png"
    assert "aspect_ratio" not in args
    assert args["enable_prompt_expansion"] is False


def test_build_fal_r2v_lists():
    args = node.build_fal_arguments(
        "Image 1 walks through the garden",
        "reference_to_video",
        "2K",
        "adaptive",
        5,
        True,
        True,
        reference_image_urls=["https://example.com/a.png"],
        reference_video_urls=["https://example.com/m.mp4"],
        reference_audio_urls=["https://example.com/s.wav"],
    )
    assert args["aspect_ratio"] == "adaptive"
    assert args["reference_image_urls"] == ["https://example.com/a.png"]
    assert args["reference_video_urls"] == ["https://example.com/m.mp4"]
    assert args["reference_audio_urls"] == ["https://example.com/s.wav"]


def test_build_muapi_t2v_payload():
    payload = node.build_muapi_payload(
        "Aerial shot over a valley",
        "text_to_video",
        "16:9",
        5,
    )
    assert payload["resolution"] == "2k"
    assert payload["duration"] == 5
    assert payload["aspect_ratio"] == "16:9"


def test_build_muapi_i2v_uses_last_image_url():
    payload = node.build_muapi_payload(
        "Subject turns toward the light",
        "image_to_video",
        "16:9",
        6,
        image_url="https://example.com/first.png",
        last_image_url="https://example.com/last.png",
    )
    assert payload["image_url"] == "https://example.com/first.png"
    assert payload["last_image_url"] == "https://example.com/last.png"
    assert "end_image_url" not in payload
    assert "aspect_ratio" not in payload


def test_build_muapi_r2v_lists():
    payload = node.build_muapi_payload(
        "Use the references",
        "reference_to_video",
        "adaptive",
        5,
        reference_images=["https://example.com/a.png"],
        reference_videos=["https://example.com/m.mp4"],
        reference_audios=["https://example.com/s.wav"],
    )
    assert payload["aspect_ratio"] == "16:9"
    assert payload["reference_images"] == ["https://example.com/a.png"]
    assert payload["reference_videos"] == ["https://example.com/m.mp4"]
    assert payload["reference_audios"] == ["https://example.com/s.wav"]


def test_input_types_surface():
    required = node.DigitMiniMaxVideo.INPUT_TYPES()["required"]
    optional = node.DigitMiniMaxVideo.INPUT_TYPES()["optional"]
    assert required["provider"][0] == ["fal", "muapi"]
    assert required["resolution"][1]["default"] == "2K"
    assert required["duration"][1]["default"] == "5"
    assert "generate_audio" not in required
    assert "model" not in required
    assert "first_frame" in optional
    assert "reference_image9" in optional
    assert "reference_video3" in optional
    assert "reference_audio3" in optional
