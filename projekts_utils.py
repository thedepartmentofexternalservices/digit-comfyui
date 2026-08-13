"""Shared PROJEKTS pipeline utilities used by Image Saver, Video Saver, and SRT Maker."""

import logging
import os
import re
import time

logger = logging.getLogger("DigitProjekts")

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


_last_scan_error = None


def get_last_scan_error():
    """Most recent storage/scan failure, or None."""
    return _last_scan_error


def record_scan_error(message):
    """Remember a scan failure for /digit/health and journalctl."""
    global _last_scan_error
    _last_scan_error = {"message": str(message), "ts": time.time()}
    logger.warning("projekts_scan_error %s", message)


def _reject(message):
    logger.warning("projekts_path_rejected %s", message)
    raise ValueError(message)


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


def combo_choices(items):
    """Dropdown options for INPUT_TYPES. Never bake UI sentinels into /object_info."""
    if not items or is_storage_unavailable(items):
        return [""]
    cleaned = [name for name in items if not is_placeholder(name)]
    return cleaned if cleaned else [""]


def validate_segment(name, value):
    """Reject empty, placeholder, or path-escaping pipeline segments.

    Raises ValueError. Call before joining segments into a filesystem path.
    """
    if value is None:
        _reject(f"{name} is required")
    text = str(value).strip()
    if text == "":
        _reject(f"{name} is required")
    if is_placeholder(text) or text == SENTINEL_STORAGE_UNAVAILABLE:
        _reject(
            f"Invalid {name} {text!r} — pipeline list was empty or unavailable. "
            "Refresh PROJEKTS and re-select a real project/shot."
        )
    if os.path.isabs(text):
        _reject(f"{name} must be a relative folder name, not an absolute path")
    if any(sep in text for sep in ("/", "\\", "\x00")):
        _reject(f"{name} must not contain path separators: {text!r}")
    if text in (".", ".."):
        _reject(f"{name} must not be '.' or '..'")
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
            logger.warning(
                "projekts_listdir_retry path=%s attempt=%s/%s error=%s",
                path, attempt + 1, attempts, exc,
            )
            if attempt < attempts - 1:
                sleeper(delay * (2 ** attempt))
    message = f"Cannot list {path}: {last_error}"
    record_scan_error(message)
    raise StorageUnavailableError(message) from last_error


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


def scan_child_folders(projekts_root, project, shot, subfolder=None):
    """List immediate child folders under a shot, or under shot/subfolder.

    Used by /digit/subfolders and /digit/tasks. Returns ``[""]`` when the
    parent path is missing or a segment is invalid; storage errors use the
    storage sentinel.
    """
    try:
        validate_segment("project", project)
        validate_segment("shot", shot)
        if subfolder is not None:
            validate_segment("subfolder", subfolder)
    except ValueError:
        return [""]
    if subfolder is None:
        path = os.path.join(projekts_root, project, "shots", shot)
    else:
        path = os.path.join(projekts_root, project, "shots", shot, subfolder)
    if not _is_dir_safe(path):
        return [""]
    try:
        names = listdir_resilient(path)
    except StorageUnavailableError:
        return [SENTINEL_STORAGE_UNAVAILABLE]
    folders = []
    for name in sorted(names):
        if is_placeholder(name):
            continue
        if _is_dir_safe(os.path.join(path, name)):
            folders.append(name)
    return folders if folders else [""]


_MAX_FOLDER_DEPTH = 8


def parse_folder(folder):
    """Validated relative folder segments under a shot.

    Empty defaults to ``comfy/comp``. Rejects ``..``, absolute paths, and
    more than ``_MAX_FOLDER_DEPTH`` segments.
    """
    text = str(folder or "").strip().replace("\\", "/").strip("/")
    if text == "":
        return ["comfy", "comp"]
    parts = [part for part in text.split("/") if part]
    if not parts:
        return ["comfy", "comp"]
    for part in parts:
        validate_segment("folder", part)
    if len(parts) > _MAX_FOLDER_DEPTH:
        _reject(f"folder can be at most {_MAX_FOLDER_DEPTH} levels")
    return parts


