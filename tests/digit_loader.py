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
    folder_paths.get_input_directory = lambda: tempfile.gettempdir()
    folder_paths.get_annotated_filepath = lambda name: name
    folder_paths.filter_files_content_types = lambda files, types_: files
    sys.modules["folder_paths"] = folder_paths


def _ensure_server_stub() -> None:
    if "server" in sys.modules:
        return
    from aiohttp import web

    server = types.ModuleType("server")

    class FakePromptServer:
        instance = None

        def __init__(self):
            self.routes = web.RouteTableDef()

    FakePromptServer.instance = FakePromptServer()
    server.PromptServer = FakePromptServer
    sys.modules["server"] = server


def _ensure_comfy_stub() -> None:
    if "comfy" in sys.modules:
        return
    comfy = types.ModuleType("comfy")
    utils = types.ModuleType("comfy.utils")
    utils.ProgressBar = lambda *args, **kwargs: None
    comfy.utils = utils
    sys.modules["comfy"] = comfy
    sys.modules["comfy.utils"] = utils


def _ensure_torch_stub() -> None:
    if "torch" in sys.modules:
        return
    try:
        import torch  # noqa: F401
        return
    except ImportError:
        pass

    import numpy as np

    torch = types.ModuleType("torch")

    class _Tensor:
        def __init__(self, array):
            self._array = array

        def cpu(self):
            return self

        def numpy(self):
            return self._array

        @property
        def shape(self):
            return self._array.shape

        def __getitem__(self, idx):
            return _Tensor(self._array[idx])

        def unsqueeze(self, _dim):
            return _Tensor(np.expand_dims(self._array, 0))

    torch.from_numpy = lambda array: _Tensor(array)
    sys.modules["torch"] = torch


def load_digit_module(module_name: str):
    """Import a repo-root module as ``comfyui_digit.<module_name>``."""
    _ensure_folder_paths_stub()
    _ensure_comfy_stub()
    _ensure_server_stub()
    _ensure_torch_stub()

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
