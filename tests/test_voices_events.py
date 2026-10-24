"""Event subtitles under the English voice end with the English line (wozmod.voices.events).

Every check runs the written file through a copy of the game's runner (0x49D9A0) written here from the engine's
rules, not through the retimer's own model: the clock counts 0, 1, 2 a frame, a gate above the clock stops the
chain, a window runs from a speak to the next text record or the sub-scene end, and a false conditional block skips
what is inside it.
"""

from __future__ import annotations

import math
import os
import struct
from array import array
from pathlib import Path

import pytest

from wozmod.voices.events import SUBTITLE_PAD, audible_end, retime_event

# ---------------------------------------------------------------------------------------------------------------
# A replica of the runner.

WIDTH = {0: "B", 1: "H", 2: "I", 3: "B", 4: "H", 5: "I", 6: "f", 7: "I", 8: "I"}

def chain(data: bytes, base: int, order: str = "<") -> list[tuple[int, int, tuple, int]]:
    """(type, sub, fields, offset) of each record in chain order."""
    out, at = [], base
    while True:
        following, kind, sub, n = struct.unpack_from(order + "I", data, at)[0], data[at + 4], data[at + 5], data[at + 6]
        if kind == 0xFF:
            return out
        types, start, sizes = data[at + 7:at + 7 + n], data[at + 7 + n], data[at + 8 + n:at + 8 + 2 * n]
        values, p = [], at + 7 + start
        for t, size in zip(types, sizes, strict=True):  # the type says the width (a gate gives its float size 0)
            values.append(struct.unpack_from(order + WIDTH.get(t, "H"), data, p)[0])
            p += size
        out.append((kind, sub, tuple(values), at))
        at = base + following


def run(data: bytes, true_blocks=lambda fields: True, order: str = "<"):
    """Per sub-scene: the (tick, type, sub, fields) of every record the runner executes, and the windows as
    {line index: (start tick, end tick)}; true_blocks says which conditional blocks hold."""
    count = struct.unpack_from(order + "I", data)[0]
    scenes = []
    for base in struct.unpack_from(f"{order}{count}I", data, 4):
        records = chain(data, base, order)
        length = next(f[0] for k, s, f, _ in records if (k, s) == (0, 1))
        ran, clock, ip, tick, skip = [], -1.0, 0, 0, 0
        while True:
            clock = 0.0 if clock < 0 else clock + 1.0
            while ip < len(records):
                kind, sub, fields, at = records[ip]
                if kind == 8:
                    skip = (skip + 1 if skip or not true_blocks(fields) else 0) if sub in (1, 2) else max(skip - 1, 0)
                elif not skip:
                    if (kind, sub) == (0, 0) and fields[0] > clock:
                        break
                    ran.append((tick, kind, sub, fields, at))
                ip += 1
            if not clock <= length:
                break
            tick += 1
        windows, shown = {}, None
        for t, kind, sub, fields, _ in ran:
            if kind == 5:
                if shown is not None:
                    windows[shown[0]] = (shown[1], t)
                shown = (struct.unpack_from(order + "H", data, base + fields[1] + 4)[0], t) if sub == 5 else None
        if shown is not None:
            windows[shown[0]] = (shown[1], tick)
        scenes.append((ran, windows))
    return scenes


def others(scene) -> list[tuple]:
    """The executed records that are neither text nor gates: (tick, type, sub, offset). The retime never moves or
    copies them, so a record is its offset."""
    return [(t, kind, sub, at) for t, kind, sub, _, at in scene[0] if kind != 5 and (kind, sub) != (0, 0)]


def links(data: bytes) -> set[int]:
    """The offsets of every record's link field."""
    count = struct.unpack_from("<I", data)[0]
    return {at + k for base in struct.unpack_from(f"<{count}I", data, 4) for *_, at in chain(data, base)
            for k in range(4)} | {base + k for base in struct.unpack_from(f"<{count}I", data, 4) for k in range(4)}


