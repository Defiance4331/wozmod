"""The read gate (install/gate.py): a step reads only the stock pieces, donor files, bundled files, programs,
settings and modules its manifest declares; enforce raises, observe logs, record writes every read down.

The in-process cases watch synthetic reads over tmp_path folders laid out as an install lays them out (the build
folder, with the donors, inside the game folder). The worker cases run a fake step through steps.run, as an install
runs the real ones.
"""

from __future__ import annotations

import dataclasses
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

import fake_program
import wozmod.install as installer
from synthetic import stock_game
from wozmod import game_files, resources
from wozmod.install import gate, manifest, steps
from wozmod.install.manifest import Manifest
from wozmod.install.stock import Piece
from wozmod.progress import Progress
from wozmod.text.keys import stock_key

BOUND = os.environ  # bound before any step is watched, as a module that did `from os import environ` holds it


def _manifest(**changes) -> Manifest:
    return dataclasses.replace(Manifest(
        step="text", inputs=("game", "donors"), installer={}, modules=("wozmod",), bundled=("data/movies.json",),
        donors={"wo1-x360": ("linkdata_ens.idx",)}, programs=(), stock_stamp=(1, 2),
        stock=(Piece("OROCHI_Z.exe", 0, "aa", frozenset({"text"}), length=4),), environment=("WOZMOD_NATIVE",),
        starts=("ffmpeg",)), **changes)


@pytest.fixture
def folders(tmp_path):
    """The step's arguments, and a function from a name to the file a case reads."""
    game, build = tmp_path / "game", tmp_path / "game/.wozmod-build-x"
    files = {"game exe": game / "OROCHI_Z.exe", "game archive": game / "LINKDATA1.BIN",
             "beside the game folder": tmp_path / "game2/OROCHI_Z.exe",
             "donor member": build / "donors/wo1-x360/linkdata_ens.idx",
             "other donor member": build / "donors/wo1-x360/linkdata_ens.lnk",
             "its own output": build / "steps/text/wozmod/table.bin"}
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"bytes")
    files |= {"bundled": resources.root() / "data/movies.json", "other bundled": resources.root() / "data/stock_pieces.json"}
    arguments = {"game": game, "donors": build / "donors", "out": build / "steps/text"}
    ffmpeg = fake_program.program(tmp_path / "bin/ffmpeg", "")
    return arguments, files, ffmpeg


# Each read, and what the gate makes of it: (kind, item) when undeclared, None when declared or not checked.
CASES = {
    "game exe": ("game", "OROCHI_Z.exe"),  # opened around game_files, which serves the game's pieces
    "beside the game folder": None,
    "donor member": None,
    "other donor member": ("donor", "wo1-x360/linkdata_ens.lnk"),
    "its own output": None,  # in the build folder, inside the game folder
    "bundled": None,
    "other bundled": ("bundled", "data/stock_pieces.json"),
    "ffmpeg on a declared file": None,
    "ffmpeg on an undeclared file": ("game", "LINKDATA1.BIN"),
    "another program": ("program", Path(sys.executable).name.removesuffix(".exe")),
    "a declared setting": None,
    "an undeclared setting": ("setting", "WOZMOD_KNOB"),
    "an undeclared setting by os.getenv": ("setting", "WOZMOD_KNOB"),
    "an undeclared setting through os.environ bound earlier": ("setting", "WOZMOD_KNOB"),
}


def _do(case: str, files: dict, ffmpeg: Path) -> None:
    if case in files:
        open(files[case], "rb").close()
    elif case.startswith("ffmpeg"):
        subprocess.run([str(ffmpeg), str(files["game archive" if "undeclared" in case else "donor member"])], check=True)
    elif case == "another program":
        subprocess.run([sys.executable, "-c", "pass"], check=True)
    elif case == "a declared setting":
        os.environ.get("WOZMOD_NATIVE")
    elif case == "an undeclared setting":
        os.environ.get("WOZMOD_KNOB")
    elif case == "an undeclared setting by os.getenv":
        os.getenv("WOZMOD_KNOB")
    else:
        BOUND.get("WOZMOD_KNOB")


