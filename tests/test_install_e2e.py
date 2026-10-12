"""Full install and uninstall into a copy of the stock game from the real disc images (about 5 minutes).

Run with WOZMOD_INSTALL_E2E=1 and the inputs below; give pytest a --basetemp on the
game's filesystem so the copy can be a reflink. WOZMOD_INSTALL_BUDGET (seconds, default
300) is the time the install may take with the default WOZMOD_JOBS.
"""

from __future__ import annotations

import filecmp
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from wozmod.__main__ import main

INPUTS = {"wo1": "WOZMOD_WO1_ISO", "x360": "WOZMOD_X360_ISO", "psp": "WOZMOD_PSP_ISO",
          "x360-tool": "WOZMOD_X360_TOOL", "psp-tool": "WOZMOD_PSP_TOOL",
          "lettering": "WOZMOD_LETTERING", "dll": "WOZMOD_DLL"}

pytestmark = pytest.mark.skipif(
    os.environ.get("WOZMOD_INSTALL_E2E") != "1"
    or not all(os.environ.get(v) for v in ["WOZMOD_GAME", *INPUTS.values()]),
    reason="set WOZMOD_INSTALL_E2E=1, WOZMOD_GAME and " + ", ".join(INPUTS.values()),
)


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    """One install into a copy of the stock game: (game copy, install command, seconds, log lines)."""
    game = tmp_path_factory.mktemp("e2e") / "game"
    subprocess.run(["cp", "-a", "--reflink=auto", os.environ["WOZMOD_GAME"], str(game)], check=True)
    command = ["install", "--game", str(game)]
    for flag, variable in INPUTS.items():
        command += [f"--{flag}", os.environ[variable]]
    start = time.perf_counter()
    run = subprocess.run([sys.executable, "-m", "wozmod", *command], capture_output=True, text=True)
    seconds = time.perf_counter() - start
    assert run.returncode == 0, run.stderr
    return game, command, seconds, run.stdout.splitlines()


def test_install_finishes_within_its_time_budget(installed, capsys):
    # Bug: a stage falls back to serial work (one worker, one ffmpeg per clip) or stops overlapping the others.
    _, _, seconds, log = installed
    budget = float(os.environ.get("WOZMOD_INSTALL_BUDGET", "300"))
    with capsys.disabled():
        print(f"\ninstall: {seconds:.0f} s (budget {budget:.0f} s, WOZMOD_JOBS={os.environ.get('WOZMOD_JOBS', 'CPU count')})")
        print("\n".join(line for line in log if line.endswith(" s)")))
    assert seconds <= budget


def test_install_lists_every_file_it_wrote_and_uninstall_restores_the_stock_folder(installed):
    # Bug: a built file missing from install.json (left by uninstall), a stale hash, or a stock file touched.
    game, command, _, _ = installed
    stock = Path(os.environ["WOZMOD_GAME"])
    listed = json.loads((game / "wozmod/install.json").read_text())["files"]
    on_disk = {p.relative_to(game).as_posix() for p in game.rglob("*")
               if p.is_file() and not (stock / p.relative_to(game)).exists()} - {"wozmod/install.json"}
    assert set(listed) == on_disk
    assert all(hashlib.sha256((game / name).read_bytes()).hexdigest() == digest
               for name, digest in listed.items())

    assert main(["uninstall", "--game", str(game), "--remove-settings"]) == 0
    assert sorted(p.relative_to(game) for p in game.rglob("*")) == sorted(
        p.relative_to(stock) for p in stock.rglob("*"))
    assert all(filecmp.cmp(p, game / p.relative_to(stock), shallow=False)
               for p in stock.rglob("*") if p.is_file())
