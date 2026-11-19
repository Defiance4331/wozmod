"""run_check.py and prepare_run.py without a game: fakes stand in for the supervisor and the harness."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.linux_only("run_check drives the game through the Linux game harness")


@pytest.fixture
def tools():
    import game_harness
    import prepare_run
    import run_check
    return SimpleNamespace(prepare_run=prepare_run, run_check=run_check, game_harness=game_harness)


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


def staging(tools, tmp_path, monkeypatch) -> SimpleNamespace:
    """A stock backup, an install with its record, a save fixture and a pinned template, all in tmp_path, ready for
    prepare_run.prepare(name, install, None, "s", cache)."""
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
    pinned(template)
    fixture.mkdir(parents=True)
    (fixture / "save.dat").write_bytes(b"save")
    (fixture / "meta.json").write_text(json.dumps({"save_sha256": digest(b"save")}), encoding="utf-8")
    (tmp_path / "harness.toml").write_text(f'stock_backup = "{stock}"\n', encoding="utf-8")
    monkeypatch.setattr(prep, "WORK", work)
    monkeypatch.setattr(prep, "CONFIG", tmp_path / "harness.toml")
    monkeypatch.setattr(prep, "reflink", lambda source, target: shutil.copytree(source, target, symlinks=True)
                        if source.is_dir() else shutil.copy2(source, target))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    for sdk in ("sdk32", "sdk64"):
        (tmp_path / "home/.steam" / sdk).mkdir(parents=True)
    (install / "d3d9.dll").write_bytes(b"dll one")
    record(install)
    return SimpleNamespace(stock=stock, install=install, template=template, cache=tmp_path / "cache.json")


def test_staged_game_sha256_covers_every_file_launch_checks_and_follows_a_new_dll(tools, tmp_path, monkeypatch):
    """Bugs: game.sha256 drops a wozmod file, or d3d9.dll is hashed from a stale cache entry, so launch.sh verifies a
    stale build; a stock file is copied (or missing) instead of linked to the read-only backup; the prefix or Steam
    runtime is copied rather than left to the overlay, or the game link or save is missing from it."""
    prep, staged = tools.prepare_run, staging(tools, tmp_path, monkeypatch)
    stock, install, template, cache = staged.stock, staged.install, staged.template, staged.cache
    root = prep.prepare("one", install, None, "s", cache)
    expected = {"d3d9.dll": digest(b"dll one"), "wozmod/table.bin": digest(b"table"), "wozmod/entries/4059.bin": digest(b"entry"),
                 "wozmod/install.json": digest((install / "wozmod/install.json").read_bytes())}
    assert prep.recorded(root) == expected
    game = root / "game"
    assert sorted(p.name for p in game.iterdir()) == sorted({*(p.name for p in stock.iterdir()), "d3d9.dll", "wozmod"})
    assert all((game / p.name).is_symlink() and (game / p.name).resolve() == p for p in stock.iterdir())
    assert not (game / "d3d9.dll").is_symlink() and (game / "OROCHI_Z.exe").read_bytes() == b"OROCHI_Z.exe"
    written = sorted(p.relative_to(root).as_posix() for part in prep.OVERLAID for p in (root / part).rglob("*")
                     if p.is_symlink() or not p.is_dir())
    assert written == ["prefix/pfx/drive_c/Games/Musou Orochi Z", prep.SAVE]
    assert (root / "prefix/pfx/drive_c/Games/Musou Orochi Z").resolve() == game
    assert (root / prep.SAVE).read_bytes() == b"save"
    assert json.loads((root / "fixtures.json").read_text(encoding="utf-8"))["overlays"] == {
        part: str(template / part) for part in prep.OVERLAID}
    (install / "d3d9.dll").write_bytes(b"dll two")
    (install / "wozmod/table.bin").write_bytes(b"table 2")
    record(install)
    assert prep.recorded(prep.prepare("two", install, None, "s", cache)) == expected | {"d3d9.dll": digest(b"dll two"), "wozmod/table.bin": digest(b"table 2"),
                                 "wozmod/install.json": digest((install / "wozmod/install.json").read_bytes())}
    # a working table.bin swapped in: the run holds and lists it, the install keeps its own, and an unlisted name is refused
    (tmp_path / "work.bin").write_bytes(b"working table")
    root = prep.prepare("three", install, None, "s", cache, replace=[f"wozmod/table.bin={tmp_path / 'work.bin'}"])
    assert prep.recorded(root)["wozmod/table.bin"] == digest(b"working table") == digest((root / "game/wozmod/table.bin").read_bytes())
    assert (install / "wozmod/table.bin").read_bytes() == b"table 2"
    assert json.loads((root / "fixtures.json").read_text(encoding="utf-8"))["replaced"] == {"wozmod/table.bin": str(tmp_path / "work.bin")}
    with pytest.raises(ValueError, match="install record"):
        prep.prepare("four", install, None, "s", cache, replace=[f"wozmod/new.bin={tmp_path / 'work.bin'}"])
    # an entry the install lacks (an art change that edits one more stock entry) is added, listed and hashed
    root = prep.prepare("five", install, None, "s", cache, replace=[f"wozmod/entries/4102.bin={tmp_path / 'work.bin'}"])
    assert prep.recorded(root)["wozmod/entries/4102.bin"] == digest((root / "game/wozmod/entries/4102.bin").read_bytes())
    assert not (install / "wozmod/entries/4102.bin").exists()


def test_launch_check_hashes_only_mod_files_whose_stamp_moved_and_refuses_a_changed_or_missing_one(
        tools, tmp_path, monkeypatch):
    """Bug: the launch check trusts a rewritten mod file because a stamp exists, refuses a file only touched, or goes
    back to reading the whole multi-GB copy on every launch; or prepare_run.py stages a record it cannot read back."""
    prep, staged = tools.prepare_run, staging(tools, tmp_path, monkeypatch)
    root = prep.prepare("run", staged.install, None, "s", staged.cache)
    game = root / "game"
    monkeypatch.setattr(prep.hashlib, "file_digest", lambda *a: pytest.fail("an unchanged file was read"))
    prep.verify_copy(root, game)
    monkeypatch.undo()
    os.utime(game / "wozmod/table.bin")  # touched, same bytes
    prep.verify_copy(root, game)
    (game / "wozmod/table.bin").write_bytes(b"mud")
    with pytest.raises(ValueError, match="changed"):
        prep.verify_copy(root, game)
    (game / "wozmod/table.bin").unlink()
    with pytest.raises(ValueError, match="lost"):
        prep.verify_copy(root, game)


def test_tools_and_tests_read_the_main_checkouts_harness_toml_from_a_linked_worktree(monkeypatch, linked_worktree):
    """Bug: from a linked worktree, prepare_run.py reads (and stages under) the worktree's own work/, which a fresh
    worktree lacks, while the tests and the game slots use the main checkout's."""
    import runpy

    import checkout
    main, worktree = linked_worktree
    monkeypatch.setattr(checkout, "REPO", worktree)
    monkeypatch.delenv("WOZMOD_HARNESS_CONFIG", raising=False)
    tools_dir, tests_dir = Path(__file__).resolve().parents[1] / "tools", Path(__file__).resolve().parent
    stager, setup = runpy.run_path(str(tools_dir / "prepare_run.py")), runpy.run_path(str(tests_dir / "conftest.py"))
    assert (stager["CONFIG"], setup["CONFIG"], stager["WORK"]) == (main / "work/harness.toml",) * 2 + (main / "work",)


