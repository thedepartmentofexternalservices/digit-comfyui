"""Unit tests for GCP config resolution helpers."""

from __future__ import annotations

import pytest
from digit_loader import load_digit_module

gcp_config = load_digit_module("gcp_config")


def test_build_vertex_url_global_region():
    url = gcp_config.build_vertex_url("my-project", "global", "gemini-2.0-flash")
    assert url == (
        "https://aiplatform.googleapis.com/v1/projects/my-project/locations/global"
        "/publishers/google/models/gemini-2.0-flash:generateContent"
    )


def test_build_vertex_url_regional_host():
    url = gcp_config.build_vertex_url("my-project", "us-central1", "gemini-2.0-flash")
    assert "us-central1-aiplatform.googleapis.com" in url


def test_resolve_gcp_project_prefers_node_input():
    assert gcp_config.resolve_gcp_project("from-node") == "from-node"


def test_resolve_gcp_project_from_env(monkeypatch):
    monkeypatch.delenv("DIGIT_GCP_PROJECT", raising=False)
    monkeypatch.setenv("DIGIT_GCP_PROJECT", "env-project")
    assert gcp_config.resolve_gcp_project("") == "env-project"


def test_resolve_gcp_project_from_metadata(monkeypatch):
    monkeypatch.delenv("DIGIT_GCP_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.setattr(gcp_config, "get_gcp_metadata", lambda path: "meta-project")

    assert gcp_config.resolve_gcp_project("") == "meta-project"


def test_resolve_gcp_project_raises_when_missing(monkeypatch):
    monkeypatch.delenv("DIGIT_GCP_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.setattr(gcp_config, "get_gcp_metadata", lambda path: None)

    with pytest.raises(ValueError, match="GCP project ID is required"):
        gcp_config.resolve_gcp_project("")


def test_resolve_gcp_region_prefers_node_input():
    assert gcp_config.resolve_gcp_region("europe-west1") == "europe-west1"


def test_resolve_gcp_region_from_zone_metadata(monkeypatch):
    monkeypatch.delenv("DIGIT_GCP_REGION", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_REGION", raising=False)
    monkeypatch.delenv("GCP_REGION", raising=False)
    monkeypatch.setattr(
        gcp_config,
        "get_gcp_metadata",
        lambda path: "projects/1/zones/us-central1-a" if path == "instance/zone" else None,
    )

    assert gcp_config.resolve_gcp_region("") == "us-central1"


def test_resolve_gcs_uri_from_env(monkeypatch):
    monkeypatch.setenv("DIGIT_GCS_URI", "gs://bucket/path")
    assert gcp_config.resolve_gcs_uri("") == "gs://bucket/path"
