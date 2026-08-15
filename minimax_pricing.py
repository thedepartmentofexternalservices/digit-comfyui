"""MiniMax H3 provider routing + pricing data for DIGIT nodes.

Single source of truth for:
- Fal app IDs and MUAPI endpoint slugs per generation mode.
- Per-second cost tables for fal and muapi.
- Provider blurbs shown in tooltips and the node cost strip.

Prices verified 2026-08-15 against:
- fal:   https://fal.ai/models/minimax/h3/text-to-video pricing notes
- MUAPI: POST https://api.muapi.ai/api/v1/models/<m>/estimate-cost (no auth)
         5s/2k = $0.912, 10s/2k = $1.825 → $0.1825/s

Reprice here; nothing else in the codebase hardcodes a MiniMax dollar amount.
"""

import logging

logger = logging.getLogger("DigitMiniMaxPricing")

PROVIDERS = ["fal", "muapi"]

MODES = ("text_to_video", "image_to_video", "first_last_frame", "reference_to_video")
RESOLUTIONS = ("480P", "768P", "2K", "4K")
MUAPI_RESOLUTION = "2K"
MUAPI_RESOLUTION_PARAM = "2k"

FAL_APPS = {
    "text_to_video": "minimax/h3/text-to-video",
    "image_to_video": "minimax/h3/image-to-video",
    "first_last_frame": "minimax/h3/image-to-video",
    "reference_to_video": "minimax/h3/reference-to-video",
}

MUAPI_ENDPOINTS = {
    "text_to_video": "minimax-h3-text-to-video",
    "image_to_video": "minimax-h3-image-to-video",
    "first_last_frame": "minimax-h3-image-to-video",
    "reference_to_video": "minimax-h3-reference-to-video",
}

PROVIDER_BLURBS = {
    "fal": (
        "fal — Strict filtering: blocks real people and likenesses. "
        "Fastest queue. 480P/768P native; 2K/4K upscale a 768P base."
    ),
    "muapi": (
        "muapi — Low/reduced filtering: people OK. "
        "Hosted MiniMax H3 is 2K only."
    ),
}

PROVIDER_FILTER_LABELS = {
    "fal": "strict filter",
    "muapi": "reduced filter",
}

# fal published per-second rates (USD) for MiniMax H3.
FAL_COST_PER_SECOND = {
    "480P": 0.05,
    "768P": 0.08,
    "2K": 0.13,
    "4K": 0.16,
}

# MUAPI hosted H3 is 2K only. Verified via estimate-cost 2026-08-15.
MUAPI_COST_PER_SECOND = 0.1825

# R2V extras from MUAPI's reference-to-video page (not in the base estimate-cost).
MUAPI_EXTRA_IMAGE_COST = 0.055  # each image after the first 5
MUAPI_FREE_REFERENCE_IMAGES = 5
MUAPI_VIDEO_REF_COST_PER_SECOND = 0.1825


def fal_app_for_mode(mode):
    app_id = FAL_APPS.get(mode)
    if app_id is None:
        raise ValueError(f"Unknown MiniMax H3 mode: {mode}")
    return app_id


def muapi_endpoint_for_mode(mode):
    endpoint = MUAPI_ENDPOINTS.get(mode)
    if endpoint is None:
        raise ValueError(f"Unknown MiniMax H3 mode: {mode}")
    return endpoint


def require_muapi_resolution(resolution):
    """MUAPI hosted H3 only accepts 2k. Raises if the widget is anything else."""
    if resolution != MUAPI_RESOLUTION:
        raise ValueError(
            f"MUAPI MiniMax H3 is 2K only. Resolution '{resolution}' is fal-only. "
            "Switch provider to fal, or set resolution to 2K."
        )
    return MUAPI_RESOLUTION_PARAM


def fal_cost_per_second(resolution):
    return FAL_COST_PER_SECOND.get(resolution)


