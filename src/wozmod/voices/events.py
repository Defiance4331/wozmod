"""Event (in-engine cutscene) subtitles timed to the English voice.

The stock event files hold each line's subtitle window as Koei authored it for the Japanese clip. Under the English
voice the installer writes a copy of each event file whose windows end with the English clip, to
wozmod/captions/voice/<4251 + event>.bin, which the runtime serves only while the voices are English.

An event file starts with u32 sub-scene count and the sub-scene offsets. A sub-scene is a chain of records the
runner (0x49D9A0) executes: u32 offset of the next record from the sub-scene start | u8 type | u8 sub | u8 field
count n | n field types | u8 data offset (from byte 7) | n field sizes | data. A gate (type 0 sub 0, one float) whose
value is above the clock stops the chain for this frame; the clock counts one a frame (60 a second) from 0. Type 0
sub 1 is the sub-scene length. Type 5 is text: sub 5 speaks a line (field 1 is the line record's offset from the
sub-scene start: u32 mask, u16 line index, u16 lip size, the text to its NUL), sub 1 clears it, sub 0 sets a place
caption. Type 8 sub 1 or 2 opens a conditional block and type 8 sub 0 closes it. The draw reads the line the last
speak stored until a clear or the next speak, so a window runs from its speak's tick to the next text record's.

The retime only relinks the chain: it moves a line's clear, or adds one (and a gate when no record has its tick),
appended at the end of the file. Line records and every other record keep their offsets and their ticks.
"""

from __future__ import annotations

import math
import struct
import sys
from array import array
from dataclasses import dataclass, field
from pathlib import Path

TICKS_PER_SECOND = 60
# Seconds a subtitle stays after the English clip stops being audible: Koei's own median margin over the Japanese
# clip in the WO2 events (0.48 s), and it covers a voice that starts up to 0.3 s after its speak record.
SUBTITLE_PAD = 0.5
AUDIBLE_RATE = 16_000  # Hz of the mono s16le PCM audible_end reads
AUDIBLE_FLOOR_DB = -30.0  # a 10 ms frame is audible when its peak is within this of the clip's peak

SPEAK, CLEAR, CAPTION = 5, 1, 0
GATE = bytes([0, 0, 1, 6, 5, 0, 0, 0])  # type 0 sub 0, one float field at +12: 16 bytes with the float
CLEAR_RECORD = bytes([5, 1, 0, 0])  # type 5 sub 1, no fields: 8 bytes with the link
_FIELD = {0: "B", 1: "H", 2: "I", 3: "B", 4: "H", 5: "I", 6: "f", 7: "I", 8: "I"}


def audible_end(pcm: bytes, rate: int = AUDIBLE_RATE) -> float:
    """Seconds from a clip's start to the end of its last 10 ms frame whose peak is within AUDIBLE_FLOOR_DB of the
    clip's peak (pcm: mono signed 16-bit little-endian); 0 for a silent clip."""
    samples = array("h")
    samples.frombytes(pcm[:len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    hop = rate // 100
    peaks = [max(map(abs, samples[i:i + hop])) for i in range(0, len(samples) - hop + 1, hop)]
    loudest = max(peaks, default=0)
    if not loudest:
        return 0.0
    floor = loudest * 10 ** (AUDIBLE_FLOOR_DB / 20)
    last = max(i for i, peak in enumerate(peaks) if peak > floor)
    return (last + 1) * hop / rate


def subtitle_window(stock: tuple[int, int], donor: tuple[int, int] | None, audible: float, limit: float,
                    ) -> tuple[int, int]:
    """The timing model: the window (speak tick, end tick) a line shows under the English voice.

    Where the donor's window for the line differs from the stock one, Koei retimed it for the English voice: the
    donor window, exactly. Elsewhere the stock start, and the end SUBTITLE_PAD after the English clip's audible end
    (seconds from the speak). Either end is no later than limit: the next line or caption, or the sub-scene end."""
    if donor is not None and donor != stock:
        start, end = donor
    else:
        start, end = stock[0], stock[0] + math.ceil((audible + SUBTITLE_PAD) * TICKS_PER_SECOND)
    return start, min(end, math.floor(limit))

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


@dataclass
class Retimed:
    """What retime_event changed, by (sub-scene, line index) of each visible spoken line."""
    moved: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)  # (stock end, new end), in ticks
    kept: dict[tuple[int, int], str] = field(default_factory=dict)  # why a line keeps its stock window
    started: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)  # (stock start, donor start)
    # why a line Koei retimed in the donor cannot start at the donor's tick: it keeps its start and ends with its clip
    unshown: dict[tuple[int, int], str] = field(default_factory=dict)


