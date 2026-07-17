import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parent
PKG_NAME = "comfyui_digit"


def _load_module(name):
    if "folder_paths" not in sys.modules:
        folder_paths = types.ModuleType("folder_paths")
        folder_paths.get_temp_directory = lambda: tempfile.gettempdir()
        sys.modules["folder_paths"] = folder_paths

    pkg = sys.modules.get(PKG_NAME)
    if pkg is None:
        pkg = types.ModuleType(PKG_NAME)
        pkg.__path__ = [str(REPO)]
        pkg.__package__ = PKG_NAME
        sys.modules[PKG_NAME] = pkg

    full_name = f"{PKG_NAME}.{name}"
    spec = importlib.util.spec_from_file_location(
        full_name,
        REPO / f"{name}.py",
        submodule_search_locations=[str(REPO)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


video_saver_node = _load_module("video_saver_node")
_expand_digit_batch_paths = video_saver_node._expand_digit_batch_paths
_parse_batch_timestamp = video_saver_node._parse_batch_timestamp
_resolve_source_paths = video_saver_node._resolve_source_paths


class VideoSaverBatchTests(unittest.TestCase):
    def test_parse_dance_timestamp(self):
        self.assertEqual(
            _parse_batch_timestamp("dance_1783980981_fea62bf2_0.mp4"),
            "dance_1783980981_fea62bf2_*.mp4",
        )

    def test_parse_muapi_legacy_timestamp(self):
        self.assertEqual(
            _parse_batch_timestamp("dance_muapi_1783980981_fea62bf2_0.mp4"),
            "dance_muapi_1783980981_fea62bf2_*.mp4",
        )

    def test_parse_replicate_legacy_timestamp(self):
        self.assertEqual(
            _parse_batch_timestamp("dance_replicate_1783980981_fea62bf2_0_0.mp4"),
            "dance_replicate_1783980981_fea62bf2_*.mp4",
        )

    def test_expand_digit_batch_paths_finds_siblings(self):
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
            self.assertEqual(len(expanded), 4)
            self.assertEqual(set(expanded), set(paths))

    def test_expand_muapi_legacy_batch_paths(self):
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
            self.assertEqual(len(expanded), 2)
            self.assertEqual(set(expanded), set(paths))

    def test_non_batch_path_returns_single(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "random.mp4")
            with open(path, "wb") as handle:
                handle.write(b"x")
            self.assertEqual(_expand_digit_batch_paths(path), [path])

    def test_resolve_source_paths_prefers_video_when_video_paths_empty(self):
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
            self.assertEqual(len(resolved), 2)
            self.assertEqual(set(resolved), set(paths))


if __name__ == "__main__":
    unittest.main()
