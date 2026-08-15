"""Unit tests for h3_integration helpers (no API keys required)."""

from __future__ import annotations

from digit_loader import load_digit_module

integration = load_digit_module("h3_integration")


def test_make_test_image_png():
    data = integration.make_test_image_png()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_format_results_report():
    report = integration.format_results_report([
        integration.LiveTestResult(
            "fal", "text_to_video", "minimax/h3/text-to-video", True, 12.3
        ),
        integration.LiveTestResult(
            "muapi", "image_to_video", "minimax-h3-image-to-video", False, 1.0, error="nope"
        ),
    ])
    assert "1/2 passed" in report
    assert "PASS" in report
    assert "FAIL" in report