# The part of Proton's script that keep_identical patches: try_copy as Proton has it, reduced to its copy.
PROTON = """import os
import shutil


def try_copy(src, dst, prefix=None, add_write_perm=True, copy_metadata=False, optional=False,
             follow_symlinks=True, track_file=None, link_debug=False):
    if prefix is not None:
        dst = os.path.join(prefix, dst)
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    if os.path.lexists(dst):
        os.remove(dst)
    shutil.copyfile(src, dst)


def try_copyfile(src, dst):
    pass
"""


def pinned(template: Path) -> None:
    """A template as a refresh leaves it: launch.sh runs the pinned, patched Proton, which set the prefix up after
    Steam's last update, so config_info holds what Proton computes at launch."""
    import prepare_run
    proton, steam = template / "proton", template.parents[2] / "steam"
    (proton / "files/share/wine").mkdir(parents=True)
    (proton / "files/share/wine/wine.inf").write_text("[Version]\n", encoding="utf-8")
    (proton / "files/share/default_pfx").mkdir()
    (proton / "files/share/default_pfx/system.reg").write_text("WINE REGISTRY Version 2\n", encoding="utf-8")
    (steam / "legacycompat").mkdir(parents=True)
    for name in ("steamclient.dll", "steamclient64.dll", "Steam.dll"):
        (steam / "legacycompat" / name).write_bytes(b"MZ")
    (template / "launch.sh").write_text(f'PROTON="{proton}/proton"\nexit 0\n', encoding="utf-8")
    (proton / "proton").write_text(prepare_run.keep_identical(PROTON), encoding="utf-8")
    (template / "prefix/pfx").mkdir(parents=True, exist_ok=True)
    (template / "prefix/pfx/.update-timestamp").write_text(str(int((proton / "files/share/wine/wine.inf").stat().st_mtime)), encoding="utf-8")
    mtime = lambda path: str(os.path.getmtime(path))  # noqa: E731  Proton's getmtimestr
    (template / "prefix/config_info").write_text("\n".join([
        "CachyOS-11.0-100", f"{proton}/files/share/fonts/", f"{proton}/files/lib/", str(steam),
        *(mtime(steam / "legacycompat" / n) for n in ("steamclient.dll", "steamclient64.dll", "Steam.dll")),
        f"{proton}/files/share/default_pfx/", mtime(proton / "files/share/default_pfx/system.reg"), "False", "True"]), encoding="utf-8")


@pytest.mark.parametrize("drift", ["self-updating launcher", "proton updated", "set up by another proton",
                                   "steam updated", "unpatched proton"])
