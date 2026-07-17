"""Seedance provider routing + pricing data for DIGIT nodes.

Single source of truth for:
- Which MUAPI endpoint the auto-router picks per (mode, resolution).
- Per-second cost tables for fal, muapi, and replicate.
- Provider blurbs shown to artists in tooltips and the node cost strip.

Prices verified 2026-07-17 against:
- MUAPI:      POST https://api.muapi.ai/api/v1/models/<m>/estimate-cost (no auth)
- fal:        https://fal.ai/models/bytedance/seedance-2.0/* pricing notes
- Replicate:  https://replicate.com/bytedance/seedance-2.0 published rates

Reprice here; nothing else in the codebase hardcodes a dollar amount.
"""

import logging

logger = logging.getLogger("DigitSeedancePricing")

PROVIDERS = ["fal", "muapi", "replicate"]

MODES = ("text_to_video", "image_to_video", "first_last_frame", "reference_to_video")
RESOLUTIONS = ("480p", "720p", "1080p", "4k")

PROVIDER_BLURBS = {
    "fal": "fal — Strict filtering: blocks real people and likenesses. Fastest queue.",
    "muapi": "muapi — Low/reduced filtering: people OK. Cheapest at 480/720p; only low-censorship route to 1080p/4K.",
    "replicate": "replicate — ByteDance stock filter: blocks sensitive content incl. people. Backup provider.",
}

PROVIDER_FILTER_LABELS = {
    "fal": "strict filter",
    "replicate": "strict filter",
}

# ---------------------------------------------------------------------------
# MUAPI auto-routing: (mode, resolution) -> endpoint slug.
# Cheapest endpoint with the least censorship that satisfies the combo.
# mini/mini-spicy = reduced filtering, 480p/720p only.
# VIP = low censorship, the only tier with 1080p/4k.
# FLF has no mini/spicy endpoint; VIP fast is the cheapest low-censorship FLF.
# ---------------------------------------------------------------------------
MUAPI_ROUTES = {
    ("text_to_video", "480p"): "seedance-2-mini-spicy-text-to-video",
    ("text_to_video", "720p"): "seedance-2-mini-spicy-text-to-video",
    ("text_to_video", "1080p"): "seedance-2-vip-text-to-video-1080p",
    ("text_to_video", "4k"): "seedance-2-vip-text-to-video-4k",

    ("image_to_video", "480p"): "seedance-2-mini-spicy-image-to-video",
    ("image_to_video", "720p"): "seedance-2-mini-spicy-image-to-video",
    ("image_to_video", "1080p"): "seedance-2-vip-image-to-video-1080p",
    ("image_to_video", "4k"): "seedance-2-vip-image-to-video-4k",

    ("first_last_frame", "480p"): "seedance-2-vip-first-last-frame-fast",
    ("first_last_frame", "720p"): "seedance-2-vip-first-last-frame-fast",
    ("first_last_frame", "1080p"): "seedance-2-vip-first-last-frame-1080p",
    ("first_last_frame", "4k"): "seedance-2-vip-first-last-frame-4k",

    ("reference_to_video", "480p"): "seedance-2-mini-omni-reference",
    ("reference_to_video", "720p"): "seedance-2-mini-omni-reference",
    ("reference_to_video", "1080p"): "seedance-2-vip-omni-reference-1080p",
    ("reference_to_video", "4k"): "seedance-2-vip-omni-reference-4k",
}

# Filter level per MUAPI endpoint family (documented by MUAPI's tier table).
MUAPI_FILTER_LABELS = {
    "mini-spicy": "reduced filter",
    "mini": "low filter",
    "spicy": "reduced filter",
    "vip": "low filter",
    "global": "moderate filter",
}


def muapi_filter_label(endpoint):
    if "mini-spicy" in endpoint or "spicy" in endpoint:
        return MUAPI_FILTER_LABELS["mini-spicy"]
    if "mini" in endpoint:
        return MUAPI_FILTER_LABELS["mini"]
    if "vip" in endpoint:
        return MUAPI_FILTER_LABELS["vip"]
    return MUAPI_FILTER_LABELS["global"]


def muapi_short_route(endpoint):
    """Human-readable route name: 'seedance-2-mini-spicy-text-to-video' -> 'mini-spicy'."""
    for tier in ("mini-spicy", "mini", "spicy", "vip"):
        if f"-{tier}-" in endpoint:
            return tier
    return "global"


