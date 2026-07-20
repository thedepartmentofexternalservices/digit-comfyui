"""Per-render cost scoring for DIGIT ComfyUI nodes.

Scores executed prompt-graph nodes into broker-ready cost rows.
Seedance rates live in seedance_pricing.py; this module adds
provider-aware node scoring (fal / muapi / replicate), Veo, and ElevenLabs.
"""

from __future__ import annotations

from typing import Any

try:
    from . import seedance_pricing
except ImportError:  # pragma: no cover — flat import when not packaged
    import seedance_pricing  # type: ignore

# Billable class_types. Voice selector is free; omit it.
SEEDANCE_CLASS = "DigitDanceVideo"
VEO_CLASS = "DigitVeoVideo"
ELEVENLABS_TTS_CLASSES = frozenset({
    "DigitElevenLabsTTS",
    "DigitElevenLabsDialogue",
})
ELEVENLABS_FLAT_CLASSES = {
    "DigitElevenLabsSFX": 0.12,
    "DigitElevenLabsVoiceClone": 0.0,  # one-time / account-level; don't invent spend
}
ELEVENLABS_PER_MINUTE_CLASSES = frozenset({
    "DigitElevenLabsSTS",
    "DigitElevenLabsVoiceIsolation",
})
ELEVENLABS_STT_CLASS = "DigitElevenLabsSTT"

# Published ElevenLabs API rates (USD), verified mid-2026.
ELEVENLABS_TTS_PER_1K_CHARS = 0.10
ELEVENLABS_STS_PER_MINUTE = 0.12
ELEVENLABS_STT_PER_HOUR = 0.22

VEO_COST_PER_SECOND = 0.06
VEO_DEFAULT_DURATION = 8.0
SEEDANCE_DEFAULT_DURATION = 5.0

PROVIDER_LABELS = {
    "fal": ("Sea Dance (FAL)", "FAL.ai"),
    "muapi": ("Sea Dance (MUAPI)", "MUAPI"),
    "replicate": ("Sea Dance (Replicate)", "Replicate"),
}


def _is_link(value: Any) -> bool:
    """ComfyUI API-format link: [node_id, slot]."""
    return isinstance(value, (list, tuple)) and len(value) >= 2


def _connected(inputs: dict, key: str) -> bool:
    return _is_link(inputs.get(key))


def detect_seedance_mode(inputs: dict) -> str:
    has_refs = any(
        _connected(inputs, f"reference_image{i}") for i in range(1, 10)
    ) or any(
        _connected(inputs, f"reference_video{i}") for i in range(1, 4)
    ) or any(
        _connected(inputs, f"reference_audio{i}") for i in range(1, 4)
    )
    has_first = _connected(inputs, "first_frame")
    has_last = _connected(inputs, "last_frame")
    if has_refs:
        return "reference_to_video"
    if has_first and has_last:
        return "first_last_frame"
    if has_first:
        return "image_to_video"
    return "text_to_video"


def parse_duration_seconds(inputs: dict, *, default: float = SEEDANCE_DEFAULT_DURATION) -> float:
    """Read duration from Seedance (`duration`) or Veo (`duration_seconds`)."""
    raw = inputs.get("duration_seconds", inputs.get("duration", default))
    if raw is None or raw == "" or raw == "auto":
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if value <= 0:
        return float(default)
    return value


def _provider_meta(provider: str) -> tuple[str, str]:
    return PROVIDER_LABELS.get(provider, (f"Sea Dance ({provider})", provider))


def price_seedance_node(inputs: dict) -> dict | None:
    provider = str(inputs.get("provider") or "fal").strip().lower()
    resolution = str(inputs.get("resolution") or "720p")
    model = str(inputs.get("model") or "seedance-2.0")
    muapi_route = str(inputs.get("muapi_route") or "auto")
    batch_count = max(1, int(inputs.get("batch_count") or 1))
    duration = parse_duration_seconds(inputs, default=SEEDANCE_DEFAULT_DURATION)
    mode = detect_seedance_mode(inputs)
    has_video_refs = any(_connected(inputs, f"reference_video{i}") for i in range(1, 4))

    summary = seedance_pricing.estimate(
        provider,
        mode,
        resolution,
        duration,
        batch_count,
        muapi_route=muapi_route,
        fal_model=model,
        has_video_refs=has_video_refs,
        use_live=False,
    )
    per_clip = summary.get("per_clip")
    if per_clip is None:
        return None
    # Prefer per_clip * batch over estimate()'s display total (rounded to $0.01).
    total = float(per_clip) * batch_count

    tool, provider_label = _provider_meta(provider)
    return {
        "cost": round(total, 4),
        "duration_seconds": float(duration),
        "tool": tool,
        "provider": provider_label,
        "class_type": SEEDANCE_CLASS,
        "model": summary.get("route") or model,
        "batch_count": batch_count,
    }


