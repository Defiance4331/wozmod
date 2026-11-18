"""The event file: an in-engine cutscene's script, as the stock game (little-endian) and the Xbox 360 donors
(big-endian) store it.

An event file starts with u32 sub-scene count and the sub-scene offsets. A sub-scene is a chain of records the
runner (0x49D9A0) executes: u32 offset of the next record from the sub-scene start | u8 type | u8 sub | u8 field
count n | n field types | u8 data offset (from byte 7) | n field sizes | data. A gate (type 0 sub 0, one float) whose
value is above the clock stops the chain for this frame; the clock counts one a frame (EVENT_TICKS a second) from 0.
Type 0 sub 1 is the sub-scene length. Type 5 is text: sub 2 or 5 speaks a line (field 1 is the line record's offset
from the sub-scene start: u32 mask, u16 line index, u16 lip size, the text to its NUL), sub 1 clears it, sub 0 sets a
place caption. Type 8 sub 1 or 2 opens a conditional block and type 8 sub 0 closes it. The draw reads the line the
last speak stored until a clear or the next speak, so a window runs from its speak's tick to the next text record's.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

EVENT_TICKS = 60  # an event scene's clock counts 60 a second (runner 0x49D9A0)
SPEAK, CLEAR, CAPTION = 5, 1, 0
_FIELD = {0: "B", 1: "H", 2: "I", 3: "B", 4: "H", 5: "I", 6: "f", 7: "I", 8: "I"}


def tick_of(gate: float) -> int:
    """The clock value (frame) at which a record behind this gate value runs: the clock is 0, 1, 2 and so on."""
    return max(0, math.ceil(gate))


@dataclass(eq=False)  # a record is itself: two clears with the same fields are two records
class Record:
    at: int | None  # absolute offset in the file; None for an added record until it is written
    kind: int
    sub: int
    fields: tuple
    tick: int  # the frame it runs at
    depth: int  # how many conditional blocks it is inside (the block's own open and close records count as in)
    new: bytes | None = None  # the bytes of an added record, link field included

    @property
    def text(self) -> bool:
        return self.kind == 5 and self.sub in (SPEAK, CLEAR, CAPTION)



def record_chain(data: bytes, base: int, order: str = "<") -> list[Record]:
    """A sub-scene's record chain from base; ValueError when the chain leaves data, loops or holds a record no event
    file has (event_file tells event files from other archive entries by this)."""
    records, at, gate, depth, seen = [], base, 0.0, 0, set()
    while True:
        if at in seen or not 0 <= at <= len(data) - 8:
            raise ValueError("event record chain leaves the file or loops")
        seen.add(at)
        following, kind, sub, n = *struct.unpack_from(order + "I", data, at), data[at + 4], data[at + 5], data[at + 6]
        if kind == 0xFF:
            records.append(Record(at, kind, sub, (), tick_of(gate), depth))
            return records
        if kind > 0x20 or n > 16 or at + 8 + 2 * n > len(data):
            raise ValueError("not an event record")
        types, start, sizes = data[at + 7:at + 7 + n], data[at + 7 + n], data[at + 8 + n:at + 8 + 2 * n]
        values, p = [], at + 7 + start
        for kind_of, size in zip(types, sizes, strict=True):
            values.append(struct.unpack_from(order + _FIELD.get(kind_of, "H"), data, p)[0])
            p += size
        if kind == 0 and sub == 0:
            gate = values[0]
        opens = kind == 8 and sub in (1, 2)
        depth += opens
        records.append(Record(at, kind, sub, tuple(values), tick_of(gate), depth))
        depth -= kind == 8 and sub == 0 and depth > 0
        at = base + following


@dataclass(frozen=True)
class EventWindow:
    sub: int
    index: int  # the line index: also the voice index
    start: int  # ticks from the sub-scene start
    end: int
    end_by: str  # "clear", "speak", "caption" or "end" (the sub-scene ends)
    in_block: bool  # the speak or what ends it is inside a conditional block: the simulation cannot tell its end
    text: bytes = b""  # the line record's text


@dataclass(frozen=True)
class EventCaption:
    sub: int
    ordinal: int  # the place caption's place among its sub-scene's captions
    offset: int  # of its text in the file
    text: bytes


def event_file(data: bytes, order: str = "<") -> bool:
    """Whether data starts like an event file: a sub-scene count, then that many ascending sub-scene offsets."""
    if len(data) < 8:
        return False
    count = struct.unpack_from(f"{order}I", data)[0]
    if not 0 < count <= 64 or len(data) < 4 + 4 * count:
        return False
    bases = struct.unpack_from(f"{order}{count}I", data, 4)
    if bases[0] < 4 + 4 * count or any(a >= b for a, b in zip(bases, bases[1:], strict=False)) or bases[-1] >= len(data):
        return False
    try:
        return all(record_chain(data, base, order) for base in bases)
    except (struct.error, IndexError, ValueError):
        return False


@dataclass(frozen=True)
class EventScene:
    windows: list[EventWindow]
    captions: list[EventCaption]
    lengths: tuple[float, ...]  # each sub-scene's length in ticks

    def lines(self) -> set[int]:
        return {w.index for w in self.windows}


def event_scenes(data: bytes, order: str = "<") -> EventScene:
    """Every subtitle window and place caption of an event file, by simulating each sub-scene's clock: it starts
    at -1, clamps to 0, then counts one a frame; a gate above the clock stops the chain; the scene ends when the
    clock passes its length. A subtitle shows from its speak to the next text record or the scene end."""
    windows, captions, lengths = [], [], []
    count = struct.unpack_from(f"{order}I", data)[0]
    for sub, base in enumerate(struct.unpack_from(f"{order}{count}I", data, 4)):
        records = record_chain(data, base, order)
        length = next((r.fields[0] for r in reversed(records) if r.kind == 0 and r.sub == 1), 0.5)
        lengths.append(length)
        ran, clock, ip, tick = [], -1.0, 0, 0
        while True:
            clock = 0.0 if clock < 0 else clock + 1.0
            while records[ip].kind != 0xFF and not (records[ip].kind == records[ip].sub == 0 and records[ip].fields[0] > clock):
                ran.append((tick, ip))
                ip += 1
            if not clock <= length:
                break
            tick += 1
        texts = [(t, i) for t, i in ran if records[i].kind == 5 and records[i].sub in (0, 1, 2, 5)]
        ordinal = 0
        for k, (t, i) in enumerate(texts):
            s, fields = records[i].sub, records[i].fields
            if s == 0:
                at = base + fields[0] + 4
                captions.append(EventCaption(sub, ordinal, at, data[at:data.index(b"\0", at)]))
                ordinal += 1
            elif s in (2, 5):
                index = struct.unpack_from(f"{order}H", data, base + fields[1] + 4)[0]
                text = data[base + fields[1] + 8:data.index(b"\0", base + fields[1] + 8)]
                following = texts[k + 1] if k + 1 < len(texts) else None
                end_by = "end" if following is None else {0: "caption", 1: "clear"}.get(records[following[1]].sub, "speak")
                after = texts[k + 2] if k + 2 < len(texts) else None
                if end_by == "clear" and after and after[0] == following[0] and records[after[1]].sub != 1:
                    end_by = "speak"  # cleared on the tick the next line starts: the next line ends it
                if end_by == "clear" and following[0] >= length:
                    end_by = "end"  # cleared on the sub-scene's last frame: the scene end ends it
                windows.append(EventWindow(sub, index, t, following[0] if following else tick, end_by,
                                           records[i].depth > 0 or (following is not None and records[following[1]].depth > 0),
                                           text))
    return EventScene(windows, captions, tuple(lengths))


# ---------------------------------------------------------------------------------------------------------------
# Donor truth: what each donor clip says, by the donor game's own pairing of clip and text.
