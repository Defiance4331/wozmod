"""Room the game leaves for each text it formats or composes, measured against the longest English that can land there.

The game sized these buffers for Japanese. English is often longer, and a text that outgrows its buffer overwrites the
memory after it, which can end the game (issue 3). A site whose English does not fit gets the runtime's own buffer
(``WIDE``): the runtime points the write there, and the draw that follows (``read``) at it. ``overruns`` is the check:
run on the built table, it names every site whose longest English, its terminator included, would not fit.

Not listed, each with ample room: the battle lines formatted into 256 bytes at 0x4F1955 and 0x4F63F3 and the rest of
that family (lines from the stage scripts, at most 170 bytes), the name formats at 0x49E98A (54 of 256), the joined
lines at 0x56C3AD (70 of 256), 0x5ABAA3 (148 of 256) and 0x5ABCFF (193 of 512), and the battle objectives at 0x4F919E
(105 of 128 over every objective in the stage scripts, measured 2026-10-22; the bound needs the scripts, not just the
table).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from wozmod.formats.linkdata import archive
from wozmod.formats.pe import pointer_strings

WIDE = 1024  # wide_text in english_runtime.c
MESSAGES, MESSAGE_COUNT = 0x6DE4F0, 911  # the message table 0x47D060 reads
NAME_TABLE, NAME_PARTS = 0x6F6368, 890
CAST = 65  # LINKDATA1 entry: 36-byte officer records, the first two words their surname and given-name parts
TABLES = {0x6FD310: 15, 0x6FD4E0: 20}  # string tables a format's %s takes an entry of


@dataclass(frozen=True)
class Room:
    call: int  # the call that writes the text: sprintf, or the name composer
    room: int  # bytes for the text and its terminator
    formats: tuple[int, ...] = ()  # message ids whose English is the format; none: the text is a bare name
    fills: str | int = "name"  # what a %s receives: an officer's name, or an entry of a string table in TABLES
    digits: int = 10  # the widest number a %d prints
    read: int | None = None  # for a WIDE site, the draw call that reads it


ROOMS = (
    # Ability Acquisition conditions (0x593B20), the allied-force effect line (0x598050), a list name (0x5A1A30) and
    # a result name (0x585180, through the name wrapper 0x58CE50): Japanese fits, English does not.
    Room(0x593B50, WIDE, tuple(range(0x24B, 0x254)), digits=5, read=0x593B93),
    Room(0x593B75, WIDE, tuple(range(0x242, 0x24B)), digits=5, read=0x593B93),
    Room(0x59816C, WIDE, (0x343, 0x344, 0x345), digits=5, read=0x598186),
    Room(0x5A1A65, WIDE, read=0x5A1ABD),
    Room(0x5851DE, WIDE),
    # Sites that fit today.
    Room(0x5851EA, 256, tuple(range(0x38F))),
    Room(0x527EA4, 256, (0x2FB,), fills=0x6FD310),
    Room(0x52936E, 256, (0x2FE,), fills=0x6FD310),
    Room(0x5708E8, 256, (0x37A,), fills=0x6FD4E0),
    Room(0x554EFF, 256, (0x2BC,)),
    Room(0x554F3C, 256, (0x2BD,)),
    Room(0x56C385, 256, (0x381,)),
    Room(0x56C488, 256, (0x381,)),
    Room(0x5829E2, 256, (0xE4,)),
    Room(0x5AC383, 512, (0x10, 0x11, 0x16)),
    Room(0x5AC41F, 512, (0x10, 0x11, 0x16)),
    *(Room(call, 256) for call in (0x4F18FE, 0x4F63E0, 0x4F658F, 0x4F66D1, 0x4F69D8, 0x4F6B44, 0x4F6DA2, 0x4F6DB8,
                                    0x4F6FD9, 0x4F6FF0, 0x4F7527, 0x535E9C, 0x554EC2)),
    Room(0x4F9117, 128),
    Room(0x53C046, 24),
    Room(0x543361, 32),
    Room(0x594F4B, 32),
)

_SPEC = re.compile(rb"%(?:\d+\$)?(\d*)(.)", re.S)  # %2$d: the build numbered its argument


def longest(fmt: bytes, digits: int, fill: int) -> int:
    """Length of ``fmt`` printed with its widest numbers and ``fill``-byte strings."""
    def widest(spec: re.Match) -> bytes:
        kind = spec[2]
        if kind not in b"dsu%":
            raise ValueError(f"unexpected printf form {spec[0]!r} in {fmt[:40]!r}")
        return b"%" if kind == b"%" else b"x" * (fill if kind == b"s" else max(digits, int(spec[1] or 0)))
    return len(_SPEC.sub(widest, fmt))


def overruns(messages: list[bytes], names: list[bytes], tables: dict[int, list[bytes]]) -> list[str]:
    """Each site whose longest text, its terminator included, does not fit its room.

    ``messages``: the table's English (or the stock text) by message id; ``names``: every officer's composed English
    name; ``tables``: the English entries of each table in TABLES."""
    found = []
    for site in ROOMS:
        fill = max(map(len, names if site.fills == "name" else tables[site.fills]))
        sizes = {i: longest(messages[i], site.digits, fill) for i in site.formats} or {"name": fill}
        worst = max(sizes, key=sizes.get)
        if sizes[worst] + 1 > site.room:
            source = f"message {worst:#x}" if worst != "name" else "an officer name"
            found.append(f"{site.call:#x}: {source} needs {sizes[worst] + 1} bytes, room {site.room}")
    return found


def compose(parts: list[bytes], surname: int, given: int) -> bytes:
    """An officer's name as the runtime composes it (compose in english_runtime.c)."""
    a, b = parts[surname], parts[given]
    return a if surname == given else a + b if a.endswith(b" ") else b + b" " + a


def english_inputs(stock: Path, pairs: dict[bytes, bytes], parts: list[bytes | None]):
    """``overruns``' inputs from the stock game, the table's stock-to-English pairs and its name parts."""
    exe = (stock / "OROCHI_Z.exe").read_bytes()
    messages = [pairs.get(s, s) for s in pointer_strings(exe, MESSAGES, MESSAGE_COUNT, 4)]
    stock_parts = pointer_strings(exe, NAME_TABLE, NAME_PARTS, 8)
    english = [en if en is not None else ja for en, ja in zip(parts, stock_parts, strict=True)]
    cast = archive(stock / "LINKDATA1")[CAST]
    names = [compose(english, int.from_bytes(cast[at:at + 2], "little"), int.from_bytes(cast[at + 2:at + 4], "little"))
             for at in range(0, len(cast) - 35, 36)]
    tables = {va: [pairs.get(s, s) for s in pointer_strings(exe, va, count, 4)] for va, count in TABLES.items()}
    return messages, names, tables