def test_template_a_launch_would_rebuild_is_refused(tools, tmp_path, monkeypatch, drift):
    """Bugs: the launcher runs a Proton that updates itself, the prefix was set up by another Proton build, or Steam
    updated its client DLLs since the prefix was set up, so every launch rewrites about 300 MB of Wine DLLs into the
    run's own overlay (the 100+ GB work/ held in 2026-10; ~210 GB of runs by 2026-11-15 after a Steam update)."""
    work, prep = tmp_path / "work", tools.prepare_run
    template = work / "runtime-tests/_template"
    pinned(template)
    monkeypatch.setattr(prep, "WORK", work)
    if drift == "self-updating launcher":
        (template / "launch.sh").write_text('PROTON="$STEAM_ROOT/compatibilitytools.d/Proton-CachyOS Latest/proton"\n', encoding="utf-8")
    elif drift == "proton updated":  # the pinned copy replaced in place by a newer build
        inf = template / "proton/files/share/wine/wine.inf"
        os.utime(inf, (inf.stat().st_atime, inf.stat().st_mtime + 86400))
    elif drift == "set up by another proton":
        info = template / "prefix/config_info"
        info.write_text(info.read_text(encoding="utf-8").replace(str(template / "proton"), "/elsewhere/Proton-CachyOS Latest"), encoding="utf-8")
    elif drift == "steam updated":  # Steam's client update replaces steamclient.dll, whose mtime Proton keys on
        dll = tmp_path / "steam/legacycompat/steamclient.dll"
        os.utime(dll, (dll.stat().st_atime, dll.stat().st_mtime + 86400))
    else:  # a new pinned copy, not yet patched to keep identical DLLs
        (template / "proton/proton").write_text(PROTON, encoding="utf-8")
    with pytest.raises(ValueError, match="pinned Proton|another Proton build|recopies unchanged"):
        prep.prepare("run", tmp_path / "install", None, "s")
    assert not (work / "runtime-tests/run").exists()


def test_patched_proton_keeps_identical_dlls_and_replaces_changed_ones(tools, tmp_path):
    """Bugs: the patched try_copy recopies a DLL the prefix already holds byte for byte (about 120 MB into every run's
    overlay), keeps one whose bytes differ (a run gets a stale DXVK after a Proton update), or patching twice wraps
    it twice."""
    patched = tools.prepare_run.keep_identical(PROTON)
    assert tools.prepare_run.keep_identical(patched) == patched
    proton = {}
    exec(compile(patched, "proton", "exec"), proton)
    source, prefix = tmp_path / "dist", tmp_path / "prefix/drive_c/windows/system32"
    source.mkdir()
    prefix.mkdir(parents=True)
    for name, held in (("same.dll", b"MZ same"), ("changed.dll", b"MZ old")):
        (source / name).write_bytes(b"MZ same" if name == "same.dll" else b"MZ new")
        (prefix / name).write_bytes(held)
    kept = (prefix / "same.dll").stat().st_ino
    for name in ("same.dll", "changed.dll"):
        proton["try_copy"](str(source / name), "drive_c/windows/system32", prefix=str(tmp_path / "prefix"))
    assert (prefix / "same.dll").stat().st_ino == kept
    assert (prefix / "changed.dll").read_bytes() == b"MZ new"


def test_a_proton_whose_try_copy_changed_is_not_patched(tools):
    """Bug: a newer Proton's try_copy takes other arguments, and the wrapper, patched in blind, breaks every launch."""
    with pytest.raises(ValueError, match="try_copy changed"):
        tools.prepare_run.keep_identical(PROTON.replace("link_debug=False", "link_debug=False, sandbox=None"))


def record(install: Path) -> None:
    """The installer's record of every file under install, as `wozmod install` writes it."""
    files = {p.relative_to(install).as_posix(): digest(p.read_bytes())
             for p in [install / "d3d9.dll", *(install / "wozmod").rglob("*")] if p.is_file() and p.name != "install.json"}
    (install / "wozmod/install.json").write_text(json.dumps({"format": 1, "files": files}), encoding="utf-8")


ART = ("wozmod/entries/4059.bin", "wozmod/entries/4060.bin", "wozmod/glyphs.bin", "wozmod/hires.bin",
       "wozmod/prompts-playstation.bin", "wozmod/prompts-xbox.bin")


def test_an_art_build_swaps_exactly_the_installs_menu_art(tools, tmp_path, monkeypatch):
    """Bugs: a file of the art folder that is not the install's menu art (a review file, a table.bin) is staged, or a
    partial art build (one built without --hires) goes in beside the install's older art, so a run mixes two builds;
    or a swapped file is staged with other bytes than the build's."""
    prep, staged = tools.prepare_run, staging(tools, tmp_path, monkeypatch)
    for name in ART:
        (staged.install / name).write_bytes(b"installed " + name.encode())
    record(staged.install)
    folders = {"whole build": ART, "a review file": (*ART, "wozmod/review/notes.json"),
               "a text table": (*ART, "wozmod/table.bin"), "no hires.bin": ART[:3] + ART[4:]}
    outcomes = {}
    for case, names in folders.items():
        folder = tmp_path / "art" / case.replace(" ", "-")
        for name in names:
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(b"built " + name.encode())
        try:
            root = prep.prepare(case.replace(" ", "-"), staged.install, None, "s", staged.cache, art=folder)
        except ValueError:
            outcomes[case] = "refused"
            continue
        game = {name: (root / "game" / name).read_bytes() for name in (*ART, "wozmod/table.bin")}
        outcomes[case] = (game == {**{name: b"built " + name.encode() for name in ART}, "wozmod/table.bin": b"table"}
                          and all(prep.recorded(root)[name] == digest(data) for name, data in game.items()))
    assert outcomes == {"whole build": True, "a review file": "refused", "a text table": "refused",
                        "no hires.bin": "refused"}


