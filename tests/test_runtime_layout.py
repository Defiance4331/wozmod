"""Behavioral checks for offline layout diagnostics."""

from __future__ import annotations

import pytest

from wozmod.runtime import layout


SOURCE = """
static const u32 windows[2][2] = {{45, 41}, {33, 29}};
#define SCENARIO_TEXT_WIDTH 31
#define DREAM_SUMMARY_WIDTH 23
#define CAPTION_WIDTH 19
#define HISTORY_WIDTH 15
static const int pages[][8] = {{NARRATION_DRAW, 12, 21, 31, 1, 2, 19, 17}};
static const int boxed[][2] = {{0xA0, 13}, /* ignored */ {0xB0, 11}};
static const int described[][2] = {{0xC0, 7}};
static const int single_line[][2] = {{0xD0, 9}};
"""


def table() -> bytes:
    widths = bytearray([6] * 2799)
    widths[0] = 24
    return bytes(widths)


def test_sites_does_not_drop_a_supported_array_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(layout, "SOURCE", SOURCE)
    monkeypatch.setattr(layout, "DEFINES", {
        "NARRATION_DRAW": 0x10, "SCENARIO_TEXT_DRAW": 0x20, "DREAM_SUMMARY_DRAW": 0x30, "CAPTION_CUE": 0x40,
        "HISTORY_DRAW": 0x50,
    })
    assert layout.sites() == {
        ("window", 1): (41, False), ("window", 2): (29, False),
        0x10: (17, True), 0x20: (31, True), 0x30: (23, True), 0x40: (19, False),
        0x50: (15, False),
        0xA0: (13, False), 0xB0: (11, False), 0xC0: (7, True), 0xD0: (9, False),
    }


