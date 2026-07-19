import base64
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

    if "comfy_api" not in sys.modules:
        comfy_api = types.ModuleType("comfy_api")
        latest = types.ModuleType("comfy_api.latest")
        input_impl = types.ModuleType("comfy_api.latest._input_impl")
        video_types = types.ModuleType("comfy_api.latest._input_impl.video_types")
        video_types.VideoFromFile = object
        input_impl.video_types = video_types
        latest._input_impl = input_impl
        comfy_api.latest = latest
        sys.modules["comfy_api"] = comfy_api
        sys.modules["comfy_api.latest"] = latest
        sys.modules["comfy_api.latest._input_impl"] = input_impl
        sys.modules["comfy_api.latest._input_impl.video_types"] = video_types

    pkg = sys.modules.get(PKG_NAME)
    if pkg is None:
        pkg = types.ModuleType(PKG_NAME)
        pkg.__path__ = [str(REPO)]
        pkg.__package__ = PKG_NAME
        sys.modules[PKG_NAME] = pkg

    veo_stub_name = f"{PKG_NAME}.veo_video_node"
    if veo_stub_name not in sys.modules:
        veo_stub = types.ModuleType(veo_stub_name)
        veo_stub._tensor_to_png_bytes = lambda tensor: b"png"
        sys.modules[veo_stub_name] = veo_stub

    gcp_stub_name = f"{PKG_NAME}.gcp_config"
    if gcp_stub_name not in sys.modules:
        gcp_stub = types.ModuleType(gcp_stub_name)
        gcp_stub.resolve_gcp_config = lambda project, region, region_fallback="global": (project or "test", region or region_fallback)
        gcp_stub.resolve_gcs_uri = lambda uri: uri
        gcp_stub.default_project = lambda: ""
        gcp_stub.default_region = lambda: "global"
        gcp_stub.default_gcs_uri = lambda: ""
        sys.modules[gcp_stub_name] = gcp_stub

    full_name = f"{PKG_NAME}.{name}"
    spec = importlib.util.spec_from_file_location(
        full_name,
        REPO / f"{name}.py",
        submodule_search_locations=[str(REPO)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = PKG_NAME
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


omni_video_node = _load_module("omni_video_node")
DigitOmniVideo = omni_video_node.DigitOmniVideo


class _FakeTensor:
    def __init__(self, label):
        self.label = label

    def __getitem__(self, index):
        return self


class OmniVideoBuildInputTests(unittest.TestCase):
    def setUp(self):
        self.node = DigitOmniVideo()
        self._tensor_to_png_bytes = omni_video_node._tensor_to_png_bytes
        omni_video_node._tensor_to_png_bytes = lambda tensor: tensor.label.encode("ascii")

    def tearDown(self):
        omni_video_node._tensor_to_png_bytes = self._tensor_to_png_bytes

    def test_build_input_video_only(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
            handle.write(b"fake-video")
            video_path = handle.name

        try:
            video = MagicMock()
            video.get_stream_source.return_value = video_path

            payload = self.node._build_input(
                client=None,
                prompt="edit this clip",
                first_frame=None,
                references=[],
                source_video=video,
            )

            self.assertEqual(len(payload), 2)
            self.assertEqual(payload[0]["type"], "video")
            self.assertEqual(payload[0]["mime_type"], "video/mp4")
            self.assertEqual(base64.b64decode(payload[0]["data"]), b"fake-video")
            self.assertEqual(payload[1], {"type": "text", "text": "edit this clip"})
        finally:
            os.unlink(video_path)

    def test_build_input_combines_video_references_first_frame_and_text(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
            handle.write(b"source-video")
            video_path = handle.name

        try:
            video = MagicMock()
            video.get_stream_source.return_value = video_path
            references = [_FakeTensor("ref-a"), _FakeTensor("ref-b")]
            first_frame = _FakeTensor("first-frame")

            payload = self.node._build_input(
                client=None,
                prompt="blend style from refs into this edit",
                first_frame=first_frame,
                references=references,
                source_video=video,
            )

            self.assertEqual(len(payload), 5)
            self.assertEqual(payload[0]["type"], "video")
            self.assertEqual(base64.b64decode(payload[0]["data"]), b"source-video")
            self.assertEqual(payload[1]["type"], "image")
            self.assertEqual(base64.b64decode(payload[1]["data"]), b"ref-a")
            self.assertEqual(payload[2]["type"], "image")
            self.assertEqual(base64.b64decode(payload[2]["data"]), b"ref-b")
            self.assertEqual(payload[3]["type"], "image")
            self.assertEqual(base64.b64decode(payload[3]["data"]), b"first-frame")
            self.assertEqual(
                payload[4],
                {"type": "text", "text": "blend style from refs into this edit"},
            )
        finally:
            os.unlink(video_path)

    def test_build_input_text_only_returns_plain_string(self):
        payload = self.node._build_input(
            client=None,
            prompt="make a sunset video",
            first_frame=None,
            references=[],
            source_video=None,
        )
        self.assertEqual(payload, "make a sunset video")

    def test_input_types_use_batch_count_with_128_cap(self):
        batch_count = DigitOmniVideo.INPUT_TYPES()["required"]["batch_count"]
        self.assertEqual(batch_count[1]["min"], 1)
        self.assertEqual(batch_count[1]["max"], 128)
        self.assertNotIn("sample_count", DigitOmniVideo.INPUT_TYPES()["required"])


class OmniVideoBatchSaverTests(unittest.TestCase):
    def test_expected_batch_count_reads_omni_batch_count(self):
        video_saver_node = _load_module("video_saver_node")
        prompt = {
            "1": {
                "class_type": "DigitVideoSaver",
                "inputs": {"video": ["2", 0]},
            },
            "2": {
                "class_type": "DigitOmniVideo",
                "inputs": {"batch_count": 6},
            },
        }
        self.assertEqual(video_saver_node._expected_batch_count(prompt, "1"), 6)


if __name__ == "__main__":
    unittest.main()
