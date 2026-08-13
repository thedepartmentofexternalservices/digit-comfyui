"""Shared PROJEKTS pipeline utilities used by Image Saver, Video Saver, and SRT Maker."""

import os
import re
import time

# Override with DIGIT_PROJEKTS_ROOTS env var (colon-separated paths).
# Falls back to common mount points, then home directory.
_DEFAULT_ROOTS = [
    os.path.join(os.path.expanduser("~"), "PROJEKTS"),
]

# Known mount points checked on each call so late-mounted volumes are picked up.
_CANDIDATE_ROOTS = [
    "/mnt/projekts/PROJEKTS",
    "/Volumes/projekts/PROJEKTS",
    "/Volumes/saint/goose/PROJEKTS",
    "/mnt/lucid/PROJEKTS",
]

PLACEHOLDER_RE = re.compile(r"^\(no .+ found\)$")
SENTINEL_NO_PROJECTS = "(no projects found)"
SENTINEL_NO_SHOTS = "(no shots found)"
SENTINEL_STORAGE_UNAVAILABLE = "(storage unavailable)"

_LISTDIR_RETRIES = 3
_LISTDIR_DELAY = 0.15


class StorageUnavailableError(OSError):
    """Raised when a PROJEKTS path cannot be listed after retries."""


def get_projekts_roots():
    """Return available PROJEKTS roots, re-scanning mount points each call."""
    env = os.environ.get("DIGIT_PROJEKTS_ROOTS", "")
    if env:
        return [p.strip() for p in env.split(":") if p.strip()]
    found = [c for c in _CANDIDATE_ROOTS if os.path.isdir(c)]
    return found if found else _DEFAULT_ROOTS


def get_available_projekts_roots():
    """Roots that currently exist on disk; falls back to configured list."""
    roots = get_projekts_roots()
    available = [r for r in roots if os.path.isdir(r)]
    return available if available else roots


# Back-compat alias; prefer get_projekts_roots() for fresh results.
PROJEKTS_ROOTS = get_projekts_roots()

PROJECT_RE = re.compile(r"^\d{5}_")
FRAME_RE = re.compile(r"\.(\d+)\.[^.]+$")


def is_placeholder(value):
    """True for UI sentinels like ``(no shots found)`` that must never be saved."""
    return bool(value) and PLACEHOLDER_RE.match(str(value)) is not None


def is_storage_unavailable(items):
    """True when a scan result is the storage-error sentinel."""
    return items == [SENTINEL_STORAGE_UNAVAILABLE]


def validate_segment(name, value):
    """Reject empty, placeholder, or path-escaping pipeline segments.

    Raises ValueError. Call before joining segments into a filesystem path.
    """
    if value is None:
        raise ValueError(f"{name} is required")
    text = str(value)
    if text.strip() == "":
        raise ValueError(f"{name} is required")
    if is_placeholder(text) or text == SENTINEL_STORAGE_UNAVAILABLE:
        raise ValueError(
            f"Invalid {name} {text!r} — pipeline list was empty or unavailable. "
            "Refresh PROJEKTS and re-select a real project/shot."
        )
    if os.path.isabs(text):
        raise ValueError(f"{name} must be a relative folder name, not an absolute path")
    if any(sep in text for sep in ("/", "\\", "\x00")):
        raise ValueError(f"{name} must not contain path separators: {text!r}")
    if text in (".", ".."):
        raise ValueError(f"{name} must not be '.' or '..'")
    return text


def is_within_roots(path, roots=None):
    """Return True if `path` resolves to a location inside one of the PROJEKTS roots.

    Both sides are passed through os.path.realpath, so symlinks and ``..``
    traversal cannot escape the configured roots. Used to constrain the
    /digit/browse listing endpoint to the pipeline filespace (security: M1).
    """
    roots = roots if roots is not None else get_projekts_roots()
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    for root in roots:
        try:
            real_root = os.path.realpath(root)
        except (OSError, ValueError):
            continue
        if real == real_root or real.startswith(real_root + os.sep):
            return True
    return False


