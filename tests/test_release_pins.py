"""The d3d9.dll and lettering.zip pins are what this tree builds: `tools/verify.py release` runs this, nothing before
it (installer decision 55: the release build writes the pins, and only a release checks them). ffmpeg is not built
again: its pin changes only with its recipe (decision 56), and packaging checks the proven program against it."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / f"packaging/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pins = _load("pins")
BUILDS = {  # each pinned file, built fresh into a folder
    "d3d9.dll": lambda folder: _load("release_assets").build_dll(Path(os.environ["WOZMOD_GAME"]), folder),
    "lettering.zip": lambda folder: _load("release_assets").build_lettering(Path(os.environ["WOZMOD_DONORS"]), folder),
}


@pytest.mark.linux_only("the release renders the lettering with FontForge on the Linux host")
@pytest.mark.skipif(os.environ.get("WOZMOD_RELEASE_PINS") != "1" or not os.environ.get("WOZMOD_GAME")
                    or not os.environ.get("WOZMOD_DONORS"),
                    reason="set WOZMOD_RELEASE_PINS=1, WOZMOD_GAME and WOZMOD_DONORS (a few minutes: the lettering)")
@pytest.mark.parametrize("artifact", BUILDS)
def test_each_release_pin_is_this_trees_fresh_build(artifact: str, tmp_path: Path) -> None:
    """Bug: the release commit carries a pin no fresh build of its tree matches: the release build
    (packaging/release_assets.py) was not run after a runtime or lettering change, or a compiler update moved the
    bytes since it ran. The zips would then ship files their own source cannot make again."""
    data = BUILDS[artifact](tmp_path).read_bytes()
    found = (len(data), hashlib.sha256(data).hexdigest())
    pinned = pins.asset_pin(artifact)
    assert found == pinned, (f"{artifact}: a fresh build is {found}, the pin {pinned}: run packaging/release_assets.py "
                             "--build, which writes the pins, and play-test a new DLL")
