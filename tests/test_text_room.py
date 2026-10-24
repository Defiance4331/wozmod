"""English text against the room the game leaves for it (wozmod.runtime.text_room): an overrun ends the game."""

import os
import struct
from pathlib import Path

import pytest

from wozmod.runtime import text_room
from wozmod.runtime.text_room import Room, overruns


def test_a_site_is_flagged_exactly_when_its_longest_text_and_terminator_outgrow_its_room(monkeypatch) -> None:
    # Bug: the check forgets the terminator, prints %% as two bytes, fills %s with a short name or %d with too few
    # digits, so a line one byte too long passes and the game overruns its buffer.
    messages = [b"Got %d%% of %s", b"Plain", b"%02d"]
    names = [b"Short", b"A much longer name"]  # 18 bytes
    tables = {0x100: [b"ab", b"abcdef"]}
    rows = [  # room, formats, fills, digits, fits: 4 + 5 + 1 + 4 + 18 = 32 bytes, 33 with the terminator
        (33, (0,), "name", 5, True), (32, (0,), "name", 5, False),
        (6, (1,), "name", 10, True), (5, (1,), "name", 10, False),
        (19, (), "name", 10, True), (18, (), "name", 10, False),
        (21, (0,), 0x100, 5, True), (20, (0,), 0x100, 5, False),
        (11, (2,), "name", 10, True), (10, (2,), "name", 10, False),
    ]
    monkeypatch.setattr(text_room, "ROOMS", tuple(Room(at, room, formats, fills, digits)
                                                   for at, (room, formats, fills, digits, _) in enumerate(rows)))
    flagged = {int(line.split(":")[0], 16) for line in overruns(messages, names, tables)}
    assert [at in flagged for at in range(len(rows))] == [not fits for *_, fits in rows]


def test_officer_names_are_read_from_the_records_after_the_cast_count() -> None:
    # Bug: the records are read from the entry's first byte, over its u32 count, so each "record" is the last bytes of
    # the one before and the longest name is never measured: an overrun passes unchecked.
    parts = [b"Aa", b"Bb", b"Cc ", b"Longest-name-part"]
    record = lambda surname, given: struct.pack("<HH", surname, given) + bytes(32)
    cast = struct.pack("<I", 3) + record(3, 3) + record(0, 1) + record(2, 1)
    assert text_room.cast_names(cast, parts) == [b"Longest-name-part", b"Bb Aa", b"Cc Bb"]


@pytest.mark.skipif(not (os.environ.get("WOZMOD_TEXT_ROOM") and os.environ.get("WOZMOD_GAME")
                         and os.environ.get("WOZMOD_DONORS")),
                    reason="set WOZMOD_TEXT_ROOM=1, WOZMOD_GAME and WOZMOD_DONORS (about 15 s)")
def test_every_english_text_fits_the_room_the_game_leaves_for_it() -> None:
    # Bug: a translation edit makes a line or name longer than its game buffer, and the screen that shows it ends
    # the game (issue 3). Fix by giving the site the runtime's buffer in text_room.ROOMS, not by cutting the text.
    from wozmod.text.build import resolved_text
    from wozmod.text.resolver import DonorResolver

    stock = Path(os.environ["WOZMOD_GAME"])
    _, pairs, parts = resolved_text(stock, DonorResolver(Path(os.environ["WOZMOD_DONORS"])), Path("data/text"))
    assert overruns(*text_room.english_inputs(stock, pairs, parts)) == []
