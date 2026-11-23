"""Every free-text lane the runtime sets: where a text's lines break and the width a longer line is condensed into,
beside the bounds docs/design-language.md (Text lanes) puts on it. The runtime's lane tables come from here
(``c_header``), so a lane changes here and nowhere else, and tests/test_text_lanes.py checks every row against the rule.

A lane is the narrowest of three bounds:

- ``room``: the inner width of the art drawn behind the text, from the text's start (a centred text: the art's inner
  width). None when there is no art behind it, or the art grows with the text (the message box).
- the safe area: for a centred text its width; for a text that starts at ``start``, the room from there to its right
  edge. A text in a box (``start`` None) is kept in the safe area by its box, which the screen check
  ``box_within_safe_area`` keeps there.
- ``MEASURE``, at the lane's cell, for a text that can take more than one line, unless it is a one-row line
  (``one_row``): a line read in one glance over the scene, such as a notice or a subtitle, whose second row is a
  fallback for a line too long for the screen. With one row there is no next line for the eye to find, and a second
  row costs the scene more than a long row does. The runtime splits such a line's rows evenly (``balanced_wrap`` in
  runtime/english_runtime.c: notices, the cut-in, captions, and the boxed draws whose row says so).

Text wraps at its full lane: ``width`` equals it. The page boxes' lane lives in
wozmod.runtime.text_boxes (their row here reads it). Not here, because the game sets its width: the notice box (from each
caller). The two-button confirm dialog's row has no safe-area bound yet: its frame follows the text from an x each of
its four callers fixes, not traced.

Sources: work/handbacks/text-lane-audit.md (2026-11-15), per row.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import text_boxes
from .design import SAFE_LEFT, SAFE_RIGHT

# A reading measure of 75 characters, at cell 24: 75 times the mean advance of English sentences, 13.1 px (wozmod's
# authored sentences of six words or more in data/text/table.json; tests/test_text_lanes.py measures it again).
MEASURE_CHARACTERS = 75
MEAN_ADVANCE = 13.1
MEASURE = round(MEASURE_CHARACTERS * MEAN_ADVANCE)

# The pitch of battle and event text rows at cell 24: the X360's event subtitles (cell 24, pitch 28, runtime_edits at
# 0x49EABF). The dialogue box, notices, the strategy cut-in and event subtitles all draw at it.
LINE_PITCH = 28


@dataclass(frozen=True)
class Lane:
    element: str
    kind: str  # the runtime table or value it sets (c_header)
    sites: tuple[int, ...]  # draw return addresses (boxed, described, history)
    width: int  # where its lines break, px at its cell; a single word wider than this is condensed into it
    room: int | None
    start: int | None = None
    centred: bool = False
    lines: int = 2  # most lines its text takes
    cell: int = 24
    one_row: bool = False  # a line meant to read on one row: the measure does not apply

    def bounds(self) -> dict[str, int]:
        out = {}
        if self.room is not None:
            out["art"] = self.room
        if self.centred:
            out["safe area"] = SAFE_RIGHT - SAFE_LEFT
        elif self.start is not None:
            out["safe area"] = SAFE_RIGHT - self.start
        if self.lines > 1 and not self.one_row:
            out["measure"] = MEASURE * self.cell // 24
        return out

    def lane(self) -> int:
        return min(self.bounds().values())

    def fits(self, text: str) -> bool:
        """Whether a text takes at most `lines` lines wrapped at `width` (its line breaks made spaces, as fit_lines in
        runtime/english_runtime.c draws it). One that takes more is wrapped wider and condensed back into `width`
        (wrap_lines there)."""
        return text_boxes.wrap(text.replace("\n", " "), self.width, self.cell).count("\n") < self.lines


_page = text_boxes.box("camp")
CUTIN_TEXT_X = 300  # 0x54F2F0 draws the cut-in phrase from x 300 (push 0x12C)

LANES = (
    # Window 1 (hook 6): the in-battle dialogue bubble follows its text, at most the stock frame x 164..1141 (0x54E780:
    # x 164, or 0x475 - width for a speaker on the right).
    Lane("battle dialogue", "window1", (), 977, room=1141 - 164, lines=3),
    # The strategy cut-in, in window 1 while 0xC2B8BD is set: the strip runs from x 0 to 1230 (X360 width).
    Lane("strategy cut-in", "cutin", (), SAFE_RIGHT - CUTIN_TEXT_X, room=1230 - CUTIN_TEXT_X, start=CUTIN_TEXT_X,
         one_row=True),
    # Window 2: battle notices and quote formats, centred on a 1224 bar (push 0x4C8 at 0x550E80).
    Lane("battle notices", "window2", (), SAFE_RIGHT - SAFE_LEFT, room=1224, centred=True, one_row=True),
    # The MOZ message box grows, centred, up to this; its nine-slice interior is the wrap width.
    Lane("message box", "message", (), MEASURE, room=None, centred=True, lines=4),
    # Movie captions at cell 28, centred on the movie. They balance their two rows within the lane instead of filling
    # the first (docs/design-language.md, Text lanes).
    Lane("movie captions", "caption", (), SAFE_RIGHT - SAFE_LEFT, room=None, centred=True, cell=28, one_row=True),
    # In-engine event subtitles (0x49EAA0 centres each slot on 640), on the scene.
    Lane("event subtitles", "boxed", (0x49DACE, 0x49DB4C), SAFE_RIGHT - SAFE_LEFT, room=None, centred=True,
         one_row=True),
    # The five page boxes share one lane (one rule per kind): the narrowest art is the briefing strip, 160..1120.
    Lane("page boxes", "page", (), _page.width, room=1120 - _page.x, lines=5),
    # VS and Survival rules: the panel 294..1200 (MOZ_LAYOUT), text at 477, 18 in from its right edge.
    Lane("VS and Survival rules", "boxed", (0x5A686C, 0x580876), 705, room=705, lines=5),
    Lane("treasure guide detail", "boxed", (0x5530FC,), 416, room=416, lines=4),  # room inferred
    Lane("weapon upgrade skill effect", "boxed", (0x526BE8,), 641, room=641),
    # The info boxes' descriptions: the card grew to 645 (runtime_edits.CARD_WIDTH), room 586.
    Lane("element and Upgrade Skill info", "boxed", (0x595679, 0x5956FA), 586, room=586, lines=3),
    # Two-button confirm dialog (0x593E30): frame at x + 28, text + 16 wide (at least 226), text at x + 38. Its callers
    # rest it at x 696 (0x527E80), 680 (0x52934A), 677 (0x52ED42) and 612 (0x554714); at 696 the frame, which ends 6
    # past its text, stays in the safe area for a text up to 476.
    Lane("confirm dialog", "boxed", (0x593E56, 0x593EAE), 476, room=SAFE_RIGHT - 6 - (696 + 38)),
    Lane("Strategy Info panel", "boxed", (0x598125, 0x59818B), 604, room=604, lines=3),  # 20 in each side
    Lane("pre-battle panel rows", "boxed", (0x53C225, 0x53C2A5), 578, room=578),  # panel 262..1018, text 430
    Lane("info box (0x595F00)", "described", (0x595F93,), 530, room=530, lines=3),
    Lane("Conditions panel", "described", (0x5975C6,), 490, room=490, lines=3),
    Lane("Ability Acquisition conditions", "described", (0x593B98,), 686, room=686, lines=3),
    # Battle history: box x 237, 812 wide (X360_LAYOUT), text at 279, ending a panel padding (14) inside its edge.
    Lane("battle history", "history", (0x539F9F,), 756, room=237 + 812 - 14 - 279, lines=3),
    # Z scenario objective: the stock column, cell 20, at most 5 lines; only a text that would need more widens its
    # wrap (fit_lines) and condenses by what it needs, the fallback docs/design-language.md rule 2 allows.
    Lane("Z scenario objective", "scenario", (), 290, room=290, lines=5, cell=20),
    # Dream summary column from x 980 (stock draw), clear of the help bar, at most 7 lines; fit_lines as above.
    Lane("Dream summary", "dream", (), SAFE_RIGHT - 980, room=None, start=980, lines=7),
)


# The draws the runtime's lane tables match by return address (boxed, described, history).
SITES = tuple(site for row in LANES for site in row.sites)


def lane(kind: str) -> Lane:
    (found,) = [row for row in LANES if row.kind == kind]
    return found


def c_header() -> list[str]:
    """The runtime's lane tables (english_runtime.c): widths, and site and width for the draws matched by address
    (the boxed draws also whether the text is a one-row line, whose rows the runtime splits evenly)."""
    def table(kind: str, *columns: str) -> str:
        return ", ".join("{" + ", ".join([f"{site:#x}", str(row.width), *(str(int(getattr(row, c))) for c in columns)])
                         + "}" for row in LANES if row.kind == kind for site in row.sites)
    return [
        f"#define LANE_WINDOWS {{{{{lane('window1').width}, {lane('window1').width}}}, "
        f"{{{lane('window2').width}, {lane('window2').width}}}}}",
        f"#define LANE_CUTIN {lane('cutin').width}",
        f"#define LANE_LINE_PITCH {LINE_PITCH}",
        f"#define LANE_MESSAGE_MOST {lane('message').width}",
        f"#define LANE_CAPTION {lane('caption').width}",
        f"#define LANE_HISTORY {lane('history').width}",
        f"#define LANE_SCENARIO {lane('scenario').width}",
        f"#define LANE_DREAM_SUMMARY {lane('dream').width}",
        f"#define LANE_BOXED {table('boxed', 'one_row')}",
        f"#define LANE_DESCRIBED {table('described')}",
    ]
