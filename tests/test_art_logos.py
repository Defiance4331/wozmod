from PIL import Image

from wozmod.art.logos import logo_image, resolved


class Texture:
    def __init__(self, image: Image.Image):
        self.width, self.height, self._rgba = image.width, image.height, image.tobytes()

    def decode_rgba(self) -> bytes:
        return self._rgba


class Item:
    def __init__(self, image: Image.Image):
        self.textures = [Texture(image)]


def square(size: int, box, colour) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    image.paste(colour, box)
    return image


def test_a_flattened_logo_is_lifted_and_replaced_where_scale_and_offset_put_it():
    """Bug: a sign or scale slip in mapping layer numbers onto the picture leaves the old logo showing beside the new."""
    red, blue = (255, 0, 0, 255), (0, 0, 255, 255)
    layer = square(32, (10, 10, 22, 22), red)  # the stock Japanese logo layer: centre (16, 16)
    picture = Image.new("RGBA", (64, 64))
    picture.paste(red, (25, 23, 49, 47))  # that layer flattened at scale 2, offset (5, 3)
    donors = {("moz", 1): {0: Item(layer)}, 7: {0: Item(square(32, (0, 0, 32, 32), blue))}}
    spec = {"wo1-x360": [7, 0, 0], "box": [0, 0, 32, 32], "slot": [12, 12, 20, 20],
            "under": {"footprint": [1, 0, 0], "scale": 2, "offset": [5, 3]}}

    out = logo_image(picture, spec, donors, None)

    assert [p for p in out.get_flattened_data() if p[:3] == (255, 0, 0) and p[3]] == []
    assert out.getpixel((37, 35)) == blue  # the layer's centre mapped: 16 * 2 + 5, 16 * 2 + 3
    assert out.getchannel("A").getbbox() == (29, 27, 45, 43)  # 8 px wide at scale 2, centred there


def test_the_logos_of_a_set_put_their_orochi_words_on_its_one_screen_line():
    """Bug: a set logo's screen line maps into its texture with a slip (a sign, a swapped axis, the height scaled), so
    two logos drawn from different points leave the shared line (story select's WO2 logo once sat 25 px below the
    WO1 logo)."""
    logo = Image.new("RGBA", (40, 40), (255, 0, 0, 255))
    logo.paste((255, 255, 255, 255), (0, 24, 40, 32))  # the "OROCHI" rows 24-31, low in the logo as in both donors
    donors = {7: {0: Item(logo)}}
    recipes = {"logo": {"wo1-x360": [7, 0, 0], "box": [0, 0, 40, 40], "orochi": [24, 32]},
               "left": {"recipe": "logo", "set": "pair", "window": [10, 200, 100, 140], "x": 60},
               "right": {"recipe": "logo", "set": "pair", "window": [300, 150, 100, 140], "x": 350}}
    sets = {"pair": {"line": [260, 16]}}  # the OROCHI word 16 layout px tall, its middle at y 260

    for name, (x, y) in (("left", (10, 200)), ("right", (300, 150))):
        spec = resolved({"logo": {"recipe": name}}, recipes, sets)["logo"]
        out = logo_image(Image.new("RGBA", (100, 140)), spec, donors, None)
        white = [y + row for row in range(140) if out.getpixel((50, row))[1] > 128]  # the white rows, on screen
        assert (white[0], white[-1] + 1) == (252, 268), name  # 16 rows, centred on 260
        left, _, right, _ = out.getchannel("A").getbbox()
        assert (x + left + x + right) / 2 == {"left": 60, "right": 350}[name]  # centred on its x


def test_a_set_logo_that_reaches_past_the_window_the_game_draws_is_refused():
    """Bug: the logo is checked against its texture, not the window the game draws from it, so the game cuts it off
    (v0.3.0 cut the end of the Warriors Orochi 2 logo's 2 and its glow at the window's right edge)."""
    donors = {7: {0: Item(Image.new("RGBA", (40, 40), (255, 255, 255, 255)))}}
    recipes = {"logo": {"wo1-x360": [7, 0, 0], "box": [0, 0, 40, 40], "orochi": [20, 30], "set": "pair", "x": 60}}
    sets = {"pair": {"line": [60, 10]}}  # the logo 40 texels square, centred on (60, 60) of a 100-texel texture

    for width, refused in ((81, False), (80, True)):  # its last column is texel 79: the window must show past it
        recipes["logo"]["window"] = [0, 0, width, 100]
        spec = resolved({"logo": {"recipe": "logo"}}, recipes, sets)["logo"]
        try:
            logo_image(Image.new("RGBA", (100, 100)), spec, donors, None)
        except ValueError:
            assert refused, width
        else:
            assert not refused, width


def test_the_logos_of_a_set_take_its_one_glow():
    """Bug: a set logo keeps a glow of its own instead of its set's, so the pair drift apart (v0.3.0 gave the Warriors
    Orochi 2 logo a wide purple glow beside the Warriors Orochi logo's tight blue one)."""
    donors = {7: {0: Item(Image.new("RGBA", (20, 20), (255, 255, 255, 255)))}}
    recipes = {"logo": {"wo1-x360": [7, 0, 0], "box": [0, 0, 20, 20], "orochi": [0, 20], "set": "pair",
                        "window": [0, 0, 100, 100], "x": 50, "glow": [9, 1.0, [255, 0, 0]]}}
    sets = {"pair": {"line": [50, 20], "glow": [2, 1.0, [0, 0, 255]]}}  # the logo white on (40, 40)-(60, 60)

    spec = resolved({"logo": {"recipe": "logo"}}, recipes, sets)["logo"]
    out = logo_image(Image.new("RGBA", (100, 100)), spec, donors, None)

    r, _, b, a = out.getpixel((39, 50))  # just left of the logo, inside the set's 2-texel glow
    assert a and b > r
