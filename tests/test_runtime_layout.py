"""Reading the defines and site tables out of the runtime's C source."""

from __future__ import annotations

import pytest

from wozmod.runtime import layout, text_lanes


SOURCE = """
#define NARRATION_DRAW 0x10
#define SCENARIO_TEXT_DRAW 0x20
#define DREAM_SUMMARY_DRAW 0x30
#define CAPTION_CUE 0x40
#define HISTORY_DRAW 0x50
static const int single_line[][2] = {{0xD0, 9}, /* ignored */ {0xE0, 5}};
"""


def test_sites_holds_every_lane_and_single_line():
    # Red when sites() drops a lane kind (the screen checks would then find no width for its draws) or a single-line
    # row while parsing.
    found = layout.sites(SOURCE)
    tabled = [row for row in text_lanes.LANES if row.kind in ("boxed", "described")]
    assert {site: found.get(site, (None,))[0] for row in tabled for site in row.sites} == {
        site: row.width for row in tabled for site in row.sites}
    assert (found[0x20][0], found[0x30][0], found[0x40][0], found[0x50][0]) == (
        text_lanes.lane("scenario").width, text_lanes.lane("dream").width, text_lanes.lane("caption").width,
        text_lanes.lane("history").width)
    assert (found[0xD0], found[0xE0]) == ((9, False), (5, False))


def test_sites_rejects_an_unparseable_single_line_row_instead_of_dropping_it() -> None:
    with pytest.raises(ValueError, match=r"single_line.*initializer"):
        layout.sites(SOURCE.replace("{0xD0, 9}", "{0xD0, WIDTH}"))


def test_defines_reads_each_integer_define_and_no_expression() -> None:
    """Bug: a decimal, negative or cast define is skipped (the reader used to take hex only), or the last number of an
    expression is read as the define's value."""
    source = """
#define HEX 0x5CA36D        /* comment */
#define DECIMAL 1200
#define UNSIGNED 0x10u
#define NEGATIVE (-3)
#define CALL ((void (*)(const void *, u32))0x4F4230)
#define CELL_AT (*(volatile u8 *)0x7A4570)
    #define INDENTED 7
#define SUM (0x10 + 2)
#define MASKED (*(volatile u32 *)0x1000 & 3)
#define OFFSET (*(volatile u32 *)(0x1000 + 0x14))
#define PAIR 0x5990EE, 0x5995D9
#define SCALE 2.0f
#define NAMED (BASE + 1)
#define MACRO(x) 5
"""
    assert layout.defines(source) == {"HEX": 0x5CA36D, "DECIMAL": 1200, "UNSIGNED": 0x10, "NEGATIVE": -3,
                                      "CALL": 0x4F4230, "CELL_AT": 0x7A4570, "INDENTED": 7}


def test_c_array_reads_every_row_and_refuses_what_it_cannot_read() -> None:
    """Bug: a row written over two lines, in lowercase hex, behind a comment or before a trailing comma is dropped,
    or a value the reader cannot evaluate is read as a number."""
    source = """
#define SITE 0x10
static const u32 rows[][3] = {
    {0x5CBE03, SITE, -1}, /* {0x1, 2, 3}, */ {0xab,
     12, 0},
};
static const u8 bytes_[4] = {0x0f, 0xb6, 5, SITE};
"""
    assert layout.c_array("rows", source) == [(0x5CBE03, 0x10, -1), (0xAB, 12, 0)]
    assert layout.c_array("bytes_", source) == [0x0F, 0xB6, 5, 0x10]
    for broken in ("{0xab, 12 + 1, 0}", "{0xab, 12}", "{0xab, OTHER, 0}"):
        with pytest.raises(ValueError, match="rows initializer 2"):
            layout.c_array("rows", source.replace("{0xab,\n     12, 0}", broken))
    with pytest.raises(ValueError, match="bytes_ does not have 4"):
        layout.c_array("bytes_", source.replace("5, SITE", "5"))


def test_vs_conditions_line_stays_on_one_line_ending_where_the_rules_box_ends() -> None:
    """Bug: the CONDITIONS line of a VS screen wraps at the generic box width (at cell 28 it broke onto the first
    RULES row 38 px below), or its box ends elsewhere than the RULES box under it."""
    single, boxes = layout.single_line_sites(), layout.sites()
    for conditions, rules in ((0x5A6805, 0x5A686C), (0x580811, 0x580876)):
        assert conditions in single
        assert boxes[conditions][0] == boxes[rules][0]