def test_run_starts_from_a_fixture_a_save_file_or_no_save(tools, tmp_path, monkeypatch):
    """Bugs: a save.dat path is looked up only as a fixture name, or staged with other bytes; a fresh-profile run
    (--save none) gets a save anyway, or silently sees the template prefix's save through its overlay."""
    prep, staged = tools.prepare_run, staging(tools, tmp_path, monkeypatch)
    (tmp_path / "edited.dat").write_bytes(b"edited save")
    root = prep.prepare("file", staged.install, None, str(tmp_path / "edited.dat"), staged.cache)
    assert (root / prep.SAVE).read_bytes() == b"edited save"
    assert json.loads((root / "fixtures.json").read_text(encoding="utf-8"))["save"] == {
        "name": str(tmp_path / "edited.dat"), "sha256": digest(b"edited save"), "key_configs": []}
    root = prep.prepare("fresh", staged.install, None, "none", staged.cache)
    assert not (root / prep.SAVE).exists() and (root / prep.SAVE).parent.is_dir()
    assert json.loads((root / "fixtures.json").read_text(encoding="utf-8"))["save"] == {"name": "none", "sha256": None,
                                                                        "key_configs": []}
    (staged.template / prep.SAVE).parent.mkdir(parents=True)
    (staged.template / prep.SAVE).write_bytes(b"template save")
    with pytest.raises(ValueError, match="template prefix holds"):
        prep.prepare("shadowed", staged.install, None, "none", staged.cache)


def test_fixture_key_config_lands_where_the_game_reads_it(tools, tmp_path, monkeypatch):
    """Bugs: a fixture's inputmap1.dat is staged beside save.dat in Savedata/, where the game never reads it, so the
    run keeps the template's map with no pad; or a Key Config edited after export is staged unchecked."""
    prep, staged = tools.prepare_run, staging(tools, tmp_path, monkeypatch)
    fixture = prep.WORK / "saves/s"
    (fixture / "inputmap1.dat").write_bytes(b"pad map")
    meta = json.loads((fixture / "meta.json").read_text(encoding="utf-8"))
    (fixture / "meta.json").write_text(json.dumps({**meta, "inputmap_sha256": {"inputmap1.dat": digest(b"pad map")}}), encoding="utf-8")
    root = prep.prepare("mapped", staged.install, None, "s", staged.cache)
    profile = root / "prefix/pfx/drive_c/users/steamuser/Documents/KOEI/Musou OROCHI Z"
    assert (profile / "inputmap1.dat").read_bytes() == b"pad map"
    assert json.loads((root / "fixtures.json").read_text(encoding="utf-8"))["save"]["key_configs"] == ["inputmap1.dat"]
    (fixture / "inputmap1.dat").write_bytes(b"edited map")
    with pytest.raises(ValueError, match="changed since export"):
        prep.prepare("edited-map", staged.install, None, "s", staged.cache)


@pytest.mark.parametrize("change", ["leftover", "edited", "missing"])
def test_install_that_differs_from_its_installer_record_is_refused(tools, tmp_path, change):
    """Bugs: a run stages a file the installer never wrote (a settings.ini or runtime.log from an earlier launch),
    a hand-edited table.bin, or an install missing a file, so harness evidence shows a build no player has."""
    install = tmp_path / "install"
    (install / "wozmod").mkdir(parents=True)
    (install / "d3d9.dll").write_bytes(b"dll")
    (install / "wozmod/table.bin").write_bytes(b"table")
    record(install)
    assert set(tools.prepare_run.installed_files(install, tmp_path / "cache.json")) == {"d3d9.dll", "wozmod/table.bin"}
    if change == "leftover":
        (install / "wozmod/settings.ini").write_text("[display]\nfps=120\n", encoding="utf-8")
    elif change == "edited":
        (install / "wozmod/table.bin").write_bytes(b"other")
    else:
        (install / "wozmod/table.bin").unlink()
    with pytest.raises(ValueError, match="table.bin|settings.ini"):
        tools.prepare_run.installed_files(install, tmp_path / "cache.json")


class FakeSupervisor:
    def __init__(self, root: Path, reason: str, game_exit: bool = False):
        (root / "slot.json").write_text("{}", encoding="utf-8")  # the supervisor took a game slot
        (root / "evidence").mkdir(parents=True)
        (root / "evidence/exit.json").write_text(json.dumps({"reason": reason}), encoding="utf-8")
        if game_exit:  # the game's own status, written after the harness recorded its end
            (root / "evidence/game-exit.json").write_text(json.dumps({"returncode": 0}), encoding="utf-8")


