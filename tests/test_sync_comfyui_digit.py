"""Force-sync comfyui-digit checkouts to origin/master."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync-comfyui-digit.sh"


def _git(cwd: Path, *args: str, capture: bool = False) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
    )
    return (result.stdout or "").strip()


def _init_origin(path: Path) -> str:
    path.mkdir()
    _git(path, "init", "-b", "master")
    _git(path, "config", "user.email", "digit@example.com")
    _git(path, "config", "user.name", "DIGIT")
    (path / "marker.txt").write_text("old\n", encoding="utf-8")
    _git(path, "add", "marker.txt")
    _git(path, "commit", "-m", "old")
    return _git(path, "rev-parse", "HEAD", capture=True)


def _commit_new(path: Path) -> str:
    (path / "marker.txt").write_text("new\n", encoding="utf-8")
    _git(path, "add", "marker.txt")
    _git(path, "commit", "-m", "new")
    return _git(path, "rev-parse", "HEAD", capture=True)


def _run_sync(*dirs: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["COMFYUI_SERVICE"] = ""
    env.pop("DIGIT_NODE_DIR", None)
    return subprocess.run(
        ["bash", str(SYNC_SCRIPT), *[str(d) for d in dirs]],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_sync_resets_cherry_pick_branch_to_origin_master(tmp_path):
    origin = tmp_path / "origin.git"
    old_sha = _init_origin(origin)
    install = tmp_path / "install"
    _git(tmp_path, "clone", str(origin), str(install))
    new_sha = _commit_new(origin)

    _git(install, "checkout", "-b", "cherry-pick-25")
    assert _git(install, "rev-parse", "HEAD", capture=True) == old_sha

    result = _run_sync(install)
    assert result.returncode == 0, result.stderr
    assert _git(install, "rev-parse", "HEAD", capture=True) == new_sha
    assert _git(install, "rev-parse", "--abbrev-ref", "HEAD", capture=True) == "master"
    assert " -> " in result.stdout
    assert (install / "marker.txt").read_text(encoding="utf-8") == "new\n"


def test_sync_colon_separated_env_updates_every_checkout(tmp_path):
    origin = tmp_path / "origin.git"
    _init_origin(origin)
    first = tmp_path / "first"
    second = tmp_path / "second"
    _git(tmp_path, "clone", str(origin), str(first))
    _git(tmp_path, "clone", str(origin), str(second))
    new_sha = _commit_new(origin)

    env = os.environ.copy()
    env["COMFYUI_SERVICE"] = ""
    env["DIGIT_NODE_DIR"] = f"{first}:{second}"
    result = subprocess.run(
        ["bash", str(SYNC_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert _git(first, "rev-parse", "HEAD", capture=True) == new_sha
    assert _git(second, "rev-parse", "HEAD", capture=True) == new_sha


def test_sync_errors_when_no_checkout_exists(tmp_path):
    missing = tmp_path / "nope"
    missing.mkdir()
    result = _run_sync(missing)
    assert result.returncode != 0
    assert "no comfyui-digit git checkout" in result.stderr
