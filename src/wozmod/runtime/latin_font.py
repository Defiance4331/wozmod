"""Latin glyph advances for the runtime width table."""

from __future__ import annotations

CELL = 24  # MOZ atlas cell; every glyph below LATIN_GLYPH keeps this advance
LATIN_GLYPH = 2700
OFFICIAL = bytes.fromhex(  # the WO1 PC sysfont's advances: ASCII 0x20-0x7D, then the surrogates Ō ō Ū ū ï
    "08080a121314140709090d10060b060c120b121213121212131208080e100e12141412141211111412071112111413141214"
    "1212131214141313120918090c0e1810100f10100c120f08090f07140f1010100c0f0d0f111411100f181818151113100d")
# The advances the game steps by: each letter's ink plus room for each side by that side's shape, calibrated so
# typical English keeps the official width (wozmod.fonts.side_spacing; regenerate with
# `PYTHONPATH=src python3 -m wozmod.fonts.side_spacing` under the font Python). The space keeps the official 8.
ADVANCES = bytes.fromhex(
    "0807070f1214130408080a0d0408040a130a121210121310131305050c0f0c111412131314111114140711111016141412141312"
    "111411131111100818080a0c1810111011100a111107080f0715111111110c0f0b110f130e0e0e1818181411141109")
WIDTHS = bytes([CELL] * LATIN_GLYPH) + ADVANCES  # the game's glyph width table, 2,799 bytes
