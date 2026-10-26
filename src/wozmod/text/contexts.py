"""Every subtitle a player sees, in the context they see it: one movie cue, event line, place caption, stage line,
battle quote or narration line, in one voice mode, with the English shown, the clip that plays and the window.

The translation table is keyed by the stock string, so a check keyed by it cannot see a string that needs two
English lines, or a line whose voice says something else. A context is keyed by where the player is. Its
`candidates` are the English the donor game itself pairs with what plays there: the donor line that owns the voice
clip (a donor script, event or narration line carries the clip it plays), the donor movie's own caption at that
time, or the donor scene's place caption. None of it comes from the pairing tools that made the table
(`script_alignment.official_pairs`, its `EVENTS` map) or from the table.

What the runtime does is modelled in one place below ("The runtime"): which loose file it serves in each voice
mode, and which key each text hook looks up. `tests/test_text_contexts.py` pins that model against
`runtime/english_runtime.c`; change both together.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path

from wozmod.donors import DonorsFolder
from wozmod.formats.audio import (
    MOZ_EVENT_LINES, PSP_CLIP_BASE, WO1_EVENT_VOICES, WO1_NARRATION_VOICES, WO1_RECORD_BASE, WO2_CLIP_BASE, WO2_EVENT_VOICES,
    WO2_NARRATION_VOICES, donor_voice_choice, ps2_extents, quote_voice, stage_line_voices, wo2_voice_records,
)
from wozmod.formats.linkdata import LINKDATA2_FIRST, archive, decoded_member, ps2_members
from wozmod.formats.scripts import (
    caption_cues, donor_text, event_lines, mesc_blocks, quote_tables, quote_text,
)
from wozmod.runtime.english_runtime import decode_english, table_lookup
from wozmod.text.narration_voices import VOICE_KEYED, camp_lines_drawn, line_voices
from wozmod.text.script_alignment import NARRATION, X360_ENS, quote_slot_read
from wozmod.text.stock import stage_line_form

MODES = ("english", "japanese")  # the voice language; the text is English throughout
CUE_TICKS = 29.97  # a movie caption schedule counts 29.97 a second (MovieTelop counter 0x5E91F0)
PAL_TICKS = 25  # a French or German schedule counts 25 a second
EVENT_TICKS = 60  # an event scene's clock counts 60 a second (runner 0x49D9A0)

# ---------------------------------------------------------------------------------------------------------------
# The runtime. Keep in step with runtime/english_runtime.c (tests/test_text_contexts.py compares them).

# loose_dirs: the folders under wozmod/ the runtime serves archive entries from, and whether a folder is the
# voice one, served in place of the other only while the voices are English (served()).
LOOSE_FOLDERS = (("entries", False), ("captions", False), ("captions/voice", True))

# The hooks that look each kind of text up in the table, in order: hook 9 (voiced_line) looks a script line or quote
# up with its @NNNN suffix (a spoken stage line with its stage and line before that), then the window hook 6 looks
# the bare text up; the event draw (0x49EAE4, 0x49EB1F) and narration reach hook 1 (draw text) with the drawn string. A kind with no hook is shown as the served file holds it:
# a movie cue (hook 44 only fits it), since the installer writes each captioned movie's schedule with its English.
LOOKUP_HOOKS = {"movie": (), "event": (1,), "caption": (1,), "mesc": (1,), "stage": (9, 6), "quote": (9, 6)}
# The narration entries whose lines the runtime keys by voice (runtime page_voice): the WO1 pages' draw
# (NARRATION_DRAW, 4073) and the still events' (STILL_DRAW for camp talk 4105, STILL_Z_DRAW for 4103). The Z pages
# 4071 are looked up by text.
VOICE_KEYED_MESC = VOICE_KEYED


def served_entry(mod: Path, key: int, mode: str) -> bytes | None:
    """The loose entry the runtime reads for archive key in a voice mode, or None when it reads the stock one."""
    found = None
    for folder, voice in LOOSE_FOLDERS:
        path = mod / "wozmod" / folder / f"{key}.bin"
        if path.is_file() and not (voice and mode == "japanese") and (found is None or voice):
            found = path
    return found.read_bytes() if found else None


def lookup_keys(kind: str, raw: bytes, voice_id: int | None = None,
                line: tuple[int, int] | None = None) -> tuple[bytes, ...]:
    """The keys the runtime looks a stock text up by, in order. raw is the drawn text, or for a script line or
    quote the line without its two-digit prefix and with its suffix. An event subtitle, or a still event's line
    (VOICE_KEYED_MESC), is tried with "@V" and its four-digit voice id first (runtime/event_line.h); pass voice_id
    only for those. A stage line a speak command shows, line (stage, dialog line), is tried with them first
    (stock.stage_line_form, runtime spoken_text)."""
    if not LOOKUP_HOOKS[kind]:
        return ()
    if kind in ("event", "mesc") and voice_id is not None:
        return (raw + b"@V%04d" % voice_id, raw)
    if kind in ("stage", "quote") and re.search(rb"@[0-9]+$", raw):
        own = (stage_line_form(raw, *line),) if kind == "stage" and line is not None else ()
        return (*own, raw, quote_text(b"00" + raw))
    return (raw,)


def shown_text(table: bytes, kind: str, raw: bytes, voice_id: int | None = None,
               line: tuple[int, int] | None = None) -> tuple[bytes, bytes | None]:
    """(what the player reads, the key that matched or None) for a stock text, as the runtime looks it up."""
    keys = lookup_keys(kind, raw, voice_id, line)
    for key in keys:
        if key and (english := table_lookup(table, key)) is not None:
            return english, key
    return (keys[-1] if keys else raw), None


# ---------------------------------------------------------------------------------------------------------------
# Voice ids, as the stock game numbers them.

CAMP_TALK = 4105  # the LINKDATA1 entry of the WO1 still events
# Narration and camp talk: LINKDATA1 entry -> the donor and its ENS entry holding the same lines in the same blocks
# (script_alignment.NARRATION). Each plays the ids narration_voices gives it; the donor line in the same place plays
# the record the installer's narration block maps that id to (formats.audio *_NARRATION_VOICES), as both programs
# pick the line and its voice by the same page, scene or record (tests/test_voice_blocks.py).
DONOR_MESC = {stock: (donor, X360_ENS[member] if donor == "wo2-x360" else member)
              for stock, donor, member in NARRATION}
QUOTES_ENTRY, WO1_QUOTES, WO2_QUOTES, PSP_QUOTES = 4084, 21, 17, 27
WO1_SCRIPTS = range(88, 148)  # WO1 X360 ENS: the stage scripts (loader 0x8219D948 reads ENS 88 + stage)
WO2_SCRIPTS = range(103, 171)  # WO2 X360 ENS: the stage scripts, PS2 members 2796-2863
PSP_SCRIPTS = range(270, 282)  # WO2 PSP FNS: the Z Dream stage scripts
# The WO2 PSP plays the Z Dream scenes (stock events 402-413) as movies GEV_201 to GEV_212 and loads the caption
# schedule FNS member GEV - 0x2B (EBOOT loader 0x5BA64, vaddr; 0x5BB18), and its movies 30 and 31 load member
# n + 0xAA - 4 (0x5BB64, 0x5BB68), the scenes of stock schedules 175 and 176 (movie200, movie201). No number links a
# stock event to a GEV file: the scenes pair by time (every PSP cue starts where a stock line's window starts,
# tests/test_text_psp_alignment.py), and lines pair with cues by time within a scene (psp_line_cues).
PSP_EVENT_CAPTIONS = {**{402 + i: 164 + i for i in range(6)}, **{408 + i: 158 + i for i in range(6)}}
PSP_MOVIE_CAPTIONS = {175: 196, 176: 197}


def event_voice(ordinal: int, index: int) -> int | None:
    """An event line's voice id: index + 0xAF3, or + 0x8F4 when [0x801F24] is set (0x513731), which it is for the
    WO1 scenes 245-383 and 401. For 384-400 and 402-413 the mode is not known."""
    if 177 <= ordinal < 245:
        return WO2_EVENT_VOICES[0] + index if index < MOZ_EVENT_LINES["wo2-x360"] else None
    if 245 <= ordinal < 384 or ordinal == 401:
        return WO1_EVENT_VOICES[0] + index if index < MOZ_EVENT_LINES["wo1-x360"] else None
    return None


def narration_record(donor: str, voice_id: int) -> int | None:
    """The donor record the installer's narration blocks map a narration voice id to."""
    for base, record, count in WO1_NARRATION_VOICES if donor == "wo1-x360" else WO2_NARRATION_VOICES:
        if base <= voice_id < base + count:
            return record + voice_id - base
    return None