def price_veo_node(inputs: dict) -> dict | None:
    duration = parse_duration_seconds(inputs, default=VEO_DEFAULT_DURATION)
    cost = round(duration * VEO_COST_PER_SECOND, 4)
    return {
        "cost": cost,
        "duration_seconds": float(duration),
        "tool": "Veo Video (Google)",
        "provider": "Google",
        "class_type": VEO_CLASS,
        "model": str(inputs.get("model") or "veo"),
        "batch_count": 1,
    }


def price_elevenlabs_node(class_type: str, inputs: dict) -> dict | None:
    if class_type in ELEVENLABS_TTS_CLASSES:
        text = str(inputs.get("text") or "")
        chars = len(text)
        cost = round((chars / 1000.0) * ELEVENLABS_TTS_PER_1K_CHARS, 4)
        return {
            "cost": cost,
            "duration_seconds": 0.0,
            "tool": "ElevenLabs TTS",
            "provider": "ElevenLabs",
            "class_type": class_type,
            "model": str(inputs.get("model") or ""),
            "batch_count": 1,
        }

    if class_type in ELEVENLABS_FLAT_CLASSES:
        cost = ELEVENLABS_FLAT_CLASSES[class_type]
        if cost <= 0:
            return None
        return {
            "cost": cost,
            "duration_seconds": 0.0,
            "tool": "ElevenLabs SFX",
            "provider": "ElevenLabs",
            "class_type": class_type,
            "model": "",
            "batch_count": 1,
        }

    if class_type in ELEVENLABS_PER_MINUTE_CLASSES:
        # Prompt graph rarely carries audio length; bill a 1-minute floor.
        minutes = 1.0
        cost = round(minutes * ELEVENLABS_STS_PER_MINUTE, 4)
        tool = (
            "ElevenLabs Voice Isolation"
            if class_type == "DigitElevenLabsVoiceIsolation"
            else "ElevenLabs Speech to Speech"
        )
        return {
            "cost": cost,
            "duration_seconds": minutes * 60.0,
            "tool": tool,
            "provider": "ElevenLabs",
            "class_type": class_type,
            "model": "",
            "batch_count": 1,
        }

    if class_type == ELEVENLABS_STT_CLASS:
        # No reliable duration in graph; bill a 1-minute floor of scribe.
        hours = 1.0 / 60.0
        cost = round(hours * ELEVENLABS_STT_PER_HOUR, 4)
        return {
            "cost": cost,
            "duration_seconds": 60.0,
            "tool": "ElevenLabs STT",
            "provider": "ElevenLabs",
            "class_type": class_type,
            "model": str(inputs.get("model") or "scribe_v2"),
            "batch_count": 1,
        }

    return None


def price_node(class_type: str, inputs: dict | None = None) -> dict | None:
    """Return a broker-ready cost row for one node, or None if not billable."""
    if not class_type:
        return None
    inputs = inputs or {}

    if class_type == SEEDANCE_CLASS:
        return price_seedance_node(inputs)
    if class_type == VEO_CLASS:
        return price_veo_node(inputs)
    if class_type.startswith("DigitElevenLabs"):
        return price_elevenlabs_node(class_type, inputs)
    # Legacy class removed from nodes but may still appear in old histories.
    if class_type == "DigitReplicateSeedance":
        legacy = dict(inputs)
        legacy.setdefault("provider", "replicate")
        priced = price_seedance_node(legacy)
        if priced:
            priced["class_type"] = class_type
        return priced
    return None


def extract_prompt_dict(history: dict) -> dict:
    """Pull the node map from a ComfyUI history entry."""
    prompt = history.get("prompt")
    if isinstance(prompt, list) and len(prompt) >= 3 and isinstance(prompt[2], dict):
        return prompt[2]
    if isinstance(prompt, dict):
        return prompt
    return {}


def price_execution(history: dict) -> list[dict]:
    """Score every executed billable node in a ComfyUI history entry.

    Returns a list of dicts with node_id + cost fields for the broker.
    """
    prompt_dict = extract_prompt_dict(history)
    outputs = history.get("outputs") or {}
    priced: list[dict] = []

    for node_id in outputs:
        node_def = prompt_dict.get(node_id) or {}
        class_type = node_def.get("class_type")
        row = price_node(class_type, node_def.get("inputs") or {})
        if not row:
            continue
        priced.append({
            "node_id": str(node_id),
            **row,
        })
    return priced
