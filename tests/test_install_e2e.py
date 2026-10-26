"""Full install and uninstall into a copy of the stock game from the real disc images (about 5 minutes).

Run with WOZMOD_INSTALL_E2E=1 and the inputs below. The release files (d3d9.dll, lettering.zip) are built from this
checkout first, as packaging/release_assets.py builds them (about a minute), so the install also checks their pins
against what the checkout builds. The game copy goes in work/install-e2e/, not pytest's tmp_path:
/tmp is often RAM, where a 7 GB copy cannot be a reflink. It is removed when the tests end or are stopped; a copy left
by a run killed outright (SIGKILL) is removed when the next run starts. WOZMOD_INSTALL_BUDGET (seconds, default
300) is the time the install may take with the default WOZMOD_JOBS.
"""

from __future__ import annotations

import ctypes
import fcntl
import filecmp
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from wozmod.__main__ import main
from wozmod.install import check_input

REPO = Path(__file__).resolve().parents[1]
INPUTS = {"wo1-x360": "WOZMOD_WO1_X360_ISO", "wo2-x360": "WOZMOD_WO2_X360_ISO", "wo2-psp": "WOZMOD_WO2_PSP_ISO",
          "x360-tool": "WOZMOD_X360_TOOL", "psp-tool": "WOZMOD_PSP_TOOL"}

pytestmark = [pytest.mark.skipif(
    os.environ.get("WOZMOD_INSTALL_E2E") != "1"
    or not all(os.environ.get(v) for v in ["WOZMOD_GAME", "WOZMOD_DONORS", *INPUTS.values()]),
    reason="set WOZMOD_INSTALL_E2E=1, WOZMOD_GAME, WOZMOD_DONORS (the menu words of the lettering) and "
           + ", ".join(INPUTS.values()),
), pytest.mark.xdist_group("install-e2e")]  # one xdist worker, so its tests share one install in work/install-e2e


@pytest.fixture(scope="module")
def release_files(tmp_path_factory) -> dict[str, Path]:
    """d3d9.dll and lettering.zip built from this checkout by the release's own build, by installer flag."""
    spec = importlib.util.spec_from_file_location("release_assets", REPO / "packaging/release_assets.py")
    release_assets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release_assets)
    dll, lettering = release_assets.build(Path(os.environ["WOZMOD_GAME"]), Path(os.environ["WOZMOD_DONORS"]),
                                          tmp_path_factory.mktemp("release"))
    return {"dll": dll, "lettering": lettering}


@pytest.fixture(scope="module")
def installed(release_files):
    """One install into a copy of the stock game: (game copy, install command, seconds, log lines)."""
    # Bug: the checkout builds another d3d9.dll or lettering.zip than the one its pin names (the runtime or the
    # lettering changed and the pin did not), so a release from it refuses its own files. The installer would say
    # so only after the game copy (6 to 60 s): check them first.
    wrong = [f"the {path.name} this checkout builds: {problem} Refresh its pin in wozmod.install"
             for name, path in release_files.items() if (problem := check_input(name, path))]
    if wrong:
        pytest.fail("\n".join(wrong), pytrace=False)
    folder = REPO / "work/install-e2e"
    folder.parent.mkdir(exist_ok=True)
    with folder.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # held while the copy is in use, so whatever is there now is a dead run's
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir()
        stopped = signal.signal(signal.SIGTERM, signal.default_int_handler)  # a stopped run cleans up too
        try:
            yield _install(folder / "game", release_files)
        finally:
            signal.signal(signal.SIGTERM, stopped)
            shutil.rmtree(folder)


def _install(game: Path, release_files: dict[str, Path]):
    subprocess.run(["cp", "-a", "--reflink=auto", os.environ["WOZMOD_GAME"], str(game)], check=True)
    command = ["install", "--game", str(game)]
    for flag, variable in INPUTS.items():
        command += [f"--{flag}", os.environ[variable]]
    for flag, path in release_files.items():
        command += [f"--{flag}", str(path)]
    start = time.perf_counter()
    # as on a player's machine: no font tools (build.FONT_PYTHON), whatever this machine has
    font_tools = dict(os.environ, WOZMOD_FONT_PYTHON=str(game.parent / "no-font-python"))
    run = subprocess.run([sys.executable, "-m", "wozmod", *command], capture_output=True, text=True, env=font_tools)
    seconds = time.perf_counter() - start
    assert run.returncode == 0, run.stderr
    return game, command, seconds, run.stdout.splitlines()


