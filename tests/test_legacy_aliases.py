"""Tests for backward-compatible node class aliases (DIGIT-168)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = REPO_ROOT / "__init__.py"


def _parse_init_mappings():
    """Extract NODE_CLASS_MAPPINGS keys without importing ComfyUI deps."""
    source = INIT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    class_keys: set[str] = set()
    display_keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "NODE_CLASS_MAPPINGS" and isinstance(node.value, ast.Dict):
                class_keys = {
                    key.value for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
            if target.id == "NODE_DISPLAY_NAME_MAPPINGS" and isinstance(node.value, ast.Dict):
                display_keys = {
                    key.value for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
    display = {}
    for match in re.finditer(
        r'"([^"]+)":\s*"([^"]+)"',
        source.split("NODE_DISPLAY_NAME_MAPPINGS", 1)[1],
    ):
        display[match.group(1)] = match.group(2)
    return class_keys, display, display_keys


def test_digit_replicate_seedance_registered():
    class_keys, display, display_keys = _parse_init_mappings()
    assert "DigitReplicateSeedance" in class_keys
    assert "DigitReplicateSeedance" in display_keys
    assert "[deprecated]" in display["DigitReplicateSeedance"]


def test_legacy_alias_map_matches_registration():
    from legacy_aliases import LEGACY_CLASS_ALIASES

    class_keys, _, _ = _parse_init_mappings()
    for alias, canonical in LEGACY_CLASS_ALIASES.items():
        assert alias in class_keys, f"Missing legacy alias: {alias}"
        assert canonical in class_keys, f"Missing canonical node: {canonical}"


@pytest.mark.parametrize(
    "alias,widget_key,expected_value",
    [
        ("DigitReplicateSeedance", "duration_seconds", 5),
        ("DigitReplicateSeedance", "resolution", "720p"),
    ],
)
def test_legacy_alias_preserves_widget_surface(alias, widget_key, expected_value):
    from digit_loader import load_digit_module

    seedance = load_digit_module("seedance_video_node")
    cls = getattr(seedance, alias)
    required = cls.INPUT_TYPES()["required"]
    assert widget_key in required
    default = required[widget_key][1].get("default")
    assert default == expected_value


def test_digit_replicate_seedance_forwards_to_replicate(mocker):
    from digit_loader import load_digit_module

    seedance = load_digit_module("seedance_video_node")
    cls = seedance.DigitReplicateSeedance
    spy = mocker.patch.object(
        seedance.DigitDanceVideo,
        "generate",
        return_value=({"ui": {}},),
    )
    cls().generate(
        prompt="test",
        resolution="720p",
        aspect_ratio="16:9",
        duration_seconds=5,
        generate_audio=True,
        seed=42,
    )
    spy.assert_called_once()
    assert spy.call_args.kwargs["provider"] == "replicate"
    assert spy.call_args.kwargs["model"] == "seedance-2.0"
