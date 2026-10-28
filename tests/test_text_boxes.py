"""The page text boxes (wozmod.runtime.text_boxes): their geometry, their wrap, and their real text."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from wozmod.runtime import text_boxes


def test_a_page_line_breaks_once_it_measures_the_wrap_width_glyph_by_glyph():
    # Bugs: a line breaks only past the width (> for >=), a page keeps its stock line breaks, the cell scales the line's total instead of each glyph's
    # advance (the game rounds each down), or a colour tag takes room. Oracle: the advances by hand: 'a' 16 and the
    # space 8 at cell 24, still 16 and 8 at cell 25 (16 * 25 // 24); "aaaa aaaa" is 136.
    camp = text_boxes.box("camp")
    at = lambda wrap, cell, text: replace(camp, wrap=wrap, cell=(cell, cell)).wrapped(text)  # noqa: E731
    assert at(136, 24, "aaaa aaaa") == "aaaa\naaaa"
    assert at(137, 24, "aaaa\naaaa") == "aaaa aaaa"
    assert at(137, 25, "aaaa aaaa") == "aaaa aaaa"
    assert at(137, 24, "[01aaaa aaaa]") == "[01aaaa aaaa]"


def test_each_page_box_grows_by_its_own_rows_and_keeps_the_stock_margins():
    # Bug: a page box grows by the wrong rows (or downward, under the prompt bar), its text keeps its stock y, or a
    # draw immediate stays the stock one. One row per box, (what the box gives, what it should); every failing box is
    # reported. The oracles are by hand from the stock art and the captures, as each row's comment gives them.
    camp, band, panel = text_boxes.box("camp"), text_boxes.box("briefing"), text_boxes.box("narration")
    bar, narration = text_boxes.box("z-talk"), text_boxes.box("z-narration")
    stock_camp = replace(camp, lines=2, cell=(28, 28), pitch=42)

    def immediates(box, part):
        return {va: int.from_bytes(ours[part], "little") for va, _, ours in box.edits()}

    def centred(box, fewer, most):
        return box.room(fewer)[0] - box.room(fewer)[1] == box.room(most)[0] - box.room(most)[1]

    rows = {
        # The stock camp bar's edge lines at y 468 and 615 and its stock text at y 508 (two lines, cell 28, pitch 42).
        "stock camp": ((stock_camp.edges, stock_camp.y, stock_camp.edits(), stock_camp.room(2)),
                       ((468, 615), 508, [], (40, 37))),
        # Three lines at cell 24 and pitch 36 take 24 + 2 * 36 = 96 rows, 26 more than the stock 147 leaves them,
        # with the stock margins.
        "camp": ((camp.edges, camp.y, camp.stretch, camp.room(3), immediates(camp, slice(1, None))),
                 ((442, 615), 482, (190, 190, 26), (40, 37),
                  {0x59C709: 288, 0x59C717: 288, 0x59C723: 386, 0x59C433: 372, 0x59C4A2: 386})),
        # The band's draw scales 177 rows into 178: (140 + 52) * 230 / 229 = 192.8 >= 36.8 + 24 + 4 * 33 = 192.8,
        # and 51 gives 191.8. Stock edge rows 17 and 157 drawn at y 454, stock margins 486 - 471.1 above and
        # 611.9 - 590 below, stock strips (fade 192, lane 248 and 784 wide, fade 1032); the name plate moves with it
        # and its lines centre in a band sized for its most.
        "briefing": ((band.growth, immediates(band, slice(-4, None)), [round(v, 1) for v in band.room(5)],
                      centred(band, 3, 5)),
                     (52, {0x54896B: 229, 0x548976: 230, 0x548980: 402, 0x548A0E: 360,
                           0x5488EC: 104, 0x5488FC: 160, 0x548904: 960, 0x548914: 1120}, [14.9, 21.9], True)),
        # The panel stretches: 161 * 295 / 262 = 181.3 >= 26 + 23 + 24 + 3 * 36 = 181, and 294 gives 180.7. Stock
        # edge rows 51 and 212 at y 381, stock margins 26 and 23; its lines centre on the stretched panel's middle.
        "narration": ((panel.drawn, [round(v, 1) for v in (*panel.edges, *panel.room(4))], centred(panel, 2, 4)),
                      (295, [438.4, 619.7, 26.6, 22.7], True)),
        # The stock Z bar's edge lines at 503 and 626 and stock text at 531 (pitch 35, two lines): margins 28 and
        # 626 - 594 = 32, so three lines at cell 24 and pitch 30 need 28 + 24 + 60 + 32 = 144, 21 more than 123; the
        # plate at y 440 and the name at 454 (0x59A2DE, 0x59A3D5) move up with it.
        "z-talk": ((bar.edges, bar.y, bar.stretch, bar.room(3), immediates(bar, slice(1, None))),
                   ((482, 626), 510, (22, 96, 21), (28, 32), {0x59A70D: 482, 0x59A2DE: 419, 0x59A3D5: 433})),
        # At the stock y 507 a fifth line lands on the prompt bar (y 638 to 668). The WO1 narration's four lines end
        # at 465 + 24 + 3 * 36 = 597; five Z lines at pitch 33 take 24 + 4 * 33 = 156, so they start at 441.
        "z-narration": ((narration.y, narration.y + narration.block(5)), (441, 597)),
    }
    wrong = {name: got for name, (got, expected) in rows.items() if got != expected}
    assert wrong == {}


def page_texts(stock: Path, table: bytes) -> dict[str, list[tuple[str, str]]]:
    """(id, the English the player reads) of every line each box shows, read as the runtime looks them up."""
    from wozmod.formats.linkdata import archive
    from wozmod.formats.scripts import mesc_blocks
    from wozmod.runtime.text_table import decode_english
    from wozmod.text.contexts import drawn_mesc_lines, shown_text
    from wozmod.text.narration_voices import BRIEFING_VOICES, VOICE_KEYED, line_voices

    exe, linkdata1 = (stock / "OROCHI_Z.exe").read_bytes(), archive(stock / "LINKDATA1")
    found = {}
    for kind, entry in (("narration", 4073), ("camp", 4105), ("z-narration", 4071), ("z-talk", 4103)):
        voices = line_voices(exe, entry, linkdata1[entry])
        found[kind] = [(f"{entry}.{at}", decode_english(shown_text(table, "mesc", raw, voice if entry in VOICE_KEYED
                                                                    else None)[0], "replace"))
                       for at, raw, voice in drawn_mesc_lines(entry, linkdata1[entry], voices)]
    blocks = mesc_blocks(linkdata1[4190])  # stage s shows block s - base (narration_voices)
    shown = sorted({s - base for _, stages, base in BRIEFING_VOICES for s in stages})
    found["briefing"] = [(f"4190.{b}", decode_english(shown_text(table, "mesc", blocks[b][0])[0], "replace"))
                         for b in shown]
    return found


@pytest.mark.skipif(not (os.environ.get("WOZMOD_TEXT_ROOM") and os.environ.get("WOZMOD_GAME")
                         and os.environ.get("WOZMOD_DONORS")),
                    reason="set WOZMOD_TEXT_ROOM=1, WOZMOD_GAME and WOZMOD_DONORS (about 40 s)")
def test_every_page_line_takes_its_box_lines_at_most():
    # Bug: a translation wraps to one line more than its box holds (camp talk's fourth line would fall below the
    # bar, a seventh briefing line below the band), or every line got shorter and a box is taller than its text
    # needs.
    from wozmod.runtime.text_table import table_blob
    from wozmod.text.build import resolved_text
    from wozmod.text.resolver import DonorResolver

    stock = Path(os.environ["WOZMOD_GAME"])
    _, pairs, (names, overridden) = resolved_text(stock, DonorResolver(Path(os.environ["WOZMOD_DONORS"])),
                                                  Path("data/text"))
    wrong = []
    for kind, lines in page_texts(stock, table_blob(pairs, names, overridden)).items():
        box = text_boxes.box(kind)
        counts = {at: box.wrapped(text).count("\n") + 1 for at, text in lines}
        most = max(counts, key=counts.get)
        if counts[most] != box.lines:
            wrong.append(f"{kind}: line {most} takes {counts[most]} lines, the box holds {box.lines}")
    assert wrong == []


def test_the_runtime_applies_every_box_edit():
    # Bug: a box's art grows in the art build but the runtime still draws its stock rows at the stock y (the briefing
    # band's edits were derived and never added to the code edits), so the grown art is cut off at its old height.
    from wozmod.runtime import runtime_edits
    applied = set(runtime_edits.CODE_EDITS)
    missing = [(kind, hex(edit[0])) for kind, b in text_boxes.BOXES.items() if b.art
               for edit in b.edits() if edit not in applied]
    assert missing == []
