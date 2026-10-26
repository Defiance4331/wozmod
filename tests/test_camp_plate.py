"""The camp-talk name plate (wozmod.runtime.camp_plate): the stock plate, widened to hold the longest speaker name."""

import os
from pathlib import Path

import pytest
from PIL import Image

from wozmod.runtime import camp_plate, latin_font


def synthetic_plate() -> Image.Image:
    # A 266-column plate: a 3-column left cap, an identical middle, a 4-column right cap, then transparency.
    plate = Image.new("RGBA", (512, 4))
    for x in range(camp_plate.STOCK_WIDTH):
        shade = 10 + x if x < 3 else 200 + x - 262 if x >= 262 else 100
        for y in range(4):
            plate.putpixel((x, y), (shade, y, 0, 255))
    return plate


def test_the_plate_widens_its_middle_and_keeps_both_end_caps() -> None:
    # Bug: a cap edge is read one column off, so the widened plate repeats a cap column (a seam) or drops one, and
    # the width the runtime draws no longer matches the art.
    plate = synthetic_plate()
    assert camp_plate.caps(plate) == (3, 4)
    out = camp_plate.widened(plate, camp_plate.width(plate, name=50))
    assert [out.getpixel((x, 0))[0] for x in range(58)] == [10, 11, 12, *[100] * 50, 200, 201, 202, 203, 0]


def test_each_widened_plate_stays_centred_where_the_stock_plate_is():
    # Bugs: a plate keeps an outer anchor of another layout (the X360's 26 and 1256 put the plates at the screen
    # edges), or grows to one side, so names sit off the stock places. Oracle: the stock plates at x 232 and 787,
    # 266 wide (0x59C3DF, 0x59C3E6, 0x59C421), centred on 365 and 920.
    from wozmod.runtime.runtime_edits import camp_plate_edits
    xs = {va: int.from_bytes(ours[1:], "little") for va, _, ours in camp_plate_edits(340) if va in (0x59C3DF, 0x59C3E6)}
    assert {va: x + 340 // 2 for va, x in xs.items()} == {0x59C3DF: 365, 0x59C3E6: 920}


@pytest.mark.skipif(not (os.environ.get("WOZMOD_TEXT_ROOM") and os.environ.get("WOZMOD_GAME")
                         and os.environ.get("WOZMOD_DONORS")),
                    reason="set WOZMOD_TEXT_ROOM=1, WOZMOD_GAME and WOZMOD_DONORS (about 15 s)")
def test_the_plate_is_as_wide_as_the_longest_camp_talk_speaker_name() -> None:
    # Bug: a translation makes a camp-talk speaker's name longer than LONGEST_NAME, and it overruns the plate.
    from wozmod.formats.linkdata import archive
    from wozmod.runtime import text_room
    from wozmod.runtime.text_table import decode_english
    from wozmod.text.build import resolved_text
    from wozmod.text.narration_voices import container
    from wozmod.text.resolver import DonorResolver

    stock = Path(os.environ["WOZMOD_GAME"])
    _, pairs, parts = resolved_text(stock, DonorResolver(Path(os.environ["WOZMOD_DONORS"])), Path("data/text"))
    names = text_room.english_inputs(stock, pairs, parts)[1]
    scenes = container(archive(stock / "LINKDATA1")[4105])[:56]
    widest = max((latin_font.width(decode_english(names[i], "replace")), i) for i in camp_plate.speakers(scenes))
    assert widest[0] == camp_plate.LONGEST_NAME, f"officer {widest[1]}'s name is {widest[0]} px wide"