def muapi_cost_per_second(resolution):
    if resolution != MUAPI_RESOLUTION:
        return None
    return MUAPI_COST_PER_SECOND


def muapi_live_estimate(endpoint, duration_seconds, resolution, timeout=6):
    """Query MUAPI's public estimate-cost endpoint. Returns cost or None."""
    try:
        import requests
        response = requests.post(
            f"https://api.muapi.ai/api/v1/models/{endpoint}/estimate-cost",
            json={
                "duration": int(duration_seconds),
                "resolution": MUAPI_RESOLUTION_PARAM,
            },
            timeout=timeout,
        )
        if response.status_code == 200:
            cost = response.json().get("cost")
            if isinstance(cost, (int, float)):
                return float(cost)
    except Exception as error:
        logger.debug("MUAPI live estimate failed for %s: %s", endpoint, error)
    return None


def estimate(provider, mode, resolution, duration_seconds, batch_count,
             use_live=True):
    """Build the cost summary shown on the node.

    Returns a dict:
      {provider, route, filter, per_clip, total, clips, duration, note, blurb}
    Values may be None when the combo is unpriceable (e.g. muapi at 480P).
    """
    duration_seconds = max(1, int(duration_seconds))
    batch_count = max(1, int(batch_count))

    if provider == "muapi":
        try:
            endpoint = muapi_endpoint_for_mode(mode)
            require_muapi_resolution(resolution)
        except ValueError as error:
            return _summary(provider, "", "n/a", None, duration_seconds,
                            batch_count, str(error))
        per_second = muapi_cost_per_second(resolution)
        per_clip = None
        if use_live:
            live = muapi_live_estimate(endpoint, duration_seconds, resolution)
            if live is not None:
                per_clip = live
        if per_clip is None and per_second is not None:
            per_clip = per_second * duration_seconds
        note = ""
        if per_clip is None:
            note = f"{endpoint} has no published price at {resolution}."
        return _summary(
            provider, endpoint, PROVIDER_FILTER_LABELS["muapi"],
            per_clip, duration_seconds, batch_count, note,
        )

    if provider == "fal":
        try:
            route = fal_app_for_mode(mode)
        except ValueError as error:
            return _summary(provider, "", "n/a", None, duration_seconds,
                            batch_count, str(error))
        per_second = fal_cost_per_second(resolution)
        if per_second is None:
            return _summary(
                provider, route, PROVIDER_FILTER_LABELS["fal"], None,
                duration_seconds, batch_count,
                f"MiniMax H3 does not support {resolution} on fal.",
            )
        note = ""
        if resolution in ("2K", "4K"):
            note = "2K/4K upscale a 768P base result."
        return _summary(
            provider, route, PROVIDER_FILTER_LABELS["fal"],
            per_second * duration_seconds, duration_seconds, batch_count, note,
        )

    return _summary(provider, "", "n/a", None, duration_seconds, batch_count,
                    f"Unknown provider: {provider}")


def _summary(provider, route, filter_label, per_clip, duration, clips, note):
    total = per_clip * clips if per_clip is not None else None
    return {
        "provider": provider,
        "route": route,
        "filter": filter_label,
        "per_clip": round(per_clip, 4) if per_clip is not None else None,
        "total": round(total, 2) if total is not None else None,
        "clips": clips,
        "duration": duration,
        "note": note.strip(),
        "blurb": PROVIDER_BLURBS.get(provider, ""),
    }


def format_status_lines(summary):
    """Cost lines appended to the node's status output after a run."""
    lines = [f"Provider: {summary['provider']}"]
    if summary["route"]:
        lines.append(f"Route: {summary['route']} ({summary['filter']})")
    if summary["per_clip"] is not None:
        lines.append(
            f"Cost: ${summary['per_clip']:.2f}/clip, "
            f"${summary['total']:.2f} batch total "
            f"({summary['clips']} x {summary['duration']}s)"
        )
    if summary["note"]:
        lines.append(f"Pricing note: {summary['note']}")
    return lines
