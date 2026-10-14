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
    "080608111314140509090b0f060b060c120a121212121311131306060e100e121414121413111114130611121115131412141212"
    "131313131313110918090c0e1810111011100c111006090f0614101011110c0f0c10101410100f181818141013100b")
WIDTHS = bytes([CELL] * LATIN_GLYPH) + ADVANCES  # the game's glyph width table, 2,799 bytes
