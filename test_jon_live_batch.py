#!/usr/bin/env python3
"""Live FAL batch test: queue Jon's workflow through ComfyUI and verify real MP4s on LucidLink."""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

COMFYUI = Path(os.environ.get("COMFYUI_DIR", "/opt/comfyui"))
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")

WORKFLOW_SOURCE = os.environ.get(
    "WORKFLOW_SOURCE",
    "/mnt/lucid/PROJEKTS/26068_wayfair_holiday_and_evergreen/shots/"
    "26068_purple_box/falala/comp/26068_26068_purple_box_comp.1001_ui.json",
)
TEST_SUBFOLDER = os.environ.get("TEST_SUBFOLDER", "comp_batch_test")
BATCH_COUNT = int(os.environ.get("BATCH_COUNT", "2"))
START_FRAME = int(os.environ.get("START_FRAME", "98001"))
MIN_BYTES = int(os.environ.get("MIN_BYTES", "100000"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "900"))


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
            inputs["batch_count"] = BATCH_COUNT
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
            inputs["start_frame"] = START_FRAME

        prompt[node_id] = {"class_type": class_type, "inputs": inputs}
    return prompt


def http_json(method, url, payload=None, timeout=60):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def queue_prompt(prompt):
    return http_json("POST", f"{COMFYUI_URL}/prompt", {"prompt": prompt})


def get_history(prompt_id):
    return http_json("GET", f"{COMFYUI_URL}/history/{prompt_id}")


def get_queue():
    return http_json("GET", f"{COMFYUI_URL}/queue")


def main():
    with open(WORKFLOW_SOURCE, "r", encoding="utf-8") as handle:
        workflow = json.load(handle)

    prompt = ui_to_api_prompt(workflow)
    print("QUEUE batch_count=", BATCH_COUNT, "start_frame=", START_FRAME)
    print("TARGET subfolder=", TEST_SUBFOLDER)

    try:
        queued = queue_prompt(prompt)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        print("FAIL queue HTTP", error.code, body)
        return 1

    prompt_id = queued.get("prompt_id")
    if not prompt_id:
        print("FAIL no prompt_id:", queued)
        return 1

    print("PROMPT_ID", prompt_id)
    deadline = time.time() + POLL_SECONDS

    while time.time() < deadline:
        history = get_history(prompt_id)
        if prompt_id in history:
            entry = history[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                messages = status.get("messages") or entry.get("outputs")
                print("FAIL execution error:", messages)
                return 1

            outputs = entry.get("outputs") or {}
            saver_out = outputs.get("3") or outputs.get("3")
            if saver_out:
                saved = (
                    saver_out.get("ui", {}).get("filepath_text")
                    or saver_out.get("filepath_text")
                    or []
                )
            else:
                saved = []
            if saved:
                print("SAVED_COUNT", len(saved))
                for path in saved:
                    size = os.path.getsize(path) if os.path.isfile(path) else 0
                    print("SAVED", path, size)
                    if size < MIN_BYTES:
                        print("FAIL file too small:", path, size)
                        return 1
                if len(saved) != BATCH_COUNT:
                    print("FAIL expected", BATCH_COUNT, "files got", len(saved))
                    return 1
                print("PASS live batch test")
                return 0

        queue = get_queue()
        running = queue.get("queue_running") or []
        pending = queue.get("queue_pending") or []
        still_queued = any(item[1] == prompt_id for item in running + pending)
        if not still_queued and prompt_id not in history:
            time.sleep(2)
            history = get_history(prompt_id)
            if prompt_id not in history:
                print("FAIL prompt left queue with no history entry")
                return 1
        time.sleep(5)

    print("FAIL timed out after", POLL_SECONDS, "seconds")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
