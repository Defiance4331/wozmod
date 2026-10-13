"""The installer's pinned d3d9.dll is the release build of the runtime in this checkout."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from wozmod.install import DLL
from wozmod.runtime.build import build_dll


@pytest.mark.skipif(os.environ.get("WOZMOD_RUNTIME_COMPILE") != "1" or not os.environ.get("WOZMOD_GAME"),
                    reason="set WOZMOD_RUNTIME_COMPILE=1 and WOZMOD_GAME")
def test_pinned_dll_is_the_release_build_of_this_runtime(tmp_path: Path) -> None:
    """Bug: the runtime changed after the last pin refresh, so every install from this checkout ships the old
    d3d9.dll without the change (the camera fix and lip sync were missing this way). Refresh: build with
    `python -m wozmod.runtime --game <stock> --output-dir DIR` and put its size and SHA-256 in wozmod.install.DLL."""
    built = build_dll(Path(os.environ["WOZMOD_GAME"]) / "OROCHI_Z.exe", tmp_path / "d3d9.dll").read_bytes()
    assert (len(built), hashlib.sha256(built).hexdigest()) == DLL
