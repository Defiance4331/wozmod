"""Pixel helpers the art build composes textures with: RGBA conversion, placing, keying, masks, tints and glows."""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


def rgba(texture) -> Image.Image:
    return Image.frombytes("RGBA", (texture.width, texture.height), texture.decode_rgba())

def ink(image: Image.Image):

    return image.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()

def placed(canvas: Image.Image, image: Image.Image, x: int, y: int) -> Image.Image:

    if image.width > canvas.width or image.height > canvas.height:
        raise ValueError(f"{image.width}x{image.height} art does not fit a {canvas.width}x{canvas.height} texture")
    canvas.alpha_composite(image, (min(max(0, x), canvas.width - image.width), min(max(0, y), canvas.height - image.height)))
    return canvas

def fitted(source: Image.Image, width: int, height: int, box, left: bool = False, shrink: bool = False) -> Image.Image:

    x0, y0, x1, y1 = box
    if shrink:
        scale = min(1.0, (x1 - x0) / source.width, (y1 - y0) / source.height)
        source = source.resize((max(1, round(source.width * scale)), max(1, round(source.height * scale))), Image.LANCZOS)
    return placed(Image.new("RGBA", (width, height)), source,
                  x0 if left else x0 + (x1 - x0 - source.width) // 2, y0 + (y1 - y0 - source.height) // 2)

def restriped(image: Image.Image, box, column: int) -> Image.Image:

    x0, y0, x1, y1 = box
    out = image.copy()
    out.paste(image.crop((column, y0, column + 1, y1)).resize((x1 - x0, y1 - y0)), (x0, y0))
    return out

def rows_stretched(image: Image.Image, first: int, last: int, extra: int) -> Image.Image:
    """image taller by extra rows: rows first to last resampled into extra more rows (one row: repeated), the rows
    below them moved down (the canvas keeps its size; rows pushed past its bottom are dropped)."""
    out = Image.new(image.mode, image.size)
    out.paste(image.crop((0, 0, image.width, first)), (0, 0))
    band, size = image.crop((0, first, image.width, last + 1)), (image.width, last + 1 - first + extra)
    # each channel on its own: Pillow resamples RGBA premultiplied, which blanks the colour of transparent pixels
    out.paste(Image.merge(image.mode, [c.resize(size, Image.Resampling.BILINEAR) for c in band.split()]), (0, first))
    out.paste(image.crop((0, last + 1, image.width, image.height - extra)), (0, last + 1 + extra))
    return out

def keyed(image: Image.Image, column: int, threshold: int, solid: bool = False) -> Image.Image:

    background = image.crop((column, 0, column + 1, image.height)).resize(image.size)
    difference = ImageChops.difference(image.convert("RGB"), background.convert("RGB"))
    mask = difference.convert("L").point(lambda v: 255 if v > threshold else 0)
    if solid:
        w, h = mask.size
        for point in [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]:
            if mask.getpixel(point) == 0:
                ImageDraw.floodfill(mask, point, 128)
        mask = mask.point(lambda v: 0 if v == 128 else 255)
    out = image.copy()
    out.putalpha(mask)
    return out

def plated(source: Image.Image, width: int, column: int, right: int) -> Image.Image:

    x1 = ink(source)[2]
    out = Image.new("RGBA", (max(width, source.width), source.height))
    out.paste(source.crop((0, 0, 1, source.height)), (0, 0))
    out.paste(source.crop((column, 0, column + 1, source.height)).resize((width - right - 1, source.height)), (1, 0))
    out.paste(source.crop((x1 - right, 0, x1, source.height)), (width - right, 0))
    return out

def unlettered(images: list[Image.Image], saturation: int, grow: int) -> Image.Image:

    def lettering(image):
        fill = image.convert("RGB").convert("HSV").getchannel("S").point(lambda v: 255 if v > saturation else 0)
        fill = ImageChops.multiply(fill, image.getchannel("A").point(lambda v: 255 if v > 16 else 0))
        return fill.filter(ImageFilter.MaxFilter(2 * grow + 1)).load()

    masks, pixels = [lettering(image) for image in images], [image.load() for image in images]
    out = Image.new("RGBA", images[0].size)
    px = out.load()
    holes = set()
    for y in range(out.height):
        for x in range(out.width):
            known = [p[x, y] for p, m in zip(pixels, masks, strict=True) if not m[x, y]]
            if known:
                px[x, y] = min(known, key=lambda c: (c[3] > 16, max(c[:3]) - min(c[:3])))
            else:
                holes.add((x, y))
    hole_mask = Image.new("L", out.size)
    for point in holes:
        hole_mask.putpixel(point, 255)
    return refilled(out, hole_mask)

def refilled(image: Image.Image, mask: Image.Image) -> Image.Image:

    out, holes = image.copy(), {(x, y) for y in range(image.height) for x in range(image.width) if mask.getpixel((x, y))}
    px = out.load()
    while holes:
        layer = {}
        for x, y in holes:
            near = [px[x + dx, y + dy] for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx or dy)
                    and 0 <= x + dx < out.width and 0 <= y + dy < out.height and (x + dx, y + dy) not in holes]
            if near:
                alpha = sum(c[3] for c in near)
                layer[x, y] = (*(round(sum(c[k] * c[3] for c in near) / alpha) if alpha else 0 for k in range(3)),
                               round(alpha / len(near)))
        for point, colour in layer.items():
            px[point] = colour
        holes -= layer.keys()
    feather = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(2))
    smooth = out
    for _ in range(20):
        smooth = Image.composite(smooth.filter(ImageFilter.GaussianBlur(3)), out, feather)
    return smooth

