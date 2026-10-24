import json
import os
import struct
from pathlib import Path

import pytest

from wozmod.formats.linkdata import archive, decoded_member, ps2_members
from wozmod.formats.scripts import caption_cues
from wozmod.text.contexts import (
    CUE_TICKS, EVENT_TICKS, PSP_EVENT_CAPTIONS, PSP_MOVIE_CAPTIONS, event_scenes, psp_line_cues,
)
from wozmod.text.psp_alignment import PspPair, paired_psp_rows, psp_quote_text
from wozmod.text.stock import stock_key
from synthetic import mesc, script, spans, strings, timed_event, write_stock_archive

REPO = Path(__file__).resolve().parents[1]
PSP_FNS = "wo2-psp/PSP_GAME/USRDIR/LINKDATA_NA.FNS"



def test_a_psp_cue_joins_the_stock_line_shown_at_its_time_not_the_line_at_its_position():
    # Bugs: cue k given to the k-th line by index (line 9 speaks before line 2 here; line 7 has no PSP cue), a cue
    # split in two given to two lines, or the PSP clock (29.97 a second) read as the event clock (60 a second), which
    # moves cue 3 into line 2's window.
    scene = event_scenes(timed_event([(60.0, 170.0, 9, b"a"), (180.0, 300.0, 2, b"b"), (400.0, 460.0, 5, b"c"),
                                      (500.0, 560.0, 7, b"d")], 600.0))
    cues = [(31, 80, b""), (91, 120, b""), (121, 148, b""), (200, 228, b"")]

    assert psp_line_cues(scene, cues) == {9: [[0]], 2: [[1, 2]], 5: [[3]]}


def test_psp_quote_suffix_strip_keeps_internal_tags_and_non_voice_suffixes():
    cases = [
        (b"00Fixture@SV123_45", b"Fixture"),
        (b"32Fixture@7", b"Fixture"),
        (b"11Fixture@SV7_8 inside", b"Fixture@SV7_8 inside"),
        (b"00Fixture@SV7_word", b"Fixture@SV7_word"),
        (b"00Fixture\x81\x40SV7_8", b"Fixture\x81\x40SV7_8"),
        (b"00Fixture", b"Fixture"),
    ]

    assert [(value, psp_quote_text(value)) for value, _ in cases] == cases


def _script_bytes():
    """Two notices and two lines, of which an event shows notice 0 and has person 3 speak line 0."""
    return script(strings(b"32Stock", b"32Stock"), strings(b"00Stock@0001", b"00Stock@0001"),
                  events=(((0x25, 0, 0, 0, 0, 0, 0, 0), (0x26, 3, 0, 0, 0, 0, 0, 0)),))


def _quotes(suffix):
    slot = strings(b"00Stock" + suffix)
    return spans([slot, slot, spans([slot] * 107)], struct.pack("<I", 3))


def _telop(*times):
    return struct.pack("<I", len(times)) + b"".join(struct.pack("<II", *t) for t in times) + b"Stock\0" * len(times)


def _event():
    return timed_event([(60.0, 170.0, 9, b"Stock"), (180.0, 300.0, 2, b"Stock")], 600.0)


def _write_psp_archive(path, entries):
    sector, index, body = 2, b"", b""
    for ordinal in range(282):
        data = entries.get(ordinal, b"")
        index += struct.pack("<II", sector, len(data))
        padded = data + bytes(-len(data) % 2048)
        body += padded
        sector += len(padded) // 2048
    header = struct.pack("<4I", 0x33B59, 282, 2048, 0) + index
    path.write_bytes(header + bytes(4096 - len(header)) + body)