def only_links_changed(stock: bytes, served: bytes) -> bool:
    """Whether the served file is the stock file with added bytes at its end and only chain links rewritten."""
    changed = {k for k in range(len(stock)) if stock[k] != served[k]}
    return len(served) >= len(stock) and changed <= links(stock) | links(served)


# ---------------------------------------------------------------------------------------------------------------
# Synthetic event files.

def record(kind: int, sub: int, *fields: tuple[int, str, object]) -> bytes:
    n = len(fields)
    head = 8 + 2 * n
    start = (head + 3) // 4 * 4
    body = bytes([kind, sub, n, *(t for t, _, _ in fields), start - 7, *(struct.calcsize(f) for _, f, _ in fields)])
    return (body + bytes(start - 4 - len(body)) + b"".join(struct.pack("<" + f, v) for _, f, v in fields))


def gate(t: float) -> bytes:
    return record(0, 0, (6, "f", t))


def scene_file(scenes: list[list[bytes | tuple]]) -> bytes:
    """An event file of sub-scenes; each item is record bytes or ("speak", index, text, size). Line records go
    after each chain, with a fake lip block of `size` bytes when size is nonzero."""
    out = bytearray(4 + 4 * len(scenes))
    struct.pack_into("<I", out, 0, len(scenes))
    for k, items in enumerate(scenes):
        out.extend(bytes(-len(out) % 16))
        base = len(out)
        struct.pack_into("<I", out, 4 + 4 * k, base)
        lines, sizes = [], []
        for item in items:
            sizes.append(4 + len(item if isinstance(item, bytes) else record(5, 5, (4, "H", 0), (8, "I", 0))))
        offset = sum(sizes) + 8
        offset += -offset % 16
        at = 0
        for item, size in zip(items, sizes, strict=True):
            if isinstance(item, tuple):
                _, index, text, lip = item
                line = struct.pack("<IHH", 0xFFFFFFFF, index, lip) + text + b"\0"
                line += bytes(-len(line) % 16) + bytes(lip)
                item = record(5, 5, (4, "H", 0), (8, "I", offset))
                lines.append(line)
                offset += len(line)
            out.extend(struct.pack("<I", at + size) + item)
            at += size
        out.extend(struct.pack("<I", 0) + bytes([0xFF, 0, 0, 0]))
        out.extend(bytes(-(len(out) - base) % 16))
        for line in lines:
            out.extend(line)
    return bytes(out)


A, B = b"\x82\xa0" * 4, b"\x82\xa2" * 4  # kana: what a stock line holds


def target(start: int, seconds: float) -> int:
    return start + math.ceil((seconds + SUBTITLE_PAD) * 60)


def test_each_window_ends_at_its_english_clip_and_never_blanks_the_next_line():
    """Bug: the clear lands after the next line's speak at the same tick (the next subtitle blanked), or one tick
    off; or a window that lingers is left alone. Line 1 lingers (clear at 400), line 2 is cut by its clear
    (at 500), line 3's clip runs past line 4's start (its clear must land just before line 4's speak, at the same
    tick); line 4 has no English clip and ends with the sub-scene."""
    stock = scene_file([[gate(0), record(0, 1, (6, "f", 1200.0)), gate(10), ("speak", 1, A, 0), record(9, 0),
                         gate(400), record(5, 1), gate(450), ("speak", 2, B, 0), gate(500), record(5, 1),
                         gate(700), ("speak", 3, A, 0), gate(730), record(5, 1), gate(760), ("speak", 4, B, 0), record(9, 1)]])
    holds = {1: 2.0, 2: 3.5, 3: 4.0}  # line 4 has no English clip: it keeps its stock window
    windows = run(retime_event(stock, holds)[0])[0][1]
    assert windows == {1: (10, target(10, 2.0)), 2: (450, target(450, 3.5)), 3: (700, 760), 4: run(stock)[0][1][4]}


