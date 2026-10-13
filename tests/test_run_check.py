"""run_check.py and prepare_run.py without a game: fakes stand in for the supervisor and the harness."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.syspath_prepend(str(TOOLS))
    modules = {}
    for name in ("prepare_run", "run_check"):
        spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
        modules[name] = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modules[name])
    return SimpleNamespace(**modules)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_cached_hash_is_reused_until_the_file_changes(tools, tmp_path, monkeypatch):
    """Bug: a hash cached for a stock file is reused after the file was replaced, so game.sha256 vouches
    for bytes nobody hashed."""
    cache, path = tmp_path / "cache.json", tmp_path / "LINKDATA1.BIN"
    path.write_bytes(b"one")
    assert tools.prepare_run.cached_sha256s([path], cache)[0] == digest(b"one")
    monkeypatch.setattr(tools.prepare_run, "sha256", lambda p: pytest.fail("a cache hit read the file"))
    assert tools.prepare_run.cached_sha256s([path], cache)[0] == digest(b"one")
    monkeypatch.undo()
    stamp = path.stat()
    path.write_bytes(b"two")  # same size, and the mtime put back: only the new inode tells
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    replaced = tmp_path / "other"
    replaced.write_bytes(b"two")
    os.utime(replaced, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    os.replace(replaced, path)
    assert tools.prepare_run.cached_sha256s([path], cache)[0] == digest(b"two")
    path.write_bytes(b"three!")  # a different size
    assert tools.prepare_run.cached_sha256s([path], cache)[0] == digest(b"three!")


def test_staged_game_sha256_covers_every_file_launch_checks_and_follows_a_new_dll(tools, tmp_path, monkeypatch):
    """Bugs: game.sha256 drops a wozmod file, or d3d9.dll is hashed from a stale cache entry, so launch.sh verifies a
    stale build; a stock file is copied (or missing) instead of linked to the read-only backup; the prefix or Steam
    runtime is copied rather than left to the overlay, or the game link or save is missing from it."""
    work, prep = tmp_path / "work", tools.prepare_run
    stock, install, fixture, template = tmp_path / "stock", tmp_path / "install", work / "saves/s", work / "runtime-tests/_template"
    for name in prep.STOCK_HASHED:
        (stock / name).parent.mkdir(parents=True, exist_ok=True)
        (stock / name).write_bytes(name.encode())
    (stock / "OROCHI_Z.exe").chmod(0o755)
    (install / "wozmod/entries").mkdir(parents=True)
    (install / "wozmod/table.bin").write_bytes(b"table")
    (install / "wozmod/entries/4059.bin").write_bytes(b"entry")
    for part in ("prefix/pfx/drive_c/Games", "steamrt"):
        (template / part).mkdir(parents=True)
    (template / "launch.sh").write_text("exit 0\n")
    fixture.mkdir(parents=True)
    (fixture / "save.dat").write_bytes(b"save")
    (fixture / "meta.json").write_text(json.dumps({"save_sha256": digest(b"save")}))
    (tmp_path / "harness.toml").write_text(f'stock_backup = "{stock}"\n')
    monkeypatch.setattr(prep, "WORK", work)
    monkeypatch.setattr(prep, "CONFIG", tmp_path / "harness.toml")
    monkeypatch.setattr(prep, "reflink", lambda source, target: shutil.copytree(source, target, symlinks=True)
                        if source.is_dir() else shutil.copy2(source, target))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    for sdk in ("sdk32", "sdk64"):
        (tmp_path / "home/.steam" / sdk).mkdir(parents=True)
    cache = tmp_path / "cache.json"
    (install / "d3d9.dll").write_bytes(b"dll one")
    record(install)
    root = prep.prepare("one", install, None, "s", cache)
    expected = {"d3d9.dll": digest(b"dll one"), "wozmod/table.bin": digest(b"table"), "wozmod/entries/4059.bin": digest(b"entry"),
                 "wozmod/install.json": digest((install / "wozmod/install.json").read_bytes())}
    listed = {line.split("  ")[1]: line.split("  ")[0] for line in (root / "game.sha256").read_text().splitlines()}
    assert listed == expected
    game = root / "game"
    assert sorted(p.name for p in game.iterdir()) == sorted({*(p.name for p in stock.iterdir()), "d3d9.dll", "wozmod"})
    assert all((game / p.name).is_symlink() and (game / p.name).resolve() == p for p in stock.iterdir())
    assert not (game / "d3d9.dll").is_symlink() and (game / "OROCHI_Z.exe").read_bytes() == b"OROCHI_Z.exe"
    written = sorted(p.relative_to(root).as_posix() for part in prep.OVERLAID for p in (root / part).rglob("*")
                     if p.is_symlink() or not p.is_dir())
    assert written == ["prefix/pfx/drive_c/Games/Musou Orochi Z", prep.SAVE]
    assert (root / "prefix/pfx/drive_c/Games/Musou Orochi Z").resolve() == game
    assert (root / prep.SAVE).read_bytes() == b"save"
    assert json.loads((root / "fixtures.json").read_text())["overlays"] == {
        part: str(template / part) for part in prep.OVERLAID}
    (install / "d3d9.dll").write_bytes(b"dll two")
    (install / "wozmod/table.bin").write_bytes(b"table 2")
    record(install)
    listed = {line.split("  ")[1]: line.split("  ")[0] for line in (prep.prepare("two", install, None, "s", cache) / "game.sha256").read_text().splitlines()}
    assert listed == expected | {"d3d9.dll": digest(b"dll two"), "wozmod/table.bin": digest(b"table 2"),
                                 "wozmod/install.json": digest((install / "wozmod/install.json").read_bytes())}
    # a working table.bin swapped in: the run holds and lists it, the install keeps its own, and an unlisted name is refused
    (tmp_path / "work.bin").write_bytes(b"working table")
    root = prep.prepare("three", install, None, "s", cache, replace=[f"wozmod/table.bin={tmp_path / 'work.bin'}"])
    listed = dict(reversed(line.split("  ")) for line in (root / "game.sha256").read_text().splitlines())
    assert listed["wozmod/table.bin"] == digest(b"working table") == digest((root / "game/wozmod/table.bin").read_bytes())
    assert (install / "wozmod/table.bin").read_bytes() == b"table 2"
    assert json.loads((root / "fixtures.json").read_text())["replaced"] == {"wozmod/table.bin": str(tmp_path / "work.bin")}
    with pytest.raises(ValueError, match="install record"):
        prep.prepare("four", install, None, "s", cache, replace=[f"wozmod/new.bin={tmp_path / 'work.bin'}"])


def record(install: Path) -> None:
    """The installer's record of every file under install, as `wozmod install` writes it."""
    files = {p.relative_to(install).as_posix(): digest(p.read_bytes())
             for p in [install / "d3d9.dll", *(install / "wozmod").rglob("*")] if p.is_file() and p.name != "install.json"}
    (install / "wozmod/install.json").write_text(json.dumps({"format": 1, "files": files}))