def _suffix(line: bytes) -> int | None:
    match = re.search(rb"@([0-9]+)$", line)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------------------------------------------
# Event scenes: the record chain the runner 0x49D9A0 executes, as stock (little-endian) and Xbox 360 (big-endian)
# files share it. A sub-scene is a chain of records: u32 next | type | sub | n | n field types | data offset | n
# field sizes | data. Type 0 sub 0 is a gate (a float tick the clock must reach), type 0 sub 1 the length, type 5
# a text record (sub 0 place caption, sub 1 clear, sub 2 and 5 speak), type 8 sub 1 or 2 opens a conditional
# block and type 8 sub 0 closes it.

_FIELD = {0: "B", 1: "H", 2: "I", 3: "B", 4: "H", 5: "I", 6: "f", 7: "I", 8: "I"}


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
        return all(_records(data, base, order) for base in bases)
    except (struct.error, IndexError, ValueError):
        return False


def _records(data: bytes, base: int, order: str) -> list[tuple[int, int, tuple]]:
    records, at, seen = [], base, set()
    while True:
        if at in seen or not 0 <= at <= len(data) - 8:
            raise ValueError("event record chain leaves the file or loops")
        seen.add(at)
        following, kind, sub, n = struct.unpack_from(f"{order}I", data, at)[0], data[at + 4], data[at + 5], data[at + 6]
        if kind == 0xFF:
            return records + [(kind, sub, ())]
        if kind > 0x20 or n > 16 or at + 8 + 2 * n > len(data):
            raise ValueError("not an event record")
        types, start, sizes = data[at + 7:at + 7 + n], data[at + 7 + n], data[at + 8 + n:at + 8 + 2 * n]
        values, p = [], at + 7 + start
        for kind_of, size in zip(types, sizes, strict=True):
            values.append(struct.unpack_from(order + _FIELD.get(kind_of, "H"), data, p)[0])
            p += size
        records.append((kind, sub, tuple(values)))
        at = base + following


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
        records = _records(data, base, order)
        length = next((f[0] for kind, s, f in reversed(records) if kind == 0 and s == 1), 0.5)
        lengths.append(length)
        depth, inside = 0, []
        for kind, s, _ in records:
            depth += kind == 8 and s in (1, 2)
            inside.append(depth > 0)
            depth -= kind == 8 and s == 0 and depth > 0
        ran, clock, ip, tick = [], -1.0, 0, 0
        while True:
            clock = 0.0 if clock < 0 else clock + 1.0
            while records[ip][0] != 0xFF and not (records[ip][:2] == (0, 0) and records[ip][2][0] > clock):
                ran.append((tick, ip))
                ip += 1
            if not clock <= length:
                break
            tick += 1
        texts = [(t, i) for t, i in ran if records[i][0] == 5 and records[i][1] in (0, 1, 2, 5)]
        ordinal = 0
        for k, (t, i) in enumerate(texts):
            kind, s, fields = records[i]
            if s == 0:
                at = base + fields[0] + 4
                captions.append(EventCaption(sub, ordinal, at, data[at:data.index(b"\0", at)]))
                ordinal += 1
            elif s in (2, 5):
                index = struct.unpack_from(f"{order}H", data, base + fields[1] + 4)[0]
                text = data[base + fields[1] + 8:data.index(b"\0", base + fields[1] + 8)]
                following = texts[k + 1] if k + 1 < len(texts) else None
                end_by = "end" if following is None else {0: "caption", 1: "clear"}.get(records[following[1]][1], "speak")
                after = texts[k + 2] if k + 2 < len(texts) else None
                if end_by == "clear" and after and after[0] == following[0] and records[after[1]][1] != 1:
                    end_by = "speak"  # cleared on the tick the next line starts: the next line ends it
                if end_by == "clear" and following[0] >= length:
                    end_by = "end"  # cleared on the sub-scene's last frame: the scene end ends it
                windows.append(EventWindow(sub, index, t, following[0] if following else tick, end_by,
                                           inside[i] or (following is not None and inside[following[1]]), text))
    return EventScene(windows, captions, tuple(lengths))


