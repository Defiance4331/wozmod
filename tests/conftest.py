"""Export this machine's input paths, so tests that need stock or donor files run instead of skipping.

The paths live in the `[tests]` table of `work/harness.toml` (ignored, per machine; `WOZMOD_HARNESS_CONFIG`
names another file). A variable already set in the environment wins.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
CONFIG = Path(os.environ.get("WOZMOD_HARNESS_CONFIG", Path(__file__).resolve().parents[1] / "work/harness.toml"))

if CONFIG.is_file():
    for name, value in tomllib.loads(CONFIG.read_text(encoding="utf-8")).get("tests", {}).items():
        os.environ.setdefault(name, str(value))


@pytest.fixture(scope="session")
def c_library(tmp_path_factory):
    """Compile C test glue against runtime/ with the DLL's warning flags, and load it: c_library(name, source)."""
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("a native C compiler is unavailable")

    def build(name: str, source: str) -> ctypes.CDLL:
        directory = tmp_path_factory.mktemp(name)
        (directory / f"{name}.c").write_text(source)
        subprocess.run([compiler, "-std=c11", "-O2", "-Wall", "-Werror", "-shared", "-fPIC", "-I", str(RUNTIME),
                        str(directory / f"{name}.c"), "-o", str(directory / f"{name}.so")], check=True)
        return ctypes.CDLL(str(directory / f"{name}.so"))

    return build