def effective_folder(folder="", subfolder=None, task=None):
    """Prefer the save-dialog folder string; fall back to legacy subfolder/task."""
    text = str(folder or "").strip()
    if text:
        return text
    if subfolder and task:
        return f"{subfolder}/{task}"
    if subfolder:
        return str(subfolder)
    return "comfy/comp"


def resolve_folder_dir(projekts_root, project, shot, folder):
    """Build <root>/<project>/shots/<shot>/<folder> after validating segments."""
    if not projekts_root:
        _reject("projekts_root is required")
    validate_segment("project", project)
    validate_segment("shot", shot)
    parts = parse_folder(folder)
    target_dir = os.path.join(projekts_root, project, "shots", shot, *parts)
    if not is_within_roots(target_dir, roots=[projekts_root]):
        _reject(
            f"Pipeline path escapes PROJEKTS root {projekts_root!r}: {target_dir}"
        )
    return target_dir


def scan_shot_folders(projekts_root, project, shot):
    """Existing folders under a shot, as relative paths at any depth."""
    try:
        validate_segment("project", project)
        validate_segment("shot", shot)
    except ValueError:
        return [""]
    shot_dir = os.path.join(projekts_root, project, "shots", shot)
    if not _is_dir_safe(shot_dir):
        return [""]
    found = []

    def walk(rel, depth):
        current = os.path.join(shot_dir, rel) if rel else shot_dir
        try:
            names = listdir_resilient(current)
        except StorageUnavailableError:
            raise
        for name in sorted(names):
            if is_placeholder(name):
                continue
            child = os.path.join(current, name)
            if not _is_dir_safe(child):
                continue
            path = f"{rel}/{name}" if rel else name
            found.append(path)
            if depth < _MAX_FOLDER_DEPTH:
                walk(path, depth + 1)

    try:
        walk("", 1)
    except StorageUnavailableError:
        return [SENTINEL_STORAGE_UNAVAILABLE]
    return found if found else [""]


def create_folder_dir(projekts_root, project, shot, folder):
    """Create a folder path under an existing shot. Project and shot must exist."""
    if not projekts_root:
        _reject("projekts_root is required")
    project = validate_segment("project", project)
    shot = validate_segment("shot", shot)
    shot_dir = os.path.join(projekts_root, project, "shots", shot)
    if not is_within_roots(shot_dir, roots=[projekts_root]):
        _reject(f"Shot path escapes PROJEKTS root {projekts_root!r}")
    if not _is_dir_safe(shot_dir):
        raise FileNotFoundError(f"shot not found: {shot}")
    target = resolve_folder_dir(projekts_root, project, shot, folder)
    os.makedirs(target, exist_ok=True)
    logger.info("projekts_folder_created path=%s", target)
    return target


def resolve_pipeline_dir(projekts_root, project, shot, subfolder, task):
    """Build <root>/<project>/shots/<shot>/<subfolder>/<task> after validating segments.

    Raises ValueError on placeholder values, path separators, or a join that
    escapes `projekts_root`. Call this before os.makedirs.
    """
    if not projekts_root:
        _reject("projekts_root is required")
    validate_segment("project", project)
    validate_segment("shot", shot)
    validate_segment("subfolder", subfolder)
    validate_segment("task", task)
    target_dir = os.path.join(projekts_root, project, "shots", shot, subfolder, task)
    if not is_within_roots(target_dir, roots=[projekts_root]):
        _reject(
            f"Pipeline path escapes PROJEKTS root {projekts_root!r}: {target_dir}"
        )
    return target_dir