# ---------------------------------------------------------------------------------------------------------------
# Donor truth: what each donor clip says, by the donor game's own pairing of clip and text.

@dataclass(frozen=True)
class DonorLine:
    donor: str
    pointer: tuple  # a table pointer to the line: ("wo1-x360", "ens", 249, "event", 150)
    text: str


Clip = tuple[str, int]  # (donor, record in that donor's voice bank)


def _line(donor: str, pointer: tuple, raw: bytes) -> DonorLine | None:
    text = donor_text(raw)
    return DonorLine(donor, pointer, text) if text and text.strip() else None


def script_records(donor: str, archive_name: str, entry: int, dialog: list[bytes], base: int,
                   ) -> Iterator[tuple[Clip, DonorLine]]:
    """A donor stage script's spoken lines by clip: a line ending @NNNN plays record base + NNNN - 1."""
    for i, value in enumerate(dialog):
        if (suffix := _suffix(value)) and (line := _line(donor, (donor, archive_name, entry, "dialog", i),
                                                         quote_text(value))):
            yield (donor, base + suffix - 1), line


def quote_records(donor: str, archive_name: str, entry: int, tables: list[list[bytes]], base: int,
                  ) -> Iterator[tuple[Clip, DonorLine]]:
    for t, table in enumerate(tables):
        for k, value in enumerate(table):
            if (suffix := _suffix(value)) and (line := _line(donor, (donor, archive_name, entry, "quotes", t, k),
                                                             quote_text(value))):
                yield (donor, base + suffix - 1), line


def event_records(donor: str, entries: dict[int, bytes], order: str, first: int, count: int,
                  ) -> Iterator[tuple[Clip, DonorLine]]:
    """Every donor event line by clip: line index i plays record first + i in every event file of the donor, so a
    line is found in whichever file holds it."""
    for entry, data in entries.items():
        if event_file(data, order):
            for index, raw in event_lines(data, order).items():
                if index < count and (line := _line(donor, (donor, "ens", entry, "event", index), raw)):
                    yield (donor, first + index), line


def drawn_mesc_lines(entry: int, data: bytes, voices: list[int]) -> Iterator[tuple[int, bytes, int]]:
    """(MESC position, stock text, voice id) of each line a narration entry draws: not blank, and in camp talk (4105)
    not a filler line the advance skips (narration_voices.camp_lines_drawn)."""
    drawn = camp_lines_drawn(data) if entry == CAMP_TALK else None
    position = 0
    for block in mesc_blocks(data):
        for raw in block:
            if raw.strip(b" \n\x81\x40") and (drawn is None or drawn[position]):
                yield position, raw, voices[position]
            position += 1


def mesc_records(donor: str, entry: int, blocks: list[list[bytes]], voices: list[int],
                 ) -> Iterator[tuple[Clip, DonorLine]]:
    """A donor narration entry's lines by clip: the line at each place plays the stock line's voice id there
    (voices, in MESC order), on the record narration_record maps it to."""
    if sum(map(len, blocks)) != len(voices):
        return
    position = 0
    for b, block in enumerate(blocks):
        for k, raw in enumerate(block):
            record = narration_record(donor, voices[position])
            if record is not None and (line := _line(donor, (donor, "ens", entry, "mesc", b, k), raw)):
                yield (donor, record), line
            position += 1


def _english_cues(data: bytes) -> list[tuple[int, int, str | None]]:
    return [(start, end, donor_text(raw)) for start, end, raw in caption_cues(data)]


# The donors' movie caption loaders (VA = 0x82000000 + default.pe offset; tests/test_text_movie_schedules.py reads
# the immediates). WO2 X360 0x822B9EB0, gated at 0x822B9FA8/0x822B9FB0 to movies 1-25 (from the movie start
# 0x821A072C), loads archive id nn + 0xFDF (0x822B9EEC); its archive loader 0x822F3F70 numbers ENS k as id 0xEEA + k
# (0x822B9EF0, 0x821871B4-0x821871C4). WO1 X360 0x822754A0, gated at 0x822754B4/0x822754B8 to mev02-05, loads id
# nn + 0xBC2 (0x822754F8) with ENS k at id 0xA53 + k (0x822754FC; loader 0x822A28C0), and reads it from FNS or GNS
# on French or German consoles (0x82174744). (movies, caption id, first ENS id) per donor:
MOVIE_CAPTION_LOADERS = {"wo2-x360": (range(1, 26), 0xFDF, 0xEEA, "ens"), "wo1-x360": (range(2, 6), 0xBC2, 0xA53, "fns")}


def movie_schedule(row: dict) -> tuple[str, str, int] | None:
    """The donor caption schedule timed to the movie file a data/movies.json row plays, by the donor's own loader
    (MOVIE_CAPTION_LOADERS) for the number in its file name: WO2 X360 MEVnn's English captions; WO1 X360 mev02 to
    mev05 have none in English, so their French ones (timed to the same dub)."""
    number = re.fullmatch(r"(?i)mev([0-9]+)", Path(row["source"]).stem)
    if not number or row["donor"] not in MOVIE_CAPTION_LOADERS:
        return None
    movies, caption_id, first_id, archive_name = MOVIE_CAPTION_LOADERS[row["donor"]]
    nn = int(number.group(1))
    return (row["donor"], archive_name, nn + caption_id - first_id) if nn in movies else None


def x360_schedules(movies: list[dict]) -> dict[int, int]:
    """Stock schedule entry -> the WO2 X360 ENS schedule of the movie data/movies.json plays in that slot."""
    found = {}
    for row in movies:
        entry, source = movie_slot_entry(row["slot"]), movie_schedule(row)
        if entry is not None and source is not None and source[:2] == ("wo2-x360", "ens"):
            found[entry] = source[2]
    return found


MOZ_CAPTION_KEY = 0x112C  # the stock loader 0x56A2C0 reads key index + 0x112C (0x56A319)


def movie_slot_entry(slot: str) -> int | None:
    """The LINKDATA2 entry of a movie slot's caption schedule (loader 0x56A2C0), or None when the slot loads none."""
    number = int(slot[5:])
    index = number if number < 36 else 36 + number - 100 if number < 200 else 46 + number - 200
    if index == 0 or 26 <= index <= 37 or 42 <= index <= 45 or index >= 48:
        return None
    return MOZ_CAPTION_KEY - LINKDATA2_FIRST + (index - 12 if 38 <= index <= 41 else index - 16 if index >= 46 else index)


