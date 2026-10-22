import struct

import pytest

from wozmod.formats.scripts import Line, Script, Table
from wozmod.text.psp_alignment import (
    PspPair, event_caption_slots, paired_psp_rows, psp_quote_text, stage_slots,
)
from synthetic import mesc, script, spans, strings, write_stock_archive


def _script(notices=(), dialog=()):
    return Script(
        Table(tuple(Line(b"32" + text, False) for text in notices)),
        Table(tuple(Line(b"00" + text, True) for text in dialog)),
    )


def test_stage_139_insertion_does_not_shift_dialog_after_stock_slot_67():
    stock = _script([b"Stock notice"], [f"Stock {index}".encode() for index in range(76)])
    donor = _script([b"Donor notice"], [f"Donor {index}".encode() for index in range(64)])
    expected = [("notices", 0, 0)]
    expected += [("dialog", index, index) for index in range(56)]
    expected += [("dialog", 68 + index, 56 + index) for index in range(8)]

    assert stage_slots(139, stock, donor) == expected


def test_stage_142_appended_notices_do_not_consume_a_nonexistent_donor_slot():
    stock = _script([f"Stock {index}".encode() for index in range(56)], [b"Stock dialog"])
    donor = _script([f"Donor {index}".encode() for index in range(49)], [b"Donor dialog"])
    expected = [("notices", index, index) for index in range(49)] + [("dialog", 0, 0)]

    assert stage_slots(142, stock, donor) == expected


def test_stage_pairing_rejects_unexpected_record_counts_before_zip_truncation():
    cases = [
        (132, _script([b"A", b"B"]), _script([b"A"])),
        (139, _script(dialog=[b"A"] * 75), _script(dialog=[b"B"] * 64)),
        (142, _script([b"A"] * 55), _script([b"B"] * 49)),
    ]
    accepted = []
    for ordinal, stock, donor in cases:
        try:
            stage_slots(ordinal, stock, donor)
        except ValueError:
            continue
        accepted.append(ordinal)

    assert accepted == []


def test_stage_pairing_rejects_a_shifted_notice_control_prefix():
    stock = _script([b"Stock"])
    donor = Script(Table((Line(b"11Donor", False),)), Table(()))

    with pytest.raises(ValueError, match="control prefixes"):
        stage_slots(132, stock, donor)


def test_event_pairing_sorts_ids_and_drops_shared_81_before_the_equal_count_guard():
    stock = {9: b"Stock nine", 81: b"Shared", 2: b"Stock two"}
    donor = [(0, 10, b"First"), (10, 20, b"Second")]
    cases = [
        (stock, donor, [(2, 0), (9, 1)]),
        (stock, donor[:1], []),
        (stock, [*donor, (20, 30, b"Third")], []),
    ]

    assert [event_caption_slots(ours, theirs) for ours, theirs, _ in cases] == [
        expected for _, _, expected in cases
    ]


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


def _script_bytes(notices, dialog):
    return script(strings(*([b"32Stock"] * notices)), strings(*([b"00Stock@0001"] * dialog)))


def _quotes(suffix):
    slot = strings(b"00Stock" + suffix)
    return spans([slot, slot, spans([slot] * 107)], struct.pack("<I", 3))


def _telop(count):
    return struct.pack("<I", count) + struct.pack("<II", 0, 10) * count + b"Stock\0" * count


def _event():
    body = bytes(12)
    for index in (9, 81, 2):
        body += b"\xff" * 4 + struct.pack("<HH", index, 32) + b"Stock\0"
        body += bytes((12 - len(body)) % 16)
    return body


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
    stock_entries, donor_entries, expected = {}, {}, []
    for ordinal in range(132, 144):
        member = ordinal + 138
        stock_notices, donor_notices = (56, 49) if ordinal == 142 else (1, 1)
        stock_dialog, donor_dialog = (76, 64) if ordinal == 139 else (1, 1)
        stock_entries[ordinal] = _script_bytes(stock_notices, stock_dialog)
        donor_entries[member] = _script_bytes(donor_notices, donor_dialog)
        for slot in range(donor_notices):
            expected.append(PspPair(b"Stock", ["wo2-psp", "fns", member, "notices", slot],
                                    "Z Dream stage lines", ["linkdata2", ordinal, "notices", slot]))
        for slot in range(donor_dialog):
            stock_slot = slot + 12 if ordinal == 139 and slot >= 56 else slot
            expected.append(PspPair(b"Stock", ["wo2-psp", "fns", member, "dialog", slot],
                                    "Z Dream stage lines", ["linkdata2", ordinal, "dialog", stock_slot]))
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
        donor_entries[member] = _telop(1 if ordinal == 404 else 2)
        if ordinal == 404:
            continue
        for cue, line_id in enumerate((2, 9)):
            expected.append(PspPair(b"Stock", ["wo2-psp", "fns", member, "captions", [cue]],
                                    "Event subtitles", ["linkdata2", ordinal, "event", line_id]))
    for ordinal, member in ((175, 196), (176, 197)):
        stock_entries[ordinal], donor_entries[member] = _telop(1), _telop(1)
        expected.append(PspPair(b"Stock", ["wo2-psp", "fns", member, "captions", [0]],
                                "Movie captions", ["linkdata2", ordinal, "captions", 0]))
    write_stock_archive(tmp_path, "LINKDATA2", stock_entries)
    fns_path = tmp_path / "LINKDATA_NA.FNS"
    _write_psp_archive(fns_path, donor_entries)

    assert paired_psp_rows(tmp_path, fns_path) == expected
