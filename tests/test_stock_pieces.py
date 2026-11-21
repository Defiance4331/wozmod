"""tools/stock_pieces.py on a synthetic game, and data/stock_pieces.json against what the reference builds read.

The real test (WOZMOD_STOCK_PIECES=1, with WOZMOD_GAME, WOZMOD_DONORS, WOZMOD_LETTERING, WOZMOD_SPEECH and
WOZMOD_SEPARATOR) reads the
traces of the three steps' builds (tests/conftest.py, the built fixture; tools/verify.py ref makes them). A read of a
piece the list leaves out stops a build there, at the read gate; this test catches the other way round.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import stock_pieces
from wozmod import game_files
from wozmod.install import steps
from wozmod.install.gate import records
from wozmod.install.stock import parse

INPUTS = ("WOZMOD_GAME", "WOZMOD_DONORS", "WOZMOD_LETTERING", "WOZMOD_SPEECH", "WOZMOD_SEPARATOR")


@pytest.mark.skipif(os.environ.get("WOZMOD_STOCK_PIECES") != "1" or not all(os.environ.get(name) for name in INPUTS),
                    reason="set WOZMOD_STOCK_PIECES=1, " + ", ".join(INPUTS))
def test_the_steps_read_exactly_the_committed_pieces(tmp_path, built):
    # Bug: the build no longer reads a piece the committed record lists (a player whose game differs there is stopped
    # for nothing), reads an entry in another form or from another step than the record says, or serves its own in
    # place of other entries or clips: data/stock_pieces.json was not written again with tools/stock_pieces.py.
    folders = [built(step, tmp_path / step) for step in steps.STEPS]
    found = stock_pieces.pieces_document(Path(os.environ["WOZMOD_GAME"]),
                                         [r for folder in folders for r in records(folder / "trace.jsonl")],
                                         [folder / "out" for folder in folders])
    assert stock_pieces.dumps(found) == stock_pieces.OUTPUT.read_text(encoding="utf-8"), \
        "run tools/stock_pieces.py and commit data/stock_pieces.json:\n" + stock_pieces.summary(found)


def _text(game, out):  # what each fake step reads of synthetic.stock_game, as its record says
    exe = game_files.exe(game)
    exe[0x200:0x220], exe[0x300:0x310]
    game_files.archive(game / "LINKDATA1")[3]


def _art(game, out):
    from wozmod.art.build import read_entry

    game_files.exe(game)[0x308:0x310]
    read_entry(game_files.archive(game / "LINKDATA1"), 2)


def _voices(game, out):
    game_files.archive(game / "LINKDATA2").read([0])
    game_files.voices(game).clips([3, 0])


def test_the_tool_hands_each_step_every_argument_its_function_takes(tmp_path, monkeypatch):
    # Bug: a step's function gains a parameter that the tool's call of the steps does not pass (the stale stock-pieces
    # call: the voices step gained speech, and the tool failed only when someone next ran it).
    import argparse

    called = {}

    def run(work, progress, log, mode=None, trace=None):
        for step, arguments in work.items():
            called[step] = steps._taken(steps._function(step), arguments)  # what each step's worker hands it

    monkeypatch.setattr(steps, "run", run)
    named = argparse.Namespace(**{name: tmp_path / name for name in ("game", "donors", "speech", "separator", "lettering")})
    stock_pieces.run_steps(stock_pieces.inputs(named), tmp_path / "scratch")
    assert set(called) == set(steps.STEPS)


def test_a_record_run_writes_the_pieces_the_steps_read(tmp_path, monkeypatch):
    # Bug: an entry the art reads as stored gets an unpacked hash (a repacked copy would pass, and the art would pack
    # it again where the stock entry was not packed); exe spans are kept at a shifted offset or not merged per file;
    # the voice index is listed as a piece (re-laid-out clips would stop the install); a served entry or clip is put
    # under the wrong archive or number; a step's reads are put under another step.
    import struct

    import wozmod.install as installer
    from synthetic import stock_game
    from wozmod.install import gate

    game, out, trace = tmp_path / "game", tmp_path / "out", tmp_path / "trace.jsonl"
    record = stock_game(game)
    for step, fake in (("text", _text), ("art", _art), ("voices", _voices)):
        monkeypatch.setattr(installer, steps.STEPS[step].function, fake)
        steps.run_here(step, mode=gate.RECORD, trace=trace, game=game, out=out)
    (out / "wozmod/captions").mkdir(parents=True)
    (out / "wozmod/captions/4253.bin").write_bytes(b"served in place of LINKDATA2 2")
    (out / "wozmod/voice").mkdir()
    (out / "wozmod/voice/Voice.bin").write_bytes(struct.pack("<I10I", 5, 0, 0, 40, 9, 49, 9, 0, 0, 58, 9))
    found = parse(stock_pieces.pieces_document(game, gate.records(trace), [out]))
    assert (found.stamp, sorted(found.pieces, key=repr)) == (record.stamp, sorted(record.pieces, key=repr))


def test_a_stage_lines_own_voice_slot_serves_no_stock_clip(tmp_path):
    """Bug: an id past the stock range in the built Voice.bin (a stage line's own slot, runtime/voice_slots.h) is
    taken for a stock clip wozmod serves, and the record looks it up in the stock voice index and stops."""
    import struct

    from wozmod.formats.audio import MOZ_VOICE_IDS

    count = MOZ_VOICE_IDS + 2
    sizes = {3: 10, MOZ_VOICE_IDS - 1: 10, MOZ_VOICE_IDS + 1: 10}  # two stock ids served, and one slot
    table = b"".join(struct.pack("<II", 0, sizes.get(i, 0)) for i in range(count))
    (tmp_path / "wozmod/voice").mkdir(parents=True)
    (tmp_path / "wozmod/voice/Voice.bin").write_bytes(struct.pack("<I", count) + table)
    assert stock_pieces.served_pieces([tmp_path])[1] == {3, MOZ_VOICE_IDS - 1}
