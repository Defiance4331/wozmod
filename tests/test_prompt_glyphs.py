"""The drawn button prompts cover every slot the runtime swaps, at the texture's size, centred where the game shows them."""
import pytest

from wozmod.art.prompt_glyphs import prompt_art
from wozmod.runtime.prompts import PROMPT_SLOTS

WIDE = {3, 13, 14, 15, 178, 179}  # the 64x32 icons in entry 4079, read off the stock textures
NOT_A_GLYPH = {44}  # the 2P text strip keeps its own art
SHOWN = 30  # the game draws a slot's icon from texels 0-29 (0x549AD0: a 30x30 quad, UVs 0 to 30)


@pytest.mark.parametrize("scale", [1, 4])
@pytest.mark.parametrize("family", ["xbox", "playstation"])
def test_every_swapped_slot_is_drawn_at_its_texture_size_centred_in_the_shown_texels(family, scale):
    # Bugs: a slot missing, or drawn 32 wide where the texture is 64, makes the game mis-draw the icon; an icon centred
    # on the whole 32-texel cell shows a texel low and right of its box, and ink past texel 29 is cut off.
    art = prompt_art(family, scale)
    assert set(art) == set(PROMPT_SLOTS) - NOT_A_GLYPH
    wrong = {}
    for slot, image in art.items():
        box = image.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()
        centre = ((box[0] + box[2]) / 2 / scale, (box[1] + box[3]) / 2 / scale) if box else None
        if ((image.mode, image.size) != ("RGBA", ((64 if slot in WIDE else 32) * scale, 32 * scale)) or not box
                or max(box[2], box[3]) > SHOWN * scale or max(abs(c - SHOWN / 2) for c in centre) > 0.5):
            wrong[slot] = (image.mode, image.size, box)
    assert not wrong, wrong
