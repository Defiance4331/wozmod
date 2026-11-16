from PIL import Image

from wozmod.art.logos import logo_image


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


def test_a_set_logo_puts_its_orochi_word_on_the_stated_line_at_the_stated_height():
    """Bug: the OROCHI middle maps with a slip, so the logos of a set sit off their shared line (story select's WO2
    logo once sat 25 px below the WO1 logo)."""
    logo = Image.new("RGBA", (40, 40), (255, 0, 0, 255))
    logo.paste((255, 255, 255, 255), (0, 24, 40, 32))  # the "OROCHI" rows 24-31, low in the logo as in both donors
    donors = {7: {0: Item(logo)}}
    spec = {"wo1-x360": [7, 0, 0], "box": [0, 0, 40, 40], "orochi": [24, 32], "line": [50, 60, 16]}

    out = logo_image(Image.new("RGBA", (100, 100)), spec, donors, None)

    white = [y for y in range(100) if out.getpixel((50, y))[1] > 128]  # green: the white rows, not the red body
    assert (white[0], white[-1] + 1) == (52, 68)  # 16 rows, centred on 60
    assert out.getchannel("A").getbbox() == (10, 4, 90, 84)  # the logo at 2x, centred on x 50