def overlapping_cues(cues: list[tuple], donor: list[tuple]) -> list[list[int]]:
    """For each cue, the donor cues whose greatest positive time overlap is with it, in order."""
    joined: list[list[int]] = [[] for _ in cues]
    for k, (start, end, _) in enumerate(donor):
        overlap, at = max(((min(end, e) - max(start, s), i) for i, (s, e, _) in enumerate(cues)), default=(0, 0))
        if overlap > 0:
            joined[at].append(k)
    return joined


def psp_line_cues(scene: EventScene, cues: list[tuple[int, int, bytes]]) -> dict[int, list[list[int]]]:
    """Each stock event line's cues in the WO2 PSP caption schedule of the same scene, by time: the PSP plays the
    scene as a movie and shows cue k from its start to its end (CUE_TICKS a second); MOZ shows the line from its
    speak to what ends it (EVENT_TICKS a second, runner 0x49D9A0). The movie is the sub-scene that speaks the lines,
    so both clocks start together; a cue joins the window it overlaps most. A line spoken twice may have two."""
    if len({w.sub for w in scene.windows}) > 1:
        raise ValueError("lines spoken in more than one sub-scene: the PSP movie's clock is not known")
    seconds = [(w.start / EVENT_TICKS, w.end / EVENT_TICKS, None) for w in scene.windows]
    found: dict[int, list[list[int]]] = {}
    for w, joined in zip(scene.windows, overlapping_cues(seconds, [(s / CUE_TICKS, e / CUE_TICKS, None)
                                                                   for s, e, _ in cues]), strict=True):
        if joined and joined not in found.setdefault(w.index, []):
            found[w.index].append(joined)
    return {index: joined for index, joined in found.items() if joined}


def joined_cue_text(cues: list[tuple[int, int, bytes]], joined: list[int]) -> bytes:
    """The words of the cues joined to one place, as the resolver reads a ["captions", joined] pointer."""
    return b" ".join(word for k in joined for word in cues[k][2].split())


# ---------------------------------------------------------------------------------------------------------------
# Contexts.

@dataclass(frozen=True)
class Window:
    start: int
    end: int
    ticks: float  # per second
    end_by: str = ""
    in_block: bool = False


@dataclass(frozen=True)
class Context:
    id: str  # "movie021.c1", "event283.1.150", "caption219.5.0", "stage107.31", "quote12.7", "mesc4105.162"
    kind: str  # movie, event, caption, stage, quote, mesc
    mode: str
    stock: bytes  # the stock text as the hook sees it
    voice_id: int | None
    voice: Clip | None  # the donor clip that plays, or None for the stock Japanese take (or no voice)
    shown: bytes  # what the player reads
    key: bytes | None  # the table key that matched, or None (the text was shown as served)
    row: dict | None  # the table row behind key
    candidates: tuple[DonorLine, ...]  # the donor English for what plays here
    window: Window | None = None
    movie: str | None = None  # the movie file that plays: "wo2-x360:data/movie/MEV01.wmv" or "stock"
    donor_window: Window | None = None  # an event line's window in the donor scene that holds it
    stock_window: Window | None = None  # an event line's window in the stock scene
    neighbours: tuple[str, ...] = field(default=(), compare=False)  # ids of the lines next to it

    @property
    def text(self) -> str:
        return decode_english(self.shown, "replace")


DonorCues = tuple[tuple[str, str, int], list[tuple[int, int, str | None]]]


def cue_contexts(slot: str, mode: str, cues: list[tuple[int, int, bytes]], donor: DonorCues | None, shown,
                 movie: str, stock: list[tuple[int, int, bytes]] | None = None) -> list[Context]:
    """One context per cue a movie shows. donor is (donor schedule, its cues with their English), paired with the
    cues by time; shown(kind, raw) is (what the player reads, key, row); stock, the stock schedule, names the stock
    cue each served cue stands in for (by greatest time overlap)."""
    own = [raw for _, _, raw in cues]
    if stock is not None:
        for k, served in enumerate(overlapping_cues(stock, cues)):
            for c in served:
                own[c] = stock[k][2]
    joined = overlapping_cues(cues, donor[1]) if donor else [[] for _ in cues]
    found = []
    for c, (start, end, raw) in enumerate(cues):
        english, key, row = shown("movie", raw)
        words = [w for k in joined[c] for w in (donor[1][k][2] or "").split()] if donor else []
        line = DonorLine(donor[0][0], (*donor[0], "captions", joined[c]), " ".join(words)) if words else None
        found.append(Context(f"{slot}.c{c}", "movie", mode, own[c], None, None, english, key, row,
                             (line,) if line else (), Window(start, end, CUE_TICKS), movie))
    return found