def test_every_other_record_keeps_its_tick():
    """Bug: a relink that drops, repeats or delays a record that follows a moved or added clear (a camera cut or
    animation running late), or an added gate that holds back records already due."""
    stock = scene_file([[gate(0), record(0, 1, (6, "f", 900.0)), record(9, 0), gate(10), ("speak", 1, A, 0),
                         record(9, 1), gate(50), record(9, 2), gate(300), record(5, 1), record(9, 3), gate(301),
                         record(9, 4), gate(600), ("speak", 2, B, 0), record(9, 5), gate(650), record(9, 6)]])
    retimed = retime_event(stock, {1: 0.5, 2: 0.2})[0]
    assert others(run(retimed)[0]) == others(run(stock)[0])
    assert run(retimed)[0][1] == {1: (10, target(10, 0.5)), 2: (600, target(600, 0.2))}


def test_a_line_inside_a_conditional_block_keeps_its_stock_window():
    """Bug: a clear moved out of its branch, so one cast alternative's timing cuts another's line, or a gate added
    inside a block that a false condition skips."""
    open_block = record(8, 2, (19, "I", 1000), (5, "I", 3), (10, "I", 1))
    stock = scene_file([[gate(0), record(0, 1, (6, "f", 900.0)), gate(10), open_block, ("speak", 1, A, 0),
                         record(8, 0), open_block, ("speak", 2, B, 0), record(8, 0), gate(400), record(5, 1)]])
    retimed, done = retime_event(stock, {1: 1.0, 2: 1.0})
    assert retimed == stock
    assert set(done.kept) == {(0, 1), (0, 2)}


def test_a_line_whose_record_has_no_lip_block_is_retimed():
    """Bug: a line record with lip size 0 read as a blank line (Shu 1 event 281 lines 0, 1, 3 and 12 have real
    text and size 0), so its window keeps the stock end; and a blank line given a window of its own."""
    stock = scene_file([[gate(0), record(0, 1, (6, "f", 900.0)), gate(10), ("speak", 1, A, 0), gate(400),
                         record(5, 1), gate(420), ("speak", 2, b"\x81\x40", 0), gate(800), record(5, 1),
                         gate(810), ("speak", 3, B, 32), gate(880), record(5, 1)]])
    retimed, done = retime_event(stock, {1: 1.0, 2: 1.0, 3: 0.5})
    assert run(retimed)[0][1] == {1: (10, target(10, 1.0)), 2: (420, 800), 3: (810, target(810, 0.5))}
    assert set(done.moved) == {(0, 1), (0, 3)}


def test_a_window_koei_retimed_in_the_donor_is_shown_exactly_and_no_other_is():
    """Bug: a window Koei retimed for the English voice ends at the clip instead of where the donor ends it (later:
    lines 2, 4 and 5; earlier: line 3), a start moved to the donor's tick but not the end, a stale clear left
    behind a moved speak that cuts its line (line 4: line 3's old clear, then its own at 1000, before its donor end
    1050), a line moved past another line, or the donor rule applied to a line whose donor window is the stock one
    (line 1). Lines 3 and 4: the donor moves line 4 before line 3's stock clear and ends line 3 one tick before it
    (207.2 lines 127 and 128, 179.1 lines 43 and 44). Line 5 starts later in the donor. Line 6's donor start lies
    before lines 4 and 5: it keeps its start and ends with its clip."""
    stock = scene_file([[gate(0), record(0, 1, (6, "f", 1600.0)), gate(10), ("speak", 1, A, 0), gate(200),
                         record(5, 1), gate(320), ("speak", 2, B, 0), record(9, 0), gate(500), record(5, 1),
                         gate(600), ("speak", 3, A, 0), gate(700), record(5, 1), gate(720), ("speak", 4, B, 0),
                         gate(1000), record(5, 1), gate(1100), ("speak", 5, A, 0), gate(1400), record(5, 1),
                         gate(1420), ("speak", 6, B, 0), gate(1480), record(5, 1)]])
    holds = {1: 2.0, 2: 2.0, 3: 1.5, 4: 4.0, 5: 1.0, 6: 0.2}
    donor = {(0, 1): (10, 200), (0, 2): (260, 560), (0, 3): (600, 690), (0, 4): (691, 1050), (0, 5): (1150, 1300),
             (0, 6): (1000, 1480)}
    retimed, done = retime_event(stock, holds, donor)
    assert run(retimed)[0][1] == {1: (10, target(10, 2.0)), **{i: donor[0, i] for i in range(2, 6)},
                                  6: (1420, target(1420, 0.2))}
    assert others(run(retimed)[0]) == others(run(stock)[0])
    assert set(done.started) == {(0, 2), (0, 4), (0, 5)} and set(done.unshown) == {(0, 6)}


