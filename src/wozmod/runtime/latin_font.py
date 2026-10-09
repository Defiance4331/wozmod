"""Latin glyph advances for the runtime width table."""

from __future__ import annotations

CELL = 24  # MOZ atlas cell; every glyph below LATIN_GLYPH keeps this advance
LATIN_GLYPH = 2700
ADVANCES = bytes.fromhex(
    "08080a121314140709090d10060b060c120b121213121212131208080e100e12141412141211111412071112111413141214"
    "1212131214141313120918090c0e1810100f10100c120f08090f07140f1010100c0f0d0f111411100f181818151113100d")
WIDTHS = bytes([CELL] * LATIN_GLYPH) + ADVANCES  # the game's glyph width table, 2,799 bytes