class World:
    """The stock game, the donors and a text stage's output, read once; contexts(mode) lists every context.

    mod is the folder the installer's text stage wrote wozmod/ into (see build_text_stage)."""

    def __init__(self, stock: Path, donors: Path, mod: Path, data: Path):
        self.stock, self.donors, self.mod, self.data = Path(stock), Path(donors), Path(mod), Path(data)
        self.folders = DonorsFolder(self.donors)

    # --- inputs -------------------------------------------------------------------------------------------------

    @cached_property
    def table(self) -> bytes:
        return (self.mod / "wozmod/table.bin").read_bytes()

    @cached_property
    def rows(self) -> dict[str, dict]:
        return {row["key"]: row for row in json.loads((self.data / "table.json").read_text(encoding="utf-8"))["rows"]}

    @cached_property
    def linkdata2(self):
        return archive(self.stock / "LINKDATA2")

    @cached_property
    def wo1_ens(self):
        return archive(self.folders.folder("wo1-x360") / "linkdata_ens")

    @cached_property
    def wo2_ens(self):
        return archive(self.folders.folder("wo2-x360") / "linkdata_ens")

    @cached_property
    def psp(self) -> dict[int, bytes]:
        fns = self.folders.folder("wo2-psp") / "PSP_GAME/USRDIR/LINKDATA_NA.FNS"
        members = ps2_members(fns, [PSP_QUOTES, *PSP_SCRIPTS, *PSP_EVENT_CAPTIONS.values(), *PSP_MOVIE_CAPTIONS.values()])
        return {m: d if m == PSP_QUOTES else decoded_member(d) for m, d in members.items()}

    @cached_property
    def _dialogue(self) -> tuple[dict[str, dict[int, int]], dict]:
        # The same calls as voices.build.build_voice_banks (keep in step with it).
        from wozmod.text.script_alignment import (
            QUOTES, WO1_QUOTE_INSERT, moz_scripts, paired_quote_tables, stage_joins, wo1_scripts, wo2_x360,
        )
        from wozmod.voices.build import PSP_TEXT, psp_text_voice_map
        from wozmod.voices.maps import dialogue_voice_maps, slot_records, stage_triggers, voice_slots
        wo1, wo2 = self.folders.folder("wo1-x360"), self.folders.folder("wo2-x360")
        moz = moz_scripts(self.stock / "LINKDATA2")
        wo2_donor = wo2_x360(wo2 / "linkdata_ens")
        quotes_entry, wo1_quotes, wo2_quotes = QUOTES
        quotes = archive(self.stock / "LINKDATA1")[quotes_entry]
        wo1_by_ens, wo2_by_member = wo1_scripts(wo1 / "linkdata_ens"), wo2_donor.scripts()
        joins = stage_joins(moz, wo1_by_ens, wo2_by_member)
        one, two = dialogue_voice_maps(
            moz, wo1_by_ens, wo2_by_member,
            paired_quote_tables(quotes, self.wo1_ens[wo1_quotes], ">", insert=WO1_QUOTE_INSERT),
            paired_quote_tables(quotes, wo2_donor.members([wo2_quotes])[wo2_quotes], wo2_donor.order), joins)
        slots = voice_slots(stage_triggers(moz, joins), one, two)
        three = psp_text_voice_map(moz, quotes, self.folders.folder("wo2-psp") / PSP_TEXT)
        return ({"wo1-x360": one | slot_records(slots, "wo1-x360"), "wo2-x360": two | slot_records(slots, "wo2-x360"),
                 "wo2-psp": three}, slots)

    @property
    def maps(self) -> dict[str, dict[int, int]]:
        """Stock voice id (or voice slot) -> record, per donor: the installer's own maps."""
        return self._dialogue[0]

    @property
    def slots(self) -> dict:
        """(stage, line) -> the line's voice slot (voices.maps.voice_slots), as the installer writes them."""
        return self._dialogue[1]

    @cached_property
    def voices(self) -> dict[int, Clip]:
        """The donor clip the English voice bank holds for each voice id, chosen as the installer chooses it (the
        installer also drops a WO1 clip as long as the stock take, which needs the converted clip: not modelled)."""
        wo1, wo2 = self.folders.folder("wo1-x360"), self.folders.folder("wo2-x360")
        psp = self.folders.folder("wo2-psp") / "PSP_GAME/USRDIR/sound/voice_na.bns"
        return donor_voice_choice(dialogue_banks(
            self.maps["wo1-x360"], (wo1 / "linkdata_ans.idx").read_bytes(), wo1 / "link_vodat.bdx",
            self.maps["wo2-x360"], (wo2 / "linkdata_ans.idx").read_bytes(), wo2 / "link_vodat.bdx",
            (self.maps["wo2-psp"], psp)))

    @cached_property
    def banks(self) -> dict[str, tuple[list[tuple[int, int]], Path]]:
        """Each dialogue donor's clip extents and bank file."""
        wo1, wo2 = self.folders.folder("wo1-x360"), self.folders.folder("wo2-x360")
        psp = self.folders.folder("wo2-psp") / "PSP_GAME/USRDIR/sound/voice_na.bns"
        return {source: (records, bank) for source, _, records, bank in dialogue_banks(
            {}, (wo1 / "linkdata_ans.idx").read_bytes(), wo1 / "link_vodat.bdx",
            {}, (wo2 / "linkdata_ans.idx").read_bytes(), wo2 / "link_vodat.bdx", ({}, psp))}

    @cached_property
    def movies(self) -> list[dict]:
        return json.loads((self.data.parent / "movies.json").read_text(encoding="utf-8"))["movies"]

    # --- donor truth --------------------------------------------------------------------------------------------

    @cached_property
    def wo1_events(self) -> dict[int, bytes]:
        return _event_files(self.wo1_ens)

    @cached_property
    def wo2_events(self) -> dict[int, bytes]:
        return _event_files(self.wo2_ens)

    @cached_property
    def by_clip(self) -> dict[Clip, list[DonorLine]]:
        """Every donor clip's own lines."""
        from wozmod.formats.scripts import parse_script
        found: dict[Clip, list[DonorLine]] = {}

        def add(pairs):
            for clip, line in pairs:
                found.setdefault(clip, []).append(line)

        for entry, data in self.wo1_ens.read(WO1_SCRIPTS).items():
            add(script_records("wo1-x360", "ens", entry, [r.value for r in parse_script(data, ">").dialog.records],
                               WO1_RECORD_BASE))
        for entry, data in self.wo2_ens.read(WO2_SCRIPTS).items():
            add(script_records("wo2-x360", "ens", entry, [r.value for r in parse_script(data, ">").dialog.records],
                               WO2_CLIP_BASE))
        for member in PSP_SCRIPTS:
            add(script_records("wo2-psp", "fns", member,
                               [r.value for r in parse_script(self.psp[member]).dialog.records], PSP_CLIP_BASE))
        add(quote_records("wo1-x360", "ens", WO1_QUOTES, quote_tables(self.wo1_ens[WO1_QUOTES], ">"), WO1_RECORD_BASE))
        add(quote_records("wo2-x360", "ens", WO2_QUOTES, quote_tables(self.wo2_ens[WO2_QUOTES], ">"), WO2_CLIP_BASE))
        add(quote_records("wo2-psp", "fns", PSP_QUOTES, quote_tables(self.psp[PSP_QUOTES]), PSP_CLIP_BASE))
        add(event_records("wo1-x360", self.wo1_events, ">", WO1_EVENT_VOICES[1], WO1_EVENT_VOICES[2]))
        add(event_records("wo2-x360", self.wo2_events, ">", WO2_EVENT_VOICES[1], WO2_EVENT_VOICES[2]))
        for stock_entry, (donor, entry) in DONOR_MESC.items():
            ens = self.wo1_ens if donor == "wo1-x360" else self.wo2_ens
            add(mesc_records(donor, entry, mesc_blocks(ens[entry]), self.narration_voices[stock_entry]))
        return found

    def clip_lines(self, voice_id: int | None, mode: str) -> tuple[Clip | None, tuple[DonorLine, ...]]:
        """(the clip that plays, the donor lines for it). Under the English voice that is the clip the bank holds;
        under the Japanese voice the stock take plays, and the line's donor lines are those of every donor's clip
        for its voice id."""
        if voice_id is None:
            return None, ()
        if mode == "english":
            clip = self.voices.get(voice_id)
            return clip, tuple(self.by_clip.get(clip, ())) if clip else ()
        return None, tuple(line for donor, mapping in self.maps.items() if voice_id in mapping
                           for line in self.by_clip.get((donor, mapping[voice_id]), ()))

    @cached_property
    def donor_scenes(self) -> dict[str, dict[int, EventScene]]:
        return {donor: {e: event_scenes(d, ">") for e, d in files.items()}
                for donor, files in (("wo1-x360", self.wo1_events), ("wo2-x360", self.wo2_events))}

    def scene_donor(self, ordinal: int, scene: EventScene) -> tuple[str, int] | None:
        """The donor event of the same scene: of the donor files that share at least half its voiced lines, the one
        with the most sub-scenes of the same length (many short scenes share a few stock battle lines), then the
        most shared lines. Its voice ids say which donor game: WO2 X360 for 177-244, else WO1 X360."""
        donor = "wo2-x360" if 177 <= ordinal < 245 else "wo1-x360" if event_voice(ordinal, 0) is not None else None
        ours = scene.lines()
        if donor is None or not ours:
            return None

        def score(theirs: EventScene) -> tuple:
            shared = len(ours & theirs.lines()) / len(ours | theirs.lines())
            same = sum(a == b for a, b in zip(scene.lengths, theirs.lengths, strict=False))
            return (shared >= 0.5, same - abs(len(scene.lengths) - len(theirs.lengths)), shared)

        best = max(self.donor_scenes[donor], key=lambda e: score(self.donor_scenes[donor][e]), default=None)
        return (donor, best) if best is not None and score(self.donor_scenes[donor][best])[0] else None

    # --- contexts -----------------------------------------------------------------------------------------------

    def _shown(self, kind: str, raw: bytes, voice_id: int | None = None,
               line: tuple[int, int] | None = None) -> tuple[bytes, bytes | None, dict | None]:
        shown, key = shown_text(self.table, kind, raw, voice_id, line)
        keyed = hashlib.sha256(key.decode("cp932").encode("cp932")).hexdigest() if key else None
        return shown, key, self.rows.get(keyed) if keyed else None

    def contexts(self, mode: str) -> list[Context]:
        return [*self.movie_contexts(mode), *self.event_contexts(mode), *self.stage_contexts(mode),
                *self.quote_contexts(mode), *self.mesc_contexts(mode)]

    def movie_contexts(self, mode: str) -> list[Context]:
        voiced = {row["slot"]: row for row in self.movies if row["language"] == "voice"}
        found = []
        for slot in sorted({f"movie{n:03d}" for n in (*range(1, 36), *range(100, 110), *range(200, 203))}):
            entry = movie_slot_entry(slot)
            if entry is None or entry not in self.linkdata2:
                continue
            stock = caption_cues(self.linkdata2[entry])
            served = served_entry(self.mod, LINKDATA2_FIRST + entry, mode)
            cues = caption_cues(served) if served else stock
            row = voiced.get(slot) if mode == "english" else None
            movie = f"{row['donor']}:{row['source']}" if row else "stock"
            found += cue_contexts(slot, mode, cues, self._movie_donor_cues(row, entry), self._shown, movie, stock)
        return found

    def _movie_donor_cues(self, row: dict | None, entry: int):
        """((donor, archive, entry), donor cues) for the movie that plays in a slot, paired with its cues by time:
        the donor movie's own English schedule; for a stock movie the schedule of the X360 movie data/movies.json
        plays there, or the PSP schedule of the same movie."""
        if row:
            source = movie_schedule(row)
            if source is None:
                return None
            if source[1] == "fns":
                return source, self.dub_cues(source[2])
            return source, _english_cues(self.wo2_ens[source[2]])
        if (ordinal := x360_schedules(self.movies).get(entry)) is not None:
            return ("wo2-x360", "ens", ordinal), _english_cues(self.wo2_ens[ordinal])
        if entry in PSP_MOVIE_CAPTIONS:
            member = PSP_MOVIE_CAPTIONS[entry]
            return ("wo2-psp", "fns", member), _english_cues(self.psp[member])
        return None

    def dub_cues(self, entry: int) -> list[tuple[int, int, str]]:
        """A WO1 dub movie's cues: the French schedule's times on the stock clock, each with the words the dub says
        there (data/text/dub_captions.json, owner-approved transcripts; ledger text.md row 13)."""
        from wozmod.text.resolver import DonorResolver
        movie = next(m for m in json.loads((self.data / "dub_captions.json").read_text(encoding="utf-8"))["movies"]
                     if m["timing"] == ["wo1-x360", "fns", entry])
        french = caption_cues((self.folders.folder("wo1-x360") / f"linkdata_fns/{entry}.bin").read_bytes())
        resolver = DonorResolver(self.donors)
        words = [resolver.resolve(cue) if isinstance(cue, list) else cue for cue in movie["cues"]]
        stock = lambda t: int(t * CUE_TICKS / PAL_TICKS + 0.5)  # noqa: E731
        return [(stock(start), stock(end), text) for (start, end, _), text in zip(french, words, strict=True)]

    def event_contexts(self, mode: str) -> list[Context]:
        found = []
        for ordinal in range(177, 414):
            if ordinal not in self.linkdata2:
                continue
            stock_data = self.linkdata2[ordinal]
            served = served_entry(self.mod, LINKDATA2_FIRST + ordinal, mode)
            data = served or stock_data
            if not event_file(data):
                continue
            parsed, stock = event_scenes(data), event_scenes(stock_data)
            windows, captions = parsed.windows, parsed.captions
            texts = event_lines(data)
            scene = self.scene_donor(ordinal, stock)
            stock_windows = dict(zip(_window_ids(ordinal, stock.windows),
                                     (Window(w.start, w.end, EVENT_TICKS, w.end_by, w.in_block) for w in stock.windows), strict=False))
            donor_windows = self._donor_windows(ordinal, stock) if event_voice(ordinal, 0) is not None else {}
            psp_lines = self._psp_event_lines(ordinal, stock)
            ids = _window_ids(ordinal, windows)
            for k, w in enumerate(windows):
                raw = w.text
                if not raw.strip(b" \n\x81\x40"):
                    continue
                voice_id = event_voice(ordinal, w.index)
                clip, lines = self.clip_lines(voice_id, mode)
                shown, key, row = self._shown("event", raw, voice_id)
                found.append(Context(ids[k], "event", mode, raw, voice_id, clip, shown, key, row,
                                     lines or psp_lines.get(w.index, ()),
                                     Window(w.start, w.end, EVENT_TICKS, w.end_by, w.in_block),
                                     donor_window=donor_windows.get((w.sub, w.index)),
                                     stock_window=stock_windows.get(ids[k]),
                                     neighbours=tuple(ids[max(0, k - 2):k] + ids[k + 1:k + 3])))
            spoken = {w.index for w in windows}
            for index, raw in sorted(texts.items()):  # a line no speak record reaches: shown nowhere, listed for coverage
                if index not in spoken and raw.strip(b" \n\x81\x40"):
                    shown, key, row = self._shown("event", raw)
                    found.append(Context(f"event{ordinal}.-.{index}", "event", mode, raw, None, None, shown, key, row, ()))
            for c in captions:
                shown, key, row = self._shown("caption", c.text)
                line = None
                if scene and scene[0] == "wo2-x360":  # the WO1 X360 event files caption their places in German
                    donor_captions = [d for d in self.donor_scenes[scene[0]][scene[1]].captions
                                      if (d.sub, d.ordinal) == (c.sub, c.ordinal)]
                    line = donor_captions and _line(scene[0], (scene[0], "ens", scene[1], "cstr", donor_captions[0].offset),
                                                    donor_captions[0].text)
                found.append(Context(f"caption{ordinal}.{c.sub}.{c.ordinal}", "caption", mode, c.text, None, None,
                                     shown, key, row, (line,) if line else ()))
        # A place caption is one string shown in every scene at that place: each scene's donor caption is a
        # candidate for all of them. Only WO2 X360 scenes hold English place captions: every WO1 X360 event file
        # holds English subtitles but German place captions.
        by_text: dict[bytes, dict[tuple, DonorLine]] = {}
        for context in found:
            if context.kind == "caption":
                by_text.setdefault(context.stock, {}).update({line.pointer: line for line in context.candidates})
        order = {"wo2-x360": 0, "wo1-x360": 1, "wo2-psp": 2}
        return [replace(c, candidates=tuple(sorted(by_text[c.stock].values(), key=lambda line: order[line.donor])))
                if c.kind == "caption" else c for c in found]

    def _donor_windows(self, ordinal: int, stock: EventScene) -> dict[tuple[int, int], Window]:
        """Each stock line's window in the donor sub-scene its sub-scene was made from, by (sub-scene, line index):
        the retimer's own pairing (voices.events.pair_scenes, by line indices over the donor's event files), timed
        by this module's simulation. A line spoken twice there, or whose window a conditional block touches, has
        none."""
        from wozmod.text.script_alignment import X360_ENS
        from wozmod.voices.events import donor_files, pair_scenes

        source, entries = donor_files(ordinal)
        keys = [X360_ENS[e] if source == "wo2-x360" else e for e in entries]  # donor_scenes keys files by ENS entry
        files = [self.donor_scenes[source][k] for k in keys if k in self.donor_scenes.get(source, {})]

        def lines(scene: EventScene) -> list[frozenset[int]]:
            return [frozenset(w.index for w in scene.windows if w.sub == s) for s in range(len(scene.lengths))]

        found: dict[tuple[int, int], Window] = {}
        for sub, (f, d) in pair_scenes(lines(stock), [lines(file) for file in files]).items():
            windows = [w for w in files[f].windows if w.sub == d]
            for w in windows:
                if not w.in_block and sum(v.index == w.index for v in windows) == 1:
                    found[(sub, w.index)] = Window(w.start, w.end, EVENT_TICKS, w.end_by, w.in_block)
        return found

    def _psp_event_lines(self, ordinal: int, stock: EventScene) -> dict[int, tuple[DonorLine, ...]]:
        """The PSP caption of each line of a stock event the PSP plays as a movie, by time (psp_line_cues)."""
        member = PSP_EVENT_CAPTIONS.get(ordinal)
        if member is None:
            return {}
        cues = caption_cues(self.psp[member])
        found = {index: tuple(line for k in joined if (line := _line(
                     "wo2-psp", ("wo2-psp", "fns", member, "captions", k), joined_cue_text(cues, k))))
                 for index, joined in psp_line_cues(stock, cues).items()}
        return {index: lines for index, lines in found.items() if lines}

    def stage_contexts(self, mode: str) -> list[Context]:
        """One context per stage line and the bank flag of each speak command that speaks it (no stock line has
        two); a line no command speaks has no voice. A line with a voice slot plays the slot under the English voice;
        under the Japanese voice the stock take of its id plays, and the slot's donor line is its counterpart."""
        from wozmod.text.script_alignment import moz_scripts
        found = []
        scripts = moz_scripts(self.stock / "LINKDATA2")
        for ordinal, script in sorted(scripts.items()):
            voices = stage_line_voices(ordinal, script)
            for i, record in enumerate(script.dialog.records):
                if not record.text_bytes.strip(b" \n\x81\x40"):
                    continue
                shown, key, row = self._shown("stage", record.value[2:], line=(ordinal, i) if voices.get(i) else None)
                slot = self.slots.get((ordinal, i))
                for n, voice_id in enumerate([slot.slot] * len(voices[i]) if slot else voices.get(i) or [None]):
                    clip, lines = self.clip_lines(voice_id, mode)
                    found.append(Context(f"stage{ordinal}.{i}" + (f"#{n}" if n else ""), "stage", mode,
                                         record.value[2:], voice_id, clip, shown, key, row, lines,
                                         neighbours=(f"stage{ordinal}.{i - 1}", f"stage{ordinal}.{i + 1}")))
        return found

    def quote_contexts(self, mode: str) -> list[Context]:
        found = []
        for t, table in enumerate(quote_tables(archive(self.stock / "LINKDATA1")[QUOTES_ENTRY])):
            for k, line in enumerate(table):
                if not quote_slot_read(t, k) or (suffix := _suffix(line)) is None:  # never shown, or no voice
                    continue
                voice_id = quote_voice(suffix)
                clip, lines = self.clip_lines(voice_id, mode)
                shown, key, row = self._shown("quote", line[2:])
                found.append(Context(f"quote{t}.{k}", "quote", mode, line[2:], voice_id, clip, shown, key, row, lines))
        return found

    @cached_property
    def narration_voices(self) -> dict[int, list[int]]:
        """Each narration entry's line voice ids, in MESC order."""
        exe, linkdata1 = (self.stock / "OROCHI_Z.exe").read_bytes(), archive(self.stock / "LINKDATA1")
        return {entry: line_voices(exe, entry, linkdata1[entry]) for entry in DONOR_MESC}

    def mesc_contexts(self, mode: str) -> list[Context]:
        found = []
        linkdata1 = archive(self.stock / "LINKDATA1")
        for entry, voices in self.narration_voices.items():
            for position, raw, voice_id in drawn_mesc_lines(entry, linkdata1[entry], voices):
                clip, lines = self.clip_lines(voice_id, mode)
                shown, key, row = self._shown("mesc", raw, voice_id if entry in VOICE_KEYED_MESC else None)
                found.append(Context(f"mesc{entry}.{position}", "mesc", mode, raw, voice_id, clip, shown, key, row,
                                     lines))
        return found