def test_each_sub_scene_takes_the_donor_file_it_was_made_from():
    """Bug: a stock file made of sub-scenes of several donor files read against the one EVENTS pairs (stock 283
    holds sub-scenes of two WO1 per-officer variants, ENS 249 and 257, and EVENTS pairs ENS 265): line 1 then starts
    at the paired file's tick 75 in both sub-scenes, and line 3 shows the paired file's window. In the right donor
    files line 1 and line 3 have the stock windows, so the lines follow their clips."""
    from wozmod.voices.events import donor_windows, scenes

    def variant(*lines):
        return [gate(0), record(0, 1, (6, "f", 900.0)), *(item for index, start, end in lines
                for item in (gate(start), ("speak", index, A, 0), gate(end), record(5, 1)))]

    stock = scene_file([variant((1, 135, 264), (2, 264, 400), (3, 500, 600)),
                        variant((1, 135, 314), (5, 314, 400), (3, 500, 640))])
    paired = scene_file([variant((1, 75, 258), (8, 258, 400), (3, 500, 580))])
    officers = [scene_file([variant((1, 135, 264), (2, 264, 400), (3, 500, 600))]),
                scene_file([variant((1, 135, 314), (5, 314, 400), (3, 500, 640))])]
    holds = {i: 1.0 for i in (1, 2, 3, 5)}
    windows = donor_windows(stock, [scenes(f, "<") for f in (paired, *officers)])
    shown = [scene[1] for scene in run(retime_event(stock, holds, windows)[0])]
    assert shown == [{1: (135, target(135, 1.0)), 2: (264, target(264, 1.0)), 3: (500, target(500, 1.0))},
                     {1: (135, target(135, 1.0)), 5: (314, target(314, 1.0)), 3: (500, target(500, 1.0))}]


def test_the_last_line_of_an_event_ends_on_its_last_frame_when_its_clip_runs_past_it():
    """Bug: the clear of a line whose clip outlasts the event left at its stock tick, or moved past the sub-scene's
    last frame, where it never runs. The shape of 254.1 line 438: the last line of the last sub-scene (length
    2750) is cleared at 2730 and its English clip runs 2.00 s from 2644, past the end; the window ends on the
    sub-scene's last frame, which is where the scene end cuts it."""
    stock = scene_file([[gate(0), record(0, 1, (6, "f", 360.0))],
                        [gate(0), record(0, 1, (6, "f", 2750.0)), gate(2195), ("speak", 437, A, 0), gate(2644),
                         ("speak", 438, B, 0), record(9, 0), gate(2730), record(5, 1)]])
    retimed, done = retime_event(stock, {437: 1.0, 438: 2.0})
    assert run(retimed)[1][1] == {437: (2195, target(2195, 1.0)), 438: (2644, 2750)}
    assert done.moved[(1, 438)] == (2730, 2750)


def test_the_audible_end_is_where_the_voice_stops_not_where_the_clip_does():
    """Bug: the clip's length used for its end (an Xbox 360 WO2 clip has about 0.8 s of silent tail), a frame
    off, or the quiet breath kept as voice (the floor applied to the wrong level)."""
    rate = 16_000
    voice = [int(12_000 * math.sin(i / 7)) for i in range(int(1.234 * rate))]
    breath = [int(120 * math.sin(i / 3)) for i in range(int(0.4 * rate))]  # -40 dB below the voice
    pcm = array("h", voice + breath + [0] * int(0.8 * rate)).tobytes()
    assert audible_end(pcm) == pytest.approx(1.24, abs=0.005)


