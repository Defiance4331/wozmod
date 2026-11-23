"""The official English X360 series logos in place of the stock Japanese ones.

A texture row's `logo` recipe draws a donor logo cropped to `box`, with an optional `glow` [radius, strength, [r, g, b]]
behind it; `fade` multiplies its alpha, as the stock unselected states are the selected ones at 0.377 (96/255) alpha
with their colours unchanged. `recipe` names an entry of the art data's `logos` table that the keys beside it extend;
an entry may name another, so each donor logo's crop is written once.

The logo goes where its screen's layout puts it, not where the Japanese logo's own shape put it (menu-lettering ledger
row 40). A logo in a slot (a row, card or frame) states `slot` [x0, y0, x1, y1]: it is as large as the slot holds and
centred on it. A logo drawn over a stock shape (the English logo on the Z) states `rows` [top, bottom], the rows it
fills, and `outline`, the stock layer of that shape: its ink sits sideways on the shape's outline (ledger row 45). The
outline is the convex hull of the layer's solid texels, its middle is the mean over the logo's rows of the midpoint
between the hull's left and right edges, and the logo's ink is its alpha-weighted centre, glow excluded. A box on the
shape's box would put the logo left of the Z, whose left side slants out to a point, and left again because the logo's
ink leans left of its own box. Logos that belong together name their set, `set`: the art data's `logo_sets` states once what the
set's logos share, so they cannot drift apart, and each member takes those keys over its own (the scenario badges
share a `slot` and a `glow`). A set may give `line` [y, height] in layout px (the stock 1280x720 menu units; 1.5
screen px at 1080p): its logos are shown side by side and share that OROCHI line. Each such logo states `x`, its centre
in layout px, and `window` [x, y, width, height], the layout rect the game draws its texture through: texels from
(0, 0), one per layout px (story select 0x59D640 and the Free Mode story list 0x589EE0 push the same rects). So the
OROCHI word, rows `orochi` [top, bottom] of the crop, is `height` texels tall with its middle on the set's line, and
the logo, glow included, must lie inside the window's texels or the game cuts it off.

`under` draws the logo over a flattened stock picture instead of an empty texture. The stock Japanese logo's own layer
(`footprint`, a stock texture) marks what to lift out of the picture: that footprint is cleared, then covered by the
`cover` layer (the stock Z) where given. The recipe's numbers are in the footprint layer's texture and map onto the
picture by `scale` and `offset`.

`silhouette` instead draws the white glow of items of the same package stacked in order (the loading screen's flash
4067.2 is that glow of its Z and logo).
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

from .faces import source_image, stock_image
from .imaging import glow, glowed, rgba


def resolved(edit: dict, recipes: dict, sets: dict) -> dict:
    """`edit` with its logo recipe's named entry merged under the row's own keys, and a set logo's line in its own
    texels and the rest of its set's shared keys over its own."""
    spec = edit.get("logo")
    if not spec or "recipe" not in spec:
        return edit
    while "recipe" in spec:
        spec = recipes[spec["recipe"]] | {k: v for k, v in spec.items() if k != "recipe"}
    if "set" in spec:
        shared = sets[spec["set"]]
        spec = spec | {k: v for k, v in shared.items() if k != "line"}
        if "line" in shared:
            (middle, height), (x, y, width, rows) = shared["line"], spec["window"]
            spec |= {"line": [spec["x"] - x, middle - y, height], "shown": [width, rows]}
    return edit | {"logo": spec}


def warped(image: Image.Image, size: tuple[int, int], scale: float, offset) -> Image.Image:
    """`image` scaled by `scale` and moved by `offset` onto a `size` canvas."""
    dx, dy = offset
    return image.transform(size, Image.Transform.AFFINE, (1 / scale, 0, -dx / scale, 0, 1 / scale, -dy / scale),
                           Image.Resampling.BICUBIC)


def ink_centre(image: Image.Image) -> float:
    """The alpha-weighted mean x of `image`, in texels from its left edge."""
    columns = list(image.getchannel("A").convert("F").resize((image.width, 1), Image.Resampling.BOX).get_flattened_data())
    return sum((x + 0.5) * weight for x, weight in enumerate(columns)) / sum(columns)


def outline_middle(layer: Image.Image, rows) -> float:
    """The mean, over `rows` [top, bottom), of the midpoint between the left and right edges of the convex hull of
    `layer`'s solid texels (alpha above 128)."""
    solid = layer.getchannel("A").point(lambda v: 255 if v > 128 else 0)
    ends = [(x, y) for y in range(layer.height) if (box := solid.crop((0, y, layer.width, y + 1)).getbbox())
            for x in (box[0], box[2] - 1)]
    hull = Image.new("L", layer.size)
    ImageDraw.Draw(hull).polygon(convex_hull(ends), fill=255)
    middles = []
    for y in range(*rows):
        box = hull.crop((0, y, layer.width, y + 1)).getbbox()
        if box is None:
            raise ValueError(f"row {y} of a logo's rows {rows} passes outside its outline")
        middles.append((box[0] + box[2]) / 2)
    return sum(middles) / len(middles)


def convex_hull(points) -> list[tuple[int, int]]:
    """The corners of the convex hull of `points`, in order (Andrew's monotone chain)."""
    points = sorted(set(points))
    def half(chain_points):
        chain = []
        for p in chain_points:
            while len(chain) >= 2 and ((chain[-1][0] - chain[-2][0]) * (p[1] - chain[-2][1])
                                       - (chain[-1][1] - chain[-2][1]) * (p[0] - chain[-2][0])) <= 0:
                chain.pop()
            chain.append(p)
        return chain[:-1]
    return half(points) + half(reversed(points))


def drawn(source: Image.Image, spec: dict, size: tuple[int, int], scale: float = 1.0, offset=(0, 0),
          outline: Image.Image | None = None) -> Image.Image:
    """The donor logo of `spec` on a transparent `size` canvas, its numbers mapped by `scale` and `offset`; `outline`
    is the stock layer `spec["outline"]` names, in the texels of those numbers."""
    art = source.crop(spec["box"])
    if "slot" in spec:
        x0, y0, x1, y1 = spec["slot"]
        k = min((x1 - x0) / art.width, (y1 - y0) / art.height)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    elif "outline" in spec:
        top, bottom = spec["rows"]
        k = (bottom - top) / art.height
        cx = outline_middle(outline, spec["rows"]) + (art.width / 2 - ink_centre(art)) * k
        cy = (top + bottom) / 2
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
    box = canvas.getchannel("A").getbbox()
    if "shown" in spec and box and (min(box[:2]) == 0 or box[2] >= spec["shown"][0] or box[3] >= spec["shown"][1]):
        raise ValueError(f"a logo with ink at {box} meets an edge of the {spec['shown']} texels the game draws")
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
    outline = stock_image(spec["outline"], donors) if "outline" in spec else None
    under = spec.get("under")
    if under is None:
        out = drawn(source, spec, stock.size, outline=outline)
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
        out.alpha_composite(drawn(source, spec, stock.size, scale, offset, outline))
    if "fade" in spec:
        out.putalpha(out.getchannel("A").point(lambda v: round(v * spec["fade"])))
    return out
