"""Shared video-generation helpers for DIGIT multi-provider nodes."""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from typing import Iterable
from urllib.parse import urlparse

logger = logging.getLogger("DigitVideoCommon")

DEFAULT_MIN_DURATION = 4
DEFAULT_MAX_DURATION = 15

ALLOWED_DOWNLOAD_HOST_SUFFIXES = (
    ".fal.media",
    ".fal.ai",
    ".fal.run",
    ".replicate.delivery",
    ".replicate.com",
    ".muapi.ai",
    ".amazonaws.com",
    ".cloudfront.net",
    ".googleusercontent.com",
)

DOWNLOAD_TIMEOUT_SECONDS = 120
DOWNLOAD_MAX_RETRIES = 3


def detect_video_mode(
    *,
    has_refs: bool,
    has_first_frame: bool,
    has_last_frame: bool,
) -> str:
    if has_refs:
        return "reference_to_video"
    if has_first_frame and has_last_frame:
        return "first_last_frame"
    if has_first_frame:
        return "image_to_video"
    return "text_to_video"


def validate_mode_input_conflicts(
    *,
    has_refs: bool,
    has_first_frame: bool,
    has_last_frame: bool,
    ref_audios_without_visual: bool,
    reference_count: int,
    max_reference_files: int,
    model_label: str = "Video model",
) -> None:
    if has_refs and (has_first_frame or has_last_frame):
        raise ValueError(
            "Cannot combine first_frame/last_frame with reference inputs. "
            "Use image-to-video mode OR reference-to-video mode, not both."
        )
    if ref_audios_without_visual:
        raise ValueError(
            "reference_audio requires at least one reference_image or reference_video."
        )
    if reference_count > max_reference_files:
        raise ValueError(
            f"{model_label} accepts at most {max_reference_files} reference files total; "
            f"{reference_count} are connected."
        )
    if has_last_frame and not has_first_frame:
        raise ValueError("last_frame requires first_frame to be connected.")


def parse_duration_seconds(
    raw_duration,
    *,
    minimum: int = DEFAULT_MIN_DURATION,
    maximum: int = DEFAULT_MAX_DURATION,
    default: int = 5,
) -> int:
    try:
        value = int(raw_duration)
    except (TypeError, ValueError):
        value = default
    if value < minimum or value > maximum:
        raise ValueError(
            f"Duration must be between {minimum} and {maximum} seconds; got {raw_duration!r}."
        )
    return value


def is_content_policy_error(error) -> bool:
    text = str(error).lower()
    return (
        "content_policy_violation" in text
        or "content policy" in text
        or "likenesses of real people" in text
    )


def should_retry_api_error(error) -> bool:
    if is_content_policy_error(error):
        return False
    text = str(error).lower()
    status_code = getattr(error, "status_code", None)
    if status_code in {429, 500, 502, 503, 504}:
        return True
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return False
    non_retryable = ("400", "401", "403", "404", "422", "invalid", "validation")
    if any(marker in text for marker in non_retryable):
        return False
    retryable = (
        "429", "500", "502", "503", "504", "rate", "timeout",
        "connection", "temporarily", "unavailable", "internal server",
        "server error", "gateway",
    )
    return any(marker in text for marker in retryable)


def format_api_error(error, *, provider: str = "API") -> str:
    if is_content_policy_error(error):
        return f"Blocked by {provider} content policy."
    text = str(error).strip()
    if len(text) > 240:
        text = text[:240] + "…"
    return text or f"{provider} request failed."


def _host_allowed(hostname: str, allowed_suffixes: Iterable[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    if not host:
        return False
    for suffix in allowed_suffixes:
        suffix = suffix.lower()
        if host == suffix.lstrip(".") or host.endswith(suffix):
            return True
    return False


def _resolve_host_ips(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return []
    return [info[4][0] for info in infos]


def _ip_is_public(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


def is_allowed_download_url(
    url: str,
    allowed_suffixes: Iterable[str] = ALLOWED_DOWNLOAD_HOST_SUFFIXES,
) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = parsed.hostname
    if not hostname or not _host_allowed(hostname, allowed_suffixes):
        return False
    for ip_text in _resolve_host_ips(hostname):
        if not _ip_is_public(ip_text):
            logger.warning("Blocked download URL resolving to non-public IP: %s", hostname)
            return False
    return True


def secure_download_video(
    url: str,
    destination_path: str,
    *,
    allowed_suffixes: Iterable[str] = ALLOWED_DOWNLOAD_HOST_SUFFIXES,
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
    max_retries: int = DOWNLOAD_MAX_RETRIES,
    log_prefix: str = "[DIGIT Video]",
) -> None:
    if not is_allowed_download_url(url, allowed_suffixes):
        raise ValueError(f"Refusing to download from untrusted URL host: {url}")

    import requests

    last_error = None
    for attempt in range(max_retries):
        try:
            with requests.get(url, stream=True, timeout=timeout_seconds, allow_redirects=True) as response:
                final_url = response.url
                if not is_allowed_download_url(final_url, allowed_suffixes):
                    raise ValueError(
                        f"Download redirect landed on untrusted host: {final_url}"
                    )
                response.raise_for_status()
                content_type = (response.headers.get("Content-Type") or "").lower()
                if content_type and "video" not in content_type and "octet-stream" not in content_type:
                    logger.warning(
                        "%s Unexpected content-type %s for %s",
                        log_prefix,
                        content_type,
                        final_url,
                    )
                with open(destination_path, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            handle.write(chunk)
            return
        except Exception as error:
            last_error = error
            if attempt == max_retries - 1:
                break
            delay = 2 ** attempt
            logger.warning(
                "%s Download attempt %d failed; retrying in %ds: %s",
                log_prefix,
                attempt + 1,
                delay,
                error,
            )
            time.sleep(delay)

    raise RuntimeError(f"Download failed after {max_retries} attempts: {last_error}") from last_error


def extract_fal_video_urls(result: dict) -> list[str]:
    video_items = []
    if isinstance(result, dict):
        if isinstance(result.get("videos"), list):
            video_items = result["videos"]
        elif "video" in result:
            video_items = [result["video"]]

    urls = []
    for item in video_items:
        if isinstance(item, dict):
            url = item.get("url")
        else:
            url = item
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            urls.append(url)
    return urls
