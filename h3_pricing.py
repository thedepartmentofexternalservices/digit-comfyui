"""MiniMax H3 provider pricing data for DIGIT nodes.

Single source of truth for per-second cost tables and live MUAPI estimates.
Reprice here; nothing else in the codebase hardcodes a dollar amount.
"""

import logging

try:
    from . import h3_models
except ImportError:  # standalone import (tests, linting)
    import h3_models

logger = logging.getLogger("DigitH3Pricing")

PROVIDERS = h3_models.PROVIDERS

PROVIDER_BLURBS = {
    "fal": "fal — Hosted MiniMax H3 with safety checker and prompt expansion.",
    "muapi": "muapi — MiniMax H3 via unified async API; 2K output today.",
    "replicate": "replicate — MiniMax H3 not published on Replicate yet.",
}

PROVIDER_FILTER_LABELS = {
    "fal": "safety checker",
    "muapi": "standard",
    "replicate": "n/a",
}

# fal published rate: $0.26 per second of 2K output (2026-07-31 launch notes).
# 768P and 4K use placeholder rates until verified on fal model pages.
FAL_COST_PER_SECOND = {
    "768P": 0.13,
    "2K": 0.26,
    "4K": 0.52,
}


def resolve_muapi_endpoint(mode):
    return h3_models.muapi_endpoint(mode)


def fal_cost_per_second(resolution):
    return FAL_COST_PER_SECOND.get(resolution)


def muapi_live_estimate(endpoint, duration_seconds, resolution, timeout=6):
    """Query MUAPI's public estimate-cost endpoint. Returns cost or None."""
    try:
        import requests

        response = requests.post(
            f"https://api.muapi.ai/api/v1/models/{endpoint}/estimate-cost",
            json={
                "duration": int(duration_seconds),
                "resolution": h3_models.muapi_resolution(resolution),
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


def estimate(
    provider,
    mode,
    resolution,
    duration_seconds,
    batch_count,
    has_video_refs=False,
    ref_image_count=0,
    use_live=True,
):
    """Build the cost summary shown on the node."""
    duration_seconds = max(1, int(duration_seconds))
    batch_count = max(1, int(batch_count))
    note = ""
    route = ""

    if provider == "muapi":
        if resolution not in h3_models.MUAPI_RESOLUTIONS:
            return _summary(
                provider,
                "",
                PROVIDER_FILTER_LABELS["muapi"],
                None,
                duration_seconds,
                batch_count,
                f"MUAPI H3 currently supports {', '.join(sorted(h3_models.MUAPI_RESOLUTIONS))} only.",
            )
        try:
            endpoint = resolve_muapi_endpoint(mode)
        except ValueError as error:
            return _summary(
                provider,
                "",
                "n/a",
                None,
                duration_seconds,
                batch_count,
                str(error),
            )
        route = endpoint
        per_clip = None
        if use_live:
            live = muapi_live_estimate(endpoint, duration_seconds, resolution)
            if live is not None:
                per_clip = live
        if per_clip is None:
            note = f"{endpoint} has no published offline price; live estimate unavailable."
        if has_video_refs:
            note = (note + " " if note else "") + "Reference video surcharges may apply on MUAPI."
        if ref_image_count > 5:
            note = (note + " " if note else "") + "Extra reference images may incur MUAPI fees."
        return _summary(
            provider,
            route,
            PROVIDER_FILTER_LABELS["muapi"],
            per_clip,
            duration_seconds,
            batch_count,
            note,
        )

    if provider == "fal":
        route = h3_models.FAL_APPS.get(mode, "minimax/h3")
        per_second = fal_cost_per_second(resolution)
        if per_second is None:
            return _summary(
                provider,
                route,
                PROVIDER_FILTER_LABELS["fal"],
                None,
                duration_seconds,
                batch_count,
                f"No published fal price at {resolution}.",
            )
        return _summary(
            provider,
            route,
            PROVIDER_FILTER_LABELS["fal"],
            per_second * duration_seconds,
            duration_seconds,
            batch_count,
            note,
        )

    if provider == "replicate":
        return _summary(
            provider,
            "minimax/h3",
            PROVIDER_FILTER_LABELS["replicate"],
            None,
            duration_seconds,
            batch_count,
            "MiniMax H3 is not published on Replicate yet.",
        )

    return _summary(
        provider,
        "",
        "n/a",
        None,
        duration_seconds,
        batch_count,
        f"Unknown provider: {provider}",
    )


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
