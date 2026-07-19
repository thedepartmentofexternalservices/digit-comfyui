"""Unit tests for Seedance provider routing and cost estimation."""

from __future__ import annotations

import pytest
from digit_loader import load_digit_module

pricing = load_digit_module("seedance_pricing")


@pytest.mark.parametrize(
    ("endpoint", "label"),
    [
        ("seedance-2-mini-spicy-text-to-video", "reduced filter"),
        ("seedance-2-mini-text-to-video", "low filter"),
        ("seedance-2-vip-text-to-video-1080p", "low filter"),
        ("seedance-2-text-to-video", "moderate filter"),
    ],
)
def test_muapi_filter_label(endpoint, label):
    assert pricing.muapi_filter_label(endpoint) == label


@pytest.mark.parametrize(
    ("endpoint", "short"),
    [
        ("seedance-2-mini-spicy-text-to-video", "mini-spicy"),
        ("seedance-2-vip-text-to-video-1080p", "vip"),
    ],
)
def test_muapi_short_route(endpoint, short):
    assert pricing.muapi_short_route(endpoint) == short


def test_resolve_muapi_route_auto():
    endpoint, note = pricing.resolve_muapi_route("text_to_video", "480p")
    assert endpoint == "seedance-2-mini-spicy-text-to-video"
    assert note == ""


def test_resolve_muapi_route_override():
    endpoint, note = pricing.resolve_muapi_route(
        "text_to_video",
        "480p",
        route_override="seedance-2-vip-text-to-video",
    )
    assert endpoint == "seedance-2-vip-text-to-video"
    assert note == ""


def test_resolve_muapi_route_flf_note():
    _, note = pricing.resolve_muapi_route("first_last_frame", "720p")
    assert "FLF has no mini/spicy tier" in note


def test_resolve_muapi_route_invalid_combo():
    with pytest.raises(ValueError, match="No MUAPI route"):
        pricing.resolve_muapi_route("not_a_mode", "480p")


def test_fal_cost_with_video_ref_discount():
    base = pricing.fal_cost_per_second("seedance-2.0", "720p", has_video_refs=False)
    discounted = pricing.fal_cost_per_second("seedance-2.0", "720p", has_video_refs=True)
    assert discounted == pytest.approx(base * pricing.FAL_VIDEO_REF_MULTIPLIER)


def test_estimate_muapi_offline():
    summary = pricing.estimate(
        "muapi",
        "text_to_video",
        "480p",
        duration_seconds=5,
        batch_count=2,
        use_live=False,
    )
    assert summary["provider"] == "muapi"
    assert summary["route"] == "seedance-2-mini-spicy-text-to-video"
    assert summary["per_clip"] == pytest.approx(0.08 * 5)
    assert summary["total"] == pytest.approx(0.08 * 5 * 2)


def test_estimate_muapi_live_mocked(monkeypatch):
    monkeypatch.setattr(pricing, "muapi_live_estimate", lambda *args, **kwargs: 0.42)

    summary = pricing.estimate(
        "muapi",
        "image_to_video",
        "720p",
        duration_seconds=4,
        batch_count=1,
        use_live=True,
    )
    assert summary["per_clip"] == 0.42
    assert summary["total"] == 0.42


def test_estimate_fal_unsupported_resolution():
    summary = pricing.estimate(
        "fal",
        "text_to_video",
        "1080p",
        duration_seconds=5,
        batch_count=1,
        fal_model="seedance-2.0-fast",
        use_live=False,
    )
    assert summary["per_clip"] is None
    assert "does not support 1080p" in summary["note"]


def test_format_status_lines():
    summary = pricing.estimate(
        "replicate",
        "text_to_video",
        "480p",
        duration_seconds=3,
        batch_count=2,
        use_live=False,
    )
    lines = pricing.format_status_lines(summary)
    assert lines[0] == "Provider: replicate"
    assert any(line.startswith("Cost: $") for line in lines)