# Curated manual-override choices for the muapi_route dropdown. "auto" first.
MUAPI_ROUTE_CHOICES = ["auto"] + sorted({
    *MUAPI_ROUTES.values(),
    # Priority-queue and alternate-filter escapes:
    "seedance-2-vip-text-to-video",
    "seedance-2-vip-text-to-video-fast",
    "seedance-2-vip-image-to-video",
    "seedance-2-vip-image-to-video-fast",
    "seedance-2-vip-omni-reference",
    "seedance-2-vip-omni-reference-fast",
    "seedance-2-vip-first-last-frame",
    "seedance-2-spicy-text-to-video",
    "seedance-2-spicy-text-to-video-fast",
    "seedance-2-spicy-image-to-video",
    "seedance-2-spicy-image-to-video-fast",
    "seedance-2-mini-text-to-video",
    "seedance-2-mini-image-to-video",
    "seedance-2-text-to-video",
    "seedance-2-image-to-video",
})

# ---------------------------------------------------------------------------
# Per-second cost tables (USD per second of output video).
# ---------------------------------------------------------------------------

# MUAPI: verified via estimate-cost; linear in duration.
MUAPI_COST_PER_SECOND = {
    "seedance-2-mini-spicy-text-to-video": {"480p": 0.08, "720p": 0.15},
    "seedance-2-mini-spicy-image-to-video": {"480p": 0.08, "720p": 0.15},
    "seedance-2-mini-text-to-video": {"480p": 0.08, "720p": 0.15},
    "seedance-2-mini-image-to-video": {"480p": 0.08, "720p": 0.15},
    "seedance-2-mini-omni-reference": {"480p": 0.08, "720p": 0.15},
    # Standard tier bills a flat rate regardless of resolution (verified live).
    "seedance-2-text-to-video": {"480p": 0.25, "720p": 0.25},
    "seedance-2-image-to-video": {"480p": 0.25, "720p": 0.25},
    "seedance-2-spicy-text-to-video": {"480p": 0.30, "720p": 0.30},
    "seedance-2-spicy-image-to-video": {"480p": 0.30, "720p": 0.30},
    "seedance-2-spicy-text-to-video-fast": {"480p": 0.21, "720p": 0.21},
    "seedance-2-spicy-image-to-video-fast": {"480p": 0.21, "720p": 0.21},
    "seedance-2-vip-text-to-video": {"480p": 0.30, "720p": 0.30},
    "seedance-2-vip-image-to-video": {"480p": 0.30, "720p": 0.30},
    "seedance-2-vip-text-to-video-fast": {"480p": 0.21, "720p": 0.21},
    "seedance-2-vip-image-to-video-fast": {"480p": 0.21, "720p": 0.21},
    "seedance-2-vip-omni-reference": {"480p": 0.30, "720p": 0.30},
    "seedance-2-vip-omni-reference-fast": {"480p": 0.21, "720p": 0.21},
    "seedance-2-vip-first-last-frame": {"480p": 0.30, "720p": 0.30},
    "seedance-2-vip-first-last-frame-fast": {"480p": 0.21, "720p": 0.21},
    "seedance-2-vip-text-to-video-1080p": {"1080p": 0.675},
    "seedance-2-vip-image-to-video-1080p": {"1080p": 0.675},
    "seedance-2-vip-omni-reference-1080p": {"1080p": 0.675},
    "seedance-2-vip-first-last-frame-1080p": {"1080p": 0.675},
    "seedance-2-vip-text-to-video-4k": {"4k": 1.35},
    "seedance-2-vip-image-to-video-4k": {"4k": 1.35},
    "seedance-2-vip-omni-reference-4k": {"4k": 1.35},
    "seedance-2-vip-first-last-frame-4k": {"4k": 1.35},
}

# fal: token-priced; per-second equivalents from fal's own published rates.
# Reference mode with video inputs is billed at 0.6x (fal's stated multiplier).
FAL_COST_PER_SECOND = {
    "seedance-2.0": {"480p": 0.141, "720p": 0.3034, "1080p": 0.682, "4k": 1.5552},
    "seedance-2.0-fast": {"480p": 0.112, "720p": 0.2419},
}
FAL_VIDEO_REF_MULTIPLIER = 0.6

