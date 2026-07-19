"""Load comfyui-digit modules without a full ComfyUI install."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_NAME = "comfyui_digit"


def _ensure_folder_paths_stub() -> None:
    if "folder_paths" in sys.modules:
        return
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_temp_directory = lambda: tempfile.gettempdir()
    sys.modules["folder_paths"] = folder_paths


def load_digit_module(module_name: str):
    """Import a repo-root module as ``comfyui_digit.<module_name>``."""
    _ensure_folder_paths_stub()

    pkg = sys.modules.get(PKG_NAME)
    if pkg is None:
        pkg = types.ModuleType(PKG_NAME)
        pkg.__path__ = [str(REPO_ROOT)]
        pkg.__package__ = PKG_NAME
        sys.modules[PKG_NAME] = pkg

    full_name = f"{PKG_NAME}.{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    spec = importlib.util.spec_from_file_location(
        full_name,
        REPO_ROOT / f"{module_name}.py",
        submodule_search_locations=[str(REPO_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module