def test_an_undeclared_read_raises_in_enforce_and_is_logged_once_in_observe(folders, capfd):
    """Bug: the gate takes a folder for another (the build folder inside the game folder for the game, the donors
    folder for the game, a sibling folder whose name starts like the game folder's for it), misses a path in a program's
    arguments or a setting looked up through os.getenv or an os.environ bound earlier, or observe stops a player's
    install (or logs nothing), so a read the key leaves out goes unseen or a declared one is refused."""
    arguments, files, ffmpeg = folders
    raised, logged = {}, {}
    for case in CASES:
        try:
            with gate.watch("text", _manifest(), gate.ENFORCE, arguments):
                _do(case, files, ffmpeg)
        except gate.UndeclaredRead as error:
            raised[case] = (error.kind, error.item)
            assert str(error).startswith(f"the text step read {error.item} (") and "does not declare" in str(error)
        else:
            raised[case] = None
        capfd.readouterr()
        with gate.watch("text", _manifest(), gate.OBSERVE, arguments):
            _do(case, files, ffmpeg)
            _do(case, files, ffmpeg)  # one line per undeclared item
        logged[case] = [line for line in capfd.readouterr().err.splitlines() if line.startswith(gate.LINE)]
    assert raised == CASES
    expected = {case: [f"{gate.LINE}{gate.UndeclaredRead('text', *found)}"] if found else [] for case, found in CASES.items()}
    assert logged == expected


def test_a_read_the_steps_code_catches_still_stops_it_at_the_end(folders):
    """Bug: step code that catches every error (a fallback around a read) swallows the gate's error, and the build
    goes on reading a file its key leaves out."""
    arguments, files, _ = folders
    with pytest.raises(gate.UndeclaredRead, match="LINKDATA1.BIN"):
        with gate.watch("text", _manifest(), gate.ENFORCE, arguments):
            try:
                open(files["game archive"], "rb").close()
            except Exception:
                pass


@pytest.fixture
def stock(tmp_path):
    """A synthetic game folder (synthetic.stock_game, with two exe strings and a Dream movie), and a manifest whose
    stock pieces are the record's pieces any step reads, and the movie read whole."""
    from wozmod.install.stock import Piece

    game = tmp_path / "game"
    record = stock_game(game, exe={0x204: b"\0abc\0", 0x240: b"\0xyz\0"})
    (game / "movie").mkdir()
    (game / "movie/movie200.wmv").write_bytes(b"a stock movie" * 9)
    pieces = (*(p for p in record.pieces if p.steps), Piece("movie/movie200.wmv", 0, "", frozenset({"text"}), length=117))
    return game, _manifest(stock=pieces)


# Each read through game_files, and the piece the gate refuses (None: a declared piece). The manifest declares exe
# bytes 0x200 to 0x220 and 0x300 to 0x310, LINKDATA1 2 (as stored) and 3 (only unpacked), LINKDATA2 0, clips 0 and 3.
STOCK_CASES = {
    "an exe span in a piece": (lambda game: game_files.exe(game)[0x204:0x208], None),
    "an exe span past its piece": (lambda game: game_files.exe(game)[0x21C:0x224], "OROCHI_Z.exe bytes 0x21c to 0x224"),
    "an exe byte between pieces": (lambda game: game_files.exe(game)[0x250], "OROCHI_Z.exe bytes 0x250 to 0x251"),
    "a search that runs past its piece": (lambda game: game_files.exe(game).find(b"\xff", 0x300),
                                          "OROCHI_Z.exe bytes 0x300 to 0x400"),
    "an exe string in a piece": (lambda game: game_files.exe(game).strings(stock_key)[stock_key(b"abc")[0]], None),
    "an exe string outside the pieces": (lambda game: stock_key(b"xyz")[0] in game_files.exe(game).strings(stock_key),
                                         "OROCHI_Z.exe bytes 0x240 to 0x245"),
    "an entry unpacked": (lambda game: game_files.archive(game / "LINKDATA1")[3], None),
    "an entry read only unpacked, as stored": (lambda game: game_files.archive(game / "LINKDATA1").members(3),
                                               "LINKDATA1 entry 3 (as stored)"),
    "an entry as stored": (lambda game: game_files.archive(game / "LINKDATA1").read([2], unpack=False), None),
    "an entry no piece names": (lambda game: game_files.archive(game / "LINKDATA2")[1], "LINKDATA2 entry 1 (unpacked)"),
    "a clip": (lambda game: game_files.voices(game).clips([0, 3]), None),  # located through 4058, not a piece
    "a clip only served": (lambda game: game_files.voices(game).clips([1]), "data/Sound/Voice/Voice.bin clip 1"),
    "a file read whole": (lambda game: game_files.whole(game / "movie/movie200.wmv"), None),
}


