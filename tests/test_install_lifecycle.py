"""Installer rules on synthetic game folders: input checks, refusals, rollback and uninstall."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import wozmod.install as installer
from synthetic import fake_step_keys, stock_game
from wozmod.install import InstallError, install, stock, uninstall
from wozmod.runtime import guard_report

REAL_BUILD = installer._build
BUILT = {"text": {"wozmod/table.bin": b"table"}, "voices": {}, "art": {"wozmod/entries/4059.bin": b"entry"}}


def _pin(data: bytes) -> tuple[int, str]:
    return len(data), hashlib.sha256(data).hexdigest()


def _tree(root: Path) -> dict[str, bytes | None]:
    return {p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


@pytest.fixture
def game(tmp_path: Path, monkeypatch) -> Path:
    """A small stock folder with its pieces record, fixed step keys, and a build whose text and art steps write a mod
    file each (beside d3d9.dll)."""
    folder = tmp_path / "game"
    record = stock_game(folder)
    monkeypatch.setattr(stock, "recorded", lambda: record)
    fake_step_keys(monkeypatch)

    def build(game, inputs, staging, progress, jobs, checked, cache=None, rebuilt=installer.STEPS, movies=None):
        out = staging / "out"
        out.mkdir(parents=True)
        (out / "d3d9.dll").write_bytes(b"dll")
        written = {}
        for step in rebuilt:
            written[step] = [name for name in BUILT[step]]
            for name, data in BUILT[step].items():
                (out / name).parent.mkdir(parents=True, exist_ok=True)
                (out / name).write_bytes(data)
        return out, written

    monkeypatch.setattr(installer, "_build", build)
    return folder


def _inputs(tmp_path: Path) -> dict[str, Path]:
    return {name: tmp_path / name for name in
            ("wo1-x360", "wo2-x360", "wo2-psp", "x360-tool", "psp-tool", "lettering", "dll")}


def test_every_missing_or_wrong_input_is_named_and_no_right_one(game, tmp_path, monkeypatch):
    # Bug: a wrong input passes, the error blames the wrong input, or it stops at the first problem.
    inputs = _inputs(tmp_path)
    (game / "LINKDATA1.BIN").write_bytes(b"patched archive")
    inputs["wo1-x360"].write_bytes(b"not a disc image")
    inputs["lettering"].write_bytes(b"bundle")
    inputs["dll"].write_bytes(b"dll")
    monkeypatch.setattr(installer, "LETTERING", _pin(b"bundle"))
    monkeypatch.setattr(installer, "DLL", _pin(b"dll"))
    monkeypatch.setattr(guard_report, "refused_features", lambda exe, dll: [])  # the stand-in DLL has no guard list

    with pytest.raises(InstallError) as caught:
        install(game, inputs)

    named = {line.split(":")[0] for line in str(caught.value).splitlines() if ": " in line}
    assert named == {"Game folder", "Warriors Orochi (Xbox 360, USA) disc image",
                     "Warriors Orochi 2 (Xbox 360, USA) disc image", "Warriors Orochi 2 (PSP, USA) disc image"}
    assert "LINKDATA1.BIN" in str(caught.value) and "OROCHI_Z.exe" not in str(caught.value)
    assert not (game / "wozmod").exists()


def test_a_wrong_download_is_named_before_the_game_folder_is_hashed(game, tmp_path, monkeypatch):
    # Bug: the release files are checked after the game folder's hashes (seconds on a real game), or only the
    # first wrong one is named.
    inputs = _inputs(tmp_path)
    (game / "LINKDATA1.BIN").write_bytes(b"patched archive")
    inputs["lettering"].write_bytes(b"other bundle")
    monkeypatch.setattr(installer, "DLL", _pin(b"dll"))
    hashed = []
    monkeypatch.setattr(installer, "_sha256", lambda path, real=installer._sha256: hashed.append(path) or real(path))

    with pytest.raises(InstallError) as caught:
        install(game, inputs)

    assert str(caught.value).splitlines() == [
        "lettering.zip: Not the file that goes with this release. Download wozmod again.",
        "d3d9.dll: File not found. Download wozmod again."]
    assert not [path for path in hashed if path.is_relative_to(game)]


def test_the_notes_of_the_game_check_reach_the_player_once_the_files_are_placed(game, tmp_path, monkeypatch):
    # Bug: the install drops the check's notes (another mod also replaces a voice clip), or gives
    # them before the files are in place, so a failed install still tells the player what its files will do.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs, log=None, rebuilt=None: ["Note: one", "Note: two"])
    shown = []
    install(game, _inputs(tmp_path), log=lambda _: None,
            note=lambda line: shown.append((line, (game / "wozmod/install.json").is_file())))
    assert shown == [("Note: one", True), ("Note: two", True)]


def test_install_over_the_same_version_rebuilds_and_a_newer_one_asks_first(game, tmp_path, monkeypatch):
    # Bug: a second install of the same version is refused (so "Reinstall" fails), or an install made by a
    # newer wozmod is silently replaced by this older one.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs, log=None, rebuilt=None: [])
    install(game, _inputs(tmp_path), log=lambda _: None)
    (game / "wozmod/settings.ini").write_bytes(b"player settings")
    assert installer.install_state(game) == ("reinstall", installer.VERSION)
    install(game, _inputs(tmp_path), log=lambda _: None)
    assert (game / "wozmod/settings.ini").read_bytes() == b"player settings"

    record = json.loads((game / "wozmod/install.json").read_text())
    (game / "wozmod/install.json").write_text(json.dumps({**record, "version": "99.0.0"}))
    before = _tree(game)
    with pytest.raises(InstallError, match="--force"):
        install(game, _inputs(tmp_path), log=lambda _: None)
    assert _tree(game) == before
    install(game, _inputs(tmp_path), force=True, log=lambda _: None)
    assert installer.installed_version(game) == installer.VERSION


def test_a_reinstall_leaves_unchanged_files_in_place_and_replaces_changed_ones(game, tmp_path, monkeypatch):
    # Bug: a rebuilt step writes every file anew (gigabytes of fresh disk blocks for the same bytes), or keeps a
    # file whose build changed, or one changed on disk since the last install.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs, log=None, rebuilt=None: [])
    install(game, _inputs(tmp_path), log=lambda _: None)
    inodes = {name: (game / name).stat().st_ino for name in ("d3d9.dll", "wozmod/entries/4059.bin", "wozmod/table.bin")}
    fake_step_keys(monkeypatch, {step: {"key": f"new {step} key", "parts": {}} for step in installer.STEPS})
    monkeypatch.setitem(BUILT, "text", {"wozmod/table.bin": b"new table"})  # the art step builds the same bytes
    with (game / "d3d9.dll").open("r+b") as dll:
        dll.write(b"DLL")  # same inode, other bytes
    install(game, _inputs(tmp_path), log=lambda _: None)

    now = {name: (game / name).stat().st_ino for name in inodes}
    assert [name for name in inodes if now[name] == inodes[name]] == ["wozmod/entries/4059.bin"]
    assert (game / "d3d9.dll").read_bytes() == b"dll" and (game / "wozmod/table.bin").read_bytes() == b"new table"
    assert installer.read_manifest(game)["wozmod/table.bin"] == hashlib.sha256(b"new table").hexdigest()


# The install record exactly as the first release wrote it: no "version" key.
FROZEN_FIRST_RECORD = """{
 "format": 1,
 "files": {
  "d3d9.dll": "%s",
  "wozmod/table.bin": "%s"
 }
}
""" % (hashlib.sha256(b"old dll").hexdigest(), hashlib.sha256(b"old table").hexdigest())


def test_a_record_from_the_first_release_is_still_updated(game, tmp_path, monkeypatch):
    # Bug: a later version requires a "version" key in install.json, so every install made by the first
    # release is refused as unreadable or as a downgrade, and the player has to delete files by hand.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs, log=None, rebuilt=None: [])
    (game / "wozmod").mkdir()
    (game / "d3d9.dll").write_bytes(b"old dll")
    (game / "wozmod/table.bin").write_bytes(b"old table")
    (game / "wozmod/settings.ini").write_bytes(b"player settings")
    (game / "wozmod/install.json").write_text(FROZEN_FIRST_RECORD)
    assert installer.install_state(game) == ("update", None)
    assert installer.existing_install(game) == "0"  # a record without a version reads as version 0

    install(game, _inputs(tmp_path), log=lambda _: None)  # an update needs no --force

    assert (game / "d3d9.dll").read_bytes() == b"dll" and (game / "wozmod/settings.ini").read_bytes() == b"player settings"
    assert installer.installed_version(game) == installer.VERSION
    assert installer.existing_install(game) == installer.VERSION


def test_existing_install_is_none_on_a_stock_folder(game):
    # Bug: a stock folder reads as "version 0", so a first install is announced as an update.
    assert installer.existing_install(game) is None


def test_a_record_from_the_first_release_is_still_uninstalled(game):
    # Bug: uninstall reads a "version" key the first release never wrote, so the player cannot remove it.
    stock_tree = _tree(game)
    (game / "wozmod").mkdir()
    (game / "d3d9.dll").write_bytes(b"old dll")
    (game / "wozmod/table.bin").write_bytes(b"old table")
    (game / "wozmod/install.json").write_text(FROZEN_FIRST_RECORD)
    assert uninstall(game, log=lambda _: None, keep_settings=False) == {}
    assert _tree(game) == stock_tree


def test_uninstall_that_removes_the_settings_leaves_the_stock_folder(game, tmp_path, monkeypatch):
    # Bug: settings.ini and the runtime logs (written by the game, so not in install.json) stay behind, with
    # an otherwise empty wozmod/ folder, so the folder is no longer stock.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs, log=None, rebuilt=None: [])
    before = _tree(game)
    install(game, _inputs(tmp_path), log=lambda _: None)
    (game / "wozmod/settings.ini").write_bytes(b"player settings")
    (game / "wozmod/runtime.log").write_bytes(b"log")
    (game / "wozmod/runtime.crash.log").write_bytes(b"log")

    uninstall(game, log=lambda _: None, keep_settings=False)

    assert _tree(game) == before


def test_uninstall_can_keep_the_settings(game, tmp_path, monkeypatch):
    # Bug: "keep my settings" still deletes them, so a later install starts from the defaults.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs, log=None, rebuilt=None: [])
    install(game, _inputs(tmp_path), log=lambda _: None)
    (game / "wozmod/settings.ini").write_bytes(b"player settings")
    uninstall(game, log=lambda _: None, keep_settings=True)
    assert (game / "wozmod/settings.ini").read_bytes() == b"player settings"
    assert not (game / "d3d9.dll").exists()


def test_the_version_in_the_code_is_the_version_of_the_release():
    # Bug: a release bumps pyproject.toml but not wozmod.version.__version__, so install.json records the old
    # version and the next update sees "reinstall" (or "downgrade").
    import tomllib
    from wozmod.version import __version__
    root = Path(__file__).resolve().parents[1]
    assert tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"] == __version__


def test_a_d3d9_dll_the_installer_did_not_write_is_refused_even_with_force(game, tmp_path, monkeypatch):
    # Bug: another mod's d3d9.dll is overwritten, and a later uninstall deletes it.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs, log=None, rebuilt=None: [])
    (game / "d3d9.dll").write_bytes(b"another mod")

    with pytest.raises(InstallError, match="d3d9.dll"):
        install(game, _inputs(tmp_path), force=True, log=lambda _: None)

    assert (game / "d3d9.dll").read_bytes() == b"another mod" and not (game / "wozmod").exists()


def test_a_failure_while_placing_files_removes_what_this_run_wrote(game, tmp_path, monkeypatch):
    # Bug: files placed before the failure (d3d9.dll, entries/) or the build folder are left behind.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs, log=None, rebuilt=None: [])
    (game / "wozmod/table.bin").mkdir(parents=True)  # a folder where the last file goes
    before = _tree(game)

    with pytest.raises(OSError):
        install(game, _inputs(tmp_path), log=lambda _: None)

    assert _tree(game) == before


def test_install_then_uninstall_leaves_the_folder_as_it_was(game, tmp_path, monkeypatch):
    # Bug: install.json, an empty wozmod/ or entries/ folder, or a stray build folder is left.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs, log=None, rebuilt=None: [])
    before = _tree(game)
    files = install(game, _inputs(tmp_path), log=lambda _: None)
    assert json.loads((game / "wozmod/install.json").read_text())["files"] == {
        "d3d9.dll": hashlib.sha256(b"dll").hexdigest(),
        "wozmod/entries/4059.bin": hashlib.sha256(b"entry").hexdigest(),
        "wozmod/table.bin": hashlib.sha256(b"table").hexdigest(),
    } == files

    assert uninstall(game, log=lambda _: None) == {}
    assert _tree(game) == before


def test_uninstall_leaves_changed_files_and_keeps_listing_them(game, tmp_path, monkeypatch):
    # Bug: a file the player changed after install is deleted, or forgotten by install.json.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs, log=None, rebuilt=None: [])
    files = install(game, _inputs(tmp_path), log=lambda _: None)
    (game / "wozmod/table.bin").write_bytes(b"player edit")
    (game / "wozmod/settings.ini").write_bytes(b"written by the game")

    changed = uninstall(game, log=lambda _: None)

    assert changed == {"wozmod/table.bin": files["wozmod/table.bin"]}
    assert sorted(_tree(game / "wozmod")) == ["install.json", "settings.ini", "table.bin"]
    assert (game / "wozmod/table.bin").read_bytes() == b"player edit"
    assert json.loads((game / "wozmod/install.json").read_text())["files"] == changed
    assert not (game / "d3d9.dll").exists()


def test_uninstall_refuses_an_install_json_that_lists_a_stock_file(game):
    # Bug: an edited install.json carrying a stock file's own hash makes uninstall delete it.
    exe = (game / "OROCHI_Z.exe").read_bytes()
    (game / "wozmod").mkdir()
    (game / "wozmod/install.json").write_text(json.dumps({"files": {
        "OROCHI_Z.exe": hashlib.sha256(exe).hexdigest()}}))

    with pytest.raises(InstallError, match="OROCHI_Z.exe"):
        uninstall(game, log=lambda _: None)

    assert (game / "OROCHI_Z.exe").read_bytes() == exe


def test_the_input_checks_run_while_the_discs_are_read(game, tmp_path, monkeypatch):
    # Bug: the checks finish before the first disc read starts again, so a slow hash adds its time to the install.
    import threading
    reading = threading.Event()

    def check(game, inputs, log=None, rebuilt=None):
        if not reading.wait(10):
            raise InstallError("the checks ran before any disc was read")
        return []

    def extract(*args):
        reading.set()
        raise InstallError("read stops the test")

    monkeypatch.setattr(installer, "_build", REAL_BUILD)
    monkeypatch.setattr(installer, "check_inputs", check)
    monkeypatch.setattr(installer, "_extract", extract)
    with pytest.raises(InstallError, match="read stops the test"):
        install(game, _inputs(tmp_path), log=lambda _: None)


def test_a_wrong_input_is_named_before_a_disc_that_failed_to_read(game, tmp_path, monkeypatch):
    # Bug: the disc read error hides the wrong input, and the player is told a disc is bad when it is the game folder.
    def check(game, inputs, log=None, rebuilt=None):
        raise InstallError("Game folder: wrong input")

    def extract(*args):
        raise InstallError("Warriors Orochi disc image: cannot be read")

    monkeypatch.setattr(installer, "_build", REAL_BUILD)
    monkeypatch.setattr(installer, "check_inputs", check)
    monkeypatch.setattr(installer, "_extract", extract)
    before = _tree(game)
    with pytest.raises(InstallError, match="Game folder: wrong input"):
        install(game, _inputs(tmp_path), log=lambda _: None)
    assert _tree(game) == before


def test_a_wrong_input_stops_the_disc_read_as_soon_as_the_checks_find_it(game, tmp_path, monkeypatch):
    # Bug: the check result is read only after every disc is read, so a wrong lettering.zip fails minutes late.
    import time

    import wozmod.donors
    read = []

    def check(game, inputs, log=None, rebuilt=None):
        raise InstallError("lettering.zip: wrong input")

    def extract_donor(image, donor, destination, files=None, on_bytes=None):
        for done in range(1, 1001):  # a 10 s disc read, unless on_bytes stops it
            time.sleep(0.01)
            on_bytes(done, 1000)
        read.append(image)

    monkeypatch.setattr(installer, "_build", REAL_BUILD)
    monkeypatch.setattr(installer, "check_inputs", check)
    monkeypatch.setattr(wozmod.donors, "extract_donor", extract_donor)
    with pytest.raises(InstallError, match="lettering.zip: wrong input"):
        install(game, _inputs(tmp_path), log=lambda _: None)
    assert read == []


def test_the_voice_stage_lowers_its_priority_before_it_starts_ffmpeg(monkeypatch, tmp_path):
    # Bug: the voice stage runs at the art stage's priority, so the art stage's long serial steps wait for
    # ffmpeg processes that could fill any gap, and the install takes longer.
    import os
    import wozmod.voices

    calls = []
    monkeypatch.setattr(os, "nice", lambda increment: calls.append(("nice", increment)), raising=False)
    monkeypatch.setattr(os, "sched_setscheduler", lambda *args: calls.append(("policy", args[1])), raising=False)
    monkeypatch.setattr(os, "SCHED_BATCH", 3, raising=False)
    monkeypatch.setattr(os, "sched_param", lambda priority: priority, raising=False)
    monkeypatch.setattr(wozmod.voices, "build_voice_banks", lambda *args: calls.append(("build", args[3])))
    installer._voice_banks(tmp_path, tmp_path, tmp_path, 7)
    assert calls == [("nice", installer.VOICE_NICE), ("policy", 3), ("build", 7)] and installer.VOICE_NICE > 0


def test_the_art_stage_places_every_file_the_art_build_writes(tmp_path, monkeypatch):
    """Bug: the install moves a fixed list of art outputs, so a new one is left in the staging folder and dropped."""
    import wozmod.art.compose

    built = {"entries/4059.bin": b"entry", "glyphs.bin": b"glyphs", "new-art.bin": b"new"}

    def compose(stock, donors, bundle, output_dir, squish=True, jobs=None):
        for name, data in built.items():
            (output_dir / "wozmod" / name).parent.mkdir(parents=True, exist_ok=True)
            (output_dir / "wozmod" / name).write_bytes(data)
        return output_dir / "wozmod"

    monkeypatch.setattr(wozmod.art.compose, "compose", compose)
    out = tmp_path / "out"
    (out / "wozmod").mkdir(parents=True)
    (out / "wozmod/table.bin").write_bytes(b"table")  # another stage's file, already in place
    installer._menu_art(tmp_path / "game", tmp_path / "donors", tmp_path / "lettering.zip", tmp_path / "art", out, 1)
    assert _tree(out / "wozmod") == {"entries": None, "table.bin": b"table", **built}


@pytest.mark.parametrize("wrong, title", [("wo1-x360", "Warriors Orochi (Xbox 360, USA)"),
                                          ("wo2-x360", "Warriors Orochi 2 (Xbox 360, USA)")])
def test_a_wrong_donor_archive_ends_the_art_stage_with_an_install_error_naming_its_disc(tmp_path, monkeypatch,
                                                                                        wrong, title):
    """Bug: the art build reports a donor archive that fails its pin as a usage error (SystemExit), which neither the
    window nor the command line catches; or the error names no disc, or the other Xbox 360 disc, so the player cannot
    tell which disc image to replace."""
    import dataclasses

    import wozmod.art.compose as composer
    from PIL import Image
    from wozmod.art import build
    from wozmod.art.bundle import write_bundle
    from wozmod.donors.discs import DISCS, DonorsFolder
    from wozmod.donors.inventory import DonorFile

    recipe = tmp_path / "recipe.json"
    recipe.write_bytes(json.dumps({"format_version": 2, "metrics_only": [], "requests": {"layer": {}}}).encode())
    monkeypatch.setattr(composer, "RECIPE", recipe)
    bundle = tmp_path / "lettering.zip"
    write_bundle(bundle, {"layer": (Image.new("L", (1, 1)), {})}, hashlib.sha256(recipe.read_bytes()).hexdigest(),
                 outlines=b"outlines")
    donors = DonorsFolder(tmp_path / "donors")
    for disc, attribute in (("wo1-x360", "WO1_X360"), ("wo2-x360", "WO2_X360")):  # each disc's archive pinned to its bytes
        stem = donors.folder(disc) / "linkdata_ens"
        stem.parent.mkdir(parents=True)
        pins = []
        for suffix in (".idx", ".lnk"):
            data = f"{disc}{suffix}".encode()
            stem.with_suffix(suffix).write_bytes(data[::-1] if disc == wrong and suffix == ".lnk" else data)
            pins.append(DonorFile(f"linkdata_ens{suffix}", f"linkdata_ens{suffix}", len(data),
                                  hashlib.sha256(data).hexdigest()))
        donor = dataclasses.replace(DISCS[disc].donor, files=tuple(pins))
        monkeypatch.setattr(build, attribute, donor)
        monkeypatch.setitem(DISCS, disc, dataclasses.replace(DISCS[disc], donor=donor))
    with pytest.raises(InstallError) as caught:
        installer._menu_art(tmp_path / "game", donors.root, bundle, tmp_path / "art", tmp_path / "out", 1)
    assert str(caught.value) == f"Menu art: {title} disc: Wrong donor member hash: linkdata_ens.lnk"
