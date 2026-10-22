import struct
from pathlib import Path

import pytest

from wozmod.text import DonorResolver
from wozmod.text.resolver import program_text
from synthetic import mesc, script, spans, strings, write_ens


def _script(tag: bytes, order: str) -> bytes:
    notices = strings(b"32" + tag + b" notice zero", b"11" + tag + b" notice one", order=order)
    dialog = strings(b"00" + tag + b" dialog zero@0020", b"00" + tag + b" dialog one@0021", order=order)
    return script(notices, dialog, order)


def _events(tag: bytes, order: str) -> bytes:
    body = bytearray(12)
    for line_id, text in ((7, tag + b" event seven"), (11, tag + b" event eleven")):
        body.extend(b"\xff" * 4 + struct.pack(f"{order}HH", line_id, 48) + text + b"\0")
        body.extend(bytes((12 - len(body)) % 16))
    return bytes(body)


@pytest.fixture
def donors(tmp_path: Path) -> DonorResolver:
    for folder, tag, order in (("wo1-x360", b"WO1", ">"), ("wo2-x360", b"WO2", ">")):
        quote_parts = [
            strings(b"33" + tag + b" format zero", b"11" + tag + b" format one", order=order),
            strings(b"32" + tag + b" quote notice", order=order),
            spans([
                strings(b"00" + tag + b" first officer@0010", order=order),
                strings(b"00" + tag + b" second officer zero@0020", b"00" + tag + b" second officer one@0021", order=order),
            ], order=order),
        ]
        records = [
            _script(tag, order),
            b"prefix" + mesc(tag + b" block zero slot zero", tag + b" block zero slot one")
            + mesc(tag + b" block one slot zero \xd3", tag + b" block one slot one"),
            spans(quote_parts, struct.pack(f"{order}I", 3), order),
            _events(tag, order),
            struct.pack("<7I", 3, 10, 20, 30, 40, 50, 60)
            + b" \t" + tag + b" first\ncaption  \0unused middle\0" + tag + b" last cue\0",
            b"decoy\0Prefix alpha beta gamma\0trailer\0",
            b"decoy\0Invalid donor byte \x81\0",
        ]
        (tmp_path / folder).mkdir()
        write_ens(tmp_path / folder, dict(enumerate(records)))
    return DonorResolver(tmp_path)


def test_ens_pointer_does_not_switch_entry_view_slot_or_word_order(donors):
    cases = []
    for donor, tag in (("wo1-x360", "WO1"), ("wo2-x360", "WO2")):
        cases.extend([
            ([donor, "ens", 0, "notices", 1], tag + " notice one"),
            ([donor, "ens", 0, "dialog", 1], tag + " dialog one"),
            ([donor, "ens", 1, "mesc", 1, 0], tag + " block one slot zero \u014c"),
            ([donor, "ens", 2, "quotes", 3, 1], tag + " second officer one"),
            ([donor, "ens", 3, "event", 11], tag + " event eleven"),
            ([donor, "ens", 4, "captions", [2, 0]], tag + " last cue " + tag + " first caption"),
            ([donor, "ens", 5, "cstr", 6], "Prefix alpha beta gamma"),
            ([donor, "ens", 5, "slice", 13, 5], "alpha"),
        ])
    observed = []
    for ref, _ in cases:
        try:
            value = donors.resolve(ref)
        except Exception as error:
            value = f"{type(error).__name__}: {error}"
        observed.append((ref, value))

    assert observed == cases


def test_edited_row_spans_keep_original_coordinates_after_a_length_change(donors):
    row = {
        "source": "edited", "ref": ["wo1-x360", "ens", 5, "cstr", 6],
        "edits": [[7, 12, "Z"], [18, 23, "OMEGA!"]],
    }

    assert donors.resolve_row(row) == "Prefix Z beta OMEGA!"