# ---------------------------------------------------------------------------------------------------------------
# The real game.

REAL = pytest.mark.skipif(
    not (os.environ.get("WOZMOD_EVENT_TIMING") == "1" and os.environ.get("WOZMOD_GAME")
         and os.environ.get("WOZMOD_DONORS")),
    reason="set WOZMOD_EVENT_TIMING=1, WOZMOD_GAME and WOZMOD_DONORS (builds the voice banks: a few minutes)")


def shown(data: bytes) -> list[set[int]]:
    """Per sub-scene, the lines whose stock text shows something: not only ideographic or ASCII spaces."""
    count = struct.unpack_from("<I", data)[0]
    found = []
    for base in struct.unpack_from(f"<{count}I", data, 4):
        lines = set()
        for kind, sub, fields, _ in chain(data, base):
            if (kind, sub) == (5, 5):
                at = base + fields[1]
                text = data[at + 8:data.index(b"\0", at + 8)]
                if text.replace(b"\x81\x40", b"").replace(b" ", b""):
                    lines.add(struct.unpack_from("<H", data, at + 4)[0])
        found.append(lines)
    return found


def speaks(data: bytes, order: str = "<") -> list[list[int]]:
    """Per sub-scene, the line index of each speak record in chain order."""
    count = struct.unpack_from(order + "I", data)[0]
    return [[struct.unpack_from(order + "H", data, base + fields[1] + 4)[0]
             for kind, sub, fields, _ in chain(data, base, order) if (kind, sub) == (5, 5)]
            for base in struct.unpack_from(f"{order}{count}I", data, 4)]


def donor_runs(donors: dict[str, dict[int, bytes]]) -> dict[tuple[str, int], tuple]:
    """Each donor (big-endian) event file run with every conditional block true and with every block false, and
    its speaks."""
    return {(source, e): (run(data, order=">"), run(data, lambda _: False, order=">"), speaks(data, ">"))
            for source, files in donors.items() for e, data in files.items()}


def koei_windows(ordinal: int, stock: bytes, runs: dict[tuple[str, int], tuple]) -> dict[tuple[int, int], tuple]:
    """Each stock line's window in the donor sub-scene its sub-scene was made from (the retimer's pairing), as the
    replica runs the donor file, by (sub-scene, line index): for a line spoken once there, its window the same
    whatever its blocks hold."""
    from wozmod.voices.events import donor_files, pair_scenes

    source, entries = donor_files(ordinal)
    files = [runs[source, e] for e in entries]
    pairs = pair_scenes([frozenset(lines) for lines in speaks(stock)], [[frozenset(lines) for lines in said]
                                                                       for _, _, said in files])
    found = {}
    for sub, (f, d) in pairs.items():
        (_, windows), (_, unblocked), said = files[f][0][d], files[f][1][d], files[f][2][d]
        found |= {(sub, index): window for index, window in windows.items()
                  if unblocked.get(index) == window and said.count(index) == 1}
    return found