def greyed(image: Image.Image) -> Image.Image:

    alpha = image.getchannel("A")
    luma = image.convert("L").point(lambda v: round(0.4 * v))
    shadow = Image.new("RGBA", image.size)
    shadow.putalpha(alpha.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(3)))
    shadow.alpha_composite(Image.merge("RGBA", (luma, luma, luma, alpha)))
    return shadow

def glow(image: Image.Image, radius: float, strength: float) -> Image.Image:

    mask = image.getchannel("A").point(lambda v: 255 if v > 200 else 0)
    return mask.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: min(255, round(v * strength)))

def unglowed(image: Image.Image, radius: float, strength: float) -> Image.Image:

    out, halo = image.copy(), glow(image, radius, strength).load()
    near = image.getchannel("A").point(lambda v: 255 if v > 200 else 0).filter(ImageFilter.MaxFilter(7)).load()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            if a <= 200:
                h = halo[x, y]
                px[x, y] = (r, g, b, round(max(0, a - h) * 255 / (255 - h)) if near[x, y] and h < 255 else 0)
    return out

def glowed(image: Image.Image, radius: float, strength: float, colour=(255, 255, 255)) -> Image.Image:

    out = Image.new("RGBA", image.size, (*colour, 0))
    out.putalpha(glow(image, radius, strength))
    out.alpha_composite(image)
    return out

def lettering_mask(image: Image.Image, box) -> Image.Image:

    px, fill = image.load(), Image.new("L", image.size)
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0, x1):
            if min(px[x, y][:3]) > 175 and max(px[x, y][:3]) - min(px[x, y][:3]) < 28:
                fill.putpixel((x, y), 255)
    near_dark = image.convert("L").point(lambda v: 255 if v < 110 else 0).filter(ImageFilter.MaxFilter(7))
    return ImageChops.multiply(fill, near_dark).filter(ImageFilter.MaxFilter(9))

def navy_mask(image: Image.Image, rows) -> Image.Image:

    px, mask = image.load(), Image.new("L", image.size)
    for y in range(*rows):
        for x in range(image.width):
            r, g, b, a = px[x, y]
            if a > 200 and r < 30 and g < 30 and 20 <= b <= 70 and b - r > 15:
                mask.putpixel((x, y), 255)
    return mask.filter(ImageFilter.MaxFilter(9))

def mirrored(image: Image.Image, mask: Image.Image) -> Image.Image:

    _, top, _, bottom = mask.getbbox()
    height = bottom - top
    if top < height or bottom + height > image.height:
        raise ValueError(f"mask rows {top}-{bottom} leave too little art to mirror")
    above = ImageOps.flip(image.crop((0, top - height, image.width, top)))
    below = ImageOps.flip(image.crop((0, bottom, image.width, bottom + height)))
    patch = image.copy()
    patch.paste(Image.composite(below, above, Image.linear_gradient("L").resize((image.width, height))), (0, top))
    return Image.composite(patch, image, mask.filter(ImageFilter.GaussianBlur(2)))

def spliced(image: Image.Image, left, column: int, top: int, right, width: int) -> Image.Image:

    ends = image.crop((left[0], 0, left[1], image.height)), image.crop((right[0], 0, right[1], image.height))
    middle = image.crop((column, 0, column + 1, image.height))
    middle.paste((0, 0, 0, 0), (0, 0, 1, top))
    out = Image.new("RGBA", image.size)
    out.paste(ends[0], (0, 0))
    out.paste(middle.resize((width - ends[0].width - ends[1].width, image.height)), (ends[0].width, 0))
    out.paste(ends[1], (width - ends[1].width, 0))
    return out

def tinted(image: Image.Image, factors: list[float]) -> Image.Image:
    return Image.merge("RGBA", [c.point(lambda v, f=f: round(v * f)) for c, f in zip(image.split(), [*factors, 1], strict=True)])

def hue_shifted(image: Image.Image, degrees: float) -> Image.Image:

    h, s, v = image.convert("RGB").convert("HSV").split()
    shifted = Image.merge("HSV", (h.point(lambda x: (x + round(degrees * 256 / 360)) % 256), s, v)).convert("RGB")
    shifted.putalpha(image.getchannel("A"))
    return shifted