def _records(data: bytes, base: int, order: str = "<") -> list[Record]:
    records, at, gate, depth = [], base, 0.0, 0
    for _ in range(len(data)):
        following, kind, sub, n = *struct.unpack_from(order + "I", data, at), data[at + 4], data[at + 5], data[at + 6]
        if kind == 0xFF:
            records.append(Record(at, kind, sub, (), tick_of(gate), depth))
            return records
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
    raise ValueError("event record chain does not end")


def _line(data: bytes, base: int, speak: Record, order: str = "<") -> tuple[int, bytes]:
    at = base + speak.fields[1]
    return struct.unpack_from(order + "H", data, at + 4)[0], data[at + 8:data.index(b"\0", at + 8)]


def visible(text: bytes) -> bool:
    """Whether a line's text shows anything: not only ideographic or ASCII spaces."""
    return bool(text.replace(b"\x81\x40", b"").replace(b" ", b""))


def _after(order: list[Record], tick: int, limit: int) -> int | None:
    """Where a record that runs at tick goes, before order[limit]: after every record that runs by then; None
    when that place is inside a conditional block."""
    place = min(max(k for k, r in enumerate(order) if r.tick <= tick and r.kind != 0xFF) + 1, limit)
    last = order[place - 1]
    return None if last.depth and not (last.kind == 8 and last.sub == 0 and last.depth == 1) else place


def _gate(tick: int) -> Record:
    return Record(None, 0, 0, (float(tick),), tick, 0, struct.pack("<I", 0) + GATE + struct.pack("<f", float(tick)))


def _move(order: list[Record], record: Record, tick: int, limit: int) -> bool:
    """Relink record (already out of order) to run at tick, before order[limit]: after every record that runs by
    then, behind a new gate when none has that tick. False, and order unchanged, when that place is inside a block."""
    if (place := _after(order, tick, limit)) is None:
        return False
    record.tick = tick
    order[place:place] = [*([_gate(tick)] if order[place - 1].tick < tick else []), record]
    return True


def _windows(data: bytes, base: int, order: list[Record], length: float, byte_order: str = "<",
             ) -> dict[int, tuple[int, int]]:
    """Each line's window in a sub-scene: (speak tick, tick of the next text record, else the sub-scene end), for a
    line spoken once there whose speak and whatever ends it lie outside conditional blocks."""
    texts = [r for r in order if r.text]
    found: dict[int, tuple[int, int] | None] = {}
    for k, speak in enumerate(texts):
        if speak.sub != SPEAK:
            continue
        index = _line(data, base, speak, byte_order)[0]
        ends = texts[k + 1] if k + 1 < len(texts) else None
        once = index not in found and not speak.depth and not (ends and ends.depth)
        found[index] = (speak.tick, ends.tick if ends else tick_of(length)) if once else None
    return {index: window for index, window in found.items() if window}


def _length(order: list[Record], default: float = 0.5) -> float:
    return next((r.fields[0] for r in order if r.kind == 0 and r.sub == 1), default)


@dataclass(frozen=True)
class Scene:
    """One sub-scene's lines: every line index it speaks, and the window of each line _windows can time."""
    lines: frozenset[int]
    windows: dict[int, tuple[int, int]]


def scenes(data: bytes, byte_order: str = "<") -> list[Scene]:
    """Each sub-scene of an event file as its lines and their windows."""
    found = []
    count = struct.unpack_from(byte_order + "I", data)[0]
    for base in struct.unpack_from(f"{byte_order}{count}I", data, 4):
        order = _records(data, base, byte_order)
        lines = frozenset(_line(data, base, r, byte_order)[0] for r in order if r.kind == 5 and r.sub == SPEAK)
        found.append(Scene(lines, _windows(data, base, order, _length(order), byte_order)))
    return found


def pair_scenes(ours: list[frozenset[int]], donors: list[list[frozenset[int]]]) -> dict[int, tuple[int, int]]:
    """The donor sub-scene each of our sub-scenes was made from, as (donor file, its sub-scene), by line indices
    (a line index names one donor line game-wide): the one sharing the largest part of the lines either speaks, at
    least half; on a tie the first file (the one EVENTS pairs), then the same sub-scene number. A stock file made
    of several donor files gets each sub-scene's own: stock event 283 holds sub-scenes of two of the 79 WO1 X360
    per-officer variants of its scene (ENS 187 to 265), so its EVENTS file (ENS 265) is not the donor of either."""
    found = {}
    for sub, lines in enumerate(ours):
        def share(candidate: tuple[int, int], lines=lines, sub=sub) -> tuple:
            theirs = donors[candidate[0]][candidate[1]]
            return len(lines & theirs) / len(lines | theirs), candidate[0] == 0, candidate[1] == sub
        candidates = [(f, d) for f, scenes_of in enumerate(donors) for d, theirs in enumerate(scenes_of) if theirs]
        if lines and candidates and share(best := max(candidates, key=share))[0] >= 0.5:
            found[sub] = best
    return found


