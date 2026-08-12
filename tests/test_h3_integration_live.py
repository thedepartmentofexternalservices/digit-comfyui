"""Optional live integration tests for MiniMax H3 (requires API keys).

Run with:
  FAL_KEY=... MUAPIAPP_API_KEY=... pytest tests/test_h3_integration_live.py -v

Skipped automatically when keys are missing (CI-safe).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from h3_integration import (  # noqa: E402
    has_fal_key,
    has_muapi_key,
    run_fal_mode,
    run_muapi_mode,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def test_image():
    from h3_integration import make_test_image_png

    return make_test_image_png()


@pytest.mark.skipif(not has_fal_key(), reason="FAL_KEY not set")
def test_fal_text_to_video_live():
    result = run_fal_mode("text_to_video", duration=4)
    assert result.success, result.error
    assert result.video_url
    assert "prompt" in result.payload_keys


@pytest.mark.skipif(not has_fal_key(), reason="FAL_KEY not set")
def test_fal_image_to_video_live(test_image):
    result = run_fal_mode("image_to_video", duration=4, test_image=test_image)
    assert result.success, result.error
    assert result.video_url
    assert "image_url" in result.payload_keys


@pytest.mark.skipif(not has_fal_key(), reason="FAL_KEY not set")
def test_fal_reference_to_video_live(test_image):
    result = run_fal_mode("reference_to_video", duration=4, test_image=test_image)
    assert result.success, result.error
    assert result.video_url
    assert "reference_image_urls" in result.payload_keys


@pytest.mark.skipif(not has_muapi_key(), reason="MUAPIAPP_API_KEY not set")
def test_muapi_text_to_video_live():
    result = run_muapi_mode("text_to_video", duration=4)
    assert result.success, result.error
    assert result.video_url
    assert "prompt" in result.payload_keys


@pytest.mark.skipif(not has_muapi_key(), reason="MUAPIAPP_API_KEY not set")
def test_muapi_image_to_video_live(test_image):
    result = run_muapi_mode("image_to_video", duration=4, test_image=test_image)
    assert result.success, result.error
    assert result.video_url
    assert "image_url" in result.payload_keys


@pytest.mark.skipif(not has_muapi_key(), reason="MUAPIAPP_API_KEY not set")
def test_muapi_reference_to_video_live(test_image):
    result = run_muapi_mode("reference_to_video", duration=4, test_image=test_image)
    assert result.success, result.error
    assert result.video_url
    assert "reference_images" in result.payload_keys