def _window_ids(ordinal: int, windows: list[EventWindow]) -> list[str]:
    """event<ordinal>.<sub-scene>.<line>; a line spoken again in its sub-scene (cast alternatives) adds #1, #2."""
    seen: dict[str, int] = {}
    ids = []
    for w in windows:
        base = f"event{ordinal}.{w.sub}.{w.index}"
        seen[base] = seen.get(base, -1) + 1
        ids.append(base + (f"#{seen[base]}" if seen[base] else ""))
    return ids


def _event_files(ens) -> dict[int, bytes]:
    """The event files of a donor ENS archive, found by their structure (they are stored unpacked)."""
    return {e: d for e, d in ens.read(list(ens), unpack=False).items() if d[:4] != b"LZP2" and event_file(d, ">")}


def dialogue_banks(wo1_mapping, wo1_index, wo1_bank, wo2_mapping, wo2_index, wo2_bank, psp):
    """The dialogue banks in the order formats.audio.english_voice_clips gives them: WO2 X360, WO1 X360, WO2 PSP."""
    return [("wo2-x360", wo2_mapping, wo2_voice_records(wo2_index), wo2_bank),
            ("wo1-x360", wo1_mapping, wo2_voice_records(wo1_index), wo1_bank),
            ("wo2-psp", psp[0], ps2_extents(psp[1]), psp[1])]