def listdir_resilient(path, retries=_LISTDIR_RETRIES, delay=_LISTDIR_DELAY, sleeper=time.sleep):
    """List a directory, retrying transient OSError (FUSE hiccups).

    Raises StorageUnavailableError after exhausting retries. PermissionError on
    a single unreadable child is the caller's problem; this only wraps listdir
    of `path` itself.
    """
    last_error = None
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            return os.listdir(path)
        except OSError as exc:
            last_error = exc
            if attempt < attempts - 1:
                sleeper(delay * (2 ** attempt))
    raise StorageUnavailableError(f"Cannot list {path}: {last_error}") from last_error


def _is_dir_safe(path):
    """os.path.isdir that treats OSError as False so one bad child cannot abort a scan."""
    try:
        return os.path.isdir(path)
    except OSError:
        return False


def scan_projects(projekts_root):
    """Return sorted list of project folders matching 5-digit prefix pattern."""
    if not projekts_root or not _is_dir_safe(projekts_root):
        return [SENTINEL_NO_PROJECTS]
    try:
        names = listdir_resilient(projekts_root)
    except StorageUnavailableError:
        return [SENTINEL_STORAGE_UNAVAILABLE]
    folders = []
    for name in sorted(names):
        if is_placeholder(name):
            continue
        full = os.path.join(projekts_root, name)
        if _is_dir_safe(full) and PROJECT_RE.match(name):
            folders.append(name)
    return folders if folders else [SENTINEL_NO_PROJECTS]


def scan_shots(projekts_root, project):
    """Return sorted list of shot folders inside <project>/shots/."""
    if not project or is_placeholder(project) or project == SENTINEL_STORAGE_UNAVAILABLE:
        return [SENTINEL_NO_SHOTS]
    try:
        validate_segment("project", project)
    except ValueError:
        return [SENTINEL_NO_SHOTS]
    shots_dir = os.path.join(projekts_root, project, "shots")
    if not _is_dir_safe(shots_dir):
        return [SENTINEL_NO_SHOTS]
    try:
        names = listdir_resilient(shots_dir)
    except StorageUnavailableError:
        return [SENTINEL_STORAGE_UNAVAILABLE]
    folders = []
    for name in sorted(names):
        if is_placeholder(name):
            continue
        full = os.path.join(shots_dir, name)
        if _is_dir_safe(full):
            folders.append(name)
    return folders if folders else [SENTINEL_NO_SHOTS]


def resolve_pipeline_dir(projekts_root, project, shot, subfolder, task):
    """Build <root>/<project>/shots/<shot>/<subfolder>/<task> after validating segments.

    Raises ValueError on placeholder values, path separators, or a join that
    escapes `projekts_root`. Call this before os.makedirs.
    """
    if not projekts_root:
        raise ValueError("projekts_root is required")
    validate_segment("project", project)
    validate_segment("shot", shot)
    validate_segment("subfolder", subfolder)
    validate_segment("task", task)
    target_dir = os.path.join(projekts_root, project, "shots", shot, subfolder, task)
    if not is_within_roots(target_dir, roots=[projekts_root]):
        raise ValueError(
            f"Pipeline path escapes PROJEKTS root {projekts_root!r}: {target_dir}"
        )
    return target_dir


def next_frame(target_dir, prefix, shot, task, ext, start_frame, frame_pad):
    """Find highest existing frame number in target_dir and return next frame number."""
    pat = re.compile(
        rf"^{re.escape(prefix)}_{re.escape(shot)}_{re.escape(task)}\.(\d+)\.{re.escape(ext)}$"
    )
    max_frame = start_frame - 1
    if _is_dir_safe(target_dir):
        try:
            names = listdir_resilient(target_dir)
        except StorageUnavailableError:
            names = []
        for f in names:
            m = pat.match(f)
            if m:
                max_frame = max(max_frame, int(m.group(1)))
    return max_frame + 1