def args(tmp_path, spec, only=None):
    import game_harness
    (tmp_path / "d3d9.dll").write_bytes(b"MZ" + game_harness.TEST_MAGIC)  # a --test build
    return SimpleNamespace(spec=spec, install=tmp_path, dll=None, save="s", setting=[], file=[], art=None, pad=None, only=only,
                           name="check")


@pytest.fixture
def spec(tmp_path):
    path = tmp_path / "spec.toml"
    path.write_text('[[screen]]\nname = "a"\nroute = "menu"\n[[screen.assert]]\nkind = "no_cjk"\n', encoding="utf-8")
    return path


def drive_with(tools, tmp_path, monkeypatch, spec, runs, check=0, only=None, game_exit=False):
    """run_check over scripted runs, each its exit reason. Returns (exit code, run directories staged)."""
    staged = []
    monkeypatch.setattr(tools.prepare_run, "WORK", tmp_path / "work")
    monkeypatch.setattr(tools.run_check, "stop", lambda root, proc: None)
    monkeypatch.setattr(tools.run_check.harness, "check", lambda root, spec, only: check)

    def stage(name, install, dll, save, settings=(), replace=(), art=None):
        staged.append(tmp_path / "work/runtime-tests" / name)
        staged[-1].mkdir(parents=True)
        return staged[-1]

    def start(root, pad=False, **_):
        FakeSupervisor(root, runs[len(staged) - 1], game_exit)
        return SimpleNamespace()

    return tools.run_check.run_check(args(tmp_path, spec, only), stage, start), staged


@pytest.mark.parametrize("second_run_starts", [True, False], ids=["second-run-starts", "never-starts"])
def test_a_run_that_ends_before_the_game_starts_is_retried_twice_in_fresh_run_directories(
        tools, tmp_path, monkeypatch, spec, second_run_starts):
    """Bugs: a pulse start failure (reason unknown, no game) fails the whole check instead of staging a new run; the
    retry loop runs forever, or exits 0 after a game that never started."""
    retries = tools.run_check.RETRIES
    ups = iter([False, True] if second_run_starts else [False] * (retries + 1))
    monkeypatch.setattr(tools.run_check, "wait_up", lambda root, proc: next(ups))
    ends = ["unknown", "stopped"] if second_run_starts else ["unknown"] * (retries + 1)
    code, staged = drive_with(tools, tmp_path, monkeypatch, spec, ends)
    assert (code, len(staged)) == ((0, 2) if second_run_starts else (1, retries + 1))
    assert len(set(staged)) == len(staged) and staged[1].name.endswith("-r1")


def test_run_is_never_retried_once_the_game_started(tools, tmp_path, monkeypatch, spec):
    """Bug: a game that crashed before its first frame, or drew a frame and then ended 'unknown' (compositor lost), is
    staged again and hides the failure behind a second run."""
    monkeypatch.setattr(tools.run_check, "wait_up", lambda root, proc: False)
    code, staged = drive_with(tools, tmp_path, monkeypatch, spec, ["crash"] * 3)
    assert code == 1 and len(staged) == 1
    code, staged = drive_with(tools, tmp_path, monkeypatch, spec, ["unknown"] * 3, game_exit=True)
    assert code == 1 and len(staged) == 1  # the compositor ended first, but the game had started
    monkeypatch.setattr(tools.run_check, "wait_up", lambda root, proc: True)
    code, staged = drive_with(tools, tmp_path, monkeypatch, spec, ["unknown"] * 3)
    assert code == 1 and len(staged) == 1  # the game answered a frame: its end is not a start failure


@pytest.mark.parametrize("routed, stopped", [(0, False), (1, True)])
def test_run_routed_with_to_stays_up_unless_the_route_failed(tools, tmp_path, monkeypatch, spec, routed, stopped):
    """Bugs: the game a --to run routed is stopped when run_check exits, so the hand commands that follow find no game;
    a failed route leaves its game holding a slot."""
    monkeypatch.setattr(tools.prepare_run, "WORK", tmp_path / "work")
    monkeypatch.setattr(tools.run_check, "wait_up", lambda root, proc: True)
    monkeypatch.setattr(tools.run_check.harness, "route", lambda root, screen, stage, movie=None, officers=None: routed)
    stops = []
    monkeypatch.setattr(tools.run_check, "stop", lambda root, proc: stops.append(root))

    def stage(name, install, dll, save, settings=(), replace=(), art=None):
        (tmp_path / "work/runtime-tests" / name).mkdir(parents=True)
        return tmp_path / "work/runtime-tests" / name

    def start(root, pad=False, **_):
        FakeSupervisor(root, "stopped")
        return SimpleNamespace()

    options = SimpleNamespace(**vars(args(tmp_path, spec)), to="title", stage=None)
    assert tools.run_check.run_check(options, stage, start) == routed
    assert bool(stops) == stopped