def audible_end(pcm: bytes, rate: int) -> float:
    """When a clip stops being heard: voices.events.audible_end, the retimer's own measure."""
    from wozmod.voices.events import audible_end as measure
    return measure(pcm, rate)


def audible_ends(world: World, clips: set[Clip], jobs: int | None = None) -> dict[Clip, float]:
    """The audible end of each donor clip by voice id's clip: the retimer's measure of the converted clip when the
    text stage recorded it (build_text_stage), else measured on the donor clip decoded to 16 kHz mono."""
    recorded = world.mod / "event-audible.json"
    if recorded.is_file():
        by_id = {int(i): end for i, end in json.loads(recorded.read_text(encoding="utf-8")).items()}
        by_clip = {clip: by_id[i] for i, clip in world.voices.items() if clip in clips and i in by_id}
        if set(by_clip) == set(clips):
            return by_clip
    from wozmod.formats.audio import _ffmpeg_each, _read_at
    wanted = sorted(clips)
    riffs = []
    for source in sorted({donor for donor, _ in wanted}):
        records, bank = world.banks[source]
        with bank.open("rb") as stream:
            riffs += [(clip, _read_at(stream, *records[clip[1]])) for clip in wanted if clip[0] == source]
    pcm = _ffmpeg_each([(["-threads", "1", "-f", "wav"], riff, ["-ac", "1", "-ar", "16000", "-f", "s16le"])
                        for _, riff in riffs], jobs, "measuring voice clips")
    return {clip: audible_end(data, 16000) for (clip, _), data in zip(riffs, pcm, strict=True)}


