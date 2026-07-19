"""Early pytest plugin: skip importing the ComfyUI node-pack entrypoint during test runs."""

from __future__ import annotations

from pathlib import Path

import _pytest.python as pytest_python

_REPO_ROOT = Path(__file__).resolve().parent.parent
_original_package_setup = pytest_python.Package.setup


def _package_setup(self):
    init_path = self.path / "__init__.py"
    if init_path.is_file() and init_path.resolve() == (_REPO_ROOT / "__init__.py"):
        return
    return _original_package_setup(self)


pytest_python.Package.setup = _package_setup
