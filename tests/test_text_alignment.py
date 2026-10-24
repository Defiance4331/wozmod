import os
from pathlib import Path
import struct

import pytest

from wozmod.formats.scripts import Line, Table
from wozmod.text.script_alignment import (
    NARRATION,
    QUOTE_INSERT,
    WO1_QUOTE_INSERT,
    Script,
    caption_pairs,
    donor_event_lines,
    event_caption_pairs,
    event_pairs,
    moz_scripts,
    narration_pairs,
    pair_scripts,
    prefix_pairs,
    quote_pairs,
    record_pairs,
    x360_members,
)
from synthetic import event_file, mesc, script, spans, strings, write_ens


def _script(notices=(), dialog=()):
    return Script(
        Table(tuple(Line(b"32" + text, False) for text in notices)),
        Table(tuple(Line(b"00" + text + (suffix or b""), True) for suffix, text in dialog)),
    )


def _voices(indices):
    return _script(dialog=[(b"@%04d" % index, b"Fixture") for index in indices])


def _quotes(formats, officers, order="<"):
    return spans(
        [
            strings(*formats, order=order),
            strings(b"11Fixture %s", order=order),
            spans([strings(*lines, order=order) for lines in officers], order=order),
        ],
        struct.pack(f"{order}I", 3),
        order,
    )


def _event(*lines, order="<"):
    data = b"\0" * 12
    for index, text in lines:
        data += b"\xff" * 4 + struct.pack(f"{order}HH", index, 32) + text + b"\0"
        data += b"\0" * ((12 - len(data)) % 16)
    return data


def _telop(*cues):
    times = (time for start, end, _ in cues for time in (start, end))
    return struct.pack(f"<{1 + 2 * len(cues)}I", len(cues), *times) + b"".join(
        text + b"\0" for _, _, text in cues
    )


def test_script_pairing_keeps_the_exact_union_threshold_and_skips_voiceless_rows():
    stock = {7: _voices(range(1, 10)), 8: _script(), 9: _voices([99])}
    accepted = _voices(range(1, 11))
    assert pair_scripts(stock, [accepted]) == {7: accepted}


def test_script_pairing_chooses_the_best_union_ratio_instead_of_raw_shared_count():
    stock = _voices(range(1, 11))
    too_many_extra = _voices(range(1, 21))
    exact_subset = _voices(range(1, 10))
    assert pair_scripts({3: stock}, [too_many_extra, exact_subset]) == {3: exact_subset}


def test_record_pairing_drops_duplicate_voice_candidates_and_incomplete_notice_prefixes():
    stock = _script(
        [b"Stock notice"],
        [(b"@0001", b"S1"), (b"@0002", b"S2"), (b"@0003", b"S3"), (None, b"S4")],
    )
    donor = _script(
        [b"Fixture first", b"Fixture second"],
        [(b"@0001", b"Fixture A"), (b"@0002", b"Fixture B"), (b"@0002", b"Fixture C"),
         (b"@0003", b"\x82\xa0"), (None, b"Fixture unsuffixed")],
    )
    assert record_pairs(stock, donor) == [(b"S1", "Fixture A")]


def test_prefix_pairing_keeps_renumbered_dialog_and_stops_before_appended_rows():
    stock = _script([b"N1", b"N2", b"Append"], [(b"@0102", b"S1"), (b"@0103", b"S2"), (b"@0104", b"Append")])
    donor = _script([b"Fixture first", b"Fixture second"], [(b"@0100", b"Fixture A"), (b"@0101", b"Fixture B")])
    assert prefix_pairs(stock, donor) == [
        (b"N1", "Fixture first"), (b"N2", "Fixture second"),
        (b"S1", "Fixture A"), (b"S2", "Fixture B"),
    ]


def test_narration_pairing_drops_unequal_blocks_and_blank_stock_slots():
    stock = mesc(b"S1", b"\x81\x40", b"S3") + mesc(b"S4", b"S5")
    donor = mesc(b"Fixture A", b"Fixture blank", b"Fixture C") + mesc(b"Fixture mismatch")
    assert narration_pairs(stock, donor) == [(b"S1", "Fixture A"), (b"S3", "Fixture C")]


@pytest.mark.skipif(not (os.environ.get("WOZMOD_DONORS") and os.environ.get("WOZMOD_GAME")),
                    reason="set WOZMOD_DONORS and WOZMOD_GAME")
def test_each_narration_donor_entry_has_the_stock_pages_block_for_block():
    # Catches a narration entry off by one (WO1 X360 still pages are ENS 6, which 0x8226F09C loads; ENS 7 has no
    # pages), which pairs no page at all.
    from wozmod.formats.linkdata import archive
    from wozmod.formats.scripts import mesc_blocks

    donors, game = Path(os.environ["WOZMOD_DONORS"]), Path(os.environ["WOZMOD_GAME"])
    stock = archive(game / "LINKDATA1").read(n for n, _, _ in NARRATION)
    wo1 = archive(donors / "wo1-x360/linkdata_ens").read(e for _, s, e in NARRATION if s == "wo1-x360")
    wo2 = x360_members(donors / "wo2-x360/linkdata_ens", [e for _, s, e in NARRATION if s == "wo2-x360"])
    unequal = [n for n, s, e in NARRATION if [len(b) for b in mesc_blocks(stock[n])]
               != [len(b) for b in mesc_blocks((wo1 if s == "wo1-x360" else wo2)[e])]]
    assert unequal == []


