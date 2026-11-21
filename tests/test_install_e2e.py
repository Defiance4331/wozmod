"""Install and uninstall into a copy of the stock game from the real disc images.

Run with WOZMOD_INSTALL_E2E=1 and the inputs below. Under tools/verify.py, which names the reference builds of this
tree's steps (WOZMOD_REFERENCE_TEXT, _VOICES, _ART), the game copy starts as a player's update does when nothing in
those steps changed: their files in place, recorded with their keys. The install keeps them, so these tests check its
own work (the checks, the movies, the DLL, placing the files, the record) in about a minute; tools/verify.py runs
them so, but for the time budget. Otherwise it is a first install that builds every step (about 5 minutes), as
tools/verify.py release runs it, alone: with the release files (d3d9.dll, lettering.zip) built from this checkout
first, as packaging/release_assets.py builds them (about a minute), the release's ffmpeg and no font tools. The game
copy goes in work/install-e2e/, not pytest's tmp_path:
/tmp is often RAM, where a 7 GB copy cannot be a reflink. It is removed when the tests end or are stopped; a copy left
by a run killed outright (SIGKILL) is removed when the next run starts. WOZMOD_INSTALL_BUDGET (seconds, default
300) is the time the install may take with the default WOZMOD_JOBS.
"""

from __future__ import annotations

import ctypes
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

REPO = Path(__file__).resolve().parents[1]
REFERENCES = {step: os.environ.get(f"WOZMOD_REFERENCE_{step.upper()}") for step in ("text", "voices", "art")}
KEPT = all(REFERENCES.values())  # the install keeps the reference builds' steps
INPUTS = {"wo1-x360": "WOZMOD_WO1_X360_ISO", "wo2-x360": "WOZMOD_WO2_X360_ISO", "wo2-psp": "WOZMOD_WO2_PSP_ISO",
          "x360-tool": "WOZMOD_X360_TOOL", "psp-tool": "WOZMOD_PSP_TOOL"}

pytestmark = pytest.mark.skipif(
    os.environ.get("WOZMOD_INSTALL_E2E") != "1"
    or not all(os.environ.get(v) for v in ["WOZMOD_GAME", "WOZMOD_DONORS", "WOZMOD_SPEECH", "WOZMOD_SEPARATOR",
                                           *INPUTS.values()]),
    reason="set WOZMOD_INSTALL_E2E=1, WOZMOD_GAME, WOZMOD_DONORS (the menu words of the lettering), WOZMOD_SPEECH "
           "(the speech model folder), WOZMOD_SEPARATOR (the Dream voice model folder) and " + ", ".join(INPUTS.values()),
)


@pytest.fixture(scope="module")
def release_files(tmp_path_factory) -> dict[str, Path]:
    """By installer flag: d3d9.dll built from this checkout by the release's own build, the speech model and Dream
    voice model folders the release carries, and lettering.zip: built the same way, or, when the install keeps the art step, the one its
    reference build read (WOZMOD_LETTERING), as the art key names it."""
    spec = importlib.util.spec_from_file_location("release_assets", REPO / "packaging/release_assets.py")
    release_assets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release_assets)
    game, folder = Path(os.environ["WOZMOD_GAME"]), tmp_path_factory.mktemp("release")
    if KEPT:
        dll, lettering = release_assets.build_dll(game, folder), Path(os.environ["WOZMOD_LETTERING"])
    else:
        dll, lettering = release_assets.build(game, Path(os.environ["WOZMOD_DONORS"]), folder)
    return {"dll": dll, "lettering": lettering, "speech": Path(os.environ["WOZMOD_SPEECH"]),
            "separator": Path(os.environ["WOZMOD_SEPARATOR"])}


@pytest.fixture(scope="module")
def release_ffmpeg(tmp_path_factory) -> Path:
    """A folder whose ffmpeg is the one the release carries: the proven program in work/release-assets/, checked
    against packaging/pins.json (installer decision 56). Bug: the install runs this machine's ffmpeg, so its time
    is not a player's (Fedora's, with 287 shared libraries loaded at every start, made the full install 410 s instead
    of 280). Bug: it rebuilt the pinned program, so after a compiler update every fresh worktree's timed install failed
    on bytes the recipe did not change."""
    spec = importlib.util.spec_from_file_location("pins", REPO / "packaging/pins.py")
    pins = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pins)
    program = pins.release_assets() / pins.ffmpeg_pin("linux")["file"]
    if problem := pins.ffmpeg_problem("linux", program):
        pytest.skip(problem)  # a failure where inputs are required (WOZMOD_REQUIRE_INPUTS)
    folder = tmp_path_factory.mktemp("ffmpeg")
    (folder / "ffmpeg").symlink_to(program)
    return folder


@pytest.fixture(scope="module")
def installed(request, release_files):
    """One install into a copy of the stock game, keeping the reference builds' steps when KEPT: (game copy, install
    command, seconds, log lines, _snapshot)."""
    import fcntl  # POSIX-only, so imported here: the module still collects on Windows

    folder = REPO / "work/install-e2e"
    folder.parent.mkdir(exist_ok=True)
    with folder.with_suffix(".lock").open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # held while the copy is in use, so whatever is there now is a dead run's
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir()
        stopped = signal.signal(signal.SIGTERM, signal.default_int_handler)  # a stopped run cleans up too
        path = os.environ["PATH"]
        if not KEPT:  # the steps it builds run the release's ffmpeg (a kept step's key names the one found)
            os.environ["PATH"] = f"{request.getfixturevalue('release_ffmpeg')}{os.pathsep}{path}"
        try:
            game = folder / "game"
            subprocess.run(["cp", "-a", "--reflink=auto", os.environ["WOZMOD_GAME"], str(game)], check=True)
            if KEPT:
                _lay_references(game, release_files["lettering"])
            game, command, seconds, log = _install(game, release_files, force=KEPT)
            # Bug: a kept step's files or record are not the ones an install writes, so the install builds the step
            # again (and these tests then take the full install's time for checks of the install's own work).
            assert not KEPT or [line.split(": ")[1].split(" ")[0] for line in _install_log()[:3]] == ["kept"] * 3, (
                _install_log()[:3])
            yield game, command, seconds, log, _snapshot(game)
        finally:
            os.environ["PATH"] = path
            signal.signal(signal.SIGTERM, stopped)
            shutil.rmtree(folder)


