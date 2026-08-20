"""Pin ComfyUI core checkouts to v0.15.1."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync-comfyui-core.sh"


def _git(cwd: Path, *args: str, capture: bool = False) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
    )
    return (result.stdout or "").strip()


def _write_version(path: Path, version: str) -> None:
    (path / "comfyui_version.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (path / "main.py").write_text("print('comfy')\n", encoding="utf-8")


def _init_origin(path: Path) -> str:
    path.mkdir()
    _git(path, "init", "-b", "master")
    _git(path, "config", "user.email", "digit@example.com")
    _git(path, "config", "user.name", "DIGIT")
    _write_version(path, "0.14.0")
    _git(path, "add", "comfyui_version.py", "main.py")
    _git(path, "commit", "-m", "old core")
    return _git(path, "rev-parse", "HEAD", capture=True)


def _tag_new(path: Path, tag: str = "v0.15.1") -> str:
    _write_version(path, "0.15.1")
    _git(path, "add", "comfyui_version.py")
    _git(path, "commit", "-m", tag)
    sha = _git(path, "rev-parse", "HEAD", capture=True)
    _git(path, "tag", tag)
    return sha


def _run_sync(*dirs: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["COMFYUI_SERVICE"] = ""
    env["INSTALL_FRONTEND"] = "0"
    env["COMFYUI_REF"] = "v0.15.1"
    env.pop("COMFYUI_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SYNC_SCRIPT), *[str(d) for d in dirs]],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_sync_pins_old_core_to_v0151(tmp_path):
    origin = tmp_path / "origin.git"
    old_sha = _init_origin(origin)
    new_sha = _tag_new(origin)
    install = tmp_path / "install"
    _git(tmp_path, "clone", str(origin), str(install))
    assert _git(install, "rev-parse", "HEAD", capture=True) == new_sha
    _git(install, "reset", "--hard", old_sha)
    assert (install / "comfyui_version.py").read_text(encoding="utf-8") == '__version__ = "0.14.0"\n'

    result = _run_sync(install)
    assert result.returncode == 0, result.stderr
    assert _git(install, "rev-parse", "HEAD", capture=True) == new_sha
    assert (install / "comfyui_version.py").read_text(encoding="utf-8") == '__version__ = "0.15.1"\n'
    assert "0.14.0" in result.stdout
    assert "0.15.1" in result.stdout


def test_sync_colon_separated_env_updates_every_core(tmp_path):
    origin = tmp_path / "origin.git"
    _init_origin(origin)
    new_sha = _tag_new(origin)
    first = tmp_path / "first"
    second = tmp_path / "second"
    _git(tmp_path, "clone", str(origin), str(first))
    _git(tmp_path, "clone", str(origin), str(second))
    _git(first, "reset", "--hard", "HEAD~1")
    _git(second, "reset", "--hard", "HEAD~1")

    result = _run_sync(extra_env={"COMFYUI_DIR": f"{first}:{second}"})
    assert result.returncode == 0, result.stderr
    assert _git(first, "rev-parse", "HEAD", capture=True) == new_sha
    assert _git(second, "rev-parse", "HEAD", capture=True) == new_sha


def test_sync_errors_when_no_core_checkout_exists(tmp_path):
    missing = tmp_path / "nope"
    missing.mkdir()
    result = _run_sync(missing)
    assert result.returncode != 0
    assert "no ComfyUI core git checkout" in result.stderr


def test_sync_skips_digit_pack_checkout(tmp_path):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "-b", "master")
    _git(origin, "config", "user.email", "digit@example.com")
    _git(origin, "config", "user.name", "DIGIT")
    (origin / "uber_saver_node.py").write_text("class DigitUberSaver:\n    pass\n", encoding="utf-8")
    _git(origin, "add", "uber_saver_node.py")
    _git(origin, "commit", "-m", "pack")
    install = tmp_path / "comfyui-digit"
    _git(tmp_path, "clone", str(origin), str(install))

    result = _run_sync(install)
    assert result.returncode != 0
    assert "no ComfyUI core git checkout" in result.stderr
