"""The page text boxes, and every number the runtime, the art build, the screen checks and the tests use for them.

Five stock draws show a page of text at the foot of the screen: camp talk (the WO1 still event, LINKDATA1 4105), the
WO1 chapter narration (4073), the Story Z talk scenes (the Z still event, 4103), the Dream briefing (4190) and the Z
chapter narration (4071), whose text sits on the stage picture with no box art behind it. ``box(kind)`` gives one; a
Box holds what the stock game fixes (its art, the stock text) and what we choose (cell, pitch, x, wrap, width, most
lines), and derives the rest, so a box changes here and nowhere else:

- where its first line, its edge lines and its height land on screen, and the room left above and below the text;
- how much taller its art is drawn so its most lines keep the stock margins: the art build grows it by stretching
  the rows of its middle (``stretch``; one row of a uniform middle, as in camp talk and the briefing band, is
  repeated) and it is drawn that much higher (``edits``), so its bottom edge stays where the stock game has it, clear
  of the prompt bar; or (the WO1 narration panel) its stock texture is drawn with its rows stretched;
- for a page with no box art (the Z narration), where its first line goes so its most lines end where the WO1
  narration's do, as far above the same prompt bar;
- the runtime's page table (``c_header``: english_runtime.c's pages[] holds TEXT_BOX_PAGES from the generated
  header) and the screen checks' ``text_box`` assertion;
- the page's wrap (``wrapped``), with which tests/test_text_boxes.py checks that every real line fits its box.

Values are in the game's 1280x720 layout units. The stock game draws every page at cell 28 (each draw sets
[0xC2AE0C] and [0xC2AE0D] to 0x1C just before it, for example 0x59C773); the stock text's most lines are the most
line breaks its Japanese takes. Edge rows: the first bright row of each edge line, read from the stock art and
matched to captures (work/handbacks/textbox-edges.md, 2026-10-25).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from . import layout
from .latin_font import width as text_width

STOCK_CELL = 28


@dataclass(frozen=True)
class Art:
    """The box art behind a page as the stock game draws it, and the stock pushes that place it."""
    texture: tuple[int, int]  # LINKDATA1 entry and item
    y: int  # screen y of its top row
    rows: int  # rows drawn
    edges: tuple[int, int]  # texture rows of its top and bottom edge lines
    source: int | None = None  # rows sampled, when the draw scales them into `rows`
    middle: tuple[int, int] | None = None  # its middle's first and last texture rows, stretched to grow the art
    sampled_at: tuple[int, ...] = ()  # push imm32 sites of the rows sampled
    drawn_at: tuple[int, ...] = ()  # push imm32 sites of the rows drawn
    y_at: tuple[tuple[int, int], ...] = ()  # push imm32 sites of y, each with its offset from the art's top
    lane: tuple[int, int, int] | None = None  # drawn as a strip between two fades: its stock x and width, the fades'
    lane_at: tuple[int, int, int, int] = ()  # imm32 sites of the fade-in's x, the strip's x and width, the fade-out's x


@dataclass(frozen=True)
class Box:
    kind: str
    site: str  # the english_runtime.c define of its text draw's return address
    art: Art | None
    stock: tuple[int, int, int]  # the stock text, at cell 28: first line's y, pitch, most lines
    x: int
    wrap: int  # wrap width, in pixels at the cell's width
    width: int  # a wider line is condensed into this width
    lines: int  # most lines its text takes (tests/test_text_boxes.py checks this against every real line)
    cell: tuple[int, int] | None = None  # drawn cell width and height; None keeps the stock cell and pitch
    pitch: int | None = None
    centred: bool = False  # its lines, however many, centred between the stock margins, instead of starting at y
    stretched: bool = False  # its stock art drawn taller, every row stretched, instead of grown in the art build

    @property
    def cell_size(self) -> tuple[int, int]:
        return self.cell or (STOCK_CELL, STOCK_CELL)

    @property
    def line_pitch(self) -> int:
        return self.pitch if self.cell else self.stock[1]

    def block(self, lines: int) -> int:
        """Rows from the first line's cell top to the last line's cell bottom."""
        return self.cell_size[1] + (lines - 1) * self.line_pitch

    @property
    def margins(self) -> tuple[float, float]:
        """The stock room between the edge lines and the stock text at its most lines: above, below."""
        top, bottom = self._edges(self.art.y, self.art.rows, self.art.source or self.art.rows, 0)
        y, pitch, lines = self.stock
        return y - top, bottom - (y + STOCK_CELL + (lines - 1) * pitch)

    @property
    def _needed(self) -> float:
        """Edge line to edge line for `lines` lines with the stock margins."""
        return sum(self.margins) + self.block(self.lines)

    @property
    def growth(self) -> int:
        """Rows the art's stretched middle adds so `lines` lines keep the stock margins; 0 for art drawn as it is."""
        if self.art is None or self.art.middle is None:
            return 0
        art, top, bottom, grown = self.art, *self.art.edges, 0
        while (bottom - top + grown) * (art.rows + grown) / ((art.source or art.rows) + grown) < self._needed:
            grown += 1
        return grown

    @property
    def drawn(self) -> int:
        """Rows the art is drawn before it grows: stretched art as many as `lines` lines with the stock margins take."""
        if not self.stretched:
            return self.art.rows
        top, bottom = self.art.edges
        return math.ceil(self._needed * (self.art.source or self.art.rows) / (bottom - top))

    @property
    def y(self) -> int:
        """The first line's y: the stock y, moved up with the art's top (with no art, moved so `lines` lines end where
        the stock text at its most lines ends); for centred lines, the y they centre on, half way between the stock
        margins."""
        if self.art is None:
            sibling = BOXES["narration"]
            return sibling.first_line(sibling.lines) + sibling.block(sibling.lines) - self.block(self.lines)
        if not self.centred:
            return self.stock[0] - self.growth
        (top, bottom), (above, below) = self.edges, self.margins
        return round((top + above + bottom - below) / 2)

    def first_line(self, lines: int) -> int:
        """The first line's y for a text of `lines` lines (page in english_runtime.c)."""
        return self.y - self.block(lines) // 2 if self.centred else self.y

    @property
    def edges(self) -> tuple[float, float]:
        """Screen y of the top and bottom edge lines as drawn."""
        art = self.art
        return self._edges(art.y - self.growth, self.drawn + self.growth, (art.source or art.rows) + self.growth,
                           self.growth)

    def _edges(self, y: int, drawn: int, sampled: int, growth: int) -> tuple[float, float]:
        top, bottom = self.art.edges
        return y + top * drawn / sampled, y + (bottom + growth) * drawn / sampled

    @property
    def height(self) -> float:
        """From the top edge line to the bottom one."""
        top, bottom = self.edges
        return bottom - top

    def room(self, lines: int) -> tuple[float, float]:
        """The room a text of `lines` lines leaves between it and the edge lines: above, below."""
        top, bottom = self.edges
        y = self.first_line(lines)
        return y - top, bottom - (y + self.block(lines))

    @property
    def stretch(self) -> tuple[int, int, int]:
        """The art build's stretch: the middle's first and last rows, and how many rows it gains."""
        return *self.art.middle, self.growth

    def edits(self) -> list[tuple[int, bytes, bytes]]:
        """Checked edits of the stock pushes that draw the art: rows sampled, rows drawn and y."""
        art, push = self.art, lambda va, stock, ours: (va, b"\x68" + struct.pack("<I", stock),
                                                        b"\x68" + struct.pack("<I", ours))
        found = [push(va, art.source or art.rows, (art.source or art.rows) + self.growth) for va in art.sampled_at]
        found += [push(va, art.rows, self.drawn + self.growth) for va in art.drawn_at]
        found += [push(va, art.y + offset, art.y - self.growth + offset) for va, offset in art.y_at]
        if art.lane:  # the strip under the text lane: at x, `width` wide, a fade either side
            (x, width, fade), ours = art.lane, (self.x - art.lane[2], self.x, self.width, self.x + self.width)
            found += [(va, struct.pack("<I", stock), struct.pack("<I", value)) for va, stock, value
                      in zip(art.lane_at, (x - fade, x, width, x + width), ours, strict=True)]
        return [edit for edit in found if edit[1] != edit[2]]

    def wrapped(self, text: str) -> str:
        """The text as the page draws it: its line breaks made spaces, then wrapped at the cell (page in
        english_runtime.c; a text of 512 bytes or more is drawn as it is, which no page text reaches)."""
        return _wrap(text.replace("\n", " "), self.wrap, self.cell_size[0])