def _snapshot(game: Path) -> dict[str, tuple[str, int]]:
    """Each file the install wrote, with its SHA-256 and inode."""
    files = json.loads((game / "wozmod/install.json").read_text(encoding="utf-8"))["files"]
    return {name: (hashlib.sha256((game / name).read_bytes()).hexdigest(), (game / name).stat().st_ino) for name in files}


def _install_log() -> list[str]:
    from wozmod import remembered

    return remembered.settings_path().with_name("install.log").read_text(encoding="utf-8").splitlines()


def _lay_references(game: Path, lettering: Path) -> None:
    """Each step's reference build in the game copy, with the install record a player's update finds: each step's key
    and files, each file's SHA-256 (install._write_manifest)."""
    from wozmod import install
    from wozmod.install import step_keys

    keys, steps, files = step_keys.keys(lettering=lettering), {}, {}
    for step, folder in REFERENCES.items():
        made, out = json.loads((Path(folder) / "build.json").read_text(encoding="utf-8")), Path(folder) / "out"
        assert made["key"] == keys[step]["key"], f"{folder} is the {step} build of another tree"
        subprocess.run(["cp", "-r", "--reflink=auto", f"{out}/.", str(game)], check=True)
        names = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
        files |= {name: install._sha256(game / name) for name in names}
        steps[step] = {"key": made["key"], "parts": keys[step]["parts"], "files": names}
    install._write_manifest(game, files, steps=steps)


def _install(game: Path, release_files: dict[str, Path], force: bool = False):
    command = ["install", "--game", str(game)]
    for flag, variable in INPUTS.items():
        command += [f"--{flag}", os.environ[variable]]
    for flag, path in release_files.items():
        command += [f"--{flag}", str(path)]
    start = time.perf_counter()
    # A first install builds the art as on a player's machine: no font tools (build.FONT_PYTHON), whatever this
    # machine has. Bug: the art stage makes the glyph outlines with FontForge through the system Python, which players
    # do not have, so every install from a release zip stops at the art stage (this install then fails, and every
    # test here with it). A kept art step reads the setting as the reference build did: its key holds it.
    font_tools = dict(os.environ, **({} if KEPT else {"WOZMOD_FONT_PYTHON": str(game.parent / "no-font-python")}))
    run = subprocess.run([sys.executable, "-m", "wozmod", *command, *(["--force"] if force else [])], capture_output=True,
                         text=True, encoding="utf-8", env=font_tools)
    seconds = time.perf_counter() - start
    assert run.returncode == 0, run.stderr
    return game, command, seconds, run.stdout.splitlines()


def test_install_finishes_within_its_time_budget(installed, capsys):
    # Bug: a stage falls back to serial work (one worker, one ffmpeg per clip) or stops overlapping the others.
    _, _, seconds, log, _ = installed
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
        len(json.loads((folder / "install.json").read_text(encoding="utf-8"))["files"]), [], set())


def test_a_second_install_keeps_every_step_and_leaves_the_files_of_the_first(installed, release_files, capsys):
    # Bug: a reinstall with nothing changed rebuilds a step (its key is not stable from one run to the next), recopies
    # the movies, or moves or rewrites a kept file. WOZMOD_UPDATE_BUDGET (seconds, default 60) is the time it may take.
    game, _, _, _, fresh = installed
    _, _, seconds, _ = _install(game, release_files, force=True)
    log = _install_log()
    with capsys.disabled():
        print(f"\nreinstall with nothing changed: {seconds:.0f} s; " + "; ".join(log[:5]))
    assert [line.split(":")[0] + ": " + line.split(": ")[1].split(" ")[0] for line in log[:3]] == [
        "text: kept", "voices: kept", "art: kept"]
    assert log[3].startswith("movies: kept ") and log[3].split()[2] == log[3].split()[4]
    assert _snapshot(game) == fresh
    assert seconds <= float(os.environ.get("WOZMOD_UPDATE_BUDGET", "60"))


def test_a_changed_step_key_rebuilds_only_that_step_and_the_same_files(installed, release_files):
    # Bug: one changed key rebuilds the other steps too, or the rebuilt step's files differ from a fresh install's
    # (a kept step's output fed back into it).
    game, _, _, _, fresh = installed
    record = json.loads((game / "wozmod/install.json").read_text(encoding="utf-8"))
    record["steps"]["text"]["key"] = "0" * 64
    (game / "wozmod/install.json").write_text(json.dumps(record), encoding="utf-8")
    _install(game, release_files, force=True)
    log = _install_log()
    assert [line.split(": ")[1] for line in log[:3]] == ["built", "kept", "kept"]
    assert {name: digest for name, (digest, _) in _snapshot(game).items()} == {
        name: digest for name, (digest, _) in fresh.items()}


def test_install_lists_every_file_it_wrote_and_uninstall_restores_the_stock_folder(installed):
    # Bug: a built file missing from install.json (left by uninstall), a stale hash, or a stock file touched.
    game, command, _, _, _ = installed
    stock = Path(os.environ["WOZMOD_GAME"])
    listed = json.loads((game / "wozmod/install.json").read_text(encoding="utf-8"))["files"]
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