@pytest.mark.parametrize(("array", "original", "perturbed"), [
    ("boxed", "{0xA0, 13}", "{0xa0, 13}"),
    ("pages", "{NARRATION_DRAW, 12, 21, 31, 1, 2, 19, 17}", "{NARRATION_DRAW,\n12, 21, 31, 1, 2, 19, 17}"),
    ("described", "{0xC0, 7}", "{0xc0, 7}"),
    ("single_line", "{0xD0, 9}", "{0xd0, 9}"),
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


def test_squish_report_does_not_measure_format_placeholders_as_literal_text() -> None:
    report = layout.squish_report([("format-row", 0xAA, 24, "%s: %d")], table(), {0xAA: (6, False)}, "Example")
    assert report["sites"][0xAA]["limiting_rows"] == [
        {"row": "format-row", "cell": 24, "text_width": 48, "factor": 0.125, "line_count": 2},
    ]


def test_overflows_does_not_count_original_page_breaks_after_flattening() -> None:
    assert layout.overflows(
        ("window", 1), 24, "A\nB\nC\nD", table(), {("window", 1): (60, True)}, "Example",
    ) is None


def test_squish_report_does_not_ignore_trailing_spaces_in_runtime_advance() -> None:
    report = layout.squish_report([("padded", 0xAA, 24, "A     ")], table(), {0xAA: (6, False)}, "Example")
    assert report["sites"][0xAA]["limiting_rows"] == [
        {"row": "padded", "cell": 24, "text_width": 36, "factor": 1 / 6, "line_count": 1},
    ]


def test_vertical_diagnostics_do_not_allow_one_extra_window_line() -> None:
    assert layout.overflows(("window", 1), 24, "A\nB\nC\nD", table(), {("window", 1): (60, False)}, "Example") == "A\nB\nC\nD"
    report = layout.squish_report([
        ("at-limit", ("window", 1), 24, "A\nB\nC"), ("extra", ("window", 1), 24, "A\nB\nC\nD"),
    ], table(), {("window", 1): (60, False)}, "Example")
    assert report["sites"][("window", 1)]["vertical_overflow"] == [
        {"row": "extra", "cell": 24, "text_width": 6, "factor": 1.0, "line_count": 4},
    ]


def test_squish_geometry_does_not_stretch_fitting_text_or_scale_when_disabled() -> None:
    cases = [(0, 80, True), (40, 80, True), (80, 80, True), (120, 80, False), (120, None, True), (120, 0, True)]
    assert [layout.squish_geometry(width, box, "right", enabled) for width, box, enabled in cases] == [
        (1, 0, 0), (1, 0, 40), (1, 0, 80), (1, 0, 120), (1, 0, 120), (1, 0, 120),
    ]


def test_squish_geometry_does_not_shift_the_left_centre_or_right_anchor() -> None:
    original_origins = {"left": 11, "centre": -19, "right": -49}
    drawn_edges = {}
    for alignment, origin in original_origins.items():
        factor, shift, width = layout.squish_geometry(150, 90, alignment)
        drawn_edges[alignment] = (factor, origin + shift, origin + shift + width)
    assert drawn_edges == {alignment: (0.6, 11, 101) for alignment in original_origins}


def test_squish_report_does_not_drop_the_longest_row_or_include_exactly_seventy_percent() -> None:
    report = layout.squish_report([
        ("fitting", 0xAA, 24, "A" * 4), ("boundary", 0xAA, 24, "A" * 10),
        ("longest", 0xAA, 24, "A" * 12), ("unknown", 0xCC, 24, "A" * 4),
    ], table(), {0xAA: (42, False), 0xBB: (18, False)}, "Example")
    longest = {"row": "longest", "cell": 24, "text_width": 72, "factor": 42 / 72, "line_count": 1}
    assert report == {
        "sites": {
            0xAA: {"width": 42, "rows": 3, "min_factor": 42 / 72, "limiting_rows": [longest],
                   "below_70": [longest], "vertical_overflow": []},
            0xBB: {"width": 18, "rows": 0, "min_factor": None, "limiting_rows": [],
                   "below_70": [], "vertical_overflow": []},
        },
        "unknown": [{"row": "unknown", "site": 0xCC, "cell": 24, "reason": "unknown-width"}],
    }


def test_squish_report_does_not_soft_wrap_a_single_line_site(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(layout, "SOURCE", SOURCE.replace("{0xD0, 9}", "{0xD0, 18}"))
    monkeypatch.setattr(layout, "DEFINES", {
        "NARRATION_DRAW": 0x10, "SCENARIO_TEXT_DRAW": 0x20, "DREAM_SUMMARY_DRAW": 0x30, "CAPTION_CUE": 0x40,
    })
    boxes = layout.sites()
    assert boxes[0xD0] == (18, False)
    rows = [("single", 0xD0, 24, "AA BB"), ("wrapped", 0xEE, 24, "AA BB"),
            ("supplied", 0xFF, 24, "AA BB")]
    report = layout.squish_report(rows, table(), {**boxes, 0xEE: (18, False), 0xFF: (18, False)},
                                 "Example", no_wrap_sites={0xFF})
    assert {site: report["sites"][site]["min_factor"] for site in (0xD0, 0xEE, 0xFF)} == {
        0xD0: 0.6, 0xEE: 1.0, 0xFF: 0.6,
    }


def test_overflows_does_not_reject_a_line_that_horizontal_squish_can_fit() -> None:
    assert layout.overflows(0xAA, 24, "A" * 4, table(), {0xAA: (6, False)}, "Example") is None


def test_report_wraps_each_site_at_its_x360_width_and_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    # Six-pixel glyphs at cell 24. The description wraps at its 30-pixel X360 width (A BB / CC),
    # not its 7-pixel box. The page draws at its own cell 12 (three-pixel glyphs): it wraps at 19
    # (AA BB / CC, 15 wide) and fits its 17 box, whatever cell the row was logged at.
    monkeypatch.setattr(layout, "SOURCE", SOURCE + "static const int reflow[][2] = {{0xC0, 30}};")
    monkeypatch.setattr(layout, "DEFINES", {"NARRATION_DRAW": 0x10})
    report = layout.squish_report([
        ("page", 0x10, 28, "AA BB CC"), ("description", 0xC0, 24, "A BB CC"),
    ], table(), {0x10: (17, True), 0xC0: (7, True)}, "Example")
    assert {site: (values["min_factor"], values["limiting_rows"][0]["line_count"])
            for site, values in report["sites"].items()} == {0x10: (1.0, 2), 0xC0: (7 / 24, 2)}


def test_reflow_rejects_an_unreadable_width_instead_of_dropping_a_wrap_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(layout, "SOURCE", SOURCE + "static const int reflow[][2] = {{0xC0, bad}};")
    with pytest.raises(ValueError, match="reflow.*initializer"):
        layout.squish_report([("description", 0xC0, 24, "A" * 4)], table(), {0xC0: (7, True)}, "Example")


def test_vs_conditions_line_is_condensed_on_one_line_ending_where_the_rules_box_ends() -> None:
    """At cell 28 the generic box wrap broke the CONDITIONS line onto the first RULES row 38 px below."""
    from wozmod.runtime.latin_font import WIDTHS
    boxes = layout.sites()
    text = "Alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november."
    for conditions, rules in ((0x5A6805, 0x5A686C), (0x580811, 0x580876)):
        report = layout.squish_report([(cell, conditions, cell, text) for cell in (24, 28, 32)],
                                     WIDTHS, boxes, "Example")["sites"][conditions]
        assert (report["rows"], report["min_factor"] < 1, report["limiting_rows"][0]["line_count"]) == (3, True, 1)
        assert boxes[conditions][0] == boxes[rules][0]


def test_a_stock_cell_page_is_measured_at_the_logged_cell_not_at_cell_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page row with cell 0 (the MOZ narration keeps the stock cell) was measured at cell 0: every row read
    as zero wide and the report could never flag it."""
    monkeypatch.setattr(layout, "SOURCE", SOURCE.replace("{NARRATION_DRAW, 12, 21, 31, 1, 2, 19, 17}",
                                                         "{NARRATION_DRAW, 0, 0, 0, 1, 2, 30, 17}"))
    monkeypatch.setattr(layout, "DEFINES", {"NARRATION_DRAW": 0x10})
    report = layout.squish_report([("page", 0x10, 24, "A" * 4)], table(), {0x10: (17, True)}, "Example")
    assert report["sites"][0x10]["limiting_rows"][0]["text_width"] == 24
