"""An install over an earlier one keeps each step and movie whose key is the recorded one and whose files are intact.

Through the installer's real install() and _build, with fake disc reads and steps that record each run in the game
folder; the keys are fixed (tests/test_step_keys.py covers how they are made).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import wozmod.install as installer
from synthetic import fake_step_keys, stock_game
from wozmod import movies, remembered
from wozmod.donors import Donor, DonorFile
from wozmod.install import STEPS, step_keys
from wozmod.progress import Cancelled, Progress

MOVIES = {"voice/movie001.wmv": b"first movie", "voice/movie002.wmv": b"second movie"}


def _fake_text(game, donors, out):
    _ran(game, "text")
    (out / "wozmod").mkdir(parents=True, exist_ok=True)
    (out / "wozmod/table.bin").write_bytes(b"table from " + (donors / "wo1-x360/linkdata_fns/369.bin").read_bytes())


def _fake_voices(game, donors, out, jobs):
    _ran(game, "voices")
    (out / "wozmod/voice").mkdir(parents=True, exist_ok=True)
    for name in ("Voice.bin", "Lips.bin", "SE.bin"):
        (out / "wozmod/voice" / name).write_bytes(name.encode() + b" from " + (donors / "wo2-x360/link_vodat.bdx").read_bytes())


def _fake_art(game, donors, lettering, staging, out, jobs):
    _ran(game, "art")
    (out / "wozmod/hires").mkdir(parents=True, exist_ok=True)
    (out / "wozmod/hires/hires.bin").write_bytes(b"art from " + Path(lettering).read_bytes())


def _ran(game, step):
    with open(game / "runs.txt", "a", encoding="utf-8") as runs:
        runs.write(step + "\n")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """(game folder, inputs, what each install did: {"reads", "programs", "rebuilt"}, the step keys to edit)."""
    game = tmp_path / "game"
    stock_game(game)
    (tmp_path / "lettering.zip").write_bytes(b"bundle")
    (tmp_path / "d3d9.dll").write_bytes(b"dll")
    inputs = {**{flag: tmp_path / flag for flag in ("wo1-x360", "wo2-x360", "wo2-psp", "x360-tool", "psp-tool")},
              "lettering": tmp_path / "lettering.zip", "dll": tmp_path / "d3d9.dll"}
    seen = {"reads": [], "programs": [], "rebuilt": []}

    def extract(flag, image, donor, destination, files, *rest, **options):
        movie = destination.name == "movie"
        seen["reads"] += [f"movie {name}" if movie else f"{flag}/{name}" for name in files]
        for name in files:
            (destination / name).parent.mkdir(parents=True, exist_ok=True)
            (destination / name).write_bytes(MOVIES[name] if movie else b"donor bytes")

    monkeypatch.setattr(movies, "movie_donors", lambda: {"wo2-x360": Donor("wo2-x360", "xdvdfs", tuple(
        DonorFile(path, path.rsplit("/", 1)[1], len(data), hashlib.sha256(data).hexdigest()) for path, data in MOVIES.items()))})
    monkeypatch.setattr(installer, "_extract", extract)
    monkeypatch.setattr(installer, "_place_programs", lambda inputs, donors: seen["programs"].append(True))
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs, log=None, rebuilt=None: seen["rebuilt"].append(rebuilt) or [])
    monkeypatch.setattr(remembered, "settings_path", lambda: tmp_path / "config/settings.json")
    for name, fake in (("_text_table", _fake_text), ("_voice_banks", _fake_voices), ("_menu_art", _fake_art)):
        monkeypatch.setattr(installer, name, fake)
    movie_keys = step_keys.movie_keys  # the real ones, from the movies above
    keys = fake_step_keys(monkeypatch)
    monkeypatch.setattr(step_keys, "movie_keys", movie_keys)
    return game, inputs, seen, keys


def _install(game, inputs, seen, progress=None):
    for value in seen.values():
        value.clear()
    (game / "runs.txt").unlink(missing_ok=True)
    installer.install(game, inputs, log=lambda _: None, progress=progress)
    runs = sorted((game / "runs.txt").read_text(encoding="utf-8").split()) if (game / "runs.txt").exists() else []
    (game / "runs.txt").unlink(missing_ok=True)
    return runs


def _installed(game: Path) -> dict[str, tuple[int, bytes]]:
    """Each installed file with its inode and bytes."""
    return {p.relative_to(game).as_posix(): (p.stat().st_ino, p.read_bytes())
            for p in sorted([game / "d3d9.dll", *(game / "wozmod").rglob("*")]) if p.is_file() and p.name != "install.json"}


def _record(game: Path) -> dict:
    return json.loads((game / "wozmod/install.json").read_text(encoding="utf-8"))


def test_a_reinstall_with_the_same_keys_builds_reads_and_moves_nothing(setup):
    """Bug: an update rebuilds a step whose key it already holds, reads the discs or recopies the movies anyway, or
    moves a kept file (each costing its size in new disk blocks)."""
    game, inputs, seen, _ = setup
    assert _install(game, inputs, seen) == ["art", "text", "voices"]
    before, record = _installed(game), _record(game)
    assert _install(game, inputs, seen) == []
    assert (seen["reads"], seen["programs"], seen["rebuilt"]) == ([], [], [()])
    assert _installed(game) == before
    assert _record(game) == record


def test_a_changed_key_builds_only_its_step_from_only_the_disc_files_it_reads(setup):
    """Bug: one changed step rebuilds the others, reads the disc files of steps it keeps, decrypts the programs for a
    step that does not read them, or checks the game folder as if every step were rebuilt (row 31)."""
    game, inputs, seen, keys = setup
    _install(game, inputs, seen)
    wrong = []
    for step in STEPS:
        before = _installed(game)
        keys[step] = {"key": f"another {step} key", "parts": {"mod": "another"}}
        runs = _install(game, inputs, seen)
        expected_reads = sorted(f"{disc}/{name}" for disc, names in step_keys.disc_reads(step).items() for name in names)
        after = _installed(game)
        moved = sorted(name for name in before if after[name][0] != before[name][0] and step not in _owner(name))
        if (runs, sorted(seen["reads"]), bool(seen["programs"]), seen["rebuilt"], moved) != (
                [step], expected_reads, step in step_keys.PROGRAMS, [(step,)], []):
            wrong.append(f"{step}: ran {runs}, read {len(seen['reads'])} files, programs {seen['programs']}, "
                         f"checked for {seen['rebuilt']}, moved {moved}")
        if _record(game)["steps"][step]["key"] != f"another {step} key":
            wrong.append(f"{step}: the record keeps the old key")
    assert wrong == []
    log = (game.parent / "config/install.log").read_text(encoding="utf-8")
    assert "art: built: changed since the last install: the mod's code or data" in log and "text: kept: 1 files" in log


def _owner(name: str) -> str:
    return "text" if name == "wozmod/table.bin" else "voices" if "/voice/" in name else "art" if "hires" in name else ""


def test_one_changed_byte_in_a_kept_file_rebuilds_its_step(setup):
    """Bug: the update trusts the record without hashing the kept files, so a voice bank the player's disk or another
    tool damaged stays installed."""
    game, inputs, seen, _ = setup
    _install(game, inputs, seen)
    lips = game / "wozmod/voice/Lips.bin"
    good = lips.read_bytes()
    with lips.open("r+b") as stream:
        stream.write(b"X")
    assert _install(game, inputs, seen) == ["voices"]
    assert lips.read_bytes() == good
    assert installer.read_manifest(game)["wozmod/voice/Lips.bin"] == hashlib.sha256(good).hexdigest()


def _first_release(record: dict) -> dict:
    return {"format": 1, "files": record["files"]}


def _other_platform(record: dict) -> dict:
    return {**record, "platform": "plan9"}


def _claimed_twice(record: dict) -> dict:
    steps = dict(record["steps"])
    steps["art"] = {**steps["art"], "files": [*steps["art"]["files"], "wozmod/table.bin"]}
    return {**record, "steps": steps}


def _not_installed(record: dict) -> dict:
    steps = dict(record["steps"])
    steps["text"] = {**steps["text"], "files": [*steps["text"]["files"], "wozmod/elsewhere.bin"]}
    return {**record, "steps": steps}


def _no_key(record: dict) -> dict:
    steps = dict(record["steps"])
    steps["voices"] = {"files": steps["voices"]["files"]}
    return {**record, "steps": steps}


def test_a_record_that_cannot_vouch_for_its_steps_rebuilds_everything_and_still_uninstalls(setup):
    """Bug: a record from before step keys (format 1), from another platform, or whose steps claim a file twice, name
    one it does not list or lack a key, is trusted: a step is kept that this version cannot vouch for; or the
    record this version writes over it can no longer be uninstalled."""
    game, inputs, seen, _ = setup
    _install(game, inputs, seen)
    wrong = []
    for change in (_first_release, _other_platform, _claimed_twice, _not_installed, _no_key):
        (game / "wozmod/install.json").write_text(json.dumps(change(_record(game))), encoding="utf-8")
        runs = _install(game, inputs, seen)
        if runs != ["art", "text", "voices"] or len([r for r in seen["reads"] if r.startswith("movie")]) != len(MOVIES):
            wrong.append(f"{change.__name__}: ran {runs}, read {seen['reads']}")
    assert wrong == []
    installer.uninstall(game, keep_settings=False, log=lambda _: None)
    assert not (game / "wozmod").exists() and not (game / "d3d9.dll").exists()


def test_only_the_movie_whose_pin_changed_is_copied_again(setup, monkeypatch):
    """Bug: the movies are kept by their place in the list instead of their pin, so a replaced movie keeps the old
    file, or every movie is copied again for one change."""
    game, inputs, seen, _ = setup
    _install(game, inputs, seen)
    first = (game / "wozmod/movie/voice/movie001.wmv").stat().st_ino
    monkeypatch.setitem(MOVIES, "voice/movie002.wmv", b"a recut second movie")
    assert _install(game, inputs, seen) == []
    assert seen["reads"] == ["movie voice/movie002.wmv"]
    assert (game / "wozmod/movie/voice/movie002.wmv").read_bytes() == b"a recut second movie"
    assert (game / "wozmod/movie/voice/movie001.wmv").stat().st_ino == first


def test_a_cancelled_update_leaves_the_install_as_it_was(setup):
    """Bug: an update moves or removes the kept files before the new ones are ready, so a cancel leaves a broken
    install."""
    game, inputs, seen, keys = setup
    _install(game, inputs, seen)
    before, record = _installed(game), _record(game)
    keys["text"] = {"key": "another text key", "parts": {}}

    def cancel_when(progress):
        if progress.stages["text"].running:
            progress.cancel.set()

    with pytest.raises(Cancelled):
        _install(game, inputs, seen, Progress(cancel_when))
    assert _installed(game) == before and _record(game) == record
    assert not list(game.glob(".wozmod-build-*"))


def test_an_update_stops_only_for_changed_pieces_the_steps_it_rebuilds_read(tmp_path, monkeypatch):
    """Bug: an update that keeps the menu art still stops for an entry another mod changed that only the art reads
    (row 31), or the note about it is lost; or one that rebuilds the art lets it through."""
    record = stock_game(tmp_path, linkdata1={2: b"another mod's art " * 300})
    monkeypatch.setattr(installer.stock, "recorded", lambda: record)
    problem, verdict = installer._game_check(tmp_path, ("text", "voices"))
    assert problem is None
    assert verdict.kept_notes(("text", "voices")) == [
        "Note: LINKDATA1.BIN: another mod changed entry 2, which wozmod reads to build its menu art. wozmod kept the "
        "menu art it built before. Put back the original LINKDATA1.BIN before an update needs to build its menu art "
        "again."]
    assert installer._game_check(tmp_path, STEPS)[0].startswith("LINKDATA1.BIN: another mod changed entry 2")