def donor_windows(stock: bytes, donors: list[list[Scene]]) -> dict[tuple[int, int], tuple[int, int]]:
    """Each stock line's window in the donor sub-scene its sub-scene was made from (pair_scenes), by (sub-scene,
    line index). donors are the scenes() of the donor game's event files, the one EVENTS pairs first."""
    pairs = pair_scenes([s.lines for s in scenes(stock)], [[s.lines for s in file] for file in donors])
    return {(sub, index): window for sub, (f, d) in pairs.items() for index, window in donors[f][d].windows.items()}


def _retime_scene(data: bytes, base: int, sub: int, holds: dict[int, float], done: Retimed,
                  donor: dict[tuple[int, int], tuple[int, int]]) -> list[Record]:
    order = _records(data, base)
    length = _length(order)
    stock = _windows(data, base, order, length)
    spoken = []  # (speak, line index, stock window, shows text) of each line the English voice retimes
    for speak in [r for r in order if r.kind == 5 and r.sub == SPEAK]:
        index, text = _line(data, base, speak)
        texts = [r for r in order[order.index(speak) + 1:] if r.text]
        why = ("no English clip" if index not in holds else "speak inside a conditional block" if speak.depth
               else "clear inside a conditional block" if texts and texts[0].depth else None)
        if why is None:
            spoken.append((speak, index, (speak.tick, texts[0].tick if texts else tick_of(length)), visible(text)))
        elif visible(text):
            done.kept[(sub, index)] = why
    # Starts first, of every English-voiced line (the speak also starts the voice, so a blank line's moves too): a
    # line Koei moved in the donor moves to the donor's tick when no other line or caption of ours lies between the
    # two ticks (a clear between them then belongs to the moved line, and goes with its end).
    for speak, index, window, shows in spoken:
        theirs = donor.get((sub, index)) if stock.get(index) == window else None
        start = subtitle_window(window, theirs, holds[index], length)[0]
        if start == speak.tick:
            continue
        at, old = order.index(speak), speak.tick
        between = order[:at] if start < old else order[at + 1:]
        if any(r.text and r.sub != CLEAR and min(start, old) <= r.tick <= max(start, old) for r in between):
            why = "another line lies between its stock and donor starts"
        else:
            order.remove(speak)
            limit = next((k for k, r in enumerate(order) if k >= at and r.text and r.sub != CLEAR), len(order))
            if _move(order, speak, start, at if start < old else limit):
                done.started[(sub, index)] = (old, start)
                continue
            order.insert(at, speak)
            why = "its donor start falls inside a conditional block"
        if shows:
            done.unshown[(sub, index)] = why
    # Then ends: each line's clears (those between its speak and the next line or caption) give way to one clear at
    # its end, unless the next line or caption ends it there.
    for speak, index, window, shows in spoken:
        if not shows:
            continue
        at = order.index(speak)
        limit = next((r for r in order[at + 1:] if r.text and r.sub != CLEAR), None)
        theirs = donor.get((sub, index)) if stock.get(index) == window and (sub, index) not in done.unshown else None
        end = subtitle_window(window, theirs, holds[index], limit.tick if limit else length)[1]
        clears = [r for r in order[at + 1:order.index(limit) if limit else len(order)] if r.text]
        if any(r.depth for r in clears):
            done.kept[(sub, index)] = "clear inside a conditional block"
            continue
        if [r.tick for r in clears] == [end] or (not clears and end >= (limit.tick if limit else tick_of(length))):
            continue  # it already ends there
        rest = [r for r in order if all(r is not c for c in clears)]
        clear = clears[0] if clears else Record(None, 5, CLEAR, (), end, 0, struct.pack("<I", 0) + CLEAR_RECORD)
        if not _move(rest, clear, end, rest.index(limit) if limit else len(rest)):
            done.kept[(sub, index)] = "its end falls inside a conditional block"
            continue
        order[:] = rest
        if window[1] != end:
            done.moved[(sub, index)] = (window[1], end)
    return order


