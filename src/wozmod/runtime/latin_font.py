"""Latin glyph advances for the runtime width table."""

from __future__ import annotations

CELL = 24  # MOZ atlas cell; every glyph below LATIN_GLYPH keeps this advance
# The Latin cells: game glyph LATIN_GLYPH draws ASCII LATIN_FIRST, then one cell per ASCII code up to 0x7D, then one
# per SURROGATE_LETTERS letter, which CP932 lacks: text carries each as a CP932 code from SURROGATE_CODE up.
# runtime_edits.FONT_ROUTE routes the codes to these glyphs, and the runtime's generated header gets GLYPH_LATIN.
LATIN_GLYPH = 2700
LATIN_FIRST, LATIN_COUNT = 0x20, 94
SURROGATE_LETTERS = "ŌōŪūï"
SURROGATE_CODE = 0x8440  # CP932 Cyrillic capitals, which no English text uses
SURROGATE_GLYPH = LATIN_GLYPH + LATIN_COUNT
LATIN_CHARS = "".join(map(chr, range(LATIN_FIRST, LATIN_FIRST + LATIN_COUNT))) + SURROGATE_LETTERS  # cell order
OFFICIAL = bytes.fromhex(  # the WO1 PC sysfont's advances, in LATIN_CHARS order
    "08080a121314140709090d10060b060c120b121213121212131208080e100e12141412141211111412071112111413141214"
    "1212131214141313120918090c0e1810100f10100c120f08090f07140f1010100c0f0d0f111411100f181818151113100d")
# The advances the game steps by: each letter's ink plus room for each side by that side's shape, calibrated so
# typical English keeps the official width (wozmod.fonts.side_spacing; regenerate with
# `PYTHONPATH=src python3 -m wozmod.fonts.side_spacing` under the font Python). The space keeps the official 8.
ADVANCES = bytes.fromhex(
    "080608111314140509090b0f060b060c120a121212121311131306060e100e121414121413111114130611121115131412141212"
    "131313131313110918090c0e1810111011100c111006090f0614101011110c0f0c10101410100f181818141013100b")
WIDTHS = bytes([CELL] * LATIN_GLYPH) + ADVANCES  # the game's glyph width table, one byte per glyph


def width(text: str, cell: int = CELL) -> int:
    """A line's width as the game measures it (0x54BF80) at cell width ``cell``: each glyph's advance scaled to the
    cell and rounded down on its own, a non-Latin glyph the whole cell, a colour tag ('[' and two characters) and
    ']' nothing."""
    total, i = 0, 0
    while i < len(text):
        c = text[i]
        if c == "[":
            i += 3
            continue
        if c in "]\t":
            pass
        elif c in LATIN_CHARS:
            total += ADVANCES[LATIN_CHARS.index(c)] * cell // CELL
        elif ord(c) >= 0x80:
            total += cell
        i += 1
    return total