def _wrap(text: str, at: int, cell: int) -> str:
    """wrap in english_runtime.c: a line breaks at its last space once it measures `at` or more."""
    out, line, space, i = [], 0, None, 0
    while i < len(text):
        c, mark = text[i], len(out)
        out.append(c)
        i += 1
        if c == "[" and i + 1 < len(text):  # a colour tag: '[' and two characters
            out += text[i:i + 2]
            i += 2
        if c == "\n":
            line, space = len(out), None
        elif c == " ":
            space = mark if mark > line else None
        elif space is not None and text_width("".join(out[line:]), cell) >= at:
            out[space] = "\n"
            line, space = space + 1, None
    return "".join(out)


# Every page is drawn at cell 24, most with the stock pitch scaled to it (42, 38 and 35 become 36, 33 and 30). The
# four boxed pages share one text lane: x and wrap width, centred on the screen as each box's art is.
LANE = (160, 960)
BOXES = {box.kind: box for box in (
    # WO1 chapter narration: the stock panel (4072.0) drawn taller from y 381, its rows stretched (its middle is not
    # uniform), and its lines centred.
    Box("narration", "NARRATION_DRAW", Art((4072, 0), 381, 262, (51, 212), drawn_at=(0x561087,)), (458, 42, 3),
        x=LANE[0], wrap=LANE[1], width=LANE[1], lines=4, cell=(24, 24), pitch=36, centred=True, stretched=True),
    # Camp talk: the stock bar (4104.1) grown upward by its uniform row 190; the name plate and the name keep their
    # stock places on its top (0x59C433 at -14, 0x59C4A2 at 0). Its text in the shared lane.
    Box("camp", "STILL_DRAW", Art((4104, 1), 412, 262, (56, 203), middle=(190, 190), sampled_at=(0x59C709,),
                                  drawn_at=(0x59C717,), y_at=((0x59C723, 0), (0x59C433, -14), (0x59C4A2, 0))),
        (508, 42, 2), x=LANE[0], wrap=LANE[1], width=LANE[1], lines=3, cell=(24, 24), pitch=36),
    # Z chapter narration: no box art; the X360 box's x and wrap, its most lines ending where the WO1 narration's do
    # (the stock text's place, 507, put a fifth line on the prompt bar).
    Box("z-narration", "NARRATION_Z_DRAW", None, (507, 38, 3), x=200, wrap=742, width=757, lines=5, cell=(24, 24),
        pitch=33),
    # Story Z talk scenes: the stock bar (4102.2) at the X360 still event's x and width, grown upward by stretching
    # its middle, a colour gradient between its inner lines (rows 22 to 96); the name plate and the name keep their
    # stock places on its top (0x59A2DE at -63, 0x59A3D5 at -49). Its rows are pushed as imm8, which a grown bar
    # outgrows: the runtime's detour (runtime_edits) pushes `drawn`. Its text in the shared lane.
    Box("z-talk", "STILL_Z_DRAW", Art((4102, 2), 503, 126, (0, 123), middle=(22, 96),
                                      y_at=((0x59A70D, 0), (0x59A2DE, -63), (0x59A3D5, -49))),
        (531, 35, 2), x=LANE[0], wrap=LANE[1], width=LANE[1], lines=3, cell=(24, 24), pitch=30),
    # Dream briefing: the stage's band (item 0 of entries 4150 to 4189, all alike; 0x5488C0 draws it 177 rows into
    # 178, as three strips: a fade, the strip under the text, a fade) widened to the shared lane and grown
    # upward by its uniform row 90; its lines centred, as their count varies; the stage name's plate keeps its stock
    # place on its top (0x548A0E at -42).
    Box("briefing", "BRIEFING_DRAW", Art((4150, 0), 454, 178, (17, 157), source=177, middle=(90, 90),
                                         sampled_at=(0x54896B,), drawn_at=(0x548976,),
                                         y_at=((0x548980, 0), (0x548A0E, -42)), lane=(248, 784, 56),
                                         lane_at=(0x5488EC, 0x5488FC, 0x548904, 0x548914)),
        (486, 38, 3), x=LANE[0], wrap=LANE[1], width=LANE[1], lines=5, cell=(24, 24), pitch=33, centred=True),
)}


def box(kind: str) -> Box:
    return BOXES[kind]


def _row(b: Box) -> tuple[int, ...]:
    cell_w, cell_h = b.cell or (0, 0)
    return cell_w, cell_h, b.pitch if b.cell else 0, b.x, b.y, b.wrap, b.width, int(b.centred)


def page_rows() -> dict[int, tuple[int, ...]]:
    """The runtime's page table by draw site: cell width and height (0: stock), pitch, x, y, wrap, width, and 1 when
    its lines centre on y."""
    return {layout.DEFINES[b.site]: _row(b) for b in BOXES.values()}


def c_header() -> list[str]:
    """The generated header's page table rows (english_runtime.c pages[])."""
    rows = ", ".join("{" + ", ".join(map(str, (b.site, *_row(b)))) + "}" for b in BOXES.values())
    return [f"#define TEXT_BOX_PAGES {rows}"]