def create_shot_dir(projekts_root, project, shot, subfolder=None, task=None):
    """Create <root>/<project>/shots/<shot> and optional subfolder/task.

    Project must already exist. Shot name goes through validate_segment so
    placeholders and path separators cannot become folders.
    Returns the created directory path.
    """
    if not projekts_root:
        _reject("projekts_root is required")
    project = validate_segment("project", project)
    shot = validate_segment("shot", shot)
    project_dir = os.path.join(projekts_root, project)
    if not is_within_roots(project_dir, roots=[projekts_root]):
        _reject(f"Project path escapes PROJEKTS root {projekts_root!r}")
    if not _is_dir_safe(project_dir):
        raise FileNotFoundError(f"project not found: {project}")
    if subfolder and task:
        target = resolve_pipeline_dir(projekts_root, project, shot, subfolder, task)
        os.makedirs(target, exist_ok=True)
        logger.info("projekts_shot_created path=%s", target)
        return target
    shot_dir = os.path.join(projekts_root, project, "shots", shot)
    if not is_within_roots(shot_dir, roots=[projekts_root]):
        _reject(f"Shot path escapes PROJEKTS root {projekts_root!r}")
    os.makedirs(shot_dir, exist_ok=True)
    logger.info("projekts_shot_created path=%s", shot_dir)
    return shot_dir


def probe_root(root):
    """Time a listdir of one PROJEKTS root for /digit/health."""
    started = time.perf_counter()
    try:
        names = listdir_resilient(root)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        projects = [name for name in names if PROJECT_RE.match(name) and not is_placeholder(name)]
        return {
            "path": root,
            "reachable": True,
            "listdir_ms": elapsed_ms,
            "project_count": len(projects),
            "error": None,
        }
    except OSError as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        record_scan_error(f"{root}: {exc}")
        return {
            "path": root,
            "reachable": False,
            "listdir_ms": elapsed_ms,
            "project_count": 0,
            "error": str(exc),
        }


def health_payload(pack_version=None, comfyui_version=None):
    """Snapshot of PROJEKTS reachability for /digit/health."""
    roots = get_projekts_roots()
    probes = [probe_root(root) for root in roots]
    return {
        "ok": any(item["reachable"] for item in probes) if probes else False,
        "pack_version": pack_version,
        "comfyui_version": comfyui_version,
        "roots": probes,
        "last_scan_error": get_last_scan_error(),
    }


_STRIP_EXTS = (".png", ".jpg", ".jpeg", ".exr", ".mp4", ".tif", ".tiff", ".webp", ".mov")


def sanitize_filename_stem(value):
    """Validate a typed file stem and strip a trailing extension if the artist included one."""
    text = validate_segment("filename", value)
    lower = text.lower()
    for suffix in _STRIP_EXTS:
        if lower.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return validate_segment("filename", text)


def folder_task_name(folder):
    """Last path segment of a save-dialog folder, used in PREFIX_SHOT_TASK."""
    return parse_folder(folder)[-1]


def file_stem(project, shot, task, filename=""):
    """Typed filename wins; empty falls back to PREFIX_SHOT_TASK."""
    text = str(filename or "").strip()
    if text:
        return sanitize_filename_stem(text)
    task_seg = str(task or "comp").replace("\\", "/").strip("/").split("/")[-1] or "comp"
    return f"{str(project)[:5]}_{shot}_{task_seg}"


def next_frame(target_dir, stem, ext, start_frame, frame_pad):
    """Find highest existing frame number in target_dir and return next frame number."""
    pat = re.compile(
        rf"^{re.escape(stem)}\.(\d+)\.{re.escape(ext)}$"
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


def next_output_path(projekts_root, project, shot, folder, filename="",
                     ext="png", start_frame=1001, frame_pad=4):
    """Return the next pipeline output path without creating files or folders."""
    extension = validate_segment("format", str(ext).lower().lstrip("."))
    try:
        start = int(start_frame)
        padding = int(frame_pad)
    except (TypeError, ValueError):
        _reject("start_frame and frame_pad must be integers")
    if start < 0:
        _reject("start_frame must be zero or greater")
    if padding < 1 or padding > 8:
        _reject("frame_pad must be between 1 and 8")

    folder_path = effective_folder(folder)
    target_dir = resolve_folder_dir(projekts_root, project, shot, folder_path)
    stem = file_stem(project, shot, folder_task_name(folder_path), filename)
    frame = next_frame(target_dir, stem, extension, start, padding)
    disk_name = f"{stem}.{frame:0{padding}d}.{extension}"
    return {
        "path": os.path.join(target_dir, disk_name),
        "directory": target_dir,
        "filename": disk_name,
        "frame": frame,
        "stem": stem,
        "extension": extension,
    }
