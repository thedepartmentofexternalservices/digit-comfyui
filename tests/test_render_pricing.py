"""Unit tests for per-render cost scoring (DIGIT-88)."""

from __future__ import annotations

import pytest
from digit_loader import load_digit_module

pricing = load_digit_module("render_pricing")


def test_parse_duration_from_seedance_duration():
    assert pricing.parse_duration_seconds({"duration": "12"}) == 12.0


def test_parse_duration_auto_default():
    assert pricing.parse_duration_seconds({"duration": "auto"}, default=5) == 5.0


def test_detect_mode_image_to_video():
    assert pricing.detect_seedance_mode({"first_frame": ["10", 0]}) == "image_to_video"


def test_price_seedance_fal_batch():
    row = pricing.price_node(
        "DigitDanceVideo",
        {
            "provider": "fal",
            "model": "seedance-2.0",
            "resolution": "720p",
            "duration": "5",
            "batch_count": 2,
        },
    )
    assert row is not None
    assert row["provider"] == "FAL.ai"
    assert row["cost"] == pytest.approx(3.034)


def test_price_seedance_muapi():
    row = pricing.price_node(
        "DigitDanceVideo",
        {"provider": "muapi", "resolution": "480p", "duration": "5", "batch_count": 1},
    )
    assert row["provider"] == "MUAPI"
    assert row["cost"] == pytest.approx(0.4)


def test_price_veo():
    row = pricing.price_node("DigitVeoVideo", {"duration_seconds": 8})
    assert row["cost"] == pytest.approx(0.48)
    assert row["provider"] == "Google"


def test_price_elevenlabs_tts():
    row = pricing.price_node(
        "DigitElevenLabsTTS",
        {"text": "x" * 1000, "model": "eleven_multilingual_v2"},
    )
    assert row["cost"] == pytest.approx(0.1)
    assert row["provider"] == "ElevenLabs"


def test_price_execution_from_history():
    history = {
        "prompt": [
            1,
            "prompt-1",
            {
                "7": {
                    "class_type": "DigitDanceVideo",
                    "inputs": {
                        "provider": "fal",
                        "model": "seedance-2.0",
                        "resolution": "720p",
                        "duration": "5",
                        "batch_count": 1,
                    },
                },
                "8": {"class_type": "SaveImage", "inputs": {}},
            },
        ],
        "outputs": {"7": {}, "8": {}},
    }
    rows = pricing.price_execution(history)
    assert len(rows) == 1
    assert rows[0]["node_id"] == "7"
    assert rows[0]["cost"] == pytest.approx(1.517)


def test_unknown_node_returns_none():
    assert pricing.price_node("KSampler", {}) is None
