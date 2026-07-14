#!/usr/bin/env python3
"""Integration test: Jon's purple box workflow saves all batch videos to LucidLink."""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import types
import uuid
from pathlib import Path

COMFYUI = Path(os.environ.get("COMFYUI_DIR", "/opt/comfyui"))
sys.path.insert(0, str(COMFYUI))
os.chdir(COMFYUI)

PKG_DIR = COMFYUI / "custom_nodes" / "comfyui-digit"
PKG_NAME = "comfyui_digit"

WORKFLOW_SOURCE = os.environ.get(
    "WORKFLOW_SOURCE",
    "/mnt/lucid/PROJEKTS/26068_wayfair_holiday_and_evergreen/shots/"
    "26068_purple_box/falala/comp/26068_26068_purple_box_comp.1001_ui.json",
)
WORKFLOW_COPY = os.environ.get(
    "WORKFLOW_COPY",
    "/opt/comfyui/user/default/workflows/Jon_Purple_Box_BATCH_TEST.json",
)
REAL_VIDEO_SOURCE = os.environ.get(
    "REAL_VIDEO_SOURCE",
    "/mnt/lucid/PROJEKTS/26068_wayfair_holiday_and_evergreen/shots/"
    "26068_purple_box/falala/comp/26068_26068_purple_box_comp.1001.mp4",
)
TEST_SUBFOLDER = os.environ.get("TEST_SUBFOLDER", "comp_batch_test")
BATCH_COUNT = int(os.environ.get("BATCH_COUNT", "4"))
START_FRAME = int(os.environ.get("START_FRAME", "99001"))
MIN_BYTES = int(os.environ.get("MIN_BYTES", "100000"))


def load_digit_module(module_name):
    pkg = sys.modules.get(PKG_NAME)
    if pkg is None:
        pkg = types.ModuleType(PKG_NAME)
        pkg.__path__ = [str(PKG_DIR)]
        pkg.__package__ = PKG_NAME
        sys.modules[PKG_NAME] = pkg

    full_name = f"{PKG_NAME}.{module_name}"
    spec = importlib.util.spec_from_file_location(
        full_name,
        PKG_DIR / f"{module_name}.py",
        submodule_search_locations=[str(PKG_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


video_saver_node = load_digit_module("video_saver_node")
DigitVideoSaver = video_saver_node.DigitVideoSaver


def ui_to_api_prompt(workflow):
    nodes = {str(n["id"]): n for n in workflow["nodes"]}
    links = workflow.get("links", [])

    def input_map(node_id):
        inputs = {}
        for link in links:
            padded = link + [None] * (6 - len(link))
            _, from_id, from_slot, to_id, to_slot, _ = padded
            if str(to_id) != str(node_id):
                continue
            to_node = nodes[str(to_id)]
            input_name = to_node["inputs"][to_slot]["name"]
            inputs[input_name] = [str(from_id), from_slot]
        return inputs

    prompt = {}
    for node_id, node in nodes.items():
        class_type = node["type"]
        values = list(node.get("widgets_values") or [])
        inputs = input_map(node_id)

        if class_type == "LoadImage":
            inputs["image"] = values[0] if values else inputs.get("image", "")
            if len(values) > 1:
                inputs["upload"] = values[1]
        elif class_type == "DigitDanceVideo":
            keys = [
                "prompt", "model", "resolution", "aspect_ratio", "duration",
                "generate_audio", "bitrate_mode", "batch_count", "seed",
            ]
            for key, val in zip(keys, values):
                inputs[key] = val
        elif class_type == "DigitVideoSaver":
            keys = [
                "projekts_root", "project", "shot", "subfolder", "task",
                "start_frame", "frame_pad", "save_workflow",
            ]
            for key, val in zip(keys, values):
                inputs[key] = val
            inputs["subfolder"] = TEST_SUBFOLDER
            inputs["save_workflow"] = "none"
            inputs["task"] = "comp"

        prompt[node_id] = {"class_type": class_type, "inputs": inputs}
    return prompt


def make_batch_temp_videos(count, temp_dir, source_path):
    """Mirror DigitDanceVideo batch naming: shared ts + uuid, per-job index."""
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"Real video source missing: {source_path}")

    source_size = os.path.getsize(source_path)
    if source_size < MIN_BYTES:
        raise RuntimeError(
            f"Source video too small ({source_size} bytes): {source_path}"
        )

    ts = int(time.time())
    uid = uuid.uuid4().hex[:8]
    paths = []
    for i in range(count):
        path = os.path.join(temp_dir, f"dance_{ts}_{uid}_{i}.mp4")
        shutil.copy2(source_path, path)
        paths.append(path)
    return paths


def main():
    with open(WORKFLOW_SOURCE, "r", encoding="utf-8") as handle:
        workflow = json.load(handle)

    os.makedirs(os.path.dirname(WORKFLOW_COPY), exist_ok=True)
    shutil.copy2(WORKFLOW_SOURCE, WORKFLOW_COPY)

    prompt = ui_to_api_prompt(workflow)
    prompt["1"]["inputs"]["batch_count"] = BATCH_COUNT

    temp_dir = tempfile.mkdtemp(prefix="digit-batch-test-", dir=str(COMFYUI / "temp"))
    batch_paths = make_batch_temp_videos(BATCH_COUNT, temp_dir, REAL_VIDEO_SOURCE)
    source_size = os.path.getsize(REAL_VIDEO_SOURCE)

    from comfy_api.latest._input_impl.video_types import VideoFromFile

    saver = DigitVideoSaver()
    result = saver.save_video(
        projekts_root=prompt["3"]["inputs"]["projekts_root"],
        project=prompt["3"]["inputs"]["project"],
        shot=prompt["3"]["inputs"]["shot"],
        subfolder=TEST_SUBFOLDER,
        task="comp",
        start_frame=START_FRAME,
        frame_pad=5,
        save_workflow="none",
        video=VideoFromFile(batch_paths[0]),
        video_paths=None,
        prompt=prompt,
        extra_pnginfo={"workflow": workflow},
        unique_id="3",
    )

    saved = result["ui"]["filepath_text"]
    print("WORKFLOW_COPY", WORKFLOW_COPY)
    print("SOURCE_VIDEO", REAL_VIDEO_SOURCE)
    print("SOURCE_BYTES", source_size)
    print("BATCH_COUNT", BATCH_COUNT)
    print("SAVED_COUNT", len(saved))

    if len(saved) != BATCH_COUNT:
        print("FAIL expected", BATCH_COUNT, "files got", len(saved))
        return 1

    for path in saved:
        size = os.path.getsize(path)
        print("SAVED", path, size)
        if size < MIN_BYTES:
            print("FAIL file too small:", path, size)
            return 1

    target_dir = os.path.join(
        prompt["3"]["inputs"]["projekts_root"],
        prompt["3"]["inputs"]["project"],
        "shots",
        prompt["3"]["inputs"]["shot"],
        TEST_SUBFOLDER,
        "comp",
    )
    expected_names = [
        f"26068_26068_purple_box_comp.{START_FRAME + i:05d}.mp4"
        for i in range(BATCH_COUNT)
    ]
    on_disk = [name for name in expected_names if os.path.isfile(os.path.join(target_dir, name))]
    print("ON_DISK", len(on_disk), "files in", target_dir)
    if len(on_disk) != BATCH_COUNT:
        print("FAIL on-disk count", len(on_disk), "expected", expected_names)
        return 1

    print("PASS batch save integration test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