# Replicate: published per-second rates; video-input requests cost more.
REPLICATE_COST_PER_SECOND = {
    "no_video_input": {"480p": 0.08, "720p": 0.18, "1080p": 0.45, "4k": 1.00},
    "video_input": {"480p": 0.10, "720p": 0.22, "1080p": 0.45, "4k": 1.00},
}


def resolve_muapi_route(mode, resolution, route_override="auto"):
    """Return (endpoint, note). Raises ValueError on impossible combos."""
    if route_override and route_override != "auto":
        return route_override, ""
    key = (mode, resolution)
    endpoint = MUAPI_ROUTES.get(key)
    if endpoint is None:
        raise ValueError(
            f"No MUAPI route for mode={mode} at {resolution}. "
            "1080p/4k require VIP endpoints; check the resolution setting."
        )
    note = ""
    if mode == "first_last_frame" and resolution in ("480p", "720p"):
        note = "FLF has no mini/spicy tier; routed to VIP fast."
    return endpoint, note


def muapi_cost_per_second(endpoint, resolution):
    table = MUAPI_COST_PER_SECOND.get(endpoint, {})
    return table.get(resolution)


def fal_cost_per_second(model, resolution, has_video_refs=False):
    table = FAL_COST_PER_SECOND.get(model) or FAL_COST_PER_SECOND["seedance-2.0"]
    rate = table.get(resolution)
    if rate is None:
        return None
    if has_video_refs:
        rate *= FAL_VIDEO_REF_MULTIPLIER
    return rate


def replicate_cost_per_second(resolution, has_video_input=False):
    key = "video_input" if has_video_input else "no_video_input"
    return REPLICATE_COST_PER_SECOND[key].get(resolution)


def muapi_live_estimate(endpoint, duration_seconds, resolution, timeout=6):
    """Query MUAPI's public estimate-cost endpoint. Returns cost or None."""
    try:
        import requests
        response = requests.post(
            f"https://api.muapi.ai/api/v1/models/{endpoint}/estimate-cost",
            json={"duration": int(duration_seconds), "resolution": resolution},
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
             muapi_route="auto", fal_model="seedance-2.0",
             has_video_refs=False, use_live=True):
    """Build the cost summary shown on the node.

    Returns a dict:
      {provider, route, filter, per_second, per_clip, total,
       clips, duration, note, blurb}
    Values may be None when the combo is unpriceable (e.g. fal fast at 1080p).
    """
    duration_seconds = max(1, int(duration_seconds))
    batch_count = max(1, int(batch_count))
    note = ""
    route = ""

    if provider == "muapi":
        try:
            endpoint, note = resolve_muapi_route(mode, resolution, muapi_route)
        except ValueError as error:
            return _summary(provider, "", "n/a", None, duration_seconds,
                            batch_count, str(error))
        route = endpoint
        filter_label = muapi_filter_label(endpoint)
        per_second = muapi_cost_per_second(endpoint, resolution)
        per_clip = None
        if use_live:
            live = muapi_live_estimate(endpoint, duration_seconds, resolution)
            if live is not None:
                per_clip = live
        if per_clip is None and per_second is not None:
            per_clip = per_second * duration_seconds
        if per_clip is None:
            note = (note + " " if note else "") + \
                f"{endpoint} has no published price at {resolution}."
        return _summary(provider, route, filter_label, per_clip,
                        duration_seconds, batch_count, note)

    if provider == "fal":
        route = fal_model
        per_second = fal_cost_per_second(fal_model, resolution, has_video_refs)
        if per_second is None:
            note = f"{fal_model} does not support {resolution} (Fast tops out at 720p)."
            return _summary(provider, route, PROVIDER_FILTER_LABELS["fal"], None,
                            duration_seconds, batch_count, note)
        if has_video_refs:
            note = "Video reference discount (0.6x) applied."
        return _summary(provider, route, PROVIDER_FILTER_LABELS["fal"],
                        per_second * duration_seconds, duration_seconds,
                        batch_count, note)

    if provider == "replicate":
        route = "bytedance/seedance-2.0"
        per_second = replicate_cost_per_second(resolution, has_video_refs)
        if per_second is None:
            return _summary(provider, route, PROVIDER_FILTER_LABELS["replicate"],
                            None, duration_seconds, batch_count,
                            f"No published Replicate price at {resolution}.")
        if has_video_refs:
            note = "Video-input surcharge applied."
        return _summary(provider, route, PROVIDER_FILTER_LABELS["replicate"],
                        per_second * duration_seconds, duration_seconds,
                        batch_count, note)

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
