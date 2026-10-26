"""tools/stock_pieces.py on a synthetic game, and data/stock_pieces.json against the real build: it is what the build
reads, and all of it.

The real-build tests are slow (about 22 minutes): run them with WOZMOD_STOCK_PIECES=1, WOZMOD_GAME, WOZMOD_DONORS and
WOZMOD_LETTERING. tools/stock_pieces.py records the three build steps once (one worker); a second build, from a copy
of the game that keeps only the listed pieces, must write the same bytes.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import stock_pieces
from wozmod.install import worker_count
from wozmod.install.stock import check_game, parse

INPUTS = ("WOZMOD_GAME", "WOZMOD_DONORS", "WOZMOD_LETTERING")
SLOW = pytest.mark.skipif(
    os.environ.get("WOZMOD_STOCK_PIECES") != "1" or not all(os.environ.get(name) for name in INPUTS),
    reason="set WOZMOD_STOCK_PIECES=1, " + ", ".join(INPUTS))
ONE_WORKER = pytest.mark.xdist_group("stock-pieces")  # both share one record


def _inputs() -> tuple[Path, Path, Path]:
    return tuple(Path(os.environ[name]) for name in INPUTS)


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    """(the pieces document of a fresh record, the build's output folder)."""
    game, donors, lettering = _inputs()
    started = time.monotonic()
    found = stock_pieces.record(game, donors, lettering, tmp_path_factory.mktemp("recorded"))
    print(f"record: {time.monotonic() - started:.0f} s")
    return found


@SLOW
@ONE_WORKER
def test_a_fresh_record_is_the_committed_one(recorded):
    # Bug: the build now reads a piece the committed record lacks (another mod's change there would go unnoticed and
    # change what is built), or no longer reads one (the install stops for nothing): data/stock_pieces.json was not
    # written again with tools/stock_pieces.py.
    document, _ = recorded
    assert stock_pieces.dumps(document) == stock_pieces.OUTPUT.read_text(encoding="utf-8"), \
        "run tools/stock_pieces.py and commit data/stock_pieces.json:\n" + stock_pieces.summary(document)


def _files(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*")) if p.is_file()}


@SLOW
@ONE_WORKER
def test_a_build_from_only_the_listed_pieces_writes_the_same_bytes(recorded, tmp_path_factory):
    # Bug: the build reads a piece the record does not list (an entry, exe bytes or a clip a reader the recorder does
    # not see opens), so another mod's change there passes the check and changes what is built; or the check does not
    # find the pieces through the player's indexes (every unlisted entry here changes size, so the later ones move).
    document, out = recorded
    game, donors, lettering = _inputs()
    junk = tmp_path_factory.mktemp("junk") / "game"
    stock_pieces.junk_game(game, document, junk)
    verdict = check_game(junk, parse(document))  # the clips wozmod only serves are zeroed: noted, not blocking
    assert (verdict.problem(), verdict.blocking()) == (None, ())
    started = time.monotonic()
    rebuilt = stock_pieces.run_steps(junk, donors, lettering, tmp_path_factory.mktemp("rebuilt"), worker_count())
    print(f"build from the junked copy: {time.monotonic() - started:.0f} s")
    before, after = _files(out), _files(rebuilt)
    assert sorted(after) == sorted(before)
    assert [name for name, path in before.items() if path.read_bytes() != after[name].read_bytes()] == []


def test_the_recorder_keeps_what_each_step_reads_and_how(tmp_path):
    # Bug: an entry the art reads as stored (here also unpacked by the text) gets an unpacked hash (a repacked copy would pass, and the art would pack
    # it again where the stock entry was not packed); an exe span is kept at a shifted offset; the voice index is
    # listed as a piece (re-laid-out clips would stop the install); a served entry or clip is put under the wrong
    # archive or number; or a read past the tracking opener goes unseen.
    import struct

    from synthetic import stock_game
    from wozmod.art.build import read_entry
    from wozmod.formats.linkdata import archive, parse_index
    from wozmod.install.stock import EXE, VOICE, VOICE_INDEX

    game = tmp_path / "game"
    stock_game(game)
    with stock_pieces.Recorder(game) as recorder:
        recorder.step = "text"
        archive(game / "LINKDATA1").read([3])
        exe = (game / EXE).read_bytes()
        assert exe[0x204:0x208] and struct.unpack_from("<H", exe, 0x300)
        recorder.step = "art"
        index = parse_index((game / "LINKDATA1.IDX").read_bytes(), (game / "LINKDATA1.BIN").stat().st_size)
        with (game / "LINKDATA1.BIN").open("rb") as stream:
            read_entry(stream, index.entries[2])
            read_entry(stream, index.entries[3])  # the text reads it unpacked, the art as stored: stored counts
        recorder.step = "voices"
        table = archive(game / "LINKDATA1")[VOICE_INDEX]
        archive(game / "LINKDATA2").read([0])
        with open(game / VOICE, "rb") as stream:
            stream.seek(struct.unpack_from(">I", table, 16)[0])
            stream.read(4)  # the start of clip 1
        with pytest.raises(PermissionError):
            os.open(game / "LINKDATA2.BIN", os.O_RDONLY)
        recorder.step = None
    assert recorder.violations == ["voices: opened LINKDATA2.BIN outside the tracking opener"]
    out = tmp_path / "out/wozmod"
    (out / "captions").mkdir(parents=True)
    (out / "captions/4253.bin").write_bytes(b"served in place of LINKDATA2 2")
    (out / "voice").mkdir()
    (out / "voice/Voice.bin").write_bytes(struct.pack("<I6I", 3, 0, 0, 40, 9, 0, 0))
    pieces = stock_pieces.pieces_document(game, recorder.reads, tmp_path / "out")["pieces"]
    shape = {name: [[v if not isinstance(v, str) or len(v) != 64 else "sha" for v in row] for row in rows]
             for name, rows in pieces.items()}
    assert shape == {
        EXE: [[0x204, 4, "sha", "text"], [0x300, 2, "sha", "text"]],
        "LINKDATA1": [[2, "sha", None, "art", 0], [3, "sha", None, "text art", 0]],
        "LINKDATA2": [[0, "sha", "sha", "voices", 0], [2, "sha", None, "", 1]],
        VOICE: [[1, "sha", "voices", 1]],
    }


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
    assert stock_pieces.served_pieces(tmp_path)[1] == {3, MOZ_VOICE_IDS - 1}