def test_a_stock_byte_outside_the_steps_pieces_raises_in_enforce_and_is_logged_once_in_observe(stock, capfd):
    """Bug: a step reads a byte of a game file that its stock pieces leave out (a span that runs on, a search past its
    piece, a string the scan found elsewhere, an entry read as stored where the record says only unpacked, an entry or
    clip no piece names), so a player whose game differs there passes the installer's check and gets other output;
    or the gate refuses a declared piece, or takes the voice index for one."""
    game, found = stock
    raised, logged = {}, {}
    for case, (read, _) in STOCK_CASES.items():
        try:
            with gate.watch("text", found, gate.ENFORCE, {"game": game}):
                read(game)
        except gate.UndeclaredRead as error:
            raised[case] = error.item
            assert (error.kind, str(error)) == ("stock", f"the text step read {error.item} (a piece of a game file), "
                                                "which its manifest does not declare (wozmod.install.manifest)")
        else:
            raised[case] = None
        capfd.readouterr()
        with gate.watch("text", found, gate.OBSERVE, {"game": game}):
            read(game)
            read(game)  # one line per undeclared piece
        logged[case] = [line.removeprefix(gate.LINE) for line in capfd.readouterr().err.splitlines()]
    assert raised == {case: piece for case, (_, piece) in STOCK_CASES.items()}
    assert logged == {case: [str(gate.UndeclaredRead("text", "stock", piece))] if piece else []
                      for case, (_, piece) in STOCK_CASES.items()}


def test_stock_bytes_cannot_be_read_around_their_spans(stock):
    """Bug: the exe's bytes reach struct, re or bytes() whole (a buffer), which read every byte unannounced, so a
    step's use of the exe the gate cannot see passes the check on a game that will build wrong."""
    import re
    import struct

    game, _ = stock
    exe = game_files.exe(game)
    reads = [lambda: struct.unpack_from("<I", exe, 0x200), lambda: re.search(rb"abc", exe), lambda: bytes(exe),
             lambda: memoryview(exe), lambda: list(game_files.exe(game).strings(stock_key))]
    refused = []
    for read in reads:
        try:
            read()
        except TypeError:
            refused.append(True)
        else:
            refused.append(False)
    assert refused == [True] * len(reads)


def test_record_writes_every_read_down_and_says_what_was_never_read(folders, tmp_path):
    """Bug: record mode stops at an undeclared read or leaves one out of the trace, or unread() lists a declared item
    the step did read, so tools/stock_pieces.py and the reference builds see a wrong set of reads."""
    arguments, files, ffmpeg = folders
    trace = tmp_path / "trace.jsonl"
    found = _manifest(environment=("WOZMOD_NATIVE", "WOZMOD_ART_TRACE"))
    with gate.watch("text", found, gate.RECORD, arguments, trace):
        for case in ("game exe", "game exe", "game archive", "other bundled", "ffmpeg on a declared file",
                     "a declared setting", "an undeclared setting"):
            _do(case, files, ffmpeg)
    read = {(r["kind"], r["item"], r["declared"]) for r in gate.records(trace) if r["kind"] != "module"}
    assert read == {("game", "OROCHI_Z.exe", False), ("game", "LINKDATA1.BIN", False),
                    ("bundled", "data/stock_pieces.json", False), ("program", "ffmpeg", True),
                    ("donor", "wo1-x360/linkdata_ens.idx", True),  # in ffmpeg's arguments
                    ("setting", "WOZMOD_NATIVE", True), ("setting", "WOZMOD_KNOB", False)}
    unread = gate.unread(found, gate.records(trace))
    assert {kind: names for kind, names in unread.items() if kind != "module"} == {
        "stock": ["OROCHI_Z.exe bytes 0x0 to 0x4"], "donor": [], "bundled": ["data/movies.json"], "program": [],
        "setting": ["WOZMOD_ART_TRACE"]}


