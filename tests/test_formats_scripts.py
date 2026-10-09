import struct

import pytest

from wozmod.formats.scripts import (
    ScriptError,
    caption_cues,
    donor_text,
    event_lines,
    event_texts,
    mesc_blocks,
    parse_script,
    quote_tables,
    quote_text,
)


def _table(*records, order="<"):
    header = 4 + 4 * len(records)
    offsets = [header + sum(map(len, records[:i])) for i in range(len(records))]
    return struct.pack(f"{order}{len(records) + 1}I", len(records), *offsets) + b"".join(records)


def _script(notices, dialog, order="<"):
    sections = [b"AAAA", b"BBBB", notices + bytes(-len(notices) % 4), dialog]
    offsets = [20 + sum(map(len, sections[:i])) for i in range(4)]
    return struct.pack(f"{order}5I", 4, *offsets) + b"".join(sections)


def _mesc(*lines):
    offsets, body = [], b""
    for line in lines:
        offsets.append(12 + 4 * len(lines) + len(body))
        body += line + b"\0"
    return b"MESC" + struct.pack(f"<II{len(lines)}I", len(lines), 12 + 4 * len(lines) + len(body), *offsets) + body


def _event(*lines, order="<"):
    data = b"\0" * 12
    for index, text, size in lines:
        data += b"\xff" * 4 + struct.pack(f"{order}HH", index, size) + text + b"\0"
        data += b"\0" * ((12 - len(data)) % 16)
    return data


def _strings(*lines, order="<"):
    return _table(*(line + b"\0" for line in lines), order=order)


def _spans(parts, head=b"", order="<"):
    at, table = len(head) + 8 * len(parts), b""
    for part in parts:
        table += struct.pack(f"{order}II", at, len(part))
        at += len(part)
    return head + table + b"".join(parts)


def test_parse_script_drops_exactly_two_route_bytes_in_both_sections_and_word_orders():
    for order in "<>":
        data = _script(
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
    data = _script(_table(b"00N\0"), _table(b"00D\0"))
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
            parse_script(_script(notice_table, _table(b"00D\0")))
    with pytest.raises(ScriptError):
        parse_script(_script(_table(b"00N\0"), b"\0" * 3))


def test_parse_script_rejects_a_non_numeric_route_prefix():
    with pytest.raises(ScriptError):
        parse_script(_script(_table(b"x1N\0"), _table(b"00D\0")))


def test_parse_script_rejects_an_invalid_cp932_record():
    with pytest.raises(ScriptError):
        parse_script(_script(_table(b"00\x81\0"), _table(b"00D\0")))


def test_parse_script_rejects_a_notice_route_in_a_spoken_record():
    with pytest.raises(ScriptError):
        parse_script(_script(_table(b"00N\0"), _table(b"11D\0")))


def test_parse_script_rejects_an_unterminated_spoken_record():
    with pytest.raises(ScriptError):
        parse_script(_script(_table(b"00N\0"), _table(b"00D")))


def test_mesc_blocks_skips_an_embedded_marker_before_reading_the_next_block():
    cp932 = b"\x81\x40"
    data = b"prefix" + _mesc(b"record MESC marker", cp932) + b"gap" + _mesc(b"last")
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
            _strings(b"33F1", b"11F2", order=order),
            _strings(b"32N1", order=order),
            _spans([_strings(b"00A@1001", b"", order=order), _strings(b"00B@1002", order=order)], order=order),
        ]
        data = _spans(tables, struct.pack(f"{order}I", 3), order)
        assert quote_tables(data, order) == [[b"33F1", b"11F2"], [b"32N1"], [b"00A@1001", b""], [b"00B@1002"]]


def test_quote_text_keeps_cp932_trail_at_bytes_and_non_numeric_suffixes():
    cases = [
        (b"00A\x81\x40123", b"A\x81\x40123"),
        (b"11B\x81\x40Z@12345", b"B\x81\x40Z"),
        (b"32C@word", b"C@word"),
        (b"33D@12@34", b"D@12"),
    ]
    assert [(value, quote_text(value)) for value, _ in cases] == cases


def test_event_texts_reads_direct_captions_and_indexed_subtitles():
    direct = b"\x82\xa0\x82\xa2"
    indexed = b"\x82\xa4\n\x82\xa6"
    data = b"\xff" * 4 + direct + b"\0" + b"\xff" * 4 + struct.pack("<HH", 5, 32) + indexed + b"\0"
    assert event_texts(data) == [direct, indexed]


def test_event_texts_drops_binary_markers_without_printable_full_width_text():
    pair = b"\x82\xa0\x82\xa2"
    candidates = [b"\x82\xa0", b"\x81", pair + b"\xa1", pair + b"\xf0\x40", pair + b"\x01"]
    data = b"".join(b"\xff" * 4 + text + b"\0" for text in candidates)
    assert event_texts(data) == []


def test_donor_text_rejects_unmapped_high_bytes_and_preserves_mapped_letters():
    permitted = {0xB2: "\u00ef", 0xD3: "\u014c", 0xD4: "\u014d", 0xD5: "\u016a", 0xD6: "\u016b"}
    expected = [
        (value, "A" + (chr(value) if value <= 0x7F else permitted[value]) + "B" if value <= 0x7F or value in permitted else None)
        for value in range(256)
    ]
    assert [(value, donor_text(b"A" + bytes([value]) + b"B")) for value in range(256)] == expected