def build_text_stage(stock: Path, donors: Path, out: Path) -> Path:
    """Run the installer's text stage into out (table.bin and the served caption schedules), and its event retiming
    for the event clips only (converting the whole voice bank takes minutes), and return out."""
    from wozmod.donors import WO2_X360
    from wozmod.formats.audio import english_voice_clips
    from wozmod.install import _text_table
    from wozmod.text.script_alignment import EVENTS, donor_event_entries, wo2_x360
    from wozmod.voices.events import build_event_schedules, event_line_indices, event_voice_bases
    stock, donors, out = Path(stock), Path(donors), Path(out)
    _text_table(stock, donors, out)
    world = World(stock, donors, out, Path(__file__).resolve().parents[3] / "data/text")
    stock_events = world.linkdata2.read([o for ordinals, _, _ in EVENTS for o in ordinals])
    bases = event_voice_bases()
    wanted = {bases[o] + i for o, data in stock_events.items() for i in event_line_indices(data)}
    maps = {donor: {i: r for i, r in mapping.items() if i in wanted} for donor, mapping in world.maps.items()}
    wo1, wo2 = world.folders.folder("wo1-x360"), world.folders.folder("wo2-x360")
    psp = world.folders.folder("wo2-psp") / "PSP_GAME/USRDIR/sound/voice_na.bns"
    clips, _ = english_voice_clips(
        maps["wo1-x360"], (wo1 / "linkdata_ans.idx").read_bytes(), wo1 / "link_vodat.bdx",
        maps["wo2-x360"], (wo2 / "linkdata_ans.idx").read_bytes(), wo2 / "link_vodat.bdx",
        decoded_member(archive(stock / "LINKDATA1")[4058]), stock / "data/Sound/Voice/Voice.bin",
        psp=(maps["wo2-psp"], psp))
    wo2_donor = wo2_x360(wo2 / "linkdata_ens")
    wo1_events = world.wo1_ens.read(donor_event_entries("wo1-x360"))
    wo2_events = wo2_donor.members(donor_event_entries(WO2_X360.name))
    from wozmod.voices.events import audible_end as event_audible_end, clip_pcm
    (out / "event-audible.json").write_text(json.dumps(  # measured as the retimer measures them
        {str(i): event_audible_end(pcm) for i, pcm in clip_pcm(clips).items()}), encoding="utf-8")
    build_event_schedules(stock_events, clips, out / "wozmod", None,
                          {"wo1-x360": (wo1_events, ">"), WO2_X360.name: (wo2_events, wo2_donor.order)})
    return out