def timing_failures(stock: dict[int, bytes], served: dict[int, bytes], clips: dict[int, bytes],
                    donors: dict[str, dict[int, bytes]]) -> list[str]:
    """Every way the served event files miss the English clips or the windows Koei retimed in the donor, by
    event, sub-scene and line."""
    from wozmod.voices.events import clip_pcm, donor_files, donor_windows, retime_event, scenes

    bases = {o: 0xAF3 if o < 245 else 0x8F4 for o in stock}  # the voice of line 0: WO2 events, then WO1 events
    spoken = {bases[o] + index for o, data in stock.items() for _, windows in run(data) for index in windows}
    audible = {v: audible_end(p) for v, p in clip_pcm({v: clips[v] for v in spoken if v in clips}).items()}
    runs, parsed = donor_runs(donors), {(s, e): scenes(d, ">") for s, files in donors.items() for e, d in files.items()}
    failures = [f"{o}: stock bytes other than chain links changed" for o, data in sorted(stock.items())
                if not only_links_changed(data, served[o])]
    for o, data in sorted(stock.items()):
        source, entries = donor_files(o)
        done = retime_event(data, {i: 1.0 for _, w in run(data) for i in w},
                            donor_windows(data, [parsed[source, e] for e in entries]))[1]
        koei = koei_windows(o, data, runs)
        for sub, (old, new, lines) in enumerate(zip(run(data), run(served[o]), shown(data), strict=True)):
            if others(old) != others(new):
                failures.append(f"{o}.{sub}: a non-text record runs at another tick")
            if old[1].keys() != new[1].keys():
                failures.append(f"{o}.{sub}: lines {sorted(old[1].keys() ^ new[1].keys())} gained or lost a window")
            records, windows = new
            scene_end = max(t for t, *_ in records)
            for index, (start, end) in windows.items():
                key = (sub, index)
                if index not in lines or key in done.kept or bases[o] + index not in audible:
                    continue
                if key in koei and koei[key] != old[1][index] and key not in done.unshown:
                    if (start, end) != koei[key]:
                        failures.append(f"{o}.{sub} line {index}: shown {(start, end)} where Koei's donor window "
                                        f"is {koei[key]}")
                    continue
                heard = audible[bases[o] + index]
                following = min((t for t, k, s, *_ in records if k == 5 and s in (0, 5) and t > start), default=None)
                clamped = end in (following, scene_end) or end >= scene_end
                if end < start + heard * 60 and not clamped:
                    failures.append(f"{o}.{sub} line {index}: ends {start + heard * 60 - end:.0f} ticks before "
                                    "its English line")
                if end > target(start, heard) + 1:
                    failures.append(f"{o}.{sub} line {index}: up {end - target(start, heard)} ticks past the pad")
    return failures


@REAL
def test_every_english_voiced_event_line_ends_with_its_clip(tmp_path):
    """Bug: any window on the real stock events that still cuts its English line or lingers past the pad (the
    clip of another line measured, the WO1 and WO2 voice bases swapped), a cutscene record that runs at another
    tick, a line that gains or loses its window, or a window Koei retimed in the donor sub-scene the stock one was
    made from that is not shown exactly (the donor's window read from the EVENTS file instead, 283.1 and 283.2
    line 149). Runs the voice stage as the installer does and reports every failing line by event, sub-scene and
    line index."""
    from wozmod.donors import DonorsFolder
    from wozmod.formats.linkdata import archive
    from wozmod.text.script_alignment import EVENTS, donor_event_entries, wo2_x360
    from wozmod.voices import build_voice_banks
    from wozmod.voices.events import EVENT_KEY, FOLDER

    build_voice_banks(Path(os.environ["WOZMOD_GAME"]), Path(os.environ["WOZMOD_DONORS"]), tmp_path)
    raw = (tmp_path / "voice/Voice.bin").read_bytes()
    clips = {}
    for i in range(struct.unpack_from("<I", raw)[0]):
        offset, size = struct.unpack_from("<II", raw, 4 + 8 * i)
        if size:
            clips[i] = raw[offset:offset + size]
    stock = archive(Path(os.environ["WOZMOD_GAME"]) / "LINKDATA2").read([o for r, _, _ in EVENTS for o in r])
    served = {o: (p.read_bytes() if (p := tmp_path / FOLDER / f"{EVENT_KEY + o}.bin").exists() else data)
              for o, data in stock.items()}
    folders = DonorsFolder(Path(os.environ["WOZMOD_DONORS"]))
    donors = {"wo1-x360": archive(folders.folder("wo1-x360") / "linkdata_ens").read(donor_event_entries("wo1-x360")),
              "wo2-x360": wo2_x360(folders.folder("wo2-x360") / "linkdata_ens").members(
                  donor_event_entries("wo2-x360"))}
    failures = timing_failures(stock, served, clips, donors)
    assert not failures, f"{len(failures)} lines:\n" + "\n".join(failures)