@pytest.mark.parametrize("change", ["leftover", "edited", "missing"])
def test_install_that_differs_from_its_installer_record_is_refused(tools, tmp_path, change):
    """Bugs: a run stages a file the installer never wrote (a settings.ini or runtime.log from an earlier launch),
    a hand-edited table.bin, or an install missing a file, so harness evidence shows a build no player has."""
    install = tmp_path / "install"
    (install / "wozmod").mkdir(parents=True)
    (install / "d3d9.dll").write_bytes(b"dll")
    (install / "wozmod/table.bin").write_bytes(b"table")
    record(install)
    (install / "wozmod/review.bin").write_bytes(b"test-only")  # the one file a run may add to the record
    assert set(tools.prepare_run.installed_files(install, tmp_path / "cache.json")) == {"d3d9.dll", "wozmod/table.bin", "wozmod/review.bin"}
    if change == "leftover":
        (install / "wozmod/settings.ini").write_text("[display]\nfps=120\n")
    elif change == "edited":
        (install / "wozmod/table.bin").write_bytes(b"other")
    else:
        (install / "wozmod/table.bin").unlink()
    with pytest.raises(ValueError, match="table.bin|settings.ini"):
        tools.prepare_run.installed_files(install, tmp_path / "cache.json")


class FakeSupervisor:
    def __init__(self, root: Path, reason: str, started: bool):
        (root / "evidence").mkdir(parents=True)
        (root / "evidence/exit.json").write_text(json.dumps({"reason": reason}))
        if started:
            (root / "evidence/game-exit.json").write_text(json.dumps({"returncode": 0}))


def args(tmp_path, spec, only=None):
    return SimpleNamespace(spec=spec, install=tmp_path, dll=None, save="s", setting=[], file=[], pad=False, only=only, name="check")


