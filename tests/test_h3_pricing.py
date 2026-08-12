"""Unit tests for MiniMax H3 provider routing and cost estimation."""

from __future__ import annotations

import pytest
from digit_loader import load_digit_module

models = load_digit_module("h3_models")
pricing = load_digit_module("h3_pricing")


@pytest.mark.parametrize(
    ("mode", "fal_app", "muapi_slug"),
    [
        ("text_to_video", "minimax/h3/text-to-video", "minimax-h3-text-to-video"),
        ("image_to_video", "minimax/h3/image-to-video", "minimax-h3-image-to-video"),
        ("first_last_frame", "minimax/h3/image-to-video", "minimax-h3-image-to-video"),
        ("reference_to_video", "minimax/h3/reference-to-video", "minimax-h3-reference-to-video"),
    ],
)
def test_endpoint_maps(mode, fal_app, muapi_slug):
    assert models.fal_app_id(mode) == fal_app
    assert models.muapi_endpoint(mode) == muapi_slug


def test_muapi_offline_fallback():
    summary = pricing.estimate(
        "muapi",
        "text_to_video",
        "2K",
        duration_seconds=5,
        batch_count=1,
        use_live=False,
    )
    assert summary["per_clip"] == pytest.approx(0.26 * 5)
    assert "offline" in summary["note"].lower()


def test_estimate_fal_2k():
    summary = pricing.estimate(
        "fal",
        "text_to_video",
        "2K",
        duration_seconds=5,
        batch_count=2,
        use_live=False,
    )
    assert summary["per_clip"] == pytest.approx(0.26 * 5)
    assert summary["total"] == pytest.approx(0.26 * 5 * 2)


def test_estimate_muapi_live_mocked(monkeypatch):
    monkeypatch.setattr(pricing, "muapi_live_estimate", lambda *args, **kwargs: 1.25)

    summary = pricing.estimate(
        "muapi",
        "image_to_video",
        "2K",
        duration_seconds=6,
        batch_count=1,
        use_live=True,
    )
    assert summary["per_clip"] == 1.25


def test_muapi_reference_surcharge():
    extra = pricing.muapi_reference_surcharge(
        has_video_refs=True,
        ref_image_count=7,
        ref_video_seconds=2.0,
    )
    assert extra == pytest.approx(0.11 + 2.0 * 0.1825)


def test_available_providers_in_summary():
    summary = pricing.estimate("fal", "text_to_video", "2K", 5, 1, use_live=False)
    assert "fal" in summary["available_providers"]
