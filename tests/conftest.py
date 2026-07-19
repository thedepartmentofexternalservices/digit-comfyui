"""Shared pytest fixtures for comfyui-digit."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from digit_loader import load_digit_module  # noqa: E402


@pytest.fixture
def digit_module():
    """Return a callable that loads comfyui_digit modules on demand."""

    def _loader(name: str):
        return load_digit_module(name)

    return _loader
