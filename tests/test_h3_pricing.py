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


def test_muapi_resolution_mapping():
    assert models.muapi_resolution("2K") == "2k"
    assert models.muapi_resolution("768P") == "768p"


def test_provider_supports_resolution():
    assert models.provider_supports_resolution("fal", "2K") is True
    assert models.provider_supports_resolution("muapi", "2K") is True
    assert models.provider_supports_resolution("muapi", "768P") is False
    assert models.provider_supports_resolution("replicate", "2K") is False


def test_estimate_fal_2k():
    summary = pricing.estimate(
        "fal",
        "text_to_video",
        "2K",
        duration_seconds=5,
        batch_count=2,
        use_live=False,
    )
    assert summary["provider"] == "fal"
    assert summary["route"] == "minimax/h3/text-to-video"
    assert summary["per_clip"] == pytest.approx(0.26 * 5)
    assert summary["total"] == pytest.approx(0.26 * 5 * 2)


def test_estimate_muapi_unsupported_resolution():
    summary = pricing.estimate(
        "muapi",
        "text_to_video",
        "768P",
        duration_seconds=5,
        batch_count=1,
        use_live=False,
    )
    assert summary["per_clip"] is None
    assert "supports 2K only" in summary["note"]


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
    assert summary["route"] == "minimax-h3-image-to-video"
    assert summary["per_clip"] == 1.25
    assert summary["total"] == 1.25


def test_estimate_replicate_unavailable():
    summary = pricing.estimate(
        "replicate",
        "text_to_video",
        "2K",
        duration_seconds=5,
        batch_count=1,
        use_live=False,
    )
    assert summary["per_clip"] is None
    assert "not published on Replicate" in summary["note"]


def test_format_status_lines():
    summary = pricing.estimate(
        "fal",
        "text_to_video",
        "2K",
        duration_seconds=4,
        batch_count=1,
        use_live=False,
    )
    lines = pricing.format_status_lines(summary)
    assert lines[0] == "Provider: fal"
    assert any(line.startswith("Cost: $") for line in lines)
