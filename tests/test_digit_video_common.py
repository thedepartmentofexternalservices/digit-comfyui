"""Unit tests for shared digit_video_common helpers."""

from __future__ import annotations

import pytest
from digit_loader import load_digit_module

common = load_digit_module("digit_video_common")


def test_detect_video_mode_text():
    assert common.detect_video_mode(
        has_refs=False, has_first_frame=False, has_last_frame=False
    ) == "text_to_video"


def test_detect_video_mode_reference():
    assert common.detect_video_mode(
        has_refs=True, has_first_frame=False, has_last_frame=False
    ) == "reference_to_video"


def test_parse_duration_in_range():
    assert common.parse_duration_seconds("10", minimum=4, maximum=15) == 10


def test_parse_duration_out_of_range():
    with pytest.raises(ValueError, match="between 4 and 15"):
        common.parse_duration_seconds("3", minimum=4, maximum=15)


def test_is_allowed_download_url_fal():
    assert common.is_allowed_download_url("https://v3b.fal.media/files/test.mp4")


def test_is_allowed_download_url_rejects_file_scheme():
    assert not common.is_allowed_download_url("file:///etc/passwd")


def test_extract_fal_video_urls():
    urls = common.extract_fal_video_urls({
        "video": {"url": "https://v3b.fal.media/files/a.mp4"},
    })
    assert urls == ["https://v3b.fal.media/files/a.mp4"]


def test_should_not_retry_content_policy():
    assert not common.should_retry_api_error(Exception("content_policy_violation"))


def test_format_api_error_truncates():
    message = common.format_api_error(Exception("x" * 500))
    assert len(message) <= 260
