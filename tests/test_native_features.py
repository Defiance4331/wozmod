"""The native module's optional parts still compile (slow: WOZMOD_AUDIO=1, with cargo)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.environ.get("WOZMOD_AUDIO") != "1" or not shutil.which("cargo"), reason="set WOZMOD_AUDIO=1, with cargo")
def test_the_cuda_engine_still_compiles():
    # Bug: a change to the network's description or the separator's shared code (network.rs, separator.rs) breaks
    # the CUDA engine, which no build compiles: turning it back on (native/src/cuda.rs) then starts with a repair.
    done = subprocess.run(["cargo", "check", "--quiet", "--features", "cuda"], cwd=REPO / "native", capture_output=True,
                          text=True, encoding="utf-8")

    assert done.returncode == 0, done.stderr[-4000:]