def test_psp_candidate_reader_keeps_duplicate_occurrences_at_their_original_member_and_slot(tmp_path):
    # Bugs: a reader that pairs stage records by place also pairs the notice and line no command shows; a quote,
    # briefing or caption occurrence lands at another member or slot, or a duplicate string is dropped.
    stock_entries, donor_entries, expected = {}, {}, []
    for ordinal in range(132, 144):
        member = ordinal + 138
        stock_entries[ordinal], donor_entries[member] = _script_bytes(), _script_bytes()
        for view in ("notices", "dialog"):
            expected.append(PspPair(b"Stock", ["wo2-psp", "fns", member, view, 0],
                                    "Z Dream stage lines", ["linkdata2", ordinal, view, 0]))
    write_stock_archive(tmp_path, "LINKDATA1", {4190: mesc(b"Stock") * 40, 4084: _quotes(b"@0001")})
    for row in range(28, 40):
        donor_entries[33 + row] = mesc(b"Stock")
        expected.append(PspPair(b"Stock", ["wo2-psp", "fns", 33 + row, "mesc", 0, 0],
                                "Z Dream briefings", ["linkdata1", 4190, "mesc", row, 0]))
    donor_entries[27] = _quotes(b"@SV1_2")
    for table in range(109):
        group = "New officers' quotes" if table in (96, 97) else "Other quotes"
        expected.append(PspPair(b"Stock", ["wo2-psp", "fns", 27, "quotes", table, 0], group,
                                ["linkdata1", 4084, "quotes", table, 0]))
    event_members = [(402 + slot, 164 + slot) for slot in range(6)]
    event_members += [(408 + slot, 158 + slot) for slot in range(6)]
    for ordinal, member in event_members:
        stock_entries[ordinal] = _event()
        donor_entries[member] = _telop((31, 80)) if ordinal == 404 else _telop((31, 80), (91, 148))
        for cue, line_id in enumerate((9,) if ordinal == 404 else (9, 2)):
            expected.append(PspPair(b"Stock", ["wo2-psp", "fns", member, "captions", [cue]],
                                    "Event subtitles", ["linkdata2", ordinal, "event", line_id]))
    for ordinal, member in ((175, 196), (176, 197)):
        stock_entries[ordinal], donor_entries[member] = _telop((0, 10)), _telop((0, 10))
        expected.append(PspPair(b"Stock", ["wo2-psp", "fns", member, "captions", [0]],
                                "Movie captions", ["linkdata2", ordinal, "captions", 0]))
    write_stock_archive(tmp_path, "LINKDATA2", stock_entries)
    fns_path = tmp_path / "LINKDATA_NA.FNS"
    _write_psp_archive(fns_path, donor_entries)

    assert paired_psp_rows(tmp_path, fns_path) == expected


@pytest.fixture(scope="module")
def psp_inputs() -> tuple[Path, Path]:
    game, donors = os.environ.get("WOZMOD_GAME"), os.environ.get("WOZMOD_DONORS")
    if not game or not donors or not Path(donors, PSP_FNS).is_file() or not Path(game).is_dir():
        pytest.skip("set WOZMOD_GAME and WOZMOD_DONORS (with the WO2 PSP extract)")
    return Path(game), Path(donors, PSP_FNS)


def test_each_psp_scene_is_the_stock_scene_its_cues_are_timed_to(psp_inputs):
    # Bug: a stock event or movie given another scene's PSP schedule (PSP_EVENT_CAPTIONS, PSP_MOVIE_CAPTIONS: no
    # number links them, see contexts.py), or the PSP movie's clock started at the wrong stock sub-scene. Expected:
    # every PSP cue starts where a stock line's window (or the stock movie's cue) starts, within 0.1 s (3 PSP
    # frames; the 92 cues measured differ by 0.06 s at most), when both clocks start at the speaking sub-scene.
    game, fns = psp_inputs
    pairs = {**PSP_EVENT_CAPTIONS, **PSP_MOVIE_CAPTIONS}
    stock = archive(game / "LINKDATA2").read(list(pairs))
    members = ps2_members(fns, list(pairs.values()))
    failing = []
    for ordinal, member in pairs.items():
        if ordinal in PSP_EVENT_CAPTIONS:
            starts = [w.start / EVENT_TICKS for w in event_scenes(stock[ordinal]).windows]
            cues = caption_cues(decoded_member(members[member]))
        else:
            starts = [start / CUE_TICKS for start, _, _ in caption_cues(stock[ordinal])]
            cues = caption_cues(members[member])
        failing += [f"{ordinal}->fns {member} cue {k}" for k, (start, _, _) in enumerate(cues)
                    if min((abs(start / CUE_TICKS - s) for s in starts), default=1) > 0.1]
    assert failing == []


def test_every_psp_caption_row_points_at_the_cues_shown_at_its_line(psp_inputs):
    # Bug: a row paired by position (the k-th sorted line to cue k, movie cue k to cue k), which drifts where the
    # stock speaks a higher line index first, adds a line the PSP does not caption or splits a sentence: the row
    # shows a neighbour's caption. Expected: every table row that points at a PSP event or movie caption points at
    # the cues the time join (psp_line_cues, overlapping_cues) gives one of the places showing its stock string.
    game, fns = psp_inputs
    members = {*PSP_EVENT_CAPTIONS.values(), *PSP_MOVIE_CAPTIONS.values()}
    joins: dict[str, list] = {}
    for pair in paired_psp_rows(game, fns):
        if pair.ref[2] in members and pair.ref[3] == "captions":
            joins.setdefault(stock_key(pair.stock)[0], []).append(pair)
    rows = json.loads((REPO / "data/text/table.json").read_text(encoding="utf-8"))["rows"]
    failing = [(row["key"][:12], row["ref"], [(p.location, p.ref) for p in joins.get(row["key"], [])])
               for row in rows if (ref := row.get("ref")) and ref[:2] == ["wo2-psp", "fns"] and ref[2] in members
               and ref not in [p.ref for p in joins.get(row["key"], [])]]
    assert failing == []

