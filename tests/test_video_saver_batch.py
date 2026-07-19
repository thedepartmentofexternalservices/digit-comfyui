"""Unit tests for DigitVideoSaver batch path helpers."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest
from digit_loader import load_digit_module

video_saver_node = load_digit_module("video_saver_node")
_expand_digit_batch_paths = video_saver_node._expand_digit_batch_paths
_parse_batch_timestamp = video_saver_node._parse_batch_timestamp
_resolve_source_paths = video_saver_node._resolve_source_paths


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("dance_1783980981_fea62bf2_0.mp4", "dance_1783980981_fea62bf2_*.mp4"),
        ("dance_muapi_1783980981_fea62bf2_0.mp4", "dance_muapi_1783980981_fea62bf2_*.mp4"),
        (
            "dance_replicate_1783980981_fea62bf2_0_0.mp4",
            "dance_replicate_1783980981_fea62bf2_*.mp4",
        ),
    ],
)
def test_parse_batch_timestamp(filename, expected):
    assert _parse_batch_timestamp(filename) == expected


def test_expand_digit_batch_paths_finds_siblings():
    with tempfile.TemporaryDirectory() as temp_dir:
        names = [
            "dance_100_deadbeef_0.mp4",
            "dance_100_deadbeef_1.mp4",
            "dance_100_deadbeef_2.mp4",
            "dance_100_deadbeef_3.mp4",
        ]
        paths = []
        for name in names:
            path = os.path.join(temp_dir, name)
            with open(path, "wb") as handle:
                handle.write(b"x")
            paths.append(path)

        expanded = _expand_digit_batch_paths(paths[0])
        assert len(expanded) == 4
        assert set(expanded) == set(paths)


def test_expand_muapi_legacy_batch_paths():
    with tempfile.TemporaryDirectory() as temp_dir:
        names = [
            "dance_muapi_100_deadbeef_0.mp4",
            "dance_muapi_100_deadbeef_1.mp4",
        ]
        paths = []
        for name in names:
            path = os.path.join(temp_dir, name)
            with open(path, "wb") as handle:
                handle.write(b"x")
            paths.append(path)

        expanded = _expand_digit_batch_paths(paths[0])
        assert len(expanded) == 2
        assert set(expanded) == set(paths)


def test_non_batch_path_returns_single():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "random.mp4")
        with open(path, "wb") as handle:
            handle.write(b"x")
        assert _expand_digit_batch_paths(path) == [path]


def test_resolve_source_paths_prefers_video_when_video_paths_empty():
    with tempfile.TemporaryDirectory() as temp_dir:
        names = [
            "dance_200_cafebabe_0.mp4",
            "dance_200_cafebabe_1.mp4",
        ]
        paths = []
        for name in names:
            path = os.path.join(temp_dir, name)
            with open(path, "wb") as handle:
                handle.write(b"x")
            paths.append(path)

        video = MagicMock()
        video.get_stream_source.return_value = paths[0]

        resolved = _resolve_source_paths([], video)
        assert len(resolved) == 2
        assert set(resolved) == set(paths)