def test_release_dll_given_with_dll_is_refused_before_staging(tools, tmp_path, monkeypatch, spec):
    """Bug: a release d3d9.dll, which has no test mailbox, is staged and booted, and the run waits out its five-minute
    start limit before failing."""
    options = args(tmp_path, spec)
    options.dll = tmp_path / "release.dll"
    options.dll.write_bytes(b"MZ release build")
    staged = []
    assert tools.run_check.run_check(options, lambda *a, **k: staged.append(a), None) == 1
    assert staged == []


def test_installs_release_dll_is_replaced_by_a_test_build_of_the_checkout(tools, tmp_path, monkeypatch, spec):
    """Bug: with no --dll, the install's release d3d9.dll is staged (no route or check can answer), or the test build
    is made but the install's DLL is what goes into the run."""
    import game_harness
    monkeypatch.setattr(tools.prepare_run, "WORK", tmp_path / "work")
    options = args(tmp_path, spec)
    (tmp_path / "d3d9.dll").write_bytes(b"MZ release build")

    def build(install, into):
        assert install == tmp_path
        (into / "d3d9.dll").write_bytes(b"MZ fresh" + game_harness.TEST_MAGIC)
        return into / "d3d9.dll"

    staged = []

    def stage(name, install, dll, save, settings=(), replace=(), art=None):
        staged.append(dll.read_bytes())
        raise ValueError("staged")

    with pytest.raises(ValueError, match="staged"):
        tools.run_check.run_check(options, stage, None, build)
    assert staged[-1] == b"MZ fresh" + game_harness.TEST_MAGIC


def test_misspelt_only_name_fails_before_any_staging(tools, tmp_path, monkeypatch, spec):
    """Bug: the typo is found after a minute of staging and boot (or not at all)."""
    code, staged = drive_with(tools, tmp_path, monkeypatch, spec, [], only="a,zz")
    assert code == 1 and staged == []


def test_settings_land_where_the_runtime_reads_them(tools):
    """Bugs: a setting is written under the wrong section or key, or a key is renamed in the C, so the runtime's
    GetPrivateProfile read falls back to its default and a "120 FPS" run plays at 60."""
    import configparser
    import re
    prep = tools.prepare_run
    read = set(re.findall(r'GetPrivateProfile(?:String|Int)W\(L"(\w+)", L"(\w+)"',
                          (prep.REPO / "runtime/english_runtime.c").read_text(encoding="utf-8")))
    ini = configparser.ConfigParser()
    ini.read_string(prep.settings_ini([f"{key}={(allowed or ('70',))[0]}" for key, allowed in prep.SETTINGS.items()]))
    assert {(section, key) for section in ini.sections() for key in ini[section]} == read


def test_contact_sheet_shows_each_screens_capture_beside_its_own_rows(tmp_path, monkeypatch):
    # Bug: rows grouped under the wrong screen, or a capture shown for a screen that was never captured,
    # makes a FAIL look like it belongs to a screen that passed.
    import contact_sheet
    from PIL import Image

    Image.new("RGB", (8, 4), "red").save(tmp_path / "a.png")
    report = {"spec": "layout.toml", "rows": [
        {"screen": "a", "assertion": "line_count", "ok": True, "numbers": {}, "capture": None},
        {"screen": "b", "assertion": "fits", "ok": False, "numbers": {"over": 3}, "capture": None},
        {"screen": "a", "assertion": "ink", "ok": True, "numbers": {}, "capture": str(tmp_path / "a.png")},
    ]}
    (tmp_path / "check-report.json").write_text(json.dumps(report), encoding="utf-8")
    page = contact_sheet.write(tmp_path).read_text(encoding="utf-8")

    a, b = page.split("<section>")[1:]
    assert "<h2>a</h2><img" in a and a.count("<li class=PASS>") == 2 and "FAIL" not in a
    assert "<h2>b</h2><p class=none>" in b and "<li class=FAIL><b>FAIL</b> fits {&quot;over&quot;: 3}" in b
    assert "2 screens, 3 rows, 1 failed" in page


def test_wait_up_keeps_waiting_while_the_run_starts_but_fails_at_once_on_a_symlinked_run_path(tools, tmp_path):
    # Bug: every ValueError counted as "not ready", so a bad run path waited out the whole boot timeout.
    running = SimpleNamespace(poll=lambda: None)
    root = tmp_path / "run"
    (root / "control").mkdir(parents=True)
    (root / "slot.json").write_text("{}", encoding="utf-8")
    began = time.monotonic()
    assert tools.run_check.wait_up(root, running, 1) is False
    assert time.monotonic() - began >= 1
    (tmp_path / "link").symlink_to(root)
    with pytest.raises(ValueError, match="symlinks"):
        tools.run_check.wait_up(tmp_path / "link", running, 2)


