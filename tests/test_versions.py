from __future__ import annotations

import sys
from importlib.metadata import version

import pretix
import solverforge
import solverforge._native as native_solverforge


def test_exact_runtime_versions_and_native_module() -> None:
    assert sys.version_info[:2] == (3, 14)
    assert pretix.__version__ == "2026.6.1"
    assert version("pretix") == "2026.6.1"
    assert solverforge.__version__ == "0.6.4"
    assert version("solverforge") == "0.6.4"
    assert native_solverforge.__name__ == "solverforge._native"
    assert native_solverforge.native_version() == "0.6.4"
