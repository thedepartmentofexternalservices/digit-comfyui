"""Unit tests for Seedance provider routing and cost estimation."""

from __future__ import annotations

import pytest
from digit_loader import load_digit_module

pricing = load_digit_module("seedance_pricing")


def test_published_seedance_models_include_25():
    assert pricing.SEEDANCE_MODELS == (
        "seedance-2.0",
        "seedance-2.0-fast",
        "seedance-2.5",
    )


@pytest.mark.parametrize(
    ("endpoint", "label"),
    [
        ("seedance-2-mini-spicy-text-to-video", "reduced filter"),
        ("seedance-2-mini-text-to-video", "low filter"),
        ("seedance-2-vip-text-to-video-1080p", "low filter"),
        ("seedance-2-text-to-video", "moderate filter"),
        ("seedance-2.5-text-to-video", "standard"),
        ("seedance-2.5-spicy-text-to-video-4k", "reduced filter"),
        ("seedance-2.5-video-edit-4k", "standard"),
    ],
)
def test_muapi_filter_label(endpoint, label):
    assert pricing.muapi_filter_label(endpoint) == label


@pytest.mark.parametrize(
    ("endpoint", "short"),
    [
        ("seedance-2-mini-spicy-text-to-video", "mini-spicy"),
        ("seedance-2-vip-text-to-video-1080p", "vip"),
        ("seedance-2.5-text-to-video-4k", "2.5"),
        ("seedance-2.5-intl-text-to-video-4k", "2.5-intl"),
        ("seedance-2.5-spicy-image-to-video", "2.5-spicy"),
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


def test_fal_cost_seedance_25():
    assert pricing.fal_cost_per_second("seedance-2.5", "480p") == pytest.approx(0.2205)
    assert pricing.fal_cost_per_second("seedance-2.5", "720p") == pytest.approx(0.47)


@pytest.mark.parametrize("resolution", ["1080p", "4k"])
def test_estimate_fal_seedance_25_unsupported_resolution(resolution):
    summary = pricing.estimate(
        "fal",
        "image_to_video",
        resolution,
        duration_seconds=10,
        batch_count=1,
        fal_model="seedance-2.5",
        use_live=False,
    )
    assert summary["per_clip"] is None
    assert "does not support" in summary["note"]
    assert "seedance-2.5" in summary["note"]


def test_estimate_fal_seedance_25_total():
    summary = pricing.estimate(
        "fal",
        "text_to_video",
        "720p",
        duration_seconds=5,
        batch_count=2,
        fal_model="seedance-2.5",
        use_live=False,
    )
    assert summary["route"] == "seedance-2.5"
    assert summary["per_clip"] == pytest.approx(0.47 * 5)
    assert summary["total"] == pytest.approx(0.47 * 5 * 2)


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


def test_resolve_muapi_route_2_0_unchanged():
    endpoint, note = pricing.resolve_muapi_route(
        "text_to_video", "4k", model="seedance-2.0"
    )
    assert endpoint == "seedance-2-vip-text-to-video-4k"
    assert note == ""


@pytest.mark.parametrize(
    ("mode", "resolution", "slug"),
    [
        ("text_to_video", "480p", "seedance-2.5-text-to-video-480p"),
        ("text_to_video", "720p", "seedance-2.5-text-to-video"),
        ("text_to_video", "1080p", "seedance-2.5-text-to-video-1080p"),
        ("text_to_video", "4k", "seedance-2.5-text-to-video-4k"),
        ("image_to_video", "720p", "seedance-2.5-image-to-video"),
        ("first_last_frame", "4k", "seedance-2.5-first-last-frame-4k"),
        ("reference_to_video", "480p", "seedance-2.5-omni-reference-480p"),
        ("video_edit", "4k", "seedance-2.5-video-edit-4k"),
        ("video_extend", "720p", "seedance-2.5-video-extend"),
    ],
)
def test_resolve_muapi_route_25(mode, resolution, slug):
    endpoint, note = pricing.resolve_muapi_route(
        mode, resolution, model="seedance-2.5"
    )
    assert endpoint == slug
    assert note == ""


def test_estimate_muapi_25_4k_offline():
    summary = pricing.estimate(
        "muapi",
        "text_to_video",
        "4k",
        duration_seconds=5,
        batch_count=1,
        fal_model="seedance-2.5",
        use_live=False,
    )
    assert summary["route"] == "seedance-2.5-text-to-video-4k"
    assert summary["per_clip"] == pytest.approx(1.70 * 5)


def test_estimate_muapi_video_edit_4k():
    summary = pricing.estimate(
        "muapi",
        "video_edit",
        "4k",
        duration_seconds=8,
        batch_count=1,
        fal_model="seedance-2.5",
        use_live=False,
        source_duration_seconds=8,
    )
    assert summary["route"] == "seedance-2.5-video-edit-4k"
    assert summary["per_clip"] == pytest.approx(1.105 * 16)


def test_estimate_fal_25_unsupported_4k():
    summary = pricing.estimate(
        "fal",
        "text_to_video",
        "4k",
        duration_seconds=5,
        batch_count=1,
        fal_model="seedance-2.5",
        use_live=False,
    )
    assert summary["per_clip"] is None
    assert "2.5 tops out at 720p" in summary["note"]


@pytest.mark.parametrize(
    ("seconds", "model", "expected"),
    [
        (8.24, "seedance-2.0", 8),
        (8.6, "seedance-2.0", 9),
        (3.2, "seedance-2.0", 4),
        (17, "seedance-2.0", 15),
        (17, "seedance-2.5", 17),
        (31.4, "seedance-2.5", 30),
    ],
)
def test_clamp_duration_seconds(seconds, model, expected):
    assert pricing.clamp_duration_seconds(seconds, model) == expected


def test_2_5_slugs_are_muapi_route_choices():
    assert "seedance-2.5-text-to-video-4k" in pricing.MUAPI_ROUTE_CHOICES
    assert "seedance-2.5-video-edit-4k" in pricing.MUAPI_ROUTE_CHOICES
