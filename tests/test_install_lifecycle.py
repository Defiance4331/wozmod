"""Installer rules on synthetic game folders: input checks, refusals, rollback and uninstall."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import wozmod.install as installer
from wozmod.install import InstallError, install, uninstall


def _pin(data: bytes) -> tuple[int, str]:
    return len(data), hashlib.sha256(data).hexdigest()


def _tree(root: Path) -> dict[str, bytes | None]:
    return {p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


@pytest.fixture
def game(tmp_path: Path, monkeypatch) -> Path:
    """A stock folder of two pinned files, and a build that writes three mod files."""
    folder = tmp_path / "game"
    (folder / "data").mkdir(parents=True)
    (folder / "OROCHI_Z.exe").write_bytes(b"stock exe")
    (folder / "data/LINKDATA1.BIN").write_bytes(b"stock archive")
    monkeypatch.setattr(installer, "STOCK_FILES", {"OROCHI_Z.exe": _pin(b"stock exe"),
                                                   "data/LINKDATA1.BIN": _pin(b"stock archive")})

    def build(game, inputs, staging, log, jobs):
        out = staging / "out"
        (out / "wozmod/entries").mkdir(parents=True)
        (out / "d3d9.dll").write_bytes(b"dll")
        (out / "wozmod/entries/4059.bin").write_bytes(b"entry")
        (out / "wozmod/table.bin").write_bytes(b"table")
        return out

    monkeypatch.setattr(installer, "_build", build)
    return folder


def _inputs(tmp_path: Path) -> dict[str, Path]:
    return {name: tmp_path / name for name in
            ("wo1", "x360", "psp", "x360-exe", "psp-eboot", "lettering", "dll")}


def test_every_missing_or_wrong_input_is_named_and_no_right_one(game, tmp_path, monkeypatch):
    # Bug: a wrong input passes, the error blames the wrong input, or it stops at the first problem.
    inputs = _inputs(tmp_path)
    (game / "data/LINKDATA1.BIN").write_bytes(b"patched archive")
    inputs["wo1"].write_bytes(b"image")
    inputs["x360-exe"].write_bytes(b"not the decrypted program")
    inputs["lettering"].write_bytes(b"bundle")
    inputs["dll"].write_bytes(b"other dll")
    monkeypatch.setattr(installer, "LETTERING", _pin(b"bundle"))
    monkeypatch.setattr(installer, "DLL", _pin(b"dll"))

    with pytest.raises(InstallError) as caught:
        install(game, inputs)

    named = {line.split(":")[0] for line in str(caught.value).splitlines() if line.startswith("--")}
    assert named == {"--game", "--x360", "--psp", "--x360-exe", "--psp-eboot", "--dll"}
    assert "data/LINKDATA1.BIN" in str(caught.value) and "OROCHI_Z.exe" not in str(caught.value)
    assert not (game / "wozmod").exists()


def test_second_install_refuses_without_force_and_keeps_the_first(game, tmp_path, monkeypatch):
    # Bug: a second install rebuilds over the first without --force.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs: None)
    install(game, _inputs(tmp_path), log=lambda _: None)
    before = _tree(game)

    with pytest.raises(InstallError, match="--force"):
        install(game, _inputs(tmp_path), log=lambda _: None)

    assert _tree(game) == before


def test_a_d3d9_dll_the_installer_did_not_write_is_refused_even_with_force(game, tmp_path, monkeypatch):
    # Bug: another mod's d3d9.dll is overwritten, and a later uninstall deletes it.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs: None)
    (game / "d3d9.dll").write_bytes(b"another mod")

    with pytest.raises(InstallError, match="d3d9.dll"):
        install(game, _inputs(tmp_path), force=True, log=lambda _: None)

    assert (game / "d3d9.dll").read_bytes() == b"another mod" and not (game / "wozmod").exists()


def test_a_failure_while_placing_files_removes_what_this_run_wrote(game, tmp_path, monkeypatch):
    # Bug: files placed before the failure (d3d9.dll, entries/) or the build folder are left behind.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs: None)
    (game / "wozmod/table.bin").mkdir(parents=True)  # a folder where the last file goes
    before = _tree(game)

    with pytest.raises(OSError):
        install(game, _inputs(tmp_path), log=lambda _: None)

    assert _tree(game) == before


def test_install_then_uninstall_leaves_the_folder_as_it_was(game, tmp_path, monkeypatch):
    # Bug: install.json, an empty wozmod/ or entries/ folder, or a stray build folder is left.
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs: None)
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
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs: None)
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
    (game / "wozmod").mkdir()
    (game / "wozmod/install.json").write_text(json.dumps({"files": {
        "OROCHI_Z.exe": hashlib.sha256(b"stock exe").hexdigest()}}))

    with pytest.raises(InstallError, match="OROCHI_Z.exe"):
        uninstall(game, log=lambda _: None)

    assert (game / "OROCHI_Z.exe").read_bytes() == b"stock exe"
