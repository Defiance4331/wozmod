import struct

import pytest

from wozmod.formats.scripts import (
    ScriptError,
    caption_cues,
    donor_text,
    event_captions,
    event_lines,
    event_texts,
    mesc_blocks,
    parse_script,
    quote_tables,
    quote_text,
    speak_flags,
)
from synthetic import event_file, mesc, script, spans, strings


def _table(*records, order="<"):
    header = 4 + 4 * len(records)
    offsets = [header + sum(map(len, records[:i])) for i in range(len(records))]
    return struct.pack(f"{order}{len(records) + 1}I", len(records), *offsets) + b"".join(records)


def _event(*lines, order="<"):
    data = b"\0" * 12
    for index, text, size in lines:
        data += b"\xff" * 4 + struct.pack(f"{order}HH", index, size) + text + b"\0"
        data += b"\0" * ((12 - len(data)) % 16)
    return data


def test_parse_script_drops_exactly_two_route_bytes_in_both_sections_and_word_orders():
    for order in "<>":
        data = script(
            _table(b"32Notice A\0", b"11Notice B\0\xaa", order=order),
            _table(b"00Record A@0042\0\xcc", b"00Record B\0", order=order),
            order,
        )
        parsed = parse_script(data, order)
        assert [r.text_bytes for r in parsed.notices.records] == [b"Notice A", b"Notice B"]
        assert [(r.dialog_suffix, r.text_bytes) for r in parsed.dialog.records] == [
            (b"@0042", b"Record A"), (None, b"Record B"),
        ]


def test_parse_script_rejects_invalid_section_headers_and_offsets():
    data = script(_table(b"00N\0"), _table(b"00D\0"))
    count, *offsets = struct.unpack_from("<5I", data)
    headers = [
        (3, *offsets),
        (count, 24, *offsets[1:]),
        (count, offsets[0], offsets[0], *offsets[2:]),
        (count, *offsets[:-1], len(data)),
        (count, offsets[0], offsets[1] + 1, *offsets[2:]),
    ]
    for broken in [data[:19], *(struct.pack("<5I", *h) + data[20:] for h in headers)]:
        with pytest.raises(ScriptError):
            parse_script(broken)


def test_parse_script_rejects_table_offsets_that_do_not_bound_string_records():
    table = _table(b"00A\0", b"00B\0")
    broken = [
        struct.pack("<I", 10) + table[4:],
        struct.pack("<3I", 2, 8, 16) + table[12:],
        struct.pack("<3I", 2, 16, 20) + b"00A\0" + b"00B\0" + b"00C\0",
        struct.pack("<3I", 2, 12, 12) + table[12:],
        struct.pack("<3I", 2, 12, len(table)) + table[12:],
    ]
    for notice_table in broken:
        with pytest.raises(ScriptError):
            parse_script(script(notice_table, _table(b"00D\0")))
    with pytest.raises(ScriptError):
        parse_script(script(_table(b"00N\0"), b"\0" * 3))


@pytest.mark.parametrize("notices, spoken", [
    (b"x1N\0", b"00D\0"),
    (b"00\x81\0", b"00D\0"),
    (b"00N\0", b"11D\0"),
    (b"00N\0", b"00D"),
], ids=["non-numeric-route", "invalid-cp932", "notice-route-in-spoken", "unterminated-spoken"])
def test_parse_script_rejects_a_record_that_is_not_a_terminated_numeric_route_cp932_string(notices, spoken):
    # Bug: a notice or spoken record with a non-numeric route, invalid CP932, a notice route in the spoken table, or no
    # terminator is accepted.
    with pytest.raises(ScriptError):
        parse_script(script(_table(notices), _table(spoken)))


def test_mesc_blocks_skips_an_embedded_marker_before_reading_the_next_block():
    cp932 = b"\x81\x40"
    data = b"prefix" + mesc(b"record MESC marker", cp932) + b"gap" + mesc(b"last")
    assert mesc_blocks(data) == [[b"record MESC marker", cp932], [b"last"]]


def test_event_lines_drops_conflicting_indices_but_keeps_identical_repeats():
    for order in "<>":
        data = _event((5, b"A", 32), (6, b"B", 32), (5, b"A", 32), (6, b"C", 32), order=order)
        assert event_lines(data, order) == {5: b"A"}


def test_event_lines_rejects_unaligned_matches_and_invalid_record_metadata():
    for order in "<>":
        data = _event((5, b"A", 32), (5000, b"B", 32), (7, b"C", 31), (8, b"D", 0x400), order=order)
        unaligned = b"\xff" * 4 + struct.pack(f"{order}HH", 9, 32) + b"E\0"
        assert event_lines(data + b"!" + unaligned, order) == {5: b"A"}