def test_overlapping_edit_spans_cannot_duplicate_donor_characters(donors):
    row = {
        "source": "edited", "ref": ["wo1-x360", "ens", 5, "cstr", 6],
        "edits": [[7, 12, "Z"], [11, 16, "Y"]],
    }

    with pytest.raises(ValueError, match="overlap"):
        donors.resolve_row(row)


def test_official_rows_cannot_embed_english_and_bypass_the_donor_pointer(donors):
    for source in ("wo1-x360", "wo2-x360", "wo2-psp", "edited"):
        row = {"source": source, "ref": ["wo1-x360", "ens", 5, "cstr", 6], "en": "Authored replacement"}
        with pytest.raises(ValueError, match="Only authored"):
            donors.resolve_row(row)


def test_negative_ens_indices_cannot_silently_select_the_last_donor_record(donors):
    refs = [
        ["wo1-x360", "ens", -1, "cstr", 0],
        ["wo1-x360", "ens", 0, "notices", -1],
        ["wo1-x360", "ens", 0, "dialog", -1],
        ["wo1-x360", "ens", 1, "mesc", -1, 0],
        ["wo1-x360", "ens", 1, "mesc", 0, -1],
        ["wo1-x360", "ens", 2, "quotes", -1, 0],
        ["wo1-x360", "ens", 2, "quotes", 2, -1],
        ["wo1-x360", "ens", 3, "event", -1],
        ["wo1-x360", "ens", 4, "captions", [-1]],
        ["wo1-x360", "ens", 5, "cstr", -1],
        ["wo1-x360", "ens", 5, "slice", 6, -1],
    ]
    accepted = []
    for ref in refs:
        try:
            donors.resolve(ref)
        except ValueError:
            continue
        except Exception as error:
            accepted.append((ref, type(error).__name__))
        else:
            accepted.append((ref, "accepted"))

    assert accepted == []


def test_pointer_to_non_english_donor_bytes_cannot_ship_an_unresolved_row(donors):
    with pytest.raises(ValueError, match="donor English"):
        donors.resolve(["wo1-x360", "ens", 6, "cstr", 6])


def test_official_prompt_controls_keep_original_spans_and_numeric_glyphs(donors):
    row = {
        "source": "wo2-x360", "ref": ["wo2-x360", "ens", 5, "cstr", 6],
        "controls": [[7, 12, [0xE000, 32]], [18, 23, [0xE016]]],
    }

    assert donors.resolve_row(row) == "Prefix \ue000  beta \ue016"


def test_prompt_controls_cannot_insert_literal_text_or_an_unmapped_shape(donors):
    accepted = []
    for code in (31, 65, 0xE017):
        row = {
            "source": "wo1-x360", "ref": ["wo1-x360", "ens", 5, "cstr", 6],
            "controls": [[7, 12, [code]]],
        }
        try:
            donors.resolve_row(row)
        except ValueError:
            continue
        accepted.append(code)

    assert accepted == []


def test_program_prompt_glyph_does_not_swallow_the_following_single_byte_letter():
    raw = b"Lead \xf0\x40A\xd3!\xf0\x56Z"

    assert program_text(raw) == "Lead \ue000A\u014c!\ue016Z"


def test_program_decoder_cannot_accept_a_non_prompt_cp932_pair_as_donor_english():
    with pytest.raises(ValueError, match="donor program text"):
        program_text(b"Lead \x82\xa0 tail")


def test_edit_can_insert_a_whole_donor_string_by_pointer(donors):
    # Bug: a pointer in an edit is pasted as its list, or skipped, instead of the donor string it names, so a
    # name moved by an edit drops out of the line.
    row = {
        "source": "edited", "ref": ["wo1-x360", "ens", 5, "cstr", 6],
        "edits": [[7, 12, ["wo1-x360", "ens", 5, "cstr", 19]], [12, 12, " and"]],
    }

    assert donors.resolve_row(row) == "Prefix beta gamma and beta gamma"
