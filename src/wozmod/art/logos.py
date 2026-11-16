"""The official English X360 series logos in place of the stock Japanese ones.

A texture row's `logo` recipe draws a donor logo cropped to `box`, with an optional `glow` [radius, strength, [r, g, b]]
behind it; `fade` multiplies its alpha, as the stock unselected states are the selected ones at 0.377 (96/255) alpha
with their colours unchanged. `recipe` names an entry of the art data's `logos` table that the keys beside it extend;
an entry may name another, so each donor logo's crop is written once.

The logo goes where its screen's layout puts it, not where the Japanese logo's own shape put it (menu-lettering ledger
row 40). A logo in a slot (a row, card or frame) states `slot` [x0, y0, x1, y1]: it is as large as the slot holds and
centred on it. Logos shown together as a set state `line` [x, y, height]: the OROCHI word, rows `orochi` [top, bottom]
of the crop, is `height` pixels tall with its middle at `y`, and the logo is centred on `x`; the set's rows give equal
heights and one screen line.

`under` draws the logo over a flattened stock picture instead of an empty texture. The stock Japanese logo's own layer
(`footprint`, a stock texture) marks what to lift out of the picture: that footprint is cleared, then covered by the
`cover` layer (the stock Z) where given. The recipe's numbers are in the footprint layer's texture and map onto the
picture by `scale` and `offset`.

`silhouette` instead draws the white glow of items of the same package stacked in order (the loading screen's flash
4067.2 is that glow of its Z and logo).
"""
from __future__ import annotations

from PIL import Image, ImageFilter

from .faces import source_image, stock_image
from .imaging import glow, glowed, rgba


def resolved(edit: dict, recipes: dict) -> dict:
    """`edit` with its logo recipe's named entry merged under the row's own keys."""
    spec = edit.get("logo")
    if not spec or "recipe" not in spec:
        return edit
    while "recipe" in spec:
        spec = recipes[spec["recipe"]] | {k: v for k, v in spec.items() if k != "recipe"}
    return edit | {"logo": spec}


def warped(image: Image.Image, size: tuple[int, int], scale: float, offset) -> Image.Image:
    """`image` scaled by `scale` and moved by `offset` onto a `size` canvas."""
    dx, dy = offset
    return image.transform(size, Image.Transform.AFFINE, (1 / scale, 0, -dx / scale, 0, 1 / scale, -dy / scale),
                           Image.Resampling.BICUBIC)


def drawn(source: Image.Image, spec: dict, size: tuple[int, int], scale: float = 1.0, offset=(0, 0)) -> Image.Image:
    """The donor logo of `spec` on a transparent `size` canvas, its numbers mapped by `scale` and `offset`."""
    art = source.crop(spec["box"])
    if "slot" in spec:
        x0, y0, x1, y1 = spec["slot"]
        k = min((x1 - x0) / art.width, (y1 - y0) / art.height)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    else:
        (cx, middle, height), (top, bottom) = spec["line"], spec["orochi"]
        k = height / (bottom - top)
        cy = middle + (art.height / 2 - (top + bottom) / 2) * k
    art = art.resize((round(art.width * k * scale), round(art.height * k * scale)), Image.Resampling.LANCZOS)
    x = round(cx * scale + offset[0] - art.width / 2)
    y = round(cy * scale + offset[1] - art.height / 2)
    if x < 0 or y < 0 or x + art.width > size[0] or y + art.height > size[1]:
        raise ValueError(f"a {art.width}x{art.height} logo at ({x}, {y}) leaves its {size[0]}x{size[1]} texture")
    canvas = Image.new("RGBA", size)
    canvas.alpha_composite(art, (x, y))
    if "glow" in spec:
        radius, strength, colour = spec["glow"]
        canvas = glowed(canvas, radius * scale, strength, tuple(colour))
    return canvas


def logo_image(stock: Image.Image, spec: dict, donors: dict, package) -> Image.Image:
    """The texture `spec` draws in place of `stock`; `package` is its entry with the earlier rows already applied."""
    if "silhouette" in spec:
        radius, strength = spec["silhouette"]["radius"], spec["silhouette"]["strength"]
        stack = Image.new("RGBA", stock.size)
        for item in spec["silhouette"]["items"]:
            stack.alpha_composite(rgba(package.items[item].g1t.textures[0]))
        out = Image.new("RGBA", stock.size, (255, 255, 255, 0))
        out.putalpha(glow(stack, radius, strength))
        return out
    source = source_image(spec, donors, None)
    under = spec.get("under")
    if under is None:
        out = drawn(source, spec, stock.size)
    else:
        scale, offset = under["scale"], under["offset"]
        footprint = (warped(stock_image(under["footprint"], donors), stock.size, scale, offset).getchannel("A")
                     .point(lambda v: 255 if v > 40 else 0).filter(ImageFilter.MaxFilter(3)))
        out = stock.copy()
        out.paste((0, 0, 0, 0), mask=footprint)
        if "cover" in under:
            cover = Image.new("RGBA", stock.size)
            cover.paste(warped(stock_image(under["cover"], donors), stock.size, scale, offset), mask=footprint)
            out.alpha_composite(cover)
        out.alpha_composite(drawn(source, spec, stock.size, scale, offset))
    if "fade" in spec:
        out.putalpha(out.getchannel("A").point(lambda v: round(v * spec["fade"])))
    return out
