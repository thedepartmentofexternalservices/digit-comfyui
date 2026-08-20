#!/usr/bin/env python3
"""Build optimized Hornitos IMAGES-Rd_2 workflow copies for Travis."""
import copy
import json
import sys
from pathlib import Path

SOURCE = Path("/tmp/IMAGES-Rd_2.json")
OUT_DIR = Path(__file__).resolve().parents[1] / "workflows" / "hornitos"

# DigitSeedreamImage widgets_values order in ComfyUI UI export:
# prompt, model, image_size, output_format, num_images, enable_safety_checker,
# seed, randomize_control, max_images, custom_width, custom_height, batch_count


def optimize_seedream_widgets(widgets: list, prompt: str | None = None) -> list:
    w = list(widgets)
    if prompt is not None:
        w[0] = prompt
    w[1] = "seedream-5.0-pro"
    w[2] = "auto_2K"
    w[4] = 1  # num_images: iteration speed (was 6)
    w[8] = 1  # max_images (lite-only; harmless on pro)
    w[9] = 2048
    w[10] = 2048
    w[11] = 1  # batch_count
    return w


def subset_workflow(source: dict, keep_node_ids: set[int], title_suffix: str) -> dict:
    wf = copy.deepcopy(source)
    wf["nodes"] = [n for n in wf["nodes"] if n["id"] in keep_node_ids]
    wf["links"] = [
        link
        for link in wf["links"]
        if link[1] in keep_node_ids and link[3] in keep_node_ids
    ]

    nodes_by_id = {n["id"]: n for n in wf["nodes"]}

    # Enable all nodes; original had Slide 03 branch muted (mode 4).
    for node in wf["nodes"]:
        node["mode"] = 0
        if node["type"] == "DigitSeedreamImage":
            node["title"] = f"Seedream FAST — {title_suffix}"
            node["properties"] = dict(node.get("properties") or {})
            node["properties"]["Node name for S&R"] = node["title"]

    extra = wf.setdefault("extra", {})
    ds = extra.setdefault("ds", {})
    ds["title"] = f"IMAGES-Rd_2 FAST — {title_suffix}"
    return wf


def build_slide03(source: dict) -> dict:
    wf = subset_workflow(source, {1, 2, 3, 4, 5, 6}, "Slide 03")
    nodes = {n["id"]: n for n in wf["nodes"]}
    nodes[1]["widgets_values"] = optimize_seedream_widgets(nodes[1]["widgets_values"])
    return wf


def build_slide02(source: dict) -> dict:
    wf = subset_workflow(source, {3, 4, 6, 8, 9, 10, 11}, "Slide 02")
    nodes = {n["id"]: n for n in wf["nodes"]}

    # Inline prompt from Text Multiline node 7; drop fragile link wiring.
    slide02_prompt = ""
    for n in source["nodes"]:
        if n["id"] == 7:
            slide02_prompt = (n.get("widgets_values") or [""])[0]
            break

    seedream = nodes[9]
    seedream["widgets_values"] = optimize_seedream_widgets(
        seedream["widgets_values"],
        prompt=slide02_prompt or seedream["widgets_values"][0],
    )
    # Remove external prompt link
    seedream["inputs"] = [
        inp for inp in seedream["inputs"] if not (inp.get("name") == "prompt" and inp.get("link") == 12)
    ]
    for inp in seedream["inputs"]:
        if inp.get("name") == "prompt":
            inp["link"] = None

    return wf


def main():
    source_path = Path(sys.argv[1] if len(sys.argv) > 1 else SOURCE)
    with source_path.open(encoding="utf-8") as handle:
        source = json.load(handle)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    slide03 = build_slide03(source)
    slide02 = build_slide02(source)

    out03 = OUT_DIR / "IMAGES-Rd_2_FAST_Slide03.json"
    out02 = OUT_DIR / "IMAGES-Rd_2_FAST_Slide02.json"

    out03.write_text(json.dumps(slide03, indent=2), encoding="utf-8")
    out02.write_text(json.dumps(slide02, indent=2), encoding="utf-8")

    print(f"Wrote {out03}")
    print(f"Wrote {out02}")


if __name__ == "__main__":
    main()