def test_caption_cues_keeps_each_interval_with_its_corresponding_text():
    data = struct.pack("<7I", 3, 7, 18, 19, 25, 30, 44) + b"cue A\0\0cue C\0padding\0"
    assert caption_cues(data) == [(7, 18, b"cue A"), (19, 25, b""), (30, 44, b"cue C")]


def test_quote_tables_reads_every_nested_officer_span_in_both_word_orders():
    for order in "<>":
        tables = [
            strings(b"33F1", b"11F2", order=order),
            strings(b"32N1", order=order),
            spans([strings(b"00A@1001", b"", order=order), strings(b"00B@1002", order=order)], order=order),
        ]
        data = spans(tables, struct.pack(f"{order}I", 3), order)
        assert quote_tables(data, order) == [[b"33F1", b"11F2"], [b"32N1"], [b"00A@1001", b""], [b"00B@1002"]]


def test_quote_text_keeps_cp932_trail_at_bytes_and_non_numeric_suffixes():
    cases = [
        (b"00A\x81\x40123", b"A\x81\x40123"),
        (b"11B\x81\x40Z@12345", b"B\x81\x40Z"),
        (b"32C@word", b"C@word"),
        (b"33D@12@34", b"D@12"),
    ]
    assert [(value, quote_text(value)) for value, _ in cases] == cases


def test_event_captions_read_each_caption_record_by_sub_scene_with_its_text_offset():
    # Bugs: the mask offset taken from the file start instead of the sub-scene start (a shifted text), an ordinal
    # that counts other records, a text offset at the mask (a cstr pointer that resolves to no English).
    for order in "<>":
        data, texts = event_file([(5, 0, b"A"), (5, 1, b"x"), (5, 0, b"B")], [(3, 0, b"y"), (5, 0, b"C")],
                                 order=order, gap=6)
        assert event_captions(data, order) == [(0, 0, texts[0], b"A"), (0, 1, texts[2], b"B"), (1, 0, texts[4], b"C")]


def test_event_texts_keeps_direct_and_indexed_texts_and_drops_binary_markers():
    # Bug: event_texts keeps a marker hit from a binary table (one kana, a lone lead byte, a half-width kana, a
    # private-use or control character), or drops a direct caption or an indexed subtitle. Every failing case is
    # reported.
    direct, indexed = b"\x82\xa0\x82\xa2", b"\x82\xa4\n\x82\xa6"
    cases = {"direct and indexed": (
        b"\xff" * 4 + direct + b"\0" + b"\xff" * 4 + struct.pack("<HH", 5, 32) + indexed + b"\0", [direct, indexed])}
    for text in (b"\x82\xa0", b"\x81", direct + b"\xa1", direct + b"\xf0\x40", direct + b"\x01"):
        cases[f"marker {text!r}"] = (b"\xff" * 4 + text + b"\0", [])
    wrong = [name for name, (data, expected) in cases.items() if event_texts(data) != expected]
    assert wrong == []


def test_donor_text_rejects_unmapped_high_bytes_and_preserves_mapped_letters():
    permitted = {0xB2: "\u00ef", 0xD3: "\u014c", 0xD4: "\u014d", 0xD5: "\u016a", 0xD6: "\u016b"}
    expected = [
        (value, "A" + (chr(value) if value <= 0x7F else permitted[value]) + "B" if value <= 0x7F or value in permitted else None)
        for value in range(256)
    ]
    assert [(value, donor_text(b"A" + bytes([value]) + b"B")) for value in range(256)] == expected


def test_speak_flags_reads_each_speak_commands_line_and_eighth_word():
    # Bugs: the flag is read from another word than the eighth (the handler's [esi+0xC], esi at word 1), the
    # conditions before a script's commands are skipped at another size than 18 bytes, a script offset is taken from
    # the file start instead of section 1's, or a command that is not opcode 38 counts as speaking its word 2. Each
    # turns a line's voice into the other bank's clip in the context model. Expected by hand from the layout below.
    def command(opcode, line, flag, sixth=0):
        return struct.pack("<8H", opcode, 0, line, 0, 0, 0, sixth, flag)

    scripts = [
        bytes([0, 1, 0, 0, 2, 3]) + bytes([0xAA] * 36)  # two conditions, then three commands
        + command(0x26, 3, 0, sixth=1) + command(0x26, 5, 1) + command(0x25, 7, 1),
        bytes([0, 1, 0, 0, 0, 1]) + command(0x26, 3, 4),  # line 3 again, from another script, flag set
    ]
    offsets = [12 + 4 * len(scripts)]
    offsets.append(offsets[0] + len(scripts[0]))
    section = struct.pack("<3I2I", len(scripts), 0, 0, *offsets) + b"".join(scripts)
    head = struct.pack("<5I", 4, 20, 24, 24 + len(section), 24 + len(section)) + b"HEAD"
    assert speak_flags(head + section) == {3: {0, 1}, 5: {1}}
