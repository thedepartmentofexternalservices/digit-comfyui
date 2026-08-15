"""Unit tests for MiniMax H3 provider routing and cost estimation."""

from __future__ import annotations

import pytest
from digit_loader import load_digit_module

pricing = load_digit_module("minimax_pricing")


def test_fal_app_for_mode():
    assert pricing.fal_app_for_mode("text_to_video") == "minimax/h3/text-to-video"
    assert pricing.fal_app_for_mode("first_last_frame") == "minimax/h3/image-to-video"
    assert pricing.fal_app_for_mode("reference_to_video") == "minimax/h3/reference-to-video"


def test_muapi_endpoint_for_mode():
    assert pricing.muapi_endpoint_for_mode("text_to_video") == "minimax-h3-text-to-video"
    assert pricing.muapi_endpoint_for_mode("first_last_frame") == "minimax-h3-image-to-video"


def test_require_muapi_resolution_2k():
    assert pricing.require_muapi_resolution("2K") == "2k"


def test_require_muapi_resolution_rejects_others():
    with pytest.raises(ValueError, match="2K only"):
        pricing.require_muapi_resolution("480P")


def test_fal_cost_per_second():
    assert pricing.fal_cost_per_second("480P") == pytest.approx(0.05)
    assert pricing.fal_cost_per_second("768P") == pytest.approx(0.08)
    assert pricing.fal_cost_per_second("2K") == pytest.approx(0.13)
    assert pricing.fal_cost_per_second("4K") == pytest.approx(0.16)
    assert pricing.fal_cost_per_second("720p") is None


def test_estimate_fal_offline():
    summary = pricing.estimate(
        "fal",
        "text_to_video",
        "2K",
        duration_seconds=5,
        batch_count=4,
        use_live=False,
    )
    assert summary["provider"] == "fal"
    assert summary["route"] == "minimax/h3/text-to-video"
    assert summary["per_clip"] == pytest.approx(0.13 * 5)
    assert summary["total"] == pytest.approx(round(0.13 * 5 * 4, 2))
    assert "upscale" in summary["note"]


def test_estimate_muapi_offline():
    summary = pricing.estimate(
        "muapi",
        "text_to_video",
        "2K",
        duration_seconds=5,
        batch_count=2,
        use_live=False,
    )
    assert summary["provider"] == "muapi"
    assert summary["route"] == "minimax-h3-text-to-video"
    assert summary["per_clip"] == pytest.approx(0.1825 * 5)
    assert summary["total"] == pytest.approx(round(0.1825 * 5 * 2, 2))


def test_estimate_muapi_non_2k():
    summary = pricing.estimate(
        "muapi",
        "text_to_video",
        "480P",
        duration_seconds=5,
        batch_count=1,
        use_live=False,
    )
    assert summary["per_clip"] is None
    assert "2K only" in summary["note"]


def test_estimate_muapi_live_mocked(monkeypatch):
    monkeypatch.setattr(pricing, "muapi_live_estimate", lambda *args, **kwargs: 0.91)

    summary = pricing.estimate(
        "muapi",
        "image_to_video",
        "2K",
        duration_seconds=5,
        batch_count=1,
        use_live=True,
    )
    assert summary["per_clip"] == 0.91
    assert summary["total"] == 0.91


def test_format_status_lines():
    summary = pricing.estimate(
        "fal",
        "text_to_video",
        "768P",
        duration_seconds=5,
        batch_count=2,
        use_live=False,
    )
    lines = pricing.format_status_lines(summary)
    assert lines[0] == "Provider: fal"
    assert any(line.startswith("Cost: $") for line in lines)