@pytest.fixture
def spec(tmp_path):
    path = tmp_path / "spec.toml"
    path.write_text('[[screen]]\nname = "a"\nroute = "menu"\n[[screen.assert]]\nkind = "no_cjk"\n')
    return path


def drive_with(tools, tmp_path, monkeypatch, spec, runs, check=0, only=None):
    """run_check over scripted runs, each (exit reason, game started). Returns (exit code, run directories staged)."""
    staged = []
    monkeypatch.setattr(tools.prepare_run, "WORK", tmp_path / "work")
    monkeypatch.setattr(tools.run_check, "stop", lambda root, proc: None)
    monkeypatch.setattr(tools.run_check.harness, "check", lambda root, spec, only: check)

    def stage(name, install, dll, save, settings=(), replace=()):
        staged.append(tmp_path / "work/runtime-tests" / name)
        staged[-1].mkdir(parents=True)
        return staged[-1]

    def start(root, pad=False):
        reason, started = runs[len(staged) - 1]
        FakeSupervisor(root, reason, started)
        return SimpleNamespace()

    return tools.run_check.run_check(args(tmp_path, spec, only), stage, start), staged


def test_run_that_ends_before_the_game_starts_is_retried_in_a_fresh_run_directory(tools, tmp_path, monkeypatch, spec, capsys):
    """Bug: a pulse start failure (reason unknown, no game) fails the whole check instead of staging a new run."""
    ups = iter([False, True])
    monkeypatch.setattr(tools.run_check, "wait_up", lambda root, proc: next(ups))
    code, staged = drive_with(tools, tmp_path, monkeypatch, spec, [("unknown", False), ("stopped", True)])
    assert code == 0 and len(staged) == 2 and staged[0] != staged[1] and staged[1].name.endswith("-r1")


def test_run_is_never_retried_once_the_game_started(tools, tmp_path, monkeypatch, spec):
    """Bug: a game that drew a frame and then ended 'unknown' (compositor lost) is staged again and
    hides the failure behind a second run."""
    monkeypatch.setattr(tools.run_check, "wait_up", lambda root, proc: False)
    code, staged = drive_with(tools, tmp_path, monkeypatch, spec, [("unknown", True)] * 3)
    assert code == 1 and len(staged) == 1
    monkeypatch.setattr(tools.run_check, "wait_up", lambda root, proc: True)
    code, staged = drive_with(tools, tmp_path, monkeypatch, spec, [("unknown", False)] * 3)
    assert code == 1 and len(staged) == 1  # the game answered a frame: its end is not a start failure


def test_start_failures_are_retried_twice_then_reported(tools, tmp_path, monkeypatch, spec):
    """Bug: the retry loop runs forever, or exits 0 after a game that never started."""
    monkeypatch.setattr(tools.run_check, "wait_up", lambda root, proc: False)
    code, staged = drive_with(tools, tmp_path, monkeypatch, spec, [("unknown", False)] * 3)
    assert code == 1 and len(staged) == tools.run_check.RETRIES + 1


@pytest.mark.parametrize("check, reason, code", [(0, "stopped", 0), (1, "stopped", 1), (0, "crash", 1)])
def test_exit_code_is_one_for_a_failed_row_or_a_run_not_ended_by_stop(tools, tmp_path, monkeypatch, spec, check, reason, code):
    """Bug: a FAIL row, or a game that crashed after the last row, still exits 0."""
    monkeypatch.setattr(tools.run_check, "wait_up", lambda root, proc: True)
    assert drive_with(tools, tmp_path, monkeypatch, spec, [(reason, True)], check)[0] == code


def test_misspelt_only_name_fails_before_any_staging(tools, tmp_path, monkeypatch, spec):
    """Bug: the typo is found after a minute of staging and boot (or not at all)."""
    code, staged = drive_with(tools, tmp_path, monkeypatch, spec, [], only="a,zz")
    assert code == 1 and staged == []


def test_settings_land_where_the_runtime_reads_them(tools):
    """Bug: a setting is written under the wrong section or key, so the runtime's GetPrivateProfile read falls back
    to its default and a "120 FPS" run plays at 60."""
    import configparser
    ini = configparser.ConfigParser()
    ini.read_string(tools.prepare_run.settings_ini(["display.fps=120", "language.voice=japanese", "camera.fov=70"]))
    assert {(s, k): v for s in ini.sections() for k, v in ini[s].items()} == {
        ("display", "fps"): "120", ("language", "voice"): "japanese", ("camera", "fov"): "70"}