def test_an_enforced_build_with_a_trace_writes_its_reads_and_still_stops_at_an_undeclared_one(folders, tmp_path):
    """Bug: a reference build (tools/verify.py ref: enforce, with a trace) leaves its trace empty, so its evidence
    shows nothing, or the trace turns enforce into record and an undeclared read goes through."""
    arguments, files, ffmpeg = folders
    trace = tmp_path / "trace.jsonl"
    with pytest.raises(gate.UndeclaredRead, match="data/stock_pieces.json"):
        with gate.watch("text", _manifest(), gate.ENFORCE, arguments, trace):
            for case in ("ffmpeg on a declared file", "a declared setting", "a declared setting", "other bundled"):
                _do(case, files, ffmpeg)
    assert [(r["kind"], r["item"], r["declared"]) for r in gate.records(trace) if r["kind"] != "module"] == [
        ("program", "ffmpeg", True), ("donor", "wo1-x360/linkdata_ens.idx", True), ("setting", "WOZMOD_NATIVE", True),
        ("bundled", "data/stock_pieces.json", False)]


def test_our_builds_enforce_and_a_frozen_release_only_observes(monkeypatch):
    """Bug (D1): a player's release stops an install over a manifest gap, or our own builds (tests, dev installs,
    reference builds) only log one, so a read the key leaves out reaches players unseen."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    checkout = gate.default_mode()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert (checkout, gate.default_mode()) == (gate.ENFORCE, gate.OBSERVE)


# In the worker, as an install runs a step


def _read_the_voice_clips(game, out):
    game_files.voices(game).clips([0])


def _load_a_module(out):
    importlib.import_module("wozmod.formats.textures")  # the art's, not the text's


def test_the_worker_holds_its_step_to_its_manifest(tmp_path, monkeypatch):
    """Bug: the worker runs the step outside the gate, or in a mode other than the one the install chose, so a read
    the step key leaves out goes unseen in our own builds."""
    monkeypatch.setattr(installer, "_text_table", _read_the_voice_clips)
    stock_game(tmp_path / "game")
    assert not any(p.file == game_files.VOICE for p in manifest.manifests()["text"].stock)  # the voices' clips
    with pytest.raises(gate.UndeclaredRead, match="Voice.bin clip 0"):
        steps.run({"text": {"game": tmp_path / "game", "out": tmp_path / "out"}}, Progress(), None)


def test_a_module_the_manifest_leaves_out_is_caught_when_it_is_imported(tmp_path, monkeypatch):
    """Bug: a step imports a wozmod module its closure leaves out (an import by name the source does not show), so a
    change to that module keeps a stale step; enforce must stop it, observe must log it."""
    monkeypatch.setattr(installer, "_text_table", _load_a_module)
    assert "wozmod.formats.textures" not in manifest.manifests()["text"].modules
    with pytest.raises(gate.UndeclaredRead, match="wozmod.formats.textures"):
        steps.run({"text": {"out": tmp_path / "out"}}, Progress(), None)
    log = tmp_path / "install.log"
    steps.run({"text": {"out": tmp_path / "out"}}, Progress(), log, mode=gate.OBSERVE)
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"{gate.LINE}{gate.UndeclaredRead('text', 'module', 'wozmod.formats.textures')}"]


def _write_a_file(out):
    out.mkdir(parents=True)
    (out / "built").write_text("built", encoding="utf-8")


def test_a_release_without_manifests_still_builds_and_our_builds_stop(tmp_path, monkeypatch):
    """Bug: a release whose step_keys.json is missing or unreadable stops every player's install at the gate (before
    the gate it rebuilt every step), or a checkout that cannot make its manifests builds its steps unchecked."""
    def broken(root=None):
        raise manifest.StepKeyError("this release has no step_keys.json")

    monkeypatch.setattr(manifest, "manifests", broken)
    monkeypatch.setattr(installer, "_text_table", _write_a_file)
    log = tmp_path / "install.log"
    steps.run({"text": {"out": tmp_path / "out"}}, Progress(), log, mode=gate.OBSERVE)
    assert (tmp_path / "out/built").is_file()
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"{gate.LINE}no step manifests (this release has no step_keys.json); the steps run unchecked"]
    with pytest.raises(manifest.StepKeyError):
        steps.run({"text": {"out": tmp_path / "again"}}, Progress(), None, mode=gate.ENFORCE)