def test_install_draws_game_text_without_font_tools(installed):
    # Bug: the art stage makes the glyph outlines with FontForge through the system Python, which players do not
    # have, so every install from a release zip stops at the art stage (the fixture runs with no font Python).
    game = installed[0]
    assert (game / "wozmod/glyphs.bin").stat().st_size > 0


def test_install_finishes_within_its_time_budget(installed, capsys):
    # Bug: a stage falls back to serial work (one worker, one ffmpeg per clip) or stops overlapping the others.
    _, _, seconds, log = installed
    budget = float(os.environ.get("WOZMOD_INSTALL_BUDGET", "300"))
    with capsys.disabled():
        print(f"\ninstall: {seconds:.0f} s (budget {budget:.0f} s, WOZMOD_JOBS={os.environ.get('WOZMOD_JOBS', 'CPU count')})")
        print("\n".join(line for line in log if line.endswith(" s)")))
    assert seconds <= budget


ACCEPTS = r'''
#include "startup_files.h"
#include "prompt_hires.h"
#include "glyph_raster.h"
int record_names(const char *json, char *out, unsigned size)
{
    const char *at = record_files(json);
    int count = 0, next;
    if (!at)
        return -1;
    while ((next = record_next(&at, out, size)) > 0) {
        while (*out)
            out++, size--;
        out++, size--, count++;
    }
    return next < 0 ? -1 : count;
}
int table(const unsigned *t, unsigned size) { return table_ok(t, size); }
int lips(const unsigned char *d, unsigned size) { return lips_ok(d, size); }
int package(const unsigned char *d, unsigned size, const unsigned char *slots, unsigned n)
{
    return package_items_ok(d, size, slots, n);
}
int copies(const unsigned char *d, unsigned size, unsigned slot)
{
    unsigned w;
    return prompt_hires_whole(d, size) && prompt_hires_find(d, size, slot, 2, &w) && prompt_hires_find(d, size, slot, 4, &w);
}
int glyphs(const unsigned char *d, unsigned size)
{
    struct gr_font font;
    unsigned head = size < 4 ? 0 : d[0] | d[1] << 8 | d[2] << 16 | (unsigned)d[3] << 24;
    return size >= 4 && size - 4 >= head && gr_open(&font, d + 4 + head, size - 4 - head);
}
'''


def test_the_dll_accepts_every_file_the_install_wrote(installed, c_library):
    # Bug: the DLL's checks at start (runtime/startup_files.h) refuse a file the installer writes, so wozmod turns
    # itself off for every player: a record name it cannot read or does not find, a table, Lips.bin, glyph file,
    # prompt package or sharper copy it calls damaged. (hires.bin's reader has its own writer test, test_art_hires.)
    from wozmod.runtime.prompts import PROMPT_SLOTS, STICK_ITEMS

    lib = c_library("accepts", ACCEPTS)
    game, folder = installed[0], installed[0] / "wozmod"
    out = ctypes.create_string_buffer(1 << 20)
    count = lib.record_names((folder / "install.json").read_bytes(), out, len(out))
    names = [n.decode() for n in out.raw.split(b"\0")[:count]] if count >= 0 else []
    slots = bytes(PROMPT_SLOTS + STICK_ITEMS)
    data = {name: (folder / name).read_bytes() for name in ("table.bin", "voice/Lips.bin", "glyphs.bin",
                                                           *(f"prompts-{family}{kind}.bin" for family in
                                                             ("xbox", "playstation") for kind in ("", "-hires")))}
    refused = {name for name, d in data.items() if not (
        lib.table(d, len(d)) if name == "table.bin" else lib.lips(d, len(d)) if name == "voice/Lips.bin"
        else lib.glyphs(d, len(d)) if name == "glyphs.bin"
        else lib.copies(d, len(d), slots[0]) if "hires" in name
        else lib.package(d, len(d), slots, len(slots)))}
    assert (count, sorted(n for n in names if not (game / n).is_file()), refused) == (
        len(json.loads((folder / "install.json").read_text())["files"]), [], set())


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