@pytest.mark.parametrize("video", [True, False])
def test_video_reaches_the_supervisor(tools, tmp_path, monkeypatch, spec, video):
    """Bug: run_check --video is accepted but not passed to `game_harness.py run`, so the game outputs 4K through FSR
    and a recording halves its frame rate."""
    commands = []
    monkeypatch.setattr(tools.prepare_run, "WORK", tmp_path / "work")
    monkeypatch.setattr(tools.run_check.subprocess, "Popen", lambda command, **_: commands.append(command))
    monkeypatch.setattr(tools.run_check, "wait_up", lambda root, proc: True)
    monkeypatch.setattr(tools.run_check.harness, "route", lambda root, screen, stage, movie=None, officers=None: 0)

    def stage(name, install, dll, save, settings=(), replace=(), art=None):
        (tmp_path / "work/runtime-tests" / name).mkdir(parents=True)
        return tmp_path / "work/runtime-tests" / name

    options = SimpleNamespace(**vars(args(tmp_path, spec)), to="title", stage=None, video=video)
    assert tools.run_check.run_check(options, stage) == 0
    assert ["--video" in command for command in commands] == [video]


def test_boot_clock_starts_once_the_run_holds_a_slot_and_the_wait_names_the_holders(tools, tmp_path, monkeypatch,
                                                                                    capsys):
    """Bugs: the wait for a busy slot counts against the boot limit, so a launch behind another game fails as never
    started; or the waiting launch cannot say who holds the slots (a free one listed, or a holder left out)."""
    monkeypatch.setattr(tools.run_check.harness, "work", lambda: tmp_path / "work")
    monkeypatch.setattr(tools.run_check.harness.machine_lock, "work", lambda: tmp_path / "work")  # the slots' files
    (tmp_path / "work").mkdir()
    held = tools.run_check.harness.take_slot("their-run")[0]
    assert tools.run_check.harness.slot_holders() == ["their-run (0 min)"]
    root = tmp_path / "work/runtime-tests/r1"
    (root / "control").mkdir(parents=True)
    threading.Timer(1.2, lambda: (root / "slot.json").write_text("{}", encoding="utf-8")).start()
    began = time.monotonic()
    assert tools.run_check.wait_up(root, SimpleNamespace(poll=lambda: None), 0.5) is False
    os.close(held)
    assert time.monotonic() - began >= 1.7  # 1.2 s for the slot, then the whole 0.5 s boot limit
    assert tools.run_check.harness.slot_holders() == []


def test_a_launch_waiting_behind_a_heavy_run_names_the_machine_lock_and_who_is_ahead(tools, tmp_path, monkeypatch,
                                                                                     capsys):
    """Bugs: a launch queued for the machine (behind a benchmark, or the first-come line) reports that it waits for a
    game slot "held by nobody now"; or it counts shared runs ahead of it, which it would go in beside, as waits."""
    import subprocess
    import sys

    lock = tools.run_check.harness.machine_lock
    monkeypatch.setattr(lock, "work", lambda: tmp_path / "work")
    monkeypatch.delenv(lock.HELD, raising=False)
    (tmp_path / "work").mkdir()
    heavy = lock.claim("bench", 0.01)
    shared, install = [], []
    threading.Thread(target=lambda: shared.append(lock.share(0.01, "a test run")), daemon=True).start()
    time.sleep(0.2)
    threading.Thread(target=lambda: install.append(lock.claim("install", 0.01)), daemon=True).start()
    time.sleep(0.2)
    launch = subprocess.Popen([sys.executable, "-c", (  # the game harness's run(): in line for the machine
        f"import sys, time; sys.path.insert(0, {str(Path(lock.__file__).parent)!r}); import machine_lock; "
        f"machine_lock.work = lambda: __import__('pathlib').Path({str(tmp_path / 'work')!r}); "
        "machine_lock.share(0.01); time.sleep(30)")], env={k: v for k, v in os.environ.items() if k != lock.HELD})
    try:
        root = tmp_path / "work/runtime-tests/r1"
        root.mkdir(parents=True)
        assert tools.run_check.wait_slot(root, launch, 1.5, 0.5) is False
    finally:
        launch.kill()
        launch.wait()
        os.close(heavy)
        for waiter in (shared, install):  # each goes in once the one before it leaves
            deadline = time.monotonic() + 5
            while not waiter and time.monotonic() < deadline:
                time.sleep(0.01)
            os.close(waiter[0])
    said = [line for line in capsys.readouterr().err.splitlines() if line.startswith("run_check:")]
    assert said and set(said) == {"run_check: waiting for the machine, held by bench (0 min); 1 run(s) wait ahead"}


def test_ab_runs_a_on_the_install_as_it_stands_and_b_with_the_swaps_and_exits_on_b(tools, tmp_path, monkeypatch):
    """Bugs: a swap leaks into run A, so both sides show the working build; B is staged without its swaps; A's FAIL
    rows (the state the swap fixes) fail the A/B run."""
    spec = tmp_path / "spec.toml"
    spec.write_text('[[screen]]\nname = "s"\nroute = "menu"\n[[screen.assert]]\nkind = "no_cjk"\n', encoding="utf-8")
    seen = {}

    def run(args, stage):
        root = stage(args.name)
        (root / "evidence").mkdir(parents=True)
        (root / "evidence/check-report.json").write_text(json.dumps({"spec": str(spec), "rows": [
            {"screen": "s", "assertion": "no_cjk", "ok": args.name.endswith("-b"), "numbers": {}, "capture": None}]}), encoding="utf-8")
        seen[args.name] = (args.dll, args.file, args.art)
        return 0 if args.name.endswith("-b") else 1

    dll, file = tmp_path / "test.dll", ["wozmod/table.bin=new.bin"]
    args = SimpleNamespace(spec=spec, name="x", dll=dll, file=file, art=tmp_path / "art", to=None, ab="install",
                           setting=[])
    code = tools.run_check.ab(args, run, stage=lambda name: tmp_path / name)
    assert code == 0
    assert seen == {"x-a": (None, [], None), "x-b": (dll, file, tmp_path / "art")}
    assert (tmp_path / "x-b/evidence/ab-sheet.html").is_file()