def retime_event(stock: bytes, holds: dict[int, float], donor: dict[tuple[int, int], tuple[int, int]] | None = None,
                 ) -> tuple[bytes, Retimed]:
    """A copy of a stock (little-endian) event file whose line windows follow subtitle_window, with what changed.

    holds maps a line index to its English clip's audible end in seconds; a line without one keeps its window. Lines
    inside conditional blocks keep theirs (see Retimed.kept). donor (donor_windows) gives each line's window in the
    donor sub-scene its sub-scene was made from: where it differs from stock, the line shows it exactly."""
    data, done = bytearray(stock), Retimed()
    count = struct.unpack_from("<I", stock)[0]
    for sub, base in enumerate(struct.unpack_from(f"<{count}I", stock, 4)):
        order = _retime_scene(stock, base, sub, holds, done, donor or {})
        for record in order:
            if record.at is None:
                data.extend(bytes(-len(data) % 4))
                record.at = len(data)
                data.extend(record.new)
        for record, following in zip(order, order[1:], strict=False):
            struct.pack_into("<I", data, record.at, following.at - base)
    return bytes(data), done


EVENT_KEY = 4251  # the runtime key of LINKDATA2 entry n is 4251 + n
FOLDER = "captions/voice"  # below wozmod/: served in place of the stock entry only while the voices are English


def clip_pcm(clips: dict[int, bytes], jobs: int | None = None) -> dict[int, bytes]:
    """Each KOVS clip decoded to mono 16 kHz signed 16-bit PCM, by voice id."""
    from wozmod.formats.audio import KOVS_HEADER_BYTES, _ffmpeg_each

    def ogg(record: bytes) -> bytes:
        stream = bytearray(record[KOVS_HEADER_BYTES:])
        for position in range(min(256, len(stream))):
            stream[position] ^= position
        return bytes(stream)

    ids = sorted(clips)
    pcms = _ffmpeg_each([(["-f", "ogg"], ogg(clips[i]), ["-ac", "1", "-ar", str(AUDIBLE_RATE), "-f", "s16le"])
                         for i in ids], jobs, "timing event subtitles", (0.85, 0.9))
    return dict(zip(ids, pcms, strict=True))


def event_voice_bases() -> dict[int, int]:
    """Each event file the English voices speak (LINKDATA2 entry) and the voice id of its line 0."""
    from wozmod.formats.audio import WO1_EVENT_VOICES, WO2_EVENT_VOICES
    from wozmod.text.script_alignment import EVENTS

    return {o: (WO2_EVENT_VOICES if source == "wo2-x360" else WO1_EVENT_VOICES)[0]
            for ordinals, source, _ in EVENTS for o in ordinals}


def donor_files(ordinal: int) -> tuple[str, list[int]]:
    """The donor game a stock event file comes from, and the event files (EVENTS keys: WO1 X360 ENS ordinals, WO2
    X360 PS2 member ids) its sub-scenes are paired from (pair_scenes), the one EVENTS pairs with it first; no files
    for an event EVENTS does not pair."""
    from wozmod.text.script_alignment import EVENTS, donor_event_entries

    for ordinals, source, shift in EVENTS:
        if ordinal in ordinals:
            return source, [ordinal + shift, *(e for e in donor_event_entries(source) if e != ordinal + shift)]
    return "", []


def build_event_schedules(stock_events: dict[int, bytes], clips: dict[int, bytes], mod: Path,
                          jobs: int | None = None, donors: dict[str, tuple[dict[int, bytes], str]] | None = None,
                          ) -> dict[int, Retimed]:
    """Write mod/captions/voice/<4251 + n>.bin for every event file whose windows the English clips change; return
    what changed per event. clips are the English voice bank's KOVS records by voice id; donors each Xbox 360 donor's
    event files (by donor_files key) and byte order, whose windows Koei retimed for the English voice are taken."""
    bases = event_voice_bases()
    spoken = {}
    for o, data in stock_events.items():
        for index in event_line_indices(data):
            if bases[o] + index in clips:
                spoken.setdefault(bases[o] + index, None)
    ends = {i: audible_end(pcm) for i, pcm in clip_pcm({i: clips[i] for i in spoken}, jobs).items()}
    parsed = {source: {e: scenes(data, byte_order) for e, data in files.items()}
              for source, (files, byte_order) in (donors or {}).items()}
    folder = mod / FOLDER
    changes = {}
    for o, data in sorted(stock_events.items()):
        holds = {index: ends[bases[o] + index] for index in event_line_indices(data) if bases[o] + index in ends}
        source, entries = donor_files(o)
        files = [parsed[source][e] for e in entries if e in parsed.get(source, {})]
        retimed, done = retime_event(data, holds, donor_windows(data, files) if files else None)
        changes[o] = done
        if retimed != data:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{EVENT_KEY + o}.bin").write_bytes(retimed)
    return changes


def event_line_indices(data: bytes) -> set[int]:
    """The line index of every speak record of a stock event file."""
    count = struct.unpack_from("<I", data)[0]
    return {_line(data, base, r)[0] for base in struct.unpack_from(f"<{count}I", data, 4)
            for r in _records(data, base) if r.kind == 5 and r.sub == SPEAK}