def test_event_pairing_joins_line_ids_across_every_donor_event_file_with_both_word_orders():
    # A line index names one voiced line game-wide. Bugs: joining only the one donor file paired with the stock event
    # drops a line another file shows (MOZ event 283 merges WO1 officer variants); joining by order shifts lines;
    # an index two donor files caption differently is paired at all; the 0x08 byte MOZ leaves at a line is paired.
    stock = _event((5, b"\x82\xa01"), (6, b"\x82\xa02"), (7, b"\x82\xa03"), (9, b"\x82\xa04"), (4, b"\x82\xa05"), (8, b"\x08"))
    for order in "<>":
        donor = [_event((7, b"Fixture C"), (5, b"Fixture A"), (4, b"Fixture D"), order=order),
                 _event((9, b"Fixture B"), (5, b"Fixture A"), (8, b"Fixture extra"), (4, b"Fixture E"), order=order)]
        assert event_pairs(stock, donor_event_lines(donor, order)) == [
            (b"\x82\xa01", "Fixture A"), (b"\x82\xa03", "Fixture C"), (b"\x82\xa04", "Fixture B")]


def test_event_caption_pairing_joins_place_captions_by_sub_scene_and_order_inside_it():
    # Place captions carry no line index. Bugs: pairing by order over the whole file shifts every caption after a
    # sub-scene where the donor has one more; a caption the donor left in Japanese is paired.
    stock, _ = event_file([(5, 0, b"S1")], [(5, 0, b"S2"), (5, 0, b"S3")], [(5, 0, b"S4")])
    donor, _ = event_file([(5, 0, b"Fixture A"), (5, 0, b"Fixture extra")],
                          [(5, 0, b"Fixture B"), (3, 0, b"Fixture other"), (5, 0, b"Fixture C")],
                          [(5, 0, b"\x82\xa0\x82\xa2")], order=">", gap=4)
    assert event_caption_pairs(stock, donor, ">") == [(b"S1", "Fixture A"), (b"S2", "Fixture B"), (b"S3", "Fixture C")]


def test_caption_pairing_merges_split_cues_using_greatest_overlap_and_later_ties():
    stock = _telop((0, 10, b"S1"), (10, 20, b"S2"), (25, 40, b"S3"))
    donor = _telop(
        (0, 4, b"Fixture \n A"), (4, 8, b"B"), (5, 15, b"C"),
        (19, 25, b"D"), (25, 30, b" "), (30, 35, b"E"), (50, 60, b"Outside"),
    )
    assert caption_pairs(stock, donor) == [
        (b"S1", b"Fixture A B"), (b"S2", b"C D"), (b"S3", b"E"),
    ]


def test_quote_pairing_joins_officer_tables_by_officer_id_for_each_donor():
    # Catches a donor's officers shifted by the stock officers it lacks: X360 lacks MOZ 94-95; WO1 has 0-78, then
    # its generic officers 79-87, which are MOZ 96-104.
    stock = _quotes([b"33F1", b"11F2"], [[b"00S%d@%d" % (index, 5000 + index), b""] for index in range(107)])
    donors = {QUOTE_INSERT: (105, lambda i: None if i in (94, 95) else i if i < 94 else i - 2),
              WO1_QUOTE_INSERT: (88, lambda i: i if i < 79 else i - 17 if 96 <= i <= 104 else None)}
    for insert, (count, theirs) in donors.items():
        expected = {b"F1": "Fixture one", b"F2": "Fixture two", b"Fixture %s": "Fixture %s"}
        expected |= {b"S%d" % i: f"Fixture {theirs(i)}" for i in range(107) if theirs(i) is not None}
        for order in "<>":
            donor = _quotes([b"33Fixture one", b"11Fixture two"],
                            [[b"00Fixture %d@%d" % (index, 4000 + index), b"00Extra@1"] for index in range(count)], order)
            assert dict(quote_pairs(stock, donor, order=order, insert=insert)) == expected, (insert, order)


def test_quote_pairing_with_officers_disabled_keeps_only_the_two_format_tables():
    stock = _quotes([b"33S1"], [[b"00Officer stock@1"]])
    donor = _quotes([b"33Fixture format"], [[b"00Fixture officer@2"]])
    assert quote_pairs(stock, donor, officers=False) == [
        (b"S1", "Fixture format"), (b"Fixture %s", "Fixture %s"),
    ]


def test_x360_member_reader_does_not_shift_ps2_member_ids_or_relabel_results(tmp_path):
    values = {17: b"Fixture quote", 103: b"Fixture first script", 275: b"Fixture last event"}
    ens = write_ens(tmp_path, values, 276)
    assert x360_members(ens, [2968, 45, 2796]) == {
        2968: b"Fixture last event", 45: b"Fixture quote", 2796: b"Fixture first script",
    }


def test_stock_script_reader_excludes_multimember_entries_and_invalid_script_headers(tmp_path):
    data = script(strings(b"32Fixture notice"), strings(b"00Fixture dialog@0001"))
    allocation = lambda data: data + bytes(-len(data) % 2048)
    body = allocation(data) + allocation(data[:24]) + allocation(data[24:]) + allocation(struct.pack("<I", 4))
    index = struct.pack("<9I", len(data), 0, 0, 1, 4096, 0, 4, 0, 0)
    index += struct.pack("<3I", 24, len(data) - 24, 0)
    (tmp_path / "LINKDATA2.BIN").write_bytes(body)
    (tmp_path / "LINKDATA2.IDX").write_bytes(index)
    found = moz_scripts(tmp_path / "LINKDATA2")
    assert {ordinal: (entry.notices.records[0].text_bytes, entry.dialog.records[0].text_bytes) for ordinal, entry in found.items()} == {
        0: (b"Fixture notice", b"Fixture dialog"),
    }