def test_ab_stock_runs_a_as_the_stock_game_whatever_language_the_caller_set(tools, tmp_path):
    """Bug: --ab stock stages A with the mod's art still served, so A still draws English lettering from hires.bin
    over the stock texture (runtime/english_runtime.c:2800), or a caller's language.text=english is read first.
    The runtime reads the first [language] text= of settings.ini; japanese turns off its hires rows and entries."""
    spec = tmp_path / "spec.toml"
    spec.write_text('[[screen]]\nname = "s"\nroute = "menu"\n', encoding="utf-8")
    texts = {}

    def run(args, stage):
        ini = tools.prepare_run.settings_ini(args.setting)
        texts[args.name[-1]] = next((line.split("=")[1] for line in ini.splitlines() if line.startswith("text=")), None)
        return 0

    args = SimpleNamespace(spec=spec, name="x", dll=None, file=["wozmod/entries/1.bin=n.bin"], art=None, to=None,
                           ab="stock", setting=["language.text=english", "display.fps=120"])
    tools.run_check.ab(args, run, stage=lambda name: tmp_path / name)
    assert texts == {"a": "japanese", "b": "english"}


def test_ab_sheet_pairs_each_screens_a_and_b_captures_by_name_cropped_in_layout_px(tmp_path):
    """Bugs: the A and B images trade places; pairs are matched by position, so a screen run A never reached shifts
    every later pair; the crop box is read as capture px on a 2x capture and shows a quarter of the region."""
    import base64
    import io

    import contact_sheet
    from PIL import Image

    def capture(path: Path, corner: str) -> str:
        image = Image.new("RGB", (2560, 1440), "red")  # a 2x capture of the 1280x720 layout
        image.paste(corner, (0, 0, 1280, 720))  # the layout's top-left quarter
        image.save(path)
        return str(path)

    def report(folder: Path, captures: dict[str, str]) -> Path:
        folder.mkdir()
        rows = [{"screen": n, "assertion": "ink", "ok": True, "numbers": {}, "capture": c} for n, c in captures.items()]
        (folder / "check-report.json").write_text(json.dumps({"spec": "layout.toml", "rows": rows}), encoding="utf-8")
        return folder

    a = report(tmp_path / "a", {"late": capture(tmp_path / "a.png", "green")})
    b = report(tmp_path / "b", {"early": capture(tmp_path / "b1.png", "blue"), "late": capture(tmp_path / "b2.png", "blue")})
    page = contact_sheet.write_ab(a, b, {"late": [0, 0, 640, 360]}).read_text(encoding="utf-8")

    def images(section: str) -> list[tuple[tuple[int, int], tuple[int, int, int]]]:
        found = []
        for data in section.split("src='data:image/jpeg;base64,")[1:]:
            image = Image.open(io.BytesIO(base64.b64decode(data.split("'")[0])))
            found.append((image.size, image.getpixel((image.width // 2, image.height // 2))))
        return found

    def near(pixel, colour) -> bool:
        return all(abs(p - c) < 40 for p, c in zip(pixel, colour, strict=True))

    early, late = page.split("<section>")[1:]
    assert "<h2>early</h2>" in early and "this run did not reach the screen" in early.split("<figure>")[1]
    [(size, pixel)] = images(early)  # B only, uncropped: the centre is outside the corner
    assert size == (960, 540) and near(pixel, (255, 0, 0))
    (a_size, a_pixel), (b_size, b_pixel) = images(late)  # the crop: the corner alone, A before B
    assert a_size == b_size == (960, 540) and near(a_pixel, (0, 128, 0)) and near(b_pixel, (0, 0, 255))


def test_entries_dir_swaps_each_rebuilt_entry_over_the_installed_one(tools, tmp_path, monkeypatch):
    """Bug: a rebuilt menu-art entry goes in under the wrong installed path, so the run quietly shows the old art."""
    built = tmp_path / "entries"
    built.mkdir()
    for ordinal in (4074, 4104):
        (built / f"{ordinal}.bin").write_bytes(b"x")
    seen = []
    monkeypatch.setattr(tools.run_check, "run_check", lambda args: seen.append(args.file) or 0)
    assert tools.run_check.main(["--install", str(tmp_path), "--entries-dir", str(built)]) == 0
    assert seen == [[f"wozmod/entries/{n}.bin={built / f'{n}.bin'}" for n in (4074, 4104)]]
