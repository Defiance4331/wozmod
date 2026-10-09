"""Behavioral checks for offline layout diagnostics."""

from __future__ import annotations

import pytest

from wozmod.runtime import layout


SOURCE = """
f->arg[0] == 1 ? 41 : 29;
#define SCENARIO_TEXT_WIDTH 31
#define DREAM_SUMMARY_WIDTH 23
#define CAPTION_WIDTH 19
static const int pages[][4] = {{NARRATION_DRAW, 17, 1, 2}};
static const int boxed[][2] = {{0xA0, 13}, /* ignored */ {0xB0, 11}};
static const int described[][2] = {{0xC0, 7}};
"""


def table() -> bytes:
    widths = bytearray([6] * 2799)
    widths[0] = 24
    return bytes(widths)


def test_sites_does_not_drop_a_supported_array_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(layout, "SOURCE", SOURCE)
    monkeypatch.setattr(layout, "DEFINES", {
        "NARRATION_DRAW": 0x10, "SCENARIO_TEXT_DRAW": 0x20, "DREAM_SUMMARY_DRAW": 0x30, "CAPTION_CUE": 0x40,
    })
    assert layout.sites() == {
        ("window", 1): (41, False), ("window", 2): (29, False),
        0x10: (17, True), 0x20: (31, True), 0x30: (23, True), 0x40: (19, False),
        0xA0: (13, False), 0xB0: (11, False), 0xC0: (7, True),
    }


@pytest.mark.parametrize(("array", "original", "perturbed"), [
    ("boxed", "{0xA0, 13}", "{0xa0, 13}"),
    ("pages", "{NARRATION_DRAW, 17, 1, 2}", "{NARRATION_DRAW,\n17, 1, 2}"),
    ("described", "{0xC0, 7}", "{0xc0, 7}"),
])
def test_sites_rejects_unparseable_rows_instead_of_dropping_boxes(
    monkeypatch: pytest.MonkeyPatch, array: str, original: str, perturbed: str,
) -> None:
    monkeypatch.setattr(layout, "SOURCE", SOURCE.replace(original, perturbed))
    with pytest.raises(ValueError, match=rf"{array}.*initializer"):
        layout.sites()


def test_measure_does_not_give_double_byte_surrogates_the_ascii_advance() -> None:
    widths = bytearray(table())
    widths[2700] = 3
    widths[2700 + ord("A") - 0x20] = 7
    widths[2700 + ord("B") - 0x20] = 11
    assert layout.measure("A[07Ō] B\nBB", bytes(widths), cell=12) == 21


def test_wrap_breaks_at_the_threshold_and_resets_after_a_newline() -> None:
    assert layout.wrap("AA B\nCC D", 24, table()) == "AA\nB\nCC\nD"


def test_wrap_does_not_turn_a_leading_space_into_an_empty_line() -> None:
    assert layout.wrap(" AA\n BB", 6, table()) == " AA\n BB"


def test_entry_does_not_use_the_translated_flag_as_part_of_the_hook_id() -> None:
    rows = {
        "85 00123456 0 0 0 0 1c 41": (("window", 2), 24, b"A", True),
        "01 004f4d1f 1 0 0 0 1c 42": (("window", 1), 24, b"B", False),
        "84 00123456 0 0 0 0 1c 43": (0x123456, 28, b"C", True),
        "08 00123456 0 0 0 0 1c 44": None,
        "01 004f4d1f 0 0 0 0 1c 45": (None, 28, b"E", False),
        "01 004f4d1f": None,
    }
    assert {row: layout.entry(row) for row in rows} == rows


def test_overflows_uses_the_required_sample_text_for_format_arguments() -> None:
    assert layout.overflows(0xAA, 24, "%s: %d", table(), {0xAA: (6, False)}, "Example") == "Example:\n1000"


def test_overflows_does_not_count_original_page_breaks_after_flattening() -> None:
    assert layout.overflows(
        ("window", 1), 24, "A\nB\nC\nD", table(), {("window", 1): (60, True)}, "Example",
    ) is None


def test_overflows_does_not_count_trailing_spaces_as_drawn_width() -> None:
    assert layout.overflows(0xAA, 24, "A     ", table(), {0xAA: (6, False)}, "Example") is None


def test_overflows_does_not_allow_one_extra_window_line() -> None:
    assert layout.overflows(("window", 1), 24, "A\nB\nC\nD", table(), {("window", 1): (60, False)}, "Example") == "A\nB\nC\nD"
