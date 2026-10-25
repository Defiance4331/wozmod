"""The camp-talk speaker name plate (LINKDATA1 4104.0): the stock plate, widened just enough for the longest name.

The stock game draws the plate's first STOCK_WIDTH columns (0x59C421 and 0x59C42C, `push 0x10A`). Its art is a left
end cap, a run of identical middle columns and a right end cap: the widening repeats one middle column, so the caps
stay as drawn, and the name lies on the middle. Width = left cap + the longest name + right cap, the caps read from
the stock art (caps), the name from the camp-talk speakers (LONGEST_NAME). The art build widens the texture to this
width and the runtime build draws it at this width (runtime_edits.camp_plate_edits).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ENTRY, ITEM = 4104, 0
STOCK_WIDTH = 266
# The widest English name of the 79 camp-talk speakers (the speaker byte of each 16-byte line record in the 56 scene
# blocks of LINKDATA1 4105) at the stock name cell, 24: officer 72. The release DLL is built from the stock game
# alone, so it cannot compose names (they need the donors); tests/test_camp_plate.py recomputes this from the names
# and fails, naming the speaker, when a translation makes another name longer.
LONGEST_NAME = 258


def speakers(scenes: list[bytes]) -> set[int]:
    """The officer ids that speak in camp talk: byte 1 of each 16-byte line record of the scene blocks (byte 0 of
    each record is the scene's line count), the byte the plate draw reads at 0x59C456."""
    return {block[16 * k + 1] for block in scenes for k in range(block[0])}


def caps(plate: Image.Image, drawn: int = STOCK_WIDTH) -> tuple[int, int]:
    """The widths of the plate's end caps: the drawn columns outside the run of identical columns at its middle."""
    columns = [plate.crop((x, 0, x + 1, plate.height)).tobytes() for x in range(drawn)]
    first = last = drawn // 2
    while first > 0 and columns[first - 1] == columns[first]:
        first -= 1
    while last < drawn - 1 and columns[last + 1] == columns[last]:
        last += 1
    return first, drawn - 1 - last


def width(plate: Image.Image, name: int = LONGEST_NAME) -> int:
    """The drawn width that holds a name `name` pixels wide on the plate's middle, between its stock caps."""
    left, right = caps(plate)
    return left + name + right


def widened(plate: Image.Image, to: int) -> Image.Image:
    """The plate with its middle repeated so its drawn width is `to`; the canvas keeps its size."""
    left, right = caps(plate)
    if to > plate.width:
        raise ValueError(f"the camp-talk plate needs {to} px, its texture is {plate.width} wide: give its row a size")
    out = Image.new("RGBA", plate.size)
    out.paste(plate.crop((0, 0, left, plate.height)), (0, 0))
    out.paste(plate.crop((left, 0, left + 1, plate.height)).resize((to - left - right, plate.height)), (left, 0))
    out.paste(plate.crop((STOCK_WIDTH - right, 0, STOCK_WIDTH, plate.height)), (to - right, 0))
    return out


def stock_plate(game: Path) -> Image.Image:
    """The stock plate art from the game folder's LINKDATA1."""
    from wozmod.formats.linkdata import archive
    from wozmod.formats.textures import parse_texture_package

    texture = parse_texture_package(archive(game / "LINKDATA1")[ENTRY]).items[ITEM].g1t.textures[0]
    return Image.frombytes("RGBA", (texture.width, texture.height), texture.decode_rgba())
