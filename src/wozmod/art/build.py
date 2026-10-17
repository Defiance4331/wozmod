"""Build menu textures and font atlases from supplied archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
import subprocess
import tempfile
import unicodedata
import zlib
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from functools import lru_cache
from multiprocessing import get_all_start_methods, get_context
from pathlib import Path
from statistics import fmean, median, mode

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from wozmod.formats.linkdata import (
    encode_lzp2, parse_index, parse_lzp2, wo1_entries,
)
from wozmod import native, resources
from wozmod.progress import report as report_progress
from wozmod.runtime.english_runtime import write_manifest
from . import lettering
from .hires import HIRES_OVERLAY, HIRES_RIGHT_GROW, hires_blob
from wozmod.runtime.latin_font import CELL, LATIN_GLYPH
from wozmod.donors import WO1, WO1_X360, X360, extract_donor, verify_file
from wozmod.formats.textures import (
    G1T_HEADER, G1T_TEXTURE_HEADER, PIXEL_FORMAT_BGRA8, G1tFile, parse_g1t, parse_texture_package,
)

REPO = resources.root()
from wozmod.fonts.menu_plates import X360_PLATES, X360_PLATE_ENTRIES, clean_substrate
from wozmod.fonts.menu_references import reference, x360_members
MAPPING = REPO / "data/art/menu_textures.json"
FONT_PYTHON = os.environ.get("WOZMOD_FONT_PYTHON", "/usr/bin/python3")
PACKAGE_MAGIC = 0x33B59

LZP2_CACHE: Path | None = None
SHARED: dict = {}  

REVIEW_VARIANTS = {"help": "per-screen", "camp": "x360"}
CAMP_WO1_ROWS = {(4094, 4), (4094, 16), (4098, 11), (4098, 15)}

TIME_LABEL_LAYOUT = {
    (4080, 14): ("time-purple", 5.0, 23.5),
    (4080, 27): ("time-blue", 24.0, 23.5),  
    (4074, 5): ("time-x360", 4.0, 24.0),
    (4077, 27): ("time-x360", 4.0, 24.0),
    (4085, 9): ("time-x360", 8.0, 24.0),
}

def expanded(edit: dict):
    """Each (entry, recipe) a texture recipe stands for: its count steps the item and its donor or wo2 source alike."""
    for entry, first in [(edit["entry"], edit.get("item")), *edit.get("also", [])]:
        for k in range(edit.get("count", 1)):
            step = dict(edit, entry=entry, item=first + k) if first is not None else dict(edit, entry=entry)
            for key in ("donor", "wo2"):
                if k and key in edit:
                    step[key] = [edit[key][0], edit[key][1] + k, edit[key][2]]
            yield entry, step

def native_officer_source(edit: dict) -> tuple[int, int, int] | None:
    """The WO2 X360 member an officer banner is served whole from, if its recipe names one."""
    return tuple(edit["wo2"]) if edit.get("entry") == 4063 and "wo2" in edit else None

def officer_name_role(edit: dict, row: int = 0) -> str | None:

    lines = edit.get("card", {}).get("lines")
    if edit.get("entry") != 4095 or not lines:
        return None
    position = "single" if len(lines) == 1 else "first" if row == 0 else "second"
    return f"dream-x360-{position}"

def native_dream_card(g1t: G1tFile, edit: dict, donors: dict) -> G1tFile | None:

    spec = edit.get("card", {})
    if "wo2" not in spec:
        return None
    original = (reference(f"{edit['entry']}.{edit['item']}", 0) or {}).get("original", {})
    if (edit.get("entry") != 4095 or original.get("ens") != spec["wo2"]
            or spec["wo2"][0] != 27):
        raise ValueError("native Dream card must use its paired ENS27 portrait")
    box = spec["box"]
    if (len(box) != 4 or box[0] != 0 or box[2] != 196
            or box[3] - box[1] != 196 or not 0 <= box[1] <= 70):
        raise ValueError("native Dream card needs a 196x196 window inside the 196x266 art")
    source = source_image(spec, donors, None)
    if source.width < 196 or source.height < 266:
        raise ValueError("native Dream donor cannot hold its 196x266 portrait art")
    texture = g1t.textures[edit["texture"]]
    if texture.width < 196 or texture.height < 196:
        raise ValueError("native Dream card allocation cannot hold the stock square window")
    image = Image.new("RGBA", (texture.width, texture.height))
    image.paste(source.crop(box), (0, 0))
    return native_plate(g1t, image)

def set_review_variants(variants: dict[str, str] | list[str] | None = None) -> dict[str, str]:

    from wozmod.fonts.menu_styles import HELP_TREATMENTS

    selected = {"help": "per-screen", "camp": "x360"}
    values = variants or {}
    if isinstance(values, list):
        parsed = {}
        for value in values:
            if "=" not in value:
                raise ValueError(f"review variant must be NAME=VALUE: {value}")
            key, choice = value.split("=", 1)
            if key in parsed:
                raise ValueError(f"review variant specified twice: {key}")
            parsed[key] = choice
        values = parsed
    allowed = {"help": {"per-screen", *HELP_TREATMENTS}, "camp": {"wo1", "x360"}}
    for key, choice in values.items():
        if key not in allowed or choice not in allowed[key]:
            raise ValueError(f"unsupported review variant {key}={choice}; choices are "
                             + ", ".join(f"{name}={','.join(sorted(choices))}"
                                         for name, choices in allowed.items()))
        selected[key] = choice
    REVIEW_VARIANTS.clear()
    REVIEW_VARIANTS.update(selected)
    return dict(selected)

def menu_render_options(entry: int, item: int, row: int, piece: dict, style: str,
                        *, destination: tuple[int, int] | None = None) -> list[str]:

    from wozmod.fonts.menu_styles import help_treatment

    menu_style = "dark.camp-light" if piece.get("donor", [])[:2] == [1, 17] else MENU_STYLE[style]
    record = reference(f"{entry}.{item}", row) or {}
    role = record.get("role_variant")
    if menu_style == "dark.help":
        role = (help_treatment(entry, item, row) if REVIEW_VARIANTS["help"] == "per-screen"
                else REVIEW_VARIANTS["help"])
    elif menu_style == "dark.camp" and (entry, item) in CAMP_WO1_ROWS and REVIEW_VARIANTS["camp"] == "wo1":
        role = "wo1"
    elif not role and menu_style in {"dark.pause", "dark.camp"}:
        role = "blue" if piece.get("donor", [])[:2] in [[1, 18], [1, 19]] else "purple"
    time_key = (entry, item) if destination is None else destination
    if time_key in TIME_LABEL_LAYOUT:
        role = TIME_LABEL_LAYOUT[time_key][0]
    return ["--style", menu_style] + (["--role", role] if role else [])

def donor_g1ts(data: bytes) -> dict[int, G1tFile]:

    magic, count, table, _ = struct.unpack_from("<4I", data)
    if magic != PACKAGE_MAGIC:
        return {item.ordinal: item.g1t for item in parse_texture_package(data).items}
    items = {}
    for item in range(count):
        offset, size = struct.unpack_from("<II", data, table + 8 * item)
        member = data[offset * 16:offset * 16 + size]
        if member[:4] == b"GT1G":
            items[item] = parse_g1t(member)
    return items

# Donor art entries below WO1_PC_ENTRIES are WO1 X360 archive entries; from it on, WO1 PC entries whose X360
# counterpart differs or is missing, kept until that art moves.
WO1_PC_ENTRIES = 492

def wo1_sheet(entry: int) -> int:
    """The WO1 PC entry of a donor entry: the faces name the official sheets by it (X360 entry 11 has none)."""
    return entry if entry >= WO1_PC_ENTRIES else entry + 492 if entry <= 10 else entry + 491

def wo1_donors(args, entries: set[int]) -> dict:
    """The WO1 donor art entries, X360 ones read from --wo1-x360-donor and WO1 PC ones from --wo1-donor."""
    donors = {}
    for stem, read, wanted in ((args.wo1_x360_donor, x360_members, [e for e in entries if e < WO1_PC_ENTRIES]),
                               (args.wo1_donor, donor_g1ts, [e for e in entries if e >= WO1_PC_ENTRIES])):
        if wanted:
            donors |= {k: read(v) for k, v in wo1_entries(stem.with_suffix(".lnk"), stem.with_suffix(".idx"),
                                                          sorted(wanted)).items()}
    return donors

def wo2_g1ts(data: bytes) -> dict[int, G1tFile]:

    return x360_members(data)

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
    out = Image.new("RGBA", source.size)
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

def text_lines(layer: Image.Image, baselines) -> list[tuple[Image.Image, int]]:

    lines, src = [Image.new("RGBA", layer.size) for _ in baselines], layer.load()
    for piece in strokes(layer, lambda p: p[3] > 0, 1):
        middle = (min(y for _, y in piece) + max(y for _, y in piece)) / 2
        px = lines[next((k for k, b in enumerate(baselines) if middle <= b), len(baselines) - 1)].load()
        for point in piece:
            px[point] = src[point]
    return [(line.crop(line.getbbox()), line.getbbox()[1]) for line in lines if line.getbbox()]

def spliced(image: Image.Image, left, column: int, top: int, right, width: int) -> Image.Image:

    ends = image.crop((left[0], 0, left[1], image.height)), image.crop((right[0], 0, right[1], image.height))
    middle = image.crop((column, 0, column + 1, image.height))
    middle.paste((0, 0, 0, 0), (0, 0, 1, top))
    out = Image.new("RGBA", image.size)
    out.paste(ends[0], (0, 0))
    out.paste(middle.resize((width - ends[0].width - ends[1].width, image.height)), (ends[0].width, 0))
    out.paste(ends[1], (width - ends[1].width, 0))
    return out

def lettering_layer(image: Image.Image, art: Image.Image, box, threshold: int = 24) -> Image.Image:

    white = lambda p: min(p[:3]) > 196 and max(p[:3]) - min(p[:3]) < 34
    dark = lambda p: sum(p[:3]) / 3 < 120
    pa, pb = image.load(), art.load()
    x0, y0, x1, y1 = box
    candidates = []
    for stroke in strokes(image.crop(box), white, 4):
        comp = [(x + x0, y + y0) for x, y in stroke]
        ring1 = {(x + dx, y + dy) for x, y in comp for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        ring2 = {(x + dx, y + dy) for x, y in ring1 for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        edge = [p for p in ring2 - ring1 if x0 <= p[0] < x1 and y0 <= p[1] < y1 and not white(pa[p])]
        blob = Image.new("L", image.size)
        for point in comp:
            blob.putpixel(point, 255)
        thick = blob.filter(ImageFilter.MinFilter(7)).getbbox() is not None  
        top = min(y for _, y in comp)
        cut = top == y0 or top >= y1 - 8  
        if not thick and not cut and edge and sum(dark(pa[p]) for p in edge) / len(edge) > 0.55:
            candidates.append(comp)
    boxes = [(min(x for x, _ in c), min(y for _, y in c), max(x for x, _ in c), max(y for _, y in c)) for c in candidates]
    fill = set()
    for comp, (a0, b0, a1, b1) in zip(candidates, boxes, strict=True):
        mean = [sum(pa[p][k] for p in comp) / len(comp) for k in range(3)]
        neutral = min(mean) >= 248 and max(mean) - min(mean) <= 10
        counter = any(c0 < a0 and d0 < b0 and a1 < c1 and b1 < d1 for c0, d0, c1, d1 in boxes)
        if neutral or not counter:  
            fill |= set(comp)
    mask = Image.new("L", image.size)
    for point in fill:
        mask.putpixel(point, 255)
    near2, near3 = mask.filter(ImageFilter.MaxFilter(5)).load(), mask.filter(ImageFilter.MaxFilter(7)).load()
    out = Image.new("RGBA", image.size)
    po = out.load()
    for y in range(y0, y1):
        for x in range(x0, x1):
            p = pa[x, y]
            differs = max(abs(u - v) for u, v in zip(p[:3], pb[x, y][:3], strict=True)) > threshold
            if (x, y) in fill or (near2[x, y] and dark(p)) or (near3[x, y] and differs and not white(p)):
                po[x, y] = p[:3] + (255,)
    return out.crop(box)

TWO_STROKE = "ijŌōū"  
BASELINE = 29  

BANNER_LINES = {1: ("banner_large", [96]), 2: ("banner_small", [73, 103])}
BANNER_SINGLE_CENTRE = 254  
BANNER_SAMPLE_WIDTH = 340  

CARD_CENTRE, CARD_DROP = 98, 64
DREAM_NAME_BASELINES = {1: (159,), 2: (143, 175)}

def ink_rows(atlas: Image.Image) -> list[tuple[int, int]]:

    alpha = atlas.getchannel("A").point(lambda v: 255 if v > 16 else 0)
    bands, top = [], None
    for y in range(atlas.height + 1):
        on = y < atlas.height and alpha.crop((0, y, atlas.width, y + 1)).getbbox() is not None
        if on and top is None:
            top = y
        elif not on and top is not None:
            bands.append((top, y))
            top = None
    return bands

def yellow(p) -> bool:
    return p[3] > 128 and p[0] > 140

def saturated(p) -> bool:

    return p[3] > 128 and max(p[:3]) - min(p[:3]) > 25

def opaque(p) -> bool:

    return p[3] > 128

FILLS = {"yellow": yellow, "saturated": saturated, "opaque": opaque, "solid": lambda p: p[3] > 200, "help": lambda p: p[3] > 80,
         "light": lambda p: p[3] > 200 and sum(p[:3]) > 420, "pale": lambda p: p[3] > 200 and sum(p[:3]) > 300,
         "white": lambda p: p[3] > 200 and min(p[:3]) > 200 and max(p[:3]) - min(p[:3]) < 30,
         "dark": lambda p: p[3] > 200 and max(p[:3]) < 160,
         
         "inner": lambda p: p[3] > 200 and ((p[0] > 140 and p[1] < 120) or max(p[:3]) < 170 and max(p[:3]) - min(p[:3]) < 20),
         "red_glow": lambda p: p[1] < 70 and p[0] - p[1] > 40,  
         "faint": lambda p: p[3] < 64}  

def strokes(row: Image.Image, is_fill=yellow, least: int = 3) -> list[list[tuple[int, int]]]:

    px = row.load()
    fill = {(x, y) for y in range(row.height) for x in range(row.width) if is_fill(px[x, y])}
    found = []
    while fill:
        stack, stroke = [fill.pop()], []
        while stack:
            x, y = point = stack.pop()
            stroke.append(point)
            for near in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if near in fill:
                    fill.remove(near)
                    stack.append(near)
        if len(stroke) >= least:
            found.append(stroke)
    return sorted(found, key=lambda stroke: min(x for x, _ in stroke))

def glyph_fills(row: Image.Image, text: str, is_fill=yellow, least: int = 3,
                counts: dict | None = None) -> list[list[tuple[int, int]]]:

    found, glyphs = strokes(row, is_fill, least), []
    for char in text.replace(" ", ""):
        count = (counts or {}).get(char, 2 if char in TWO_STROKE else 1)
        if len(found) < count:
            found = None
            break
        glyphs.append([p for stroke in found[:count] for p in stroke])
        found = found[count:]
    if found is None or found or not all(glyphs):
        raise ValueError(f"row does not segment as {text!r} ({len(strokes(row, is_fill, least))} strokes)")
    return glyphs

def nearest(row: Image.Image, fills: list[list[tuple[int, int]]]) -> dict[tuple[int, int], int]:

    label, queue = {}, []
    for k, fill in enumerate(fills):
        label.update(dict.fromkeys(fill, k))
        queue += fill
    px = row.load()
    for x, y in queue:
        for near in ((x + i, y + j) for i in (-1, 0, 1) for j in (-1, 0, 1)):
            if near not in label and 0 <= near[0] < row.width and 0 <= near[1] < row.height and px[near][3]:
                label[near] = label[x, y]
                queue.append(near)
    return label

def fit_spacing(pairs, prior=None, weight: float = 0.0, rounds: int = 200):

    def means(items, start):
        groups: dict[str, list[float]] = {}
        for key, value in items:
            groups.setdefault(key, []).append(value)
        if prior is None:
            return {key: fmean(values) for key, values in groups.items()}
        return start | {key: (sum(values) + weight * start[key]) / (len(values) + weight) for key, values in groups.items()}

    left, space = dict.fromkeys((b for _, b, _, _ in pairs), 0.0), 0.0
    if prior is not None:
        left = dict(prior[1])
    for _ in range(rounds):
        right = means(((a, d - left[b] - space * n) for a, b, n, d in pairs), prior and prior[0])
        left = means(((b, d - right[a] - space * n) for a, b, n, d in pairs), prior and prior[1])
        gaps = [d - right[a] - left[b] for a, b, n, d in pairs if n]
        space = fmean(gaps) if gaps else 0.0
    return right, left, space

def lighter(a: Image.Image, b: Image.Image) -> Image.Image:

    return ImageChops.lighter(a.convert("RGBa"), b.convert("RGBa")).convert("RGBA")

def merged(base, part, dx: int = 0, dy: int = 0):

    (base_image, bx, by), (part_image, px_, py) = base, part
    px_, py = px_ + dx, py + dy
    x0, y0 = min(bx, px_), min(by, py)
    size = (max(bx + base_image.width, px_ + part_image.width) - x0, max(by + base_image.height, py + part_image.height) - y0)
    layers = [Image.new("RGBA", size), Image.new("RGBA", size)]
    layers[0].paste(base_image, (bx - x0, by - y0))
    layers[1].paste(part_image, (px_ - x0, py - y0))
    return lighter(*layers), x0, y0

def painted_line(paint, is_fill, line, width: int, outline: int):

    image, gx, gy = paint
    px = image.load()
    rows = {}
    for y in range(image.height):
        found = [px[x, y] for x in range(image.width) if is_fill(px[x, y])]
        if found:
            rows[gy + y] = tuple(round(median(c[k] for c in found)) for k in range(3))
    edge = [px[x, y] for y in range(image.height) for x in range(image.width) if px[x, y][3] > 200 and not is_fill(px[x, y])]
    edge = tuple(round(median(c[k] for c in edge)) for k in range(3)) if edge else (0, 0, 0)
    x0, y0, x1, y1 = line
    pad, zoom = width + outline + 2, 4
    left, top = min(x0, x1) - pad, min(y0, y1) - pad
    size = (abs(x1 - x0) + 2 * pad, abs(y1 - y0) + 2 * pad)
    fill = Image.new("L", (size[0] * zoom, size[1] * zoom))
    ImageDraw.Draw(fill).line([((x - left) * zoom, (y - top) * zoom) for x, y in ((x0, y0), (x1, y1))], 255, width * zoom)
    ink = fill.filter(ImageFilter.MaxFilter(2 * outline * zoom + 1)) if outline else fill
    fill, ink = fill.resize(size, Image.BOX), ink.resize(size, Image.BOX)
    colours = Image.new("RGB", size)
    for y in range(size[1]):
        near = min(rows, key=lambda r: abs(r - (top + y)))
        colours.paste(rows[near], (0, y, size[0], y + 1))
    stroke = Image.composite(colours, Image.new("RGB", size, edge), fill)
    stroke.putalpha(ink)
    return stroke, left, top

def piece(glyph, box):

    image, gx, gy = glyph
    x0, y0, x1, y1 = box
    return image.crop((x0 - gx, y0 - gy, x1 - gx, y1 - gy)), x0, y0

class Face:

    def __getstate__(self):  # the fill test is a lambda: pickle its name in FILLS instead
        return {**self.__dict__, "is_fill": next(name for name, test in FILLS.items() if test is self.is_fill)}

    def __setstate__(self, state):
        self.__dict__.update(state, is_fill=FILLS[state["is_fill"]])

    def __init__(self, rows: list[tuple[Image.Image, str, object, int]], ink: float = 0, optical: bool = False):

        self.optical, self.is_fill, self.kern = optical, rows[0][2], {}
        rows = [(row, text, list(zip(text.replace(" ", ""), glyph_fills(row, text, is_fill, least, counts), strict=True)))
                for row, text, is_fill, least, counts in rows]
        span = lambda fill: max(x for x, _ in fill) - min(x for x, _ in fill) + 1
        widths: dict[str, list[int]] = {}
        for *_, glyphs in rows:
            for char, fill in glyphs:
                widths.setdefault(char, []).append(span(fill))
        typical = {char: median(w) for char, w in widths.items()}
        self.glyphs, self.rows, self.origin, pairs, inks, found, gaps = {}, {}, {}, [], [], {}, {}
        for row, text, glyphs in rows:
            scale = min(1.0, median(span(f) / typical[c] for c, f in glyphs if typical[c] > 6))
            baseline = mode([max(y for _, y in f) for c, f in glyphs if c in "acemnorsuvwxzBDEHIKLMNPTUXZ0123456789"]
                            or [max(y for _, y in f) for c, f in glyphs if c in "AFY"])  
            if scale >= 0.98:
                self.rows[text] = (row, baseline)
            label, px = nearest(row, [fill for _, fill in glyphs]), row.load()
            lefts = [min(x for x, _ in fill) for _, fill in glyphs]
            for k, (char, _) in enumerate(glyphs):
                if found.get(char, 0) >= scale:
                    continue
                points = [p for p, v in label.items() if v == k]
                x0, y0 = min(x for x, _ in points), min(y for _, y in points)
                image = Image.new("RGBA", (max(x for x, _ in points) + 1 - x0, max(y for _, y in points) + 1 - y0))
                for x, y in points:
                    image.putpixel((x - x0, y - y0), px[x, y])
                self.glyphs[char], found[char] = (image, x0 - lefts[k], y0 - baseline), scale
                self.origin[char] = text
            chars = text.replace(" ", "")
            k = 0
            for i, char in enumerate(text[:-1]):
                if char == " ":
                    continue
                gap = len(text[i + 1:]) - len(text[i + 1:].lstrip(" "))
                pairs.append((char, chars[k + 1], gap, (lefts[k + 1] - lefts[k]) / scale))
                inks.append((gap, (min(x for (x, _), v in label.items() if v == k + 1)
                                   - max(x for (x, _), v in label.items() if v == k) - 1) / scale))
                right, left = {}, {}
                for x, y in glyphs[k][1]:
                    right[y] = max(right.get(y, x), x)
                for x, y in glyphs[k + 1][1]:
                    left[y] = min(left.get(y, x), x)
                if right.keys() & left.keys():  
                    gaps.setdefault(" " if gap else char + chars[k + 1], []).append(
                        min(left[y] - right[y] - 1 for y in right.keys() & left.keys()))
                k += 1
        prior = None
        if ink:  
            gap = median(g for n, g in inks if not n)
            prior = ({c: x + image.width + gap for c, (image, x, _) in self.glyphs.items()},
                     {c: -x for c, (_, x, _) in self.glyphs.items()})
        self.right, self.left, self.space = fit_spacing(pairs, prior, ink)
        # An accent sits over its letter's body and leaves the sides alone. A letter seen only after a space
        # (the donor cards' Ō) would otherwise take that donor's word gap as its own left side.
        for char in list(self.left.keys() | self.right.keys()):
            base = unicodedata.normalize("NFD", char)[0]
            if base != char and base in self.left and base in self.right:
                self.left[char], self.right[char] = self.left[base], self.right[base]
        if optical:
            self.gap = round(median(g for pair, found in gaps.items() if pair != " " for g in found))
            self.gaps = {pair: round(median(found)) for pair, found in gaps.items()}

    def synthesize(self, recipes: dict, is_fill=yellow) -> None:

        g = self.glyphs
        for char, recipe in recipes.items():
            if "parts" in recipe:
                (name, *box), *rest = recipe["parts"]
                glyph = piece(g[name], box[0]) if box else g[name]
                for part in rest:
                    if isinstance(part, dict):
                        line = painted_line(g[part["paint"]], is_fill, part["line"], part["width"], part["outline"])
                        glyph = merged(glyph, line)
                    else:
                        name, box, dx, dy = part
                        glyph = merged(glyph, piece(g[name], box), dx, dy)
                if "shift" in recipe:  
                    (image, x, y), (dx, dy) = glyph, recipe["shift"]
                    glyph = image, x + dx, y + dy
                if recipe.get("flip"):  
                    image, x, y = glyph
                    glyph = image.transpose(Image.FLIP_LEFT_RIGHT), x, y
                g[char] = glyph
            if "turn" in recipe:  
                image, x, y = g[recipe["turn"]]
                px = image.load()
                fill = [(x + i, y + j) for j in range(image.height) for i in range(image.width) if is_fill(px[i, j])]
                turned = image.rotate(180)
                tx = min(i for i, _ in fill) + max(i for i, _ in fill) - (x + image.width - 1)
                ty = min(j for _, j in fill) + max(j for _, j in fill) - (y + image.height - 1)
                rows, out = {}, turned.load()
                for i, j in fill:
                    rows.setdefault(j, []).append(i)
                for j in range(turned.height):  
                    for i in range(turned.width):
                        if is_fill(out[i, j]) and ty + j in rows:
                            near = min(rows[ty + j], key=lambda s: abs(s - tx - i))
                            out[i, j] = px[near - x, ty + j - y][:3] + out[i, j][3:]
                g[char] = turned, tx, ty
            if "like" in recipe:  
                self.right.setdefault(char, self.right[recipe["like"]] + recipe.get("right", 0))
                self.left.setdefault(char, self.left[recipe["like"]] + recipe.get("left", 0))

    def render(self, text: str, mark=None) -> tuple[Image.Image, int]:

        if self.optical:
            return self.optical_render(text)
        if text in self.rows:
            return self.rows[text]
        placed, x, previous, spaces, marked = [], 0.0, None, 0, False
        for char in text:
            if char in "{}":
                marked = char == "{"
                continue
            if char == " ":
                spaces += 1
                continue
            if previous is not None:
                x += self.right[previous] + self.left[char] + self.space * spaces + self.kern.get(previous + char, 0)
            placed.append((char, x, marked))
            previous, spaces = char, 0
        glyphs = [self.glyphs[char] for char, _, _ in placed]
        glyphs = [(mark(image), gx, gy) if on and mark else (image, gx, gy)
                  for (image, gx, gy), (_, _, on) in zip(glyphs, placed, strict=True)]
        top = max(36, *(-y for _, _, y in glyphs))
        canvas = Image.new("RGBA", (round(x) + 16 + max(64, *(i.width + gx for i, gx, _ in glyphs)),
                                    top + max(12, *(i.height + y for i, _, y in glyphs))))
        for (char, x, _), (image, gx, gy) in zip(placed, glyphs, strict=True):
            layer = Image.new("RGBA", canvas.size)
            layer.paste(image, (round(x) + gx + 8, top + gy))
            canvas = lighter(canvas, layer)
        x0, _, x1, _ = canvas.getbbox()
        return canvas.crop((x0, 0, x1, canvas.height)), top

    def optical_render(self, text: str) -> tuple[Image.Image, int]:
        profiles = {}
        for char in set(text) - {" "}:  
            image, gx, gy = self.glyphs[char]
            px, profiles[char] = image.load(), {}
            for y in range(image.height):
                xs = [x for x in range(image.width) if self.is_fill(px[x, y])]
                if xs:
                    profiles[char][gy + y] = (gx + xs[0], gx + xs[-1])
        placed, right, previous, spaced = [], {}, None, False
        for char in text:
            if char == " ":
                spaced = True
                continue
            profile, x = profiles[char], 0
            if previous is not None:
                gap = self.gaps[" "] if spaced else self.gaps.get(previous + char, self.gap)
                shared = right.keys() & profile.keys()
                x = (max(right[y] - profile[y][0] for y in shared) if shared
                     else max(right.values()) - min(left for left, _ in profile.values())) + 1 + gap
            for y, (_, r) in profile.items():
                right[y] = max(right.get(y, x + r), x + r)
            placed.append((char, x))
            previous, spaced = char, False
        glyphs = [self.glyphs[char] for char, _ in placed]
        top = max(36, *(-y for _, _, y in glyphs))
        canvas = Image.new("RGBA", (placed[-1][1] + 32 + max(64, *(i.width + gx for i, gx, _ in glyphs)),
                                    top + max(12, *(i.height + y for i, _, y in glyphs))))
        for (_, x), (image, gx, gy) in zip(placed, glyphs, strict=True):
            layer = Image.new("RGBA", canvas.size)
            layer.paste(Image.merge("RGBA", (*[image.convert("L")] * 3, image.getchannel("A"))), (x + gx + 16, top + gy))
            canvas = lighter(canvas, layer)
        x0, _, x1, _ = canvas.getbbox()
        return canvas.crop((x0, 0, x1, canvas.height)), top

def face_rows(image: Image.Image, texts: list, is_fill=yellow, least: int = 3, counts=None) -> list[tuple]:

    return [(image.crop((0, top, image.width, bottom)), text, is_fill, least, counts)
            for (top, bottom), text in zip(ink_rows(image), texts, strict=True) if text]

FACE_WORK: tuple = ()  # (mapping, donors) for the face workers; set before they start, so forked workers share it


def _face(name: str) -> Face:
    """One lettering face, built from the donors in FACE_WORK."""
    mapping, donors = FACE_WORK
    stages = mapping["stage_names"]
    if name == "stage":
        entry, item, texture = stages["font"]
        stage = Face(face_rows(rgba(donors[entry][item].textures[texture]), stages["rows"]))
        stage.synthesize(stages["synth"])
        return stage
    if name == "title":
        titles = stages["titles"]
        title = Face([row for k, text in enumerate(stages["rows"]) if k not in titles["skip"]
                      for row in face_rows(rgba(donors[titles["donor"]][k].textures[0]), [text])])
        title.synthesize(titles["synth"])
        return title
    spec = mapping["faces"][name]
    rows = []
    for source in spec["rows"]:

        image = stock_image(source["base"], donors) if "base" in source else source_image(source, donors, None)
        shape = (FILLS[source.get("fill", spec.get("fill", "yellow"))], source.get("least", spec.get("least", 3)),
                 source.get("strokes", spec.get("strokes")))
        if "against" in source:
            rows.append((lettering_layer(image, stock_image(source["against"], donors), source["box"]),
                         source["text"], *shape))
            continue
        image = image.crop(source.get("box"))
        if "drop" in source:
            image.putdata([(*p[:3], 0) if FILLS[source["drop"]](p) else p for p in image.get_flattened_data()])
        if "glow" in source:
            image = unglowed(image, *source["glow"])
        rows += face_rows(keyed(image, *source["key"]) if "key" in source else image, source["texts"], *shape)
    face = Face(rows, spec.get("ink", 0), spec.get("optical", False))
    face.kern = spec.get("kern", {})
    face.synthesize(spec.get("synth", {}), FILLS[spec.get("fill", "yellow")])
    return face


def faces(mapping: dict, donors: dict, jobs: int = 1) -> dict:
    """The lettering faces, one worker process each when jobs > 1 (the longest step before the worker pool)."""
    global FACE_WORK
    names = ["stage", "title", *mapping.get("faces", {})]
    FACE_WORK = mapping, donors
    report_progress("art", "reading the lettering faces", 0.05, (0, len(names)))
    found = {}
    if jobs > 1:
        with worker_pool(min(jobs, len(names))) as pool:
            pending = {pool.submit(_face, name): name for name in names}
            try:
                for count, future in enumerate(as_completed(pending), 1):
                    found[pending[future]] = future.result()
                    report_progress("art", "reading the lettering faces", 0.05 + 0.45 * count / len(names),
                                    (count, len(names)))
            except BaseException:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
    else:
        for count, name in enumerate(names, 1):
            found[name] = _face(name)
            report_progress("art", "reading the lettering faces", 0.05 + 0.45 * count / len(names),
                            (count, len(names)))
    return {name: found[name] for name in names}

def stage_strip(text: str, font: Face, width: int, height: int) -> Image.Image:

    image, baseline = font.render(text)
    box = image.getbbox()
    return placed(Image.new("RGBA", (width, height)), image.crop(box), 2, BASELINE - baseline + box[1])

def source_image(spec: dict, donors: dict, stock: Image.Image) -> Image.Image:

    if "moz" in spec:
        item, index = spec["moz"]
        return rgba(donors["stock"].items[item].g1t.textures[index])
    if "wo2" in spec:
        entry, item, index = spec["wo2"]
        return rgba(donors["wo2", entry][item].textures[index])
    if "donor" in spec:
        entry, item, index = spec["donor"]
        return rgba(donors[entry][item].textures[index])
    return stock

def stock_image(ref, donors: dict) -> Image.Image:

    entry, item, index = ref
    return rgba(donors["moz", entry][item].textures[index])

def only_strokes(image: Image.Image, spec: dict) -> Image.Image:

    found = strokes(image, FILLS[spec.get("fill", "yellow")])
    start, stop = (0, spec["strokes"]) if isinstance(spec["strokes"], int) else spec["strokes"]
    keep = set(range(len(found))[start:stop])
    label, kept = nearest(image, found), Image.new("RGBA", image.size)
    for point, k in label.items():
        if k in keep:
            kept.putpixel(point, image.getpixel(point))
    return kept

def composed(stock: Image.Image, rows: list[list[dict]], donors: dict, align: str, gap: int,
             bands=None) -> Image.Image:
    """A row whose first piece names a window [x0, x1] (where its quad lets ink show) is centred in it
    and condensed to fit."""
    lines, bands = [], bands or ink_rows(stock)
    inks = [ink(stock.crop((0, top, stock.width, bottom))) for top, bottom in bands]
    left = min(box[0] for box in inks)
    for (top, bottom), pieces, (x0, _, x1, _) in zip(bands, rows, inks, strict=True):
        parts = []
        for spec in pieces:
            image = source_image(spec, donors, stock)
            if "face" in spec:  
                image = donors["faces"][spec["face"]].render(spec["text"])[0]
            elif "row" in spec:
                y0, y1 = ink_rows(image)[spec["row"]]
                image = image.crop((0, y0, image.width, y1))
            else:
                image = image.crop(spec["box"])
            if "strokes" in spec:
                image = only_strokes(image, spec)
            left_ink, _, right_ink, _ = ink(image)
            image = image.crop((left_ink, 0, right_ink, image.height))
            if "hue" in spec:
                image = hue_shifted(image, spec["hue"])
            parts.append((image, spec.get("dy", 0)))
        line = Image.new("RGBA", (sum(p.width for p, _ in parts) + gap * (len(parts) - 1),
                                 max(p.height + dy for p, dy in parts)))
        x = 0
        for part, dy in parts:
            line.alpha_composite(part, (x, dy))
            x += part.width + gap
        line = line.crop(ink(line))
        window = pieces[0].get("window")
        if window:
            x0, x1 = window
            if line.width > x1 - x0 and donors.get("squish", True):
                line = line.resize((x1 - x0, line.height), Image.Resampling.LANCZOS)
        elif align == "full":
            x0, x1 = 0, stock.width
        elif align == "left":
            x0 = left
        lines.append((line, top, bottom, x0, x1, align == "left" and not window))
    canvas = Image.new("RGBA", stock.size)
    for line, top, bottom, x0, x1, left in lines:
        placed(canvas, line, x0 if left else round((x0 + x1 - line.width) / 2), top + (bottom - top - line.height) // 2)
    return canvas

def tinted(image: Image.Image, factors: list[float]) -> Image.Image:
    return Image.merge("RGBA", [c.point(lambda v, f=f: round(v * f)) for c, f in zip(image.split(), [*factors, 1], strict=True)])

def hue_shifted(image: Image.Image, degrees: float) -> Image.Image:

    h, s, v = image.convert("RGB").convert("HSV").split()
    shifted = Image.merge("HSV", (h.point(lambda x: (x + round(degrees * 256 / 360)) % 256), s, v)).convert("RGB")
    shifted.putalpha(image.getchannel("A"))
    return shifted

def recoloured(image: Image.Image, edit: dict) -> Image.Image:

    if "tint" in edit:
        image = tinted(image, edit["tint"])
    if "gradient" in edit:  
        stops, (_, top, _, bottom) = edit["gradient"], ink(image)
        ramp = Image.new("RGB", (1, image.height))
        for y in range(image.height):
            at = min(max(0, (y - top) / max(1, bottom - 1 - top)), 1) * (len(stops) - 1)
            k = min(int(at), len(stops) - 2)
            ramp.putpixel((0, y), tuple(round(a + (b - a) * (at - k)) for a, b in zip(stops[k], stops[k + 1], strict=True)))
        shaded = ImageChops.multiply(ramp.resize(image.size), image.convert("L").convert("RGB")).convert("RGBA")
        shaded.putalpha(image.getchannel("A"))
        image = shaded
    if "hue" in edit:
        image = hue_shifted(image, edit["hue"])
    if "duotone" in edit:  
        alpha = image.getchannel("A")
        dark, bright, *points = edit["duotone"]
        image = ImageOps.colorize(image.convert("L"), tuple(dark), tuple(bright), None, *points).convert("RGBA")
        image.putalpha(alpha)
    if "glow" in edit:  
        image = glowed(image, *edit["glow"])
    return image

MENU_BATCH = "wozmod.fonts.menu_batch"
HIRES_NATIVE_MENU = {(4079, item) for item in [*range(60, 78), 80]}

ABILITY_QUAD_MINIMUM = {(4079, 69): 144, (4079, 71): 156, (4079, 75): 132}

def menu_label_key(style: str, text: str, role: str | None = None) -> tuple:

    return style, role, text

def sound_menu_rows(mapping: dict, item: int) -> list[dict]:

    rows = {}
    for source in mapping.get("faces", {}).get("sound", {}).get("rows", []):
        if source.get("base") == [4069, item, 0] and "box" not in source:
            rows.update({k: {"text": text, "at": [3, 31 + 42 * k]}
                         for k, text in enumerate(source["texts"]) if text is not None})

    for k in range(70 if item == 25 else 35):
        record = reference(f"4069.{item}", k)
        if record and record.get("pc_text"):
            rows[k] = {"text": record["pc_text"],
                       "at": [56 if item == 25 else 3, (32 if item == 25 else 31) + 42 * k]}
    for edit in mapping.get("textures", []):
        if (edit["entry"], edit.get("item")) == (4069, item):
            for piece in edit.get("draw", []):
                if piece.get("face") == "sound":
                    rows[piece["at"][1] // 42] = {"text": piece["text"], "at": piece["at"]}
    return [{"row": k, **row} for k, row in sorted(rows.items())]

def native_menu_text(entry: int, item: int, texture: int = 0, donors: dict | None = None) -> str:

    text = (donors or {}).get("menu_native_texts", {}).get((entry, item, texture))
    record = reference(f"{entry}.{item}", 0)
    text = text or (record or {}).get("pc_text")
    if not text:
        raise ValueError(f"native menu label {entry}.{item}.{texture} has no PC wording")
    return text

def native_menu_role(entry: int, item: int) -> str:

    role = (reference(f"{entry}.{item}", 0) or {}).get("role_variant")
    if role not in {"blue", "pink", "yellow"}:
        raise ValueError(f"native ability {entry}.{item} has no confirmed colour role")
    return role

def prepare_menu_labels(mapping: dict, output_dir: Path | None = None) -> dict:

    requests = {}

    def add(style, text, role=None):
        requests.setdefault(menu_label_key(style, text, role),
                            {"style": style, "text": text, **({"role": role} if role else {})})

    for text in mapping.get("stage_names", {}).get("names", []):
        add("stage.stage", text)
    for item in (23, 24, 25):
        for row in sound_menu_rows(mapping, item):
            add("sound.sound", row["text"])
    for item in range(60, 78):
        add("rounded.ability", native_menu_text(4079, item),
            native_menu_role(4079, item))
    add("rounded.base", native_menu_text(4079, 80))
    for edit in mapping.get("textures", []):
        spec = edit.get("banner") or edit.get("card")
        if spec and "lines" in spec:
            style = "banner.large" if len(spec["lines"]) == 1 else "banner.small"
            for row, text in enumerate(spec["lines"]):
                add(style, text, officer_name_role(edit, row))
    ordered = list(requests.values())
    if lettering.ACTIVE is not None:
        return {menu_label_key(r["style"], r["text"], r.get("role")):
                lettering.get(dict(kind="native", **r), None) for r in ordered}
    with tempfile.TemporaryDirectory(prefix="moz-menu-labels-") as temporary:
        folder = Path(temporary)
        path, output = folder / "requests.json", output_dir or folder / "labels"
        path.write_text(json.dumps(ordered, ensure_ascii=False), encoding="utf-8")
        subprocess.run([FONT_PYTHON, "-m", MENU_BATCH, str(path), str(output)],
                       check=True, stdout=subprocess.PIPE, text=True, env=font_environment())
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        if len(manifest) != len(ordered):
            raise ValueError("menu batch dropped a requested native label")
        labels = {}
        for index, record in enumerate(manifest):
            request = ordered[index]
            if record["index"] != index or record["request"] != request:
                raise ValueError(f"menu batch changed native label {index}")
            image = Image.open(output / record["file"]).convert("RGBA")
            labels[menu_label_key(request["style"], request["text"], request.get("role"))] = lettering.get(
                dict(kind="native", **request), lambda: (image, record["metrics"]))
    return labels

def native_menu_label(donors: dict, style: str, text: str, role: str | None = None) -> tuple:

    key = menu_label_key(style, text, role)
    if key not in donors.get("menu_labels", {}):
        raise ValueError(f"native menu label was not prepared: {key}")
    return donors["menu_labels"][key]

def squished_native_label(donors: dict, style: str, text: str, role=None,
                          *, entry: int, item: int, row: int = 0, room: float,
                          scale: int = 1, label=None, metrics=None, x360: bool = True) -> tuple:
    """Condense a native label to the width its X360 counterpart fills, or (x360=False or no
    counterpart) only as far as it overflows `room`."""
    if label is None:
        label, metrics = native_menu_label(donors, style, text, role)
    if not donors.get("squish"):
        return label, metrics
    original = (reference(f"{entry}.{item}", row) or {}).get("original", {})
    geometry = None
    if x360 and original.get("donor") == "x360" and original.get("ens"):
        ens, member, texture = original["ens"]
        source = rgba(donors[("wo2", ens)][member].textures[texture])
        source = source.crop(original.get("crop"))
        geometry = row_geometry(source, lambda r, g, b, a:
                                a / 255 * max(0, (max(r, g, b) - 100) / 140))
    if geometry is None:
        box = label.getbbox()
        natural = (metrics.get("fill") or metrics["ink"][::2])
        width = natural[1] - natural[0]
        target = width * min(1.0, room * scale / (box[2] - box[0]))
        geometry = (0, target / scale, 0)
    return squeezed_label(label, metrics, geometry, scale, True)


def native_reference_entries(mapping: dict) -> set[int]:
    keys = {(4079, item) for item in [*range(60, 78), 80]}
    for spec in mapping["stage_names"]["strips"]:
        keys |= {(spec["entry"], spec["item"] + k) for k in range(spec["count"])}
    keys |= {(4069, item) for item in (23, 24, 25)}
    from wozmod.fonts.menu_references import load_catalog
    return {record["original"]["ens"][0] for entry, item in keys
            for record in load_catalog().get(f"{entry}.{item}", {}).get("rows", {}).values()
            if record.get("original", {}).get("donor") == "x360"
            and record["original"].get("ens")}

def draw_native_menu_label(image: Image.Image, label: Image.Image, metrics: dict,
                           left: float, baseline: int,
                           sampled_window: tuple[int, int, int, int] | None = None) -> None:

    x, y = round(left - metrics["ink"][0]), baseline - metrics["baseline"]
    box = label.getbbox()
    if box is None:
        raise ValueError("native menu label has no visible pixels")
    bounds = tuple(value + offset for value, offset in zip(box, (x, y, x, y), strict=True))
    window = sampled_window or (0, 0, image.width, image.height)
    if (bounds[0] < max(0, window[0]) or bounds[1] < max(0, window[1])
            or bounds[2] > min(image.width, window[2])
            or bounds[3] > min(image.height, window[3])):
        raise ValueError(f"native menu alpha bounds {bounds} do not fit sampled window {window} "
                         f"inside {image.width}x{image.height}")
    image.alpha_composite(label, (x, y))

def native_stage_left(label: Image.Image, metrics: dict) -> int:

    left = round(2 - metrics["ink"][0]) + label.getbbox()[0]
    return 2 + max(0, -left)

def native_stage_baseline(label: Image.Image, metrics: dict) -> int:

    top = BASELINE - metrics["baseline"] + label.getbbox()[1]
    return BASELINE + max(0, -top)

def native_menu_layout(edit: dict, donors: dict) -> dict | None:

    key = edit.get("entry"), edit.get("item")
    if key[0] == 4079 and (60 <= (key[1] or 0) < 78 or key[1] == 80):
        text = native_menu_text(*key, edit.get("texture", 0), donors)
        ability = key[1] != 80
        style = "rounded.ability" if ability else "rounded.base"
        role = native_menu_role(*key) if ability else None
        label, metrics = squished_native_label(donors, style, text, role, entry=key[0], item=key[1],
                                               room=112 if ability else 140)
        source = source_image(edit, donors, None)
        geometry = row_geometry(source, lambda r, g, b, a: a / 255 * max(0, (max(r, g, b) - 100) / 140))
        if geometry is None:
            raise ValueError(f"native menu source {key} has no measurable fill")
        left, baseline = geometry[0], geometry[2]
        window = [0, 0, 112 if ability else 140, 35 if ability else 31]
        x, y = round(left - metrics["ink"][0]), baseline - metrics["baseline"]
    else:
        return None
    return {"style": style, "role": role, "text": text, "left": left, "baseline": baseline,
            "x": x, "y": y, "sampled_window": window,
            "alpha_bbox": [value + offset for value, offset in zip(label.getbbox(), (x, y, x, y), strict=True)],
            "metrics": metrics, "label": label}

def native_menu_widths(mapping: dict, donors: dict) -> dict:

    rows = []

    def record(entry, item, row, style, text, left, baseline, window, role=None):
        label, metrics = native_menu_label(donors, style, text, role)
        x, y = round(left - metrics["ink"][0]), baseline - metrics["baseline"]
        box = [value + offset for value, offset in zip(label.getbbox(), (x, y, x, y), strict=True)]
        required_right = max(window[2], box[2])
        required_bottom = max(window[3], box[3]) if window[3] is not None else None
        rows.append({"entry": entry, "item": item, "row": row, "text": text,
                     "style": style, "role": role, "left": left, "baseline": baseline,
                     "natural_fill_width": metrics["ink"][2] - metrics["ink"][0],
                     "natural_alpha_width": label.getbbox()[2] - label.getbbox()[0],
                     "placed_alpha_bbox": box, "sampled_window": window,
                     "required_right": required_right, "required_bottom": required_bottom,
                     "left_clipping_needed": box[0] < window[0],
                     "top_clipping_needed": box[1] < window[1],
                     "widening_needed": box[2] > window[2],
                     "height_growth_needed": window[3] is not None and box[3] > window[3]})

    stages = mapping.get("stage_names", {})
    for strips in stages.get("strips", []):
        width, height = (432, 42) if strips["entry"] == 4108 else (440, 35)
        for k, text in enumerate(stages["names"][:strips["count"]]):
            label, metrics = native_menu_label(donors, "stage.stage", text)
            left, baseline = native_stage_left(label, metrics), native_stage_baseline(label, metrics)
            record(strips["entry"], strips["item"] + k, 0, "stage.stage", text,
                   left, baseline, [0, 0, width, height])
            rows[-1]["placement_delta"] = [left - 2, baseline - BASELINE]
    for item in (23, 24, 25):
        for row in sound_menu_rows(mapping, item):
            top = row["row"] * 42
            window = [54 if item == 25 else 0, top, 614 if item == 25 else 600, top + 42]
            record(4069, item, row["row"], "sound.sound", row["text"],
                   *row["at"], window)
    for edit in mapping.get("textures", []):
        for entry, step in expanded(edit):
            layout = native_menu_layout(step, donors)
            if layout:
                record(entry, step["item"], 0, layout["style"], layout["text"],
                       layout["left"], layout["baseline"], layout["sampled_window"], layout["role"])
            spec = step.get("banner") or step.get("card")
            if not spec or "lines" not in spec:
                continue
            name, baselines = BANNER_LINES[len(spec["lines"])]
            if "card" in step:
                baselines = DREAM_NAME_BASELINES[len(spec["lines"])]
            style = name.replace("_", ".")
            for row, (text, baseline) in enumerate(zip(spec["lines"], baselines, strict=True)):
                role = officer_name_role(step, row)
                label, metrics = native_menu_label(donors, style, text, role)
                box = label.getbbox()
                width = box[2] - box[0]
                if "banner" in step:
                    x = BANNER_SINGLE_CENTRE - width // 2
                    window = [0, 0, BANNER_SAMPLE_WIDTH, 112]
                else:
                    x = CARD_CENTRE - width // 2
                    window = [0, 0, 2 * CARD_CENTRE, 196]
                record(entry, step["item"], row, style, text,
                       x - box[0] + metrics["ink"][0], baseline, window, role)
    return {"schema": "moz-native-menu-widths-v1", "rows": rows,
            "widenings": [row for row in rows if row["widening_needed"] or row["height_growth_needed"]],
            "clippings": [row for row in rows if row["left_clipping_needed"] or row["top_clipping_needed"]],
            "stage_vertical_adjustments": [row for row in rows if row["style"] == "stage.stage"
                                           and row["placement_delta"][1]],
            "note": "Natural public-render fill and full alpha bounds; no row condensation. Banner windows are authored lettering boxes; other widths are the statically proven source windows. Texture growth alone does not widen a sampled quad."}

def native_menu_payload(target, edit: dict, donors: dict) -> bytes | None:

    key = edit.get("entry"), edit.get("item")
    if "stage_name" in edit:
        width, height = (432, 42) if key[0] == 4108 else (440, 35)
        # MOZ strips: a stage name condenses only where it overflows its own strip.
        label, metrics = squished_native_label(donors, "stage.stage", edit["stage_name"],
                                               entry=key[0], item=key[1], room=width - 2, x360=False)
        image = Image.new("RGBA", (target.width, target.height))
        draw_native_menu_label(image, label, metrics, native_stage_left(label, metrics),
                               native_stage_baseline(label, metrics), (0, 0, width, height))
        return target.encode_rgba(image.tobytes())
    if key[0] == 4069 and key[1] in (23, 24, 25):
        image = rgba(target)
        rows = donors["menu_sound_rows"][key[1]]
        for row in rows:
            
            left = 53 if key[1] == 25 else 0
            top = row["row"] * 42
            image = restriped(image, (left, top, image.width, top + 42), image.width - 1)
            label, metrics = squished_native_label(donors, "sound.sound", row["text"],
                                                   entry=key[0], item=key[1], row=row["row"],
                                                   room=(614 if key[1] == 25 else 600) - row["at"][0])
            draw_native_menu_label(image, label, metrics, row["at"][0], row["at"][1])
        return target.encode_rgba(image.tobytes())
    layout = native_menu_layout(edit, donors)
    if layout:
        label, metrics = layout["label"], layout["metrics"]
        image = Image.new("RGBA", (target.width, target.height))
        draw_native_menu_label(image, label, metrics, layout["left"], layout["baseline"])
        return target.encode_rgba(image.tobytes())
    return None

def payload(target, edit: dict, donors: dict, package) -> bytes:
    if edit.get("blank"):
        return target.encode_rgba(bytes(4 * target.width * target.height))
    native = native_menu_payload(target, edit, donors)
    if native is not None:
        return native
    if "stage_title" in edit:  
        image, top = donors["faces"]["title"].render(edit["stage_title"])
        box = image.getbbox()
        image = placed(Image.new("RGBA", (target.width, target.height)), image.crop(box), 0, edit["baseline"] - top + box[1])
        if "wo1" in edit:
            entry, item, index = edit["wo1"]
            source = donors[entry][item].textures[index]
            if (ink(rgba(source))[2] >= ink(image)[2] - 2 and source.pixel_format == target.pixel_format
                    and len(source.payload) == len(target.payload)):  
                return source.payload
        return target.encode_rgba(image.tobytes())
    if "grey_of" in edit:
        return target.encode_rgba(greyed(rgba(package.items[edit["grey_of"]].g1t.textures[0])).tobytes())
    if "shadow" in edit:  
        image, spec = rgba(target), edit["shadow"]
        px, top = image.load(), spec["from"]
        for x in range(image.width):
            if all(px[x, y][3] == 255 and max(px[x, y][:3]) < 64 for y in (top - 2, top - 1)):
                for y, rgb in enumerate(spec["rgb"], top):
                    px[x, y] = (*rgb, 255)
        return target.encode_rgba(image.tobytes())
    if "banner" in edit:  
        spec, image = edit["banner"], rgba(target)
        image = refilled(image, lettering_mask(image, spec["erase"]))
        badge = spec["badge"]  
        donor, art = source_image(badge, donors, None), stock_image(badge["against"], donors)
        box, mask = badge["box"], Image.new("L", donor.size)
        mask.paste(ImageChops.difference(donor.convert("RGB"), art.convert("RGB")).convert("L").crop(box)
                   .point(lambda v: 255 if v > 16 else 0), box[:2])
        layer = Image.new("RGBA", donor.size)
        layer.paste(donor, (0, 0), mask.filter(ImageFilter.MaxFilter(3)))
        image.alpha_composite(layer)
        name, baselines = BANNER_LINES[1]  # an authored banner has one name line
        for row, (text, baseline) in enumerate(zip(spec["lines"], baselines, strict=True)):
            line, metrics = squished_native_label(donors, name.replace("_", "."), text,
                                                  entry=edit["entry"], item=edit["item"], row=row, room=172)
            top = metrics["baseline"]
            x0, y0, x1, _ = line.getbbox()
            x = BANNER_SINGLE_CENTRE - (x1 - x0) // 2
            if x < 0:
                raise ValueError(f"native banner line {text!r} starts outside its texture")
            if x + x1 - x0 > image.width:
                raise ValueError(f"native banner line {text!r} exceeds its {image.width}-pixel texture")
            image.alpha_composite(line.crop((x0, y0, x1, line.height)), (x, baseline - top + y0))
        return target.encode_rgba(image.tobytes())
    if "card" in edit:  
        spec, image = edit["card"], rgba(target)
        image = mirrored(image, navy_mask(image, spec["rows"]))
        drop = CARD_DROP
        lines = []  
        if "donor" in spec:  
            
            layer = lettering_layer(source_image(spec, donors, None), stock_image(spec["against"], donors), spec["box"])
            grey = layer.convert("L")
            layer = Image.merge("RGBA", (grey, grey, grey, layer.getchannel("A")))
            baselines = [b - spec["box"][1] for b in BANNER_LINES[2][1]]  
            lines += [(line, spec["box"][1] + top) for line, top in text_lines(layer, baselines)]
        else:  
            name = BANNER_LINES[len(spec["lines"])][0]
            baselines = DREAM_NAME_BASELINES[len(spec["lines"])]
            drop = 0
            for row, (text, baseline) in enumerate(zip(spec["lines"], baselines, strict=True)):
                line, metrics = squished_native_label(donors, name.replace("_", "."), text,
                                                      officer_name_role(edit, row),
                                                      entry=edit["entry"], item=edit["item"], row=row, room=196)
                top = metrics["baseline"]
                x0, y0, x1, y1 = line.getbbox()
                lines.append((line.crop((x0, y0, x1, y1)), baseline - top + y0))
        for line, top in lines:
            if "lines" in spec and line.width > 196:
                raise ValueError("Dream name including glow exceeds its 196-pixel square")
            image.alpha_composite(line, (CARD_CENTRE - line.width // 2, top + drop))
        return target.encode_rgba(image.tobytes())
    if "draw" in edit:  
        stock = rgba(target)
        image = Image.new("RGBA", stock.size) if edit.get("clear") else stock.copy()
        if "unlettered" in edit:  
            spec = edit["unlettered"]
            image = unlettered([rgba(donors["stock"].items[i].g1t.textures[edit["texture"]]) for i in spec["items"]],
                               spec["saturation"], spec["grow"])
        if "plate" in edit:
            spec = edit["plate"]
            image = plated(source_image(spec, donors, None), spec["width"], spec["column"], spec["right"])
            image = image.crop((0, 0, target.width, target.height))
        for spec in edit["draw"]:
            if "erase" in spec:  
                image = restriped(image, spec["erase"][:4], spec["erase"][4])
                continue
            if "refill" in spec:  
                (cx, cy, r), disk = spec["refill"], Image.new("L", image.size)
                ImageDraw.Draw(disk).ellipse((cx - r, cy - r, cx + r, cy + r), 255)
                image = refilled(image, disk)
                continue
            if "splice" in spec:  
                image = spliced(stock, **spec["splice"])
                continue
            top = 0
            mark = (lambda glyph, spec=spec: recoloured(glyph, spec["mark"])) if "mark" in spec else None
            if "parts" in spec:  
                face, (x, y) = donors["faces"][spec["face"]], spec["at"]
                for part in spec["parts"]:
                    if isinstance(part, str):
                        piece, top = face.render(part, mark)
                    else:  
                        piece, top = source_image(part, donors, stock).crop(part["box"]), part["rise"]
                        piece = keyed(piece, *part["key"]) if "key" in part else piece
                    image.alpha_composite(piece, (x, y - top))
                    x += piece.width + spec.get("gap", round(face.space))
                continue
            if "face" in spec:
                piece, top = donors["faces"][spec["face"]].render(spec["text"], mark)
            elif "look" in spec:  # the value face at 1x
                with tempfile.TemporaryDirectory() as tmp:
                    piece = header_label(spec["text"], 1, Path(tmp) / "label.png", VALUE_RASTER, "--look", spec["look"])[0]
            else:
                piece = source_image(spec, donors, stock).crop(spec.get("box"))
            if "key" in spec:
                piece = keyed(piece, *spec["key"])
            if "strokes" in spec:
                piece = only_strokes(piece, spec)
            if "drop" in spec:  
                piece.putdata([(*p[:3], 0) if FILLS[spec["drop"]](p) else p for p in piece.get_flattened_data()])
            pad = math.ceil(3 * spec["glow"][0]) if "glow" in spec and "at" in spec else 0  
            piece = recoloured(ImageOps.expand(piece, pad) if pad else piece, spec)
            if "at" in spec:  
                image.alpha_composite(piece, (spec["at"][0] - pad, spec["at"][1] - top - pad))
                continue
            image.alpha_composite(fitted(piece.crop(ink(piece)), target.width, target.height, spec["into"],
                                         spec.get("left", True), spec.get("shrink", False)))
        return target.encode_rgba(recoloured(image, edit).tobytes())
    if "rows" in edit:
        image = composed(rgba(target), edit["rows"], donors, edit.get("align", "centre"), edit.get("gap", 0),
                         edit.get("bands"))
        return target.encode_rgba(recoloured(image, edit).tobytes())
    if "donor" in edit or "wo2" in edit:
        entry, item, index = edit.get("donor") or edit["wo2"]
        source = donors[entry if "donor" in edit else ("wo2", entry)][item].textures[index]
        if (source.width, source.height) == (target.width, target.height) and "box" not in edit:
            if source.pixel_format == target.pixel_format and len(source.payload) == len(target.payload):
                return source.payload
            return target.encode_rgba(rgba(source).tobytes())  
    image = source_image(edit, donors, None)
    image = image.crop(edit.get("box") or ink(image))
    box = ink(rgba(target)) or (0, 0, target.width, target.height)
    return target.encode_rgba(fitted(image, target.width, target.height, box).tobytes())

def enlarged(g1t: G1tFile, edit: dict, donors: dict | None = None) -> G1tFile:

    texture = g1t.textures[edit["texture"]]
    size = list(edit.get("size", [texture.width, texture.height]))
    layout = native_menu_layout(edit, donors) if donors else None
    if layout:
        sample_minimum = ABILITY_QUAD_MINIMUM.get((edit.get("entry"), edit.get("item")), 0)
        for axis, extent in enumerate(layout["alpha_bbox"][2:]):
            required = max(1, math.ceil(extent), sample_minimum if axis == 0 else 0)
            if required > size[axis]:
                size[axis] = 1 << (required - 1).bit_length()
    if size == [texture.width, texture.height]:
        return g1t
    canvas = Image.new("RGBA", tuple(size))
    canvas.paste(rgba(texture))
    return g1t.with_texture_resized(edit["texture"], *size, canvas.tobytes())

def rekeyed(g1t: G1tFile) -> G1tFile:

    data = g1t.encode()
    return parse_g1t(data[:4] + struct.pack("<I", zlib.crc32(data)) + data[8:])

def preview_png(path: Path, stock, edited) -> None:

    before, after = rgba(stock), rgba(edited)
    width, height = before.width + 8 + after.width, max(before.height, after.height)
    sheet = Image.new("RGBA", (width, 2 * height + 8), (128, 0, 128, 255))  
    for y, grey in ((0, 48), (height + 8, 208)):
        for x, image in ((0, before), (before.width + 8, after)):
            sheet.paste((grey, grey, grey, 255), (x, y, x + image.width, y + image.height))
            sheet.alpha_composite(image, (x, y))
    sheet.save(path)

def edited_entry(data: bytes, edits: list[dict], donors: dict, preview: Path | None = None) -> bytes:

    package = parse_texture_package(data)
    donors = donors | {"stock": package}
    for edit in edits:
        if native_source := native_officer_source(edit):
            entry, member, _ = native_source

            g1t = donors[("wo2", entry)][member]
        elif (dream := native_dream_card(package.items[edit["item"]].g1t, edit, donors)) is not None:
            g1t = dream
        else:
            g1t = enlarged(package.items[edit["item"]].g1t, edit, donors)
            if edit.get("entry") == 4095 and "lines" in edit.get("card", {}):

                g1t = native_plate(g1t, rgba(g1t.textures[edit["texture"]]))
            g1t = g1t.with_texture_payload(edit["texture"], payload(g1t.textures[edit["texture"]], edit, donors, package))
        if g1t.encode() != donors["stock"].items[edit["item"]].source:
            g1t = rekeyed(g1t)
        package = package.with_g1t(edit["item"], g1t)
        if preview:
            preview_png(Path(f"{preview}.{edit['item']}.{edit['texture']}.png"),
                        donors["stock"].items[edit["item"]].g1t.textures[edit["texture"]], g1t.textures[edit["texture"]])
    return package.encode()

FONT_ENTRY = 4059  
GREY = (0x80, 0x80, 0x80)  

def latin_font(data: bytes, cells: Image.Image) -> bytes:

    if cells.height != CELL:
        raise ValueError(f"the Latin cells are {cells.height} px; the atlas takes {CELL}")
    atlas = parse_g1t(data)
    pages = [rgba(texture) for texture in atlas.textures]
    for i in range(cells.width // CELL):
        index = LATIN_GLYPH + i
        ink = Image.new("RGBA", (CELL, CELL), GREY)
        ink.putalpha(cells.crop((i * CELL, 0, i * CELL + CELL, CELL)))
        pages[index & 1].paste(ink, ((index >> 1) % 42 * CELL, (index >> 1) // 42 * CELL))
    for page, image in enumerate(pages):
        atlas = atlas.with_texture_rgba(page, image.tobytes())
    return atlas.encode()

GLYPH_SHEET = 1024  # px square of the runtime's glyph sheet (runtime/english_runtime.c GLYPH_SHEET)


def glyph_sheet_header(atlas: bytes, size: int = GLYPH_SHEET) -> bytes:
    """Everything of a G1T before the pixels, for one blank BGRA8 texture size x size: the runtime appends the
    pixels and hands it to the game's texture loader. Header fields as in the stock font atlas; its identity is the
    CRC of the blank file, as rekeyed() keys a texture."""
    source = parse_g1t(atlas)
    magic, _, _, table, _, *fields = G1T_HEADER.unpack_from(atlas)
    texture = G1T_TEXTURE_HEADER.pack(1 << 4, PIXEL_FORMAT_BGRA8, (size.bit_length() - 1) * 0x11,
                                      G1T_TEXTURE_HEADER.unpack_from(atlas, table + source.texture_offsets[0])[3])
    head = table + 4 + len(texture)
    body = struct.pack("<I", 4) + texture
    blank = G1T_HEADER.pack(magic, b"\0" * 4, head + size * size * 4, table, 1, *fields) + atlas[G1T_HEADER.size:table]
    identity = zlib.crc32(blank + body + bytes(size * size * 4)).to_bytes(4, "little")
    return blank[:4] + identity + blank[8:] + body


def glyphs_bin(atlas: bytes, outlines: bytes) -> bytes:
    """wozmod/glyphs.bin: u32 header size, the glyph sheet's G1T header, then the font's outlines."""
    header = glyph_sheet_header(atlas)
    return struct.pack("<I", len(header)) + header + outlines

PROMPT_ENTRY = 4079  

XBOX_PROMPTS = {**{slot: slot for slot in range(16)}, 11: 2, 12: 3, 44: 44, 178: 178, 179: 179}
XBOX_PROMPT_SIZE = {3: (64, 32)}  
TWO_P_SLOT, TWO_P_ROWS = 44, 56  

def xbox_prompts(stock: bytes, wo2: dict[int, G1tFile]) -> bytes:

    package = parse_texture_package(stock)
    for slot, item in XBOX_PROMPTS.items():
        g1t = wo2[item]
        if size := XBOX_PROMPT_SIZE.get(slot):
            icon = rgba(g1t.textures[0])
            g1t = g1t.with_texture_resized(0, *size, fitted(icon, *size, (0, 0, *size)).tobytes())
        if slot == TWO_P_SLOT:  
            moz = parse_g1t(package.items[slot].source)
            art = rgba(moz.textures[0])
            art.paste(rgba(g1t.textures[0]).crop((0, 0, art.width, TWO_P_ROWS)), (0, 0))
            g1t = moz.with_texture_rgba(0, art.tobytes())
        package = package.with_g1t(slot, rekeyed(g1t))
    return package.encode()

def read_entry(stream, entry) -> tuple[bytes, bool]:

    if entry.record.allocated_bytes:
        raise ValueError(f"entry {entry.ordinal} is multi-member")
    stream.seek(entry.offset)
    data = stream.read(entry.stored_bytes)
    return (parse_lzp2(data).decoded, True) if data[:4] == b"LZP2" else (data, False)

def read_members(stream, entry) -> list[bytes]:

    stream.seek(entry.offset)
    allocation = stream.read(entry.allocated_bytes)
    return [allocation[m.relative_offset:m.relative_offset + m.stored_bytes] for m in entry.members]

@lru_cache(maxsize=1)
def _lzp2_codec() -> bytes:
    """Cache key that changes with the codec. A packaged program has no source file, so it hashes the bytecode."""
    source = Path(encode_lzp2.__code__.co_filename)
    return hashlib.sha256(source.read_bytes() if source.is_file() else encode_lzp2.__code__.co_code).digest()

def worker_pool(workers: int):
    """Workers that share the already loaded donors: forked processes, or threads where fork does not exist (Windows)."""
    if "fork" in get_all_start_methods():
        return ProcessPoolExecutor(workers, mp_context=get_context("fork"))
    return ThreadPoolExecutor(workers)

def packed_lzp2(data: bytes) -> bytes:

    if LZP2_CACHE is None:
        return encode_lzp2(data)
    path = LZP2_CACHE / f"{hashlib.sha256(_lzp2_codec() + data).hexdigest()}.lzp2"
    if path.exists():
        return path.read_bytes()
    encoded = encode_lzp2(data)
    LZP2_CACHE.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(f".{os.getpid()}")
    partial.write_bytes(encoded)
    partial.replace(path)
    return encoded

HIRES = [(4069, 2, 2, 512, 1596, 42, 19), (4079, 43, 2, 512, 256, 35, 0), (4085, 16, 2, 512, 256, 35, 0),
         
         (4060, 14, 2, 512, 630, 42, 0), (4064, 5, 2, 512, 420, 42, 0), (4080, 0, 2, 256, 462, 42, 0),
         (4080, 44, 2, 512, 210, 42, 0), (4080, 45, 2, 512, 210, 42, 0), (4081, 0, 2, 512, 168, 42, 0),
         (4079, 223, 2, 256, 64, 64, 0), (4098, 14, 2, 256, 128, 64, 0),
         (4060, 44, 2, 256, 64, 64, 0), (4060, 45, 2, 256, 64, 64, 0),
         
         (4060, 33, 2, 1024, 1024, 56, 0), (4064, 4, 2, 1024, 392, 56, 0), (4069, 26, 2, 512, 512, 56, 0),
         (4080, 38, 2, 1024, 1024, 56, 0), (4079, 221, 2, 512, 64, 56, 0), (4079, 222, 2, 512, 64, 56, 0),
         (4098, 10, 2, 512, 64, 56, 0), (4094, 5, 2, 512, 64, 56, 0), (4081, 18, 2, 512, 224, 56, 0),
         (4082, 31, 2, 512, 64, 56, 0), (4077, 168, 2, 1024, 512, 56, 0), (4061, 12, 2, 1024, 256, 56, 0),
         (4061, 13, 2, 1024, 64, 56, 0), (4061, 14, 2, 1024, 64, 56, 0), (4061, 15, 2, 1024, 64, 56, 0),
         (4061, 16, 2, 1024, 64, 56, 0), (4099, 17, 2, 1024, 64, 56, 0), (4074, 0, 2, 512, 512, 56, 0),

         (4085, 5, 2, 1024, 756, 84, 0), (4085, 6, 2, 1024, 756, 84, 0), (4085, 7, 2, 1024, 756, 84, 0),
         (4085, 8, 2, 1024, 756, 84, 0),
         
         (4069, 14, 2, 256, 672, 42, 0), (4069, 15, 2, 256, 672, 42, 0), (4077, 20, 2, 256, 256, 256, 0),
         (4077, 21, 2, 256, 256, 256, 0), (4077, 22, 2, 256, 256, 256, 0), (4094, 6, 2, 256, 256, 256, 0),
         (4094, 7, 2, 256, 256, 256, 0), (4094, 8, 2, 256, 256, 256, 0), (4079, 20, 2, 128, 128, 128, 0),
         (4079, 21, 2, 128, 128, 128, 0), (4080, 40, 2, 128, 84, 42, 0),
         (4080, 41, 2, 128, 84, 42, 0)]

HEADER = {h[:2] for h in HIRES[13:31]}
HEADER_RASTER = "wozmod.fonts.headerraster"

COMPACT_HEADER = {(4060, 33, 3): ("CHARACTER GROWTH", 0.774252, 1.5)}

MAIN = {(4085, 5): "lit", (4085, 6): "dim", (4085, 7): "lit2", (4085, 8): "dim2"}
MAIN_RASTER = "wozmod.fonts.mainraster"
MAIN_PITCH, MAIN_STREAK_END, MAIN_EDGE_FADE = 84, 506, 8

HEADER_TEXTS = {(12, 0, 0): ["NEW CHARACTER", "RESULTS", "ABILITY ACQUIRED", "WEAPON ACQUIRED", "ITEM ACQUIRED",
                              "NEW SCENARIO"]}

PAGE_LABEL = {(4079, 43), (4085, 16)}
PAGE_ROWS = ["donor:5a93503c1ee64215", "donor:5ddcad9c51246326", "donor:0ec1bbbac7a32e19", "VS MODE", "donor:22b111c17b2c4be3", "donor:316a1d8733d73aba", "donor:d0b1a869600d2107"]

PAGE_RIGHT = {text: 256.8 + {"donor:316a1d8733d73aba": 0.54, "donor:d0b1a869600d2107": -0.69}.get(text, 0.15) for text in PAGE_ROWS}
MAIN_ROWS = PAGE_ROWS + ["donor:628db0c7598820b1", "QUIT GAME"]
LABEL_RASTER = "wozmod.fonts.labelraster"
HIRES_TWIN = {(4080, 45): (4080, 44)}  

VALUE = {(4069, 14): "value_dim", (4069, 15): "value_lit", (4077, 20): "choice_dim", (4077, 21): "choice_lit",
         (4077, 22): "choice_lit", (4094, 6): "choice_dim", (4094, 7): "choice_lit", (4094, 8): "choice_lit",
         (4079, 20): "confirm_dim", (4079, 21): "confirm_lit", (4080, 40): "value_dim", (4080, 41): "value_lit"}
VALUE_LEVELS = {"value_dim": (156, 49), "value_lit": (253, 49), "choice_dim": (201, 48), "choice_lit": (251, 53),
                "confirm_dim": (205, 82), "confirm_lit": (254, 82)}
VALUE_RASTER = "wozmod.fonts.valueraster"
VALUE_TEXTS = {(15, 9, 0): ["donor:27e88b861fa91ee1", "donor:db2cb3fe28e2c54f", "donor:0e24076baf990d55", "donor:dc1a2f9d7bd77176"], (16, 20, 0): ["donor:f9171164593756e5", "donor:23794d91c53ae875"]}
VALUE_TEXTS |= {(17, 36, 0): ["donor:e8a01133b1351e5f", "donor:38cca6bea010af8a"], (17, 37, 0): ["donor:e8a01133b1351e5f", "donor:38cca6bea010af8a"], (15, 10, 0): VALUE_TEXTS[(15, 9, 0)],
                (16, 21, 0): VALUE_TEXTS[(16, 20, 0)]}

DARK = {**{(4080, i): "pause" for i in range(6, 34)}, **{(4060, i): "help" for i in range(2, 14)},
        (4069, 0): "help", (4069, 1): "help", (4074, 10): "help", (4069, 19): "controls"}
DARK_PLATE = {**{(e, i): "plate" for e, items in {4069: range(27, 30), 4080: range(56, 59)}.items() for i in items},
              **{(e, i): "records" for e, items in {4077: [26], 4085: [9, 10, 11],
                 4094: [3]}.items() for i in items},
              **{(e, i): "results" for e, items in {4074: [3, 4, 5, 6], 4081: [39],
                 4077: [27, 28]}.items() for i in items},
              **{(e, i): "camp" for e, items in {4060: [23, 24, 25, 26, *range(36, 44)],
                 4064: [22, 23, 24], 4094: [4, 16], 4098: [6, 7, 11, 15], 4081: [40, 41]}.items() for i in items}}
DARK |= DARK_PLATE | {(4079, i): "attributes" for i in range(83, 97)}

# Attribute labels condense only past this room: the effect plate and the weapon card cell before its value.
ATTRIBUTE_ROOM = 96
ATTRIBUTE_QUAD_MINIMUM = {(4079, 83 + offset): width for offset, width in enumerate(
    [76, 48, 52, 72, 56, 68, 92, 52, 76, 80, 64, 80, 72, 68])}
MENU_RASTER = "wozmod.fonts.menu"
MENU_STYLE = {name: f"dark.{name}" for name in
              ("pause", "help", "controls", "plate", "records", "results", "camp", "attributes")}

HELP_QUAD_WIDTH = {(4060, i): width for i, width in
                   [(2, 548), (3, 544), (4, 392), (5, 400), (6, 476),
                    (7, 420), (8, 448), (9, 528), (10, 560), (11, 632), (13, 648)]}
HELP_QUAD_WIDTH[(4074, 10)] = 420
DARK_TEXTS = {(17, 15, 0): ["donor:de1c575feaa275ad"], (17, 17, 0): ["donor:ec2261055bc0aec6"],  
              (1, 2, 0): ["Please select officer to whom you wish to allot growth points."],
              (1, 5, 0): ["Please select amount of growth points."],
              (1, 7, 0): ["Please select fusion effects."],
              (501, 1, 0): ["Please select the action whose effect you wish to change."],
              (501, 2, 0): ["Please push the key or button which you wish to use."],
              (12, 10, 0): ["donor:514cae1e06b80c80", "donor:5a3f61cf0be5899d"]}
DARK_TEXTS |= {(508, 48 + k, 0): [t] for k, t in enumerate(["donor:3580469d06f361ee", "KEYBOARD", "GAMEPAD"])}
DARK_TEXTS |= {(e, i, 0): [t] for e, i, t in [(12, 1, "donor:ced5103bf1a4663a"), (12, 2, "donor:7ead20bebf6532b9"),
                (12, 3, "donor:5888675d99b95617"), (12, 5, "donor:00f2459ce855c53a"), (15, 12, "donor:5888675d99b95617"), (15, 13, "donor:ced5103bf1a4663a"),
                (22, 8, "donor:5888675d99b95617"), (22, 9, "donor:25df06def4b63ddc"), (1, 14, "donor:e8144b1fafeed56b"), (1, 15, "donor:c57fcb9193a69cf6"),
                (1, 16, "FUSION WEAPON"), (1, 17, "donor:f404d4ef53431418"), (1, 18, "donor:b2d86f84fddddde2"), (1, 19, "donor:2dc5bc3ce114b123")]}
DARK_TEXTS |= {(16, 82 + k, 0): [t] for k, t in enumerate(
               ["donor:dfb0f521ab244b54", "donor:2a1a6355ed7e30b0", "donor:ea0151c4cf78b351", "donor:f3577cb543db90ce", "donor:8c4587b0d81f3f73", "donor:795b048ba2164e50", "donor:171418c643055da9", "donor:aa42ff595bdb92d6", "donor:e7d91391c1519825", "donor:5de74a81b192304c", "donor:dea894b8304550ba", "donor:49796694dc677011", "donor:f68b032edc223044", "donor:110049cc8ca88c81"])}
BUTTON_RASTER = "wozmod.fonts.buttonraster"
BUTTON_CAP = 25.0  
VECTOR_WINDOW = {(4069, 2): (101, 411), (4060, 14): (0, 288), (4064, 5): (100, 412), (4080, 0): (0, 208),
                 (4081, 0): (106, 406), (4079, 223): (0, 232), (4098, 14): (0, 256), (4060, 44): (0, 210),
                 (4060, 45): (0, 210), (4080, 44): (0, 332), (4080, 45): (0, 332),
                 (4060, 33): (0, 556), (4064, 4): (0, 612), (4069, 26): (0, 412), (4080, 38): (0, 556),
                 (4079, 221): (0, 400), (4079, 222): (0, 436), (4098, 10): (0, 412), (4094, 5): (0, 480),
                 (4081, 18): (0, 400), (4082, 31): (0, 372), (4077, 168): (0, 600), (4061, 12): (0, 600),
                 (4061, 14): (0, 600), (4061, 16): (0, 600), (4099, 17): (0, 532), (4074, 0): (0, 504)}

TRACK = {1: 1.070, 17: 1.894, 9: 0.0, 493: 1.070, 508: 1.894, 501: 0.0}  # WO1 X360 entries, then WO1 PC ones not moved yet
TRACK_AUTHORED = 0.338  
CONDENSED, CONDENSED_MIN = 0.75, 4.0

# The rows wozmod adds to Game Settings: one sheet of 42 px bands in the stock label face (the runtime draws
# them, tinted, where the stock code draws no row). Labels first, then the values; every text is centred in the
# 310 px quad the stock labels use.
OPTION_ROWS = ["TEXT LANGUAGE", "VOICE LANGUAGE", "FIELD OF VIEW", "FRAME RATE", "MOVING CAMERA FIX", "ENGLISH",
               "JAPANESE", "60 FPS", "120 FPS", "FULLSCREEN", "WINDOWED", "BORDERLESS",
               *(f"< {v}% >" for v in range(100, 151, 5))]
OPTION_LABELS = 5
OPTION_W, OPTION_BAND, OPTION_ROOM = 310, 42, 300
OPTION_BASELINES = (34, 31)  # labels, and values 3 px higher as the stock values sit against the stock labels
# Every value's slot is its row's pitch less this one margin, so a word squeezed to its slot stops short of the next.
VALUE_MARGIN = 20
# The display modes step by the runtime's DISPLAY_MODE_PITCH, wide enough for each word at its natural width.
DISPLAY_MODE_PITCH = 195
OPTION_NARROW = {text: DISPLAY_MODE_PITCH - VALUE_MARGIN for text in ("FULLSCREEN", "WINDOWED", "BORDERLESS")}

PAUSE_BUTTON_ROWS = {
    (4080, 0, 0): ('donor:ae75b2880b5b5e40', 108.342679, 6.657321, 32, None, None),
    (4080, 0, 1): ('donor:cf888f8318b597b5', 122.068536, 4.975078, 32, None, None),
    (4080, 0, 2): ('TEAM INFO', 126.376947, 4.660436, 32, None, None),
    (4080, 0, 3): ('donor:0bad33e27107687c', 128.468848, 5.537383, 32, None, None),
    (4080, 0, 4): ('donor:10aae6592b39c03b', 124.012462, 5.993769, 32, None, None),
    (4080, 0, 5): ('donor:1e367f240be1b68c', 74.242991, 5.993769, 32, None, None),
    (4080, 0, 6): ('donor:23e04084df125efc', 110.210281, 5.993769, 32, None, None),
    (4080, 0, 7): ('donor:af7300795e9ccc44', 128.082554, 4.975078, 32, None, None),
    (4080, 0, 8): ('MAIN MENU', 127.049845, 4.993769, 32, None, None),
    (4080, 0, 9): ('donor:0747c96a23dbfa58', 105.344237, 5.956386, 32, None, None),
    (4080, 0, 10): ('2P QUIT', 103.228972, 3.79595, 32, None, None),
    (4080, 44, 0): ('ABILITY ACQUISITION', 197.35514, 3.975078, 32, None, None),
    (4080, 44, 1): ('STRATEGY INFO', 192.654206, 7.546729, 32, None, None),
    (4080, 44, 2): ('MOVE LIST', 137.57944, 6.663551, 32, None, None),
    (4080, 44, 3): ('GAME CONTROLS', 198.062306, 4.981308, 32, None, None),
    (4080, 44, 4): ('donor:89dbf71048801678', 113.328661, 5.993769, 31, None, None),
    (4080, 45, 0): ('ABILITY ACQUISITION', 197.35514, 3.975078, 32, None, None),
    (4080, 45, 1): ('STRATEGY INFO', 192.654206, 7.546729, 32, None, None),
    (4080, 45, 2): ('MOVE LIST', 137.57944, 6.663551, 32, None, None),
    (4080, 45, 3): ('GAME CONTROLS', 198.062306, 4.981308, 32, None, None),
    (4080, 45, 4): ('donor:89dbf71048801678', 113.328661, 5.993769, 31, None, None),

    (4081, 0, 0): ('donor:0747c96a23dbfa58', 105.095015, 165.962617, 32, None, None),
    (4081, 0, 1): ('RESTART', 105.132399, 165.904984, 32, None, None),
    (4081, 0, 2): ('CHANGE CHARACTERS', 182.580996, 164.718069, 32, None, None),
    (4081, 0, 3): ('MAIN MENU', 131.076324, 165.981308, 32, None, None),
    (4079, 223, 0): ('TREASURE GUIDE', 199.037383, 5.0, 32, None, None),
}

TRACKED_RASTER = """
import json, sys
sys.path.insert(0, sys.argv[1])
from wozmod.fonts import buttonraster as B
text, out, scale, cap, track, style = (sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5]),
                                     float(sys.argv[6]), sys.argv[7])
layout = B.layout
def tracked(text, cap=B.V.DESIGN_CAP):
    placed, advance = layout(text, cap)
    gaps, out, i = 0, [], 0
    for ch in text:
        if ch == " ":
            gaps += 1
        else:
            out.append((ch, placed[i][1] + gaps * track))
            i, gaps = i + 1, gaps + 1
    return out, advance + (gaps - 1) * track
B.layout = tracked
fills, cov = [], B.coverage
def coverage(draw, w, h, dilate=0.0, bold=0.0):
    F = cov(draw, w, h, dilate, bold)
    if not dilate:
        fills.append(F)
    return F
B.coverage = coverage
label = B.render(text, scale, cap, style)
label.image.save(out)
p = fills[0].max(0)  # the fill's sub-pixel ends, measured as the 1x rows are (fill_edges)
on = (p >= 0.5).nonzero()[0]
i, j = int(on[0]), int(on[-1])
fill = [i - (float(p[i - 1]) if i else 0), j + 1 + (float(p[j + 1]) if j + 1 < len(p) else 0)]
print(json.dumps(dict(baseline=label.baseline, ink=list(label.ink), fill=fill)))
"""

STYLE = {(4069, 2): "501.3", (4060, 14): "493.9", (4060, 44): "493.9", (4060, 45): "493.9", (4064, 5): "493.9",
         (4098, 14): "493.9", (4080, 0): "508.0", (4080, 44): "508.0", (4079, 223): "508.0", (4081, 0): "508.0"}

def row_track(entry: int, item: int, piece: dict) -> float:
    return TRACK.get((piece.get("donor") or [None])[0], TRACK_AUTHORED)

def gaps(text: str) -> int:

    return len(text) - 1

def solved_track(text: str, natural: dict, geometry, scale: int, mean: float) -> float | None:

    x0, x1 = natural["fill"]
    track = ((geometry[1] - geometry[0]) * scale - (x1 - x0)) / (gaps(text) * scale) if gaps(text) else mean
    short = (mean - track) * gaps(text)  
    return None if short > max(CONDENSED * gaps(text), CONDENSED_MIN) else track

def _render_button_label(text: str, scale: int, out: Path, track: float = 0.0,
                 style: str = "501.3") -> tuple[Image.Image, dict]:

    run = subprocess.run([FONT_PYTHON, "-c", TRACKED_RASTER, str(REPO / "src"), text, str(out),
                          str(scale), str(BUTTON_CAP), str(track), style], check=True, capture_output=True, text=True, env=font_environment())
    return Image.open(out).convert("RGBA"), json.loads(run.stdout)

PAUSE_BUTTON_RASTER = """
import json, sys
sys.path.insert(0, sys.argv[1])
from wozmod.fonts import buttonraster as B
text, out, scale = sys.argv[2], sys.argv[3], float(sys.argv[4])
target = float(sys.argv[5])
counterpart = None if sys.argv[6] == "self" else sys.argv[6]
hscale = 1.0
if counterpart is not None:
    reference = B.render_x360_pause(counterpart, 1.0, track=-0.71)
    hscale = target / (reference.ink[2] - reference.ink[0])
    target = None
fills, cov = [], B.coverage
def coverage(draw, w, h, dilate=0.0, bold=0.0):
    value = cov(draw, w, h, dilate, bold)
    if not dilate:
        fills.append(value)
    return value
B.coverage = coverage
label = B.render_x360_pause(text, scale, target_width=target, hscale=hscale, track=-0.71)
label.image.save(out)
p = fills[0].max(0)
on = (p >= 0.5).nonzero()[0]
i, j = int(on[0]), int(on[-1])
fill = [i - (float(p[i - 1]) if i else 0), j + 1 + (float(p[j + 1]) if j + 1 < len(p) else 0)]
print(json.dumps(dict(baseline=label.baseline, ink=list(label.ink), fill=fill,
                     counterpart=counterpart, hscale=hscale)))
"""

def _render_pause_button_label(text: str, scale: int, out: Path, target: float,
                       counterpart: str | None) -> tuple[Image.Image, dict]:

    run = subprocess.run([FONT_PYTHON, "-c", PAUSE_BUTTON_RASTER,
                          str(REPO / "src"), text, str(out), str(scale),
                          str(target), "self" if counterpart is None else counterpart],
                         check=True, capture_output=True, text=True, env=font_environment())
    return Image.open(out).convert("RGBA"), json.loads(run.stdout)

def header_label(text: str, scale: int, out: Path, raster: str = HEADER_RASTER, *options: str) -> tuple:
    request = dict(kind="header", text=text, scale=scale, raster=raster, options=list(options))
    return lettering.get(request, lambda: _render_header_label(text, scale, out, raster, *options))

def button_label(text: str, scale: int, out: Path, track: float = 0.0,
                 style: str = "501.3") -> tuple:
    request = dict(kind="button", text=text, scale=scale, track=track, style=style)
    return lettering.get(request, lambda: _render_button_label(text, scale, out, track, style))

def pause_button_label(text: str, scale: int, out: Path, target: float,
                       counterpart: str | None) -> tuple:
    request = dict(kind="pause", text=text, scale=scale, target=target, counterpart=counterpart)
    return lettering.get(request, lambda: _render_pause_button_label(text, scale, out, target, counterpart))

def _squeezed_label(label: Image.Image, metrics: dict, geometry, scale: int,
                   squeeze: bool = False) -> tuple[Image.Image, dict]:

    if not squeeze or geometry is None:
        return label, metrics
    left, right = metrics.get("fill") or metrics["ink"][::2]
    natural, official = right - left, (geometry[1] - geometry[0]) * scale
    if natural <= official or official <= 0:
        return label, metrics
    factor = official / natural

    horizontal = lambda x: left + (x - left) * factor
    image = label.transform((math.ceil(horizontal(label.width)), label.height),
                            Image.Transform.AFFINE, (1 / factor, 0, left * (1 - 1 / factor), 0, 1, 0),
                            Image.Resampling.BICUBIC)
    changed = dict(metrics, ink=[horizontal(v) if k % 2 == 0 else v
                                 for k, v in enumerate(metrics["ink"])])
    if "fill" in metrics:
        changed["fill"] = [horizontal(v) for v in metrics["fill"]]
    if "pen" in metrics:
        changed["pen"] = horizontal(metrics["pen"])
    if "advance" in metrics:
        changed["advance"] = metrics["advance"] * factor
    if "size" in metrics:
        changed["size"] = list(image.size)
    return image, changed

def squeezed_label(label: Image.Image, metrics: dict, geometry, scale: int,
                   squeeze: bool = False) -> tuple[Image.Image, dict]:
    if not squeeze or geometry is None:
        return label, metrics
    left, right = metrics.get("fill") or metrics["ink"][::2]
    width = (geometry[1] - geometry[0]) * scale
    if right - left <= width or width <= 0:
        return label, metrics
    if "_lettering" not in metrics:
        return _squeezed_label(label, metrics, geometry, scale, squeeze)
    request = dict(kind="squish", base=metrics["_lettering"], width=width, fill=metrics.get("fill"))
    return lettering.get(request, lambda: _squeezed_label(label, metrics, geometry, scale, True))

def page_label_art(scale: int, width: int = 512, served: Image.Image | None = None,
                   squeeze: bool = False) -> Image.Image:

    art = Image.new("RGBA", (width * scale, 256 * scale))
    with tempfile.TemporaryDirectory() as tmp:
        for i, text in enumerate(PAGE_ROWS):
            out = Path(tmp) / f"{i}.png"
            label, metrics = header_label(text, scale, out, LABEL_RASTER)
            geometry = row_geometry(served.crop((0, 35 * i, width, 35 * (i + 1))),
                                    lambda r, g, b, a: (250 - (r + g + b) / 3) / 180
                                    if a > 204 else 0) if squeeze and served is not None else None
            label, metrics = squeezed_label(label, metrics, geometry, scale, squeeze)
            art.paste(label,
                      (round(PAGE_RIGHT[text] * scale - metrics["ink"][2]), round((25.6 + 35 * i) * scale - metrics["baseline"])))
    return art

def fill_edges(profile: list[float]) -> tuple[float, float]:

    on = [x for x, v in enumerate(profile) if v >= 0.5]
    i, j = on[0], on[-1]
    return i - (profile[i - 1] if i else 0), j + 1 + (profile[j + 1] if j + 1 < len(profile) else 0)

def button_cover(r: int, g: int, b: int, a: int = 255) -> float:

    return ((r + g + b) / 3 - 38) / 214 if a > 204 else 0.0

def header_cover(r: int, g: int, b: int, a: int = 255) -> float:

    return (r - 8) / 206 if a > 204 else 0.0

def main_cover(r: int, g: int, b: int, a: int = 255) -> float:

    return ((r + g + b) / 3 - 45) / 70 if a > 204 else 0.0

def alpha_cover(r: int, g: int, b: int, a: int) -> float:

    return a / 255

def row_geometry(row: Image.Image, cover=button_cover) -> tuple[float, float, int] | None:

    px, bottoms, profile, cover_fn = row.load(), {}, [0.0] * row.width, cover
    for y in range(row.height):
        for x in range(row.width):
            r, g, b, a = px[x, y]
            cover = min(max(cover_fn(r, g, b, a), 0.0), 1.0)
            profile[x] = max(profile[x], cover)
            if cover >= 0.5:
                bottoms[x] = y
    if not bottoms:
        return None
    vals = list(bottoms.values())  
    while True:                    
        deep = [v for v in vals if v >= max(vals) - 2]
        if len(deep) * 4 >= len(bottoms) or len(deep) == len(vals):
            return *fill_edges(profile), mode(deep) + 1
        vals = [v for v in vals if v < max(vals) - 2]

def row_offset(geometry, metrics: dict, scale: int, align: str) -> tuple[int, int]:

    left, right, baseline = geometry
    x0, x1 = metrics.get("fill") or metrics["ink"][::2]
    x = scale * (left + right) / 2 - (x0 + x1) / 2 if align == "full" else scale * left - x0
    return round(x), scale * baseline - metrics["baseline"]

def donor_texts(mapping: dict) -> dict:

    texts = {tuple(s["donor"]): s["texts"] for f in mapping["faces"].values() for s in f["rows"] if "texts" in s and "donor" in s}
    return texts | HEADER_TEXTS | VALUE_TEXTS | DARK_TEXTS

def hires_texts(edit: dict, mapping: dict, donors: dict) -> list[str | None]:

    texts, out = donor_texts(mapping), []
    for row in edit.get("rows") or [edit["draw"]]:  
        piece = next((p for p in row if "text" in p or "donor" in p), row[0])  
        if "text" in piece:
            out.append(piece["text"])
        elif "donor" not in piece:
            out.append(None)  
        elif "row" in piece:
            out.append(texts.get(tuple(piece["donor"]), [None] * (piece["row"] + 1))[piece["row"]])
        else:
            entry, item, index = piece["donor"]
            bands = ink_rows(rgba(donors[entry][item].textures[index]))
            y0, y1 = piece["box"][1], piece["box"][3]
            out.append(next(t for (top, bottom), t in zip(bands, texts[tuple(piece["donor"])], strict=True) if y0 <= top and bottom <= y1))

    if edit.get("style") == "results":
        out = [(reference(f"{edit['entry']}.{edit['item']}", row) or {}).get("pc_text") or text
               for row, text in enumerate(out)]
    return out

def _render_header_label(text: str, scale: int, out: Path, raster: str = HEADER_RASTER, *options: str) -> tuple[Image.Image, dict]:

    run = subprocess.run([FONT_PYTHON, "-m", raster, text, str(out), "--scale", str(scale), *options],
                         check=True, capture_output=True, text=True, env=font_environment())
    return Image.open(out).convert("RGBA"), json.loads(run.stdout.splitlines()[-1])

def value_cover(look: str):

    peak, ring = VALUE_LEVELS[look]
    return lambda r, g, b, a: (max(r, g, b) - ring) / (peak - ring) if a > 204 else 0.0

def rows_edit(entry: int, item: int, mapping: dict, served: Image.Image, **face) -> dict:

    edits = mapping["textures"]
    edit = next((e for e in edits if (e["entry"], e.get("item")) == (entry, item) or [entry, item] in e.get("also", [])), None)
    if not edit:  
        edit = next(e for e in edits if e["entry"] == entry and 0 < item - e["item"] < e.get("count", 1))
        edit = dict(edit, donor=[edit["donor"][0], edit["donor"][1] + item - edit["item"], edit["donor"][2]])
    if "rows" in edit:
        rows = edit["rows"]
    elif "draw" in edit:
        rows = [[p] for p in edit["draw"] if "text" in p or "donor" in p]
    else:
        rows = [[{"donor": edit["donor"], "row": k}] for k in range(len(donor_texts(mapping).get(tuple(edit["donor"]), [None])))]
    bands = [(max(top - 2, 0), min(bottom + 2, served.height)) for top, bottom in ink_rows(served)]
    if (entry, item) in DARK_PLATE or face.get("style") == "attributes":
        bands = [(0, served.height)]
    if (entry, item) in {(4069, 14), (4069, 15), (4080, 40), (4080, 41)}:
        bands = [(42 * k, 42 * (k + 1)) for k in range(len(rows))]
    if len(bands) != len(rows):
        raise ValueError(f"{entry}.{item}: {len(rows)} rows on {len(bands)} ink bands")

    return dict(edit, entry=entry, item=item,
                render_reference=(edit["entry"], edit["item"]),
                rows=rows, bands=bands, **face)

def header_edit(edit: dict, mapping: dict, served: Image.Image) -> dict:

    texts = {tuple(s["donor"]): s["texts"] for s in mapping["faces"]["header"]["rows"]} | HEADER_TEXTS
    bands = edit.get("bands") or ink_rows(served)
    if "rows" in edit:
        rows = edit["rows"]
    elif "draw" in edit:  
        donor, rows = edit["draw"][0]["donor"], []
        for top, bottom in bands:
            into = next((p for p in edit["draw"][1:] if p["into"][1] <= top and bottom <= p["into"][3]), None)
            rows.append([into] if into else [{"donor": donor, "row": len(rows) - sum(len(r) for r in rows if "into" in r[0])}])
    else:
        rows = [[{"donor": edit["donor"], "row": i}] for i in range(len(texts[tuple(edit["donor"])]))]
    return dict(edit, rows=rows, bands=bands, align="left", face="header")

def stock_split(band: Image.Image, gap: int) -> int:

    alpha = band.getchannel("A")
    on = [alpha.crop((x, 0, x + 1, band.height)).getbbox() is not None for x in range(band.width)]
    x = on.index(True)
    while any(on[x:x + gap]):
        x += 1
    return x

def help_layout(entry: int, item: int, right: float, width: float) -> tuple[float, int]:

    quad = HELP_QUAD_WIDTH.get((entry, item))
    if width + 2 <= right:
        return right, 0
    if quad is None:
        growth = 4 * math.ceil((width + 2 - right) / 4)
        return right + growth, growth
    minimum = max(quad, 4 * math.ceil((width + quad - right + 2) / 4))
    return right + minimum - quad, minimum if minimum > quad else 0

def vector_art(edit: dict, served: Image.Image, texts: list, scale: int, window=None,
               squeeze: bool = False) -> Image.Image:

    art = Image.new("RGBA", (served.width * scale, served.height * scale))
    render_entry, render_item = edit.get("render_reference", (edit["entry"], edit["item"]))
    header = edit.get("face") == "header"
    official_left = next((row_geometry(served.crop((0, top, served.width, bottom)), header_cover)[0]
                          for (top, bottom), row in zip(edit.get("bands", []), edit.get("rows", []), strict=True)
                          if "donor" in row[0] and "box" not in row[0]), None) if header else None
    with tempfile.TemporaryDirectory() as tmp:
        help_labels = {}
        help_right = None
        if edit.get("style") == "help":
            geometries, widths = [], []
            for k, ((top, bottom), text) in enumerate(zip(edit.get("bands") or [(0, served.height)], texts, strict=True)):
                geometry = row_geometry(served.crop((0, top, served.width, bottom)), alpha_cover)
                if not text or geometry is None:
                    continue
                piece = (edit.get("rows") or [edit["draw"]])[k][0]
                options = menu_render_options(render_entry, render_item, k, piece, "help",
                                              destination=(edit["entry"], edit["item"]))
                label, metrics = header_label(text, scale, Path(tmp) / f"help-{k}.png", MENU_RASTER, *options)
                label, metrics = squeezed_label(label, metrics, geometry, scale, squeeze)
                help_labels[k] = label, metrics
                geometries.append(geometry)
                widths.append((metrics["ink"][2] - metrics["ink"][0]) / scale)
            if widths:
                help_right, minimum = help_layout(edit["entry"], edit["item"],
                                                  max(g[1] for g in geometries), max(widths))
                art.info["minimum"] = minimum
                if (edit["entry"], edit["item"]) not in HELP_QUAD_WIDTH and minimum:
                    art.info["right_growth"] = minimum
                    wider = Image.new("RGBA", ((served.width + minimum) * scale, art.height))
                    wider.info.update(art.info)
                    art = wider
        value_labels = {}
        if (edit["entry"], edit["item"]) in {(4069, 14), (4069, 15)}:
            for text in set(texts):
                value_labels[text] = header_label(text, scale, Path(tmp) / f"value-{text}.png", VALUE_RASTER,
                                                 "--look", edit["look"])
            for label, metrics in value_labels.values():
                fill = row_geometry(label, value_cover(edit["look"]))
                metrics["fill"] = fill[:2]
        for k, ((top, bottom), text) in enumerate(zip(edit.get("bands") or [(0, served.height)], texts, strict=True)):
            band_1x = served.crop((0, top, served.width, bottom))
            if text is None:  
                art.paste(band_1x.resize((band_1x.width * scale, band_1x.height * scale), Image.Resampling.NEAREST),
                          (0, top * scale))
                continue
            row = (edit.get("rows") or [edit["draw"]])[k]
            piece, split = row[0], 0
            if header and "donor" not in piece and "text" not in piece:  
                split = stock_split(band_1x, edit.get("gap", 9))
                art.paste(band_1x.crop((0, 0, split, band_1x.height)).resize((split * scale, band_1x.height * scale),
                                                                              Image.Resampling.NEAREST), (0, top * scale))
            look, style = edit.get("look"), edit.get("style")
            geometry = row_geometry(band_1x.crop((split, 0, band_1x.width, band_1x.height)),
                                    header_cover if header else value_cover(look) if look else alpha_cover if style else button_cover)
            if "slots" in edit and geometry:  # a value keeps its width, squeezed only past its slot, centred in it
                left, right = edit["slots"][k]
                geometry = (left + VALUE_MARGIN / 2, right - VALUE_MARGIN / 2, geometry[2])
            if not text or not geometry:
                continue
            geometry = (geometry[0] + split, geometry[1] + split, geometry[2])
            if (edit["entry"], edit["item"]) in TIME_LABEL_LAYOUT:
                _, left, baseline = TIME_LABEL_LAYOUT[(edit["entry"], edit["item"])]
                geometry = (left, left, baseline - top)
            ref = ["--ref", "{}.{}".format(wo1_sheet(piece["donor"][0]), piece["donor"][1])] if "donor" in piece else []
            if look or style:  
                if look:
                    label, metrics = value_labels[text] if text in value_labels else header_label(
                        text, scale, Path(tmp) / f"{k}.png", VALUE_RASTER, "--look", look, *ref)
                else:
                    options = menu_render_options(render_entry, render_item, k, piece, style,
                                                  destination=(edit["entry"], edit["item"]))
                    label, metrics = help_labels[k] if k in help_labels else header_label(
                        text, scale, Path(tmp) / f"{k}.png", MENU_RASTER, *options)
                fit = (geometry[0], geometry[0] + ATTRIBUTE_ROOM, geometry[2]) if style == "attributes" else geometry
                label, metrics = squeezed_label(label, metrics, fit, scale, squeeze)
                if "tint" in edit:
                    label = tinted(label, edit["tint"])
                if look and (edit["entry"], edit["item"]) in {(4069, 14), (4069, 15)}:
                    geometry = (geometry[0], geometry[1], 29)
                if style == "attributes":

                    geometry = (geometry[0], geometry[1], 23)
                x, y = row_offset(geometry, metrics, scale, "full" if look else "left")
                if (edit["entry"], edit["item"]) in TIME_LABEL_LAYOUT:
                    y = round(y)
                if style == "help":
                    x = round(help_right * scale - metrics["ink"][2])
                if x + metrics["ink"][0] < 0 or x + metrics["ink"][2] > art.width:
                    raise ValueError(f"{edit['entry']}.{edit['item']} row {k} {text}: full-width ink "
                                     f"{x / scale:.1f}-{(x + metrics['ink'][2]) / scale:.1f} "
                                     f"leaves the {served.width}-wide texture")
                band = Image.new("RGBA", (art.width, (bottom - top) * scale))
                band.paste(label, (x, y))
                art.alpha_composite(band, (0, top * scale))
                continue
            if header:
                if "text" in piece and official_left is not None:  
                    geometry = (official_left, geometry[1], geometry[2])
                compact = COMPACT_HEADER.get((edit["entry"], edit["item"], k))
                if compact:
                    if text != compact[0]:
                        raise ValueError("compact Camp title no longer resolves to CHARACTER GROWTH")
                    ref += ["--hscale", str(compact[1])]
                label, metrics = header_label(text, scale, Path(tmp) / f"{k}.png", HEADER_RASTER, *ref)
                label, metrics = squeezed_label(label, metrics, geometry, scale, squeeze)
                x, y = row_offset(geometry, metrics, scale, "left")
                if compact:
                    x = round(compact[2] * scale) - label.getbbox()[0]
                if window and x + metrics["ink"][2] > window[1] * scale:
                    print(f"{edit['entry']}.{edit['item']} row {k} {text}: ink to {(x + metrics['ink'][2]) / scale:.1f} "
                          f"leaves the quad window {window[1]}", file=sys.stderr)
                band = Image.new("RGBA", (art.width, (bottom - top) * scale))
                band.paste(label, (x, y))
                art.alpha_composite(band, (0, top * scale))
                continue
            pause = PAUSE_BUTTON_ROWS.get((edit["entry"], edit["item"], k))
            if pause:
                expected, target, left, baseline, centre, counterpart = pause
                if text != expected:
                    raise ValueError(f"pause row pairing changed: {edit['entry']}.{edit['item']} row {k}: {text!r}")
                label, metrics = pause_button_label(text, scale, Path(tmp) / f"pause-{k}.png", target, counterpart)
                label, metrics = squeezed_label(label, metrics, (left, left + target, baseline), scale, squeeze)
                width = target if counterpart is None else (metrics["fill"][1] - metrics["fill"][0]) / scale
                placement = ((centre - width / 2, centre + width / 2, baseline)
                             if centre is not None else (left, left + width, baseline))
                x, y = row_offset(placement, metrics, scale, "full" if centre is not None else "left")
                if (edit["entry"], edit["item"]) in {(4080, 44), (4080, 45)}:
                    x = max(x, 0)  
                band = Image.new("RGBA", (art.width, (bottom - top) * scale))
                band.paste(label, (x, y))
                art.paste(band, (0, top * scale))
                continue
            track = row_track(edit["entry"], edit["item"], piece)
            if "donor" in piece:  
                label, metrics = button_label(text, scale, Path(tmp) / f"{k}.png", 0.0,
                                              STYLE.get((edit["entry"], edit["item"]), "501.3"))
                solved = solved_track(text, metrics, geometry, scale, track)
                if solved is None:
                    print(f"{edit['entry']}.{edit['item']} row {k} {text}: condensed in the official art, "
                          f"set at the donor's tracking {track}", file=sys.stderr)
                track = track if solved is None else solved
            label, metrics = button_label(text, scale, Path(tmp) / f"{k}.png", track,
                                          STYLE.get((edit["entry"], edit["item"]), "501.3"))
            label, metrics = squeezed_label(label, metrics, geometry, scale, squeeze)
            x, y = row_offset(geometry, metrics, scale, edit.get("align", "full"))
            if (edit["entry"], edit["item"]) in {(4080, 44), (4080, 45)}:

                x = max(x, 0)
            quad = piece.get("window") or window
            if quad and (x < quad[0] * scale or x + label.width > quad[1] * scale):
                print(f"{edit['entry']}.{edit['item']} row {k} {text}: {x}-{x + label.width} leaves the quad window "
                      f"{quad[0] * scale}-{quad[1] * scale}", file=sys.stderr)
            band = Image.new("RGBA", (art.width, (bottom - top) * scale))
            band.paste(label, (x, y))  
            art.paste(band, (0, top * scale))
    return art

def main_art(served: Image.Image, scale: int, style: str, squeeze: bool = False) -> Image.Image:

    art = Image.new("RGBA", (served.width * scale, served.height * scale))
    bands = [(k * MAIN_PITCH, (k + 1) * MAIN_PITCH) for k in range(len(MAIN_ROWS))]
    geometry = [row_geometry(served.crop((0, top, served.width, bottom)), main_cover) for top, bottom in bands]
    official_geometry = geometry.copy()
    geometry[-1] = geometry[-2]  
    with tempfile.TemporaryDirectory() as tmp:
        for k, ((top, bottom), text, (left, right, baseline)) in enumerate(zip(bands, MAIN_ROWS, geometry, strict=True)):
            out = Path(tmp) / f"{k}.png"
            label, metrics = header_label(text, scale, out, MAIN_RASTER,
                                          "--style", style, "--room", str(MAIN_STREAK_END - left))
            label, metrics = squeezed_label(label, metrics, official_geometry[k], scale, squeeze)
            band = Image.new("RGBA", (art.width, MAIN_PITCH * scale))
            band.paste(label, row_offset((left, right, baseline), metrics, scale, "left"))
            art.alpha_composite(band, (0, top * scale))
    fade = Image.new("L", art.size, 255)  
    for x in range(MAIN_EDGE_FADE * scale):
        fade.paste(round(255 * (x + 0.5) / (MAIN_EDGE_FADE * scale)), (x, 0, x + 1, art.height))
    art.putalpha(ImageChops.multiply(art.getchannel("A"), fade))
    return art

def option_rows_art(scale: int, squeeze: bool) -> Image.Image:
    """The sheet of the added Game Settings rows, one OPTION_ROWS text per band, white for the runtime to tint."""
    art = Image.new("RGBA", (OPTION_W * scale, len(OPTION_ROWS) * OPTION_BAND * scale))
    with tempfile.TemporaryDirectory() as tmp:
        for k, text in enumerate(OPTION_ROWS):
            room = OPTION_NARROW.get(text, OPTION_ROOM)
            geometry = ((OPTION_W - room) / 2, (OPTION_W + room) / 2, OPTION_BASELINES[k >= OPTION_LABELS])
            label, metrics = button_label(text, scale, Path(tmp) / f"{k}.png", TRACK_AUTHORED)
            label, metrics = squeezed_label(label, metrics, geometry, scale, squeeze)
            x, y = row_offset(geometry, metrics, scale, "full")
            band = Image.new("RGBA", (art.width, OPTION_BAND * scale))
            band.paste(label, (x, y))
            art.paste(band, (0, k * OPTION_BAND * scale))
    return art

def hires_sheet(art: Image.Image, stock_w: int, band_h: int, per_col: int, scale: int) -> Image.Image:

    bands = art.height // (band_h * scale)
    per_col = per_col or bands  
    cols, col_h = -(-bands // per_col), per_col * band_h * scale
    canvas = Image.new("RGBA", tuple(1 << (n - 1).bit_length() for n in (cols * stock_w * scale, col_h)))
    for b in range(bands):
        row = art.crop((0, b * band_h * scale, art.width, (b + 1) * band_h * scale))
        canvas.paste(row, ((b // per_col) * stock_w * scale, (b % per_col) * band_h * scale))
    return canvas

def plate_cover(r: int, g: int, b: int, a: int) -> float:

    return (220 - (r + g + b) / 3) / 180 if a > 16 else 0.0

def native_plate(g1t: G1tFile, image: Image.Image) -> G1tFile:

    if len(g1t.textures) != 1 or g1t.textures[0].mipmap_count != 1:
        raise ValueError("a native plate needs one single-mip texture")
    texture = g1t.textures[0]
    if image.size != (texture.width, texture.height):
        raise ValueError("native plate pixels must keep their original dimensions")
    header = G1T_TEXTURE_HEADER.pack(0x10, 1,
                                    (texture.width.bit_length() - 1) | (texture.height.bit_length() - 1) << 4,
                                    texture.flags)
    changed = bytearray(g1t.source[:texture.offset] + header + image.tobytes("raw", "BGRA"))
    struct.pack_into("<I", changed, 8, len(changed))
    return parse_g1t(bytes(changed))

def plate_lettering(served: Image.Image, badge: bool = False) -> tuple[Image.Image, Image.Image, tuple]:

    cover = (lambda r, g, b, a: min(r, g, b) / 255 if a > 204 else 0) if badge else plate_cover
    fill = Image.new("L", served.size)
    fill.putdata([255 if round(255 * min(max(cover(*p), 0), 1)) >= 64 else 0
                  for p in served.get_flattened_data()])
    box = fill.getbbox()
    box = (max(0, box[0] - 1), max(0, box[1] - 1),
           min(served.width, box[2] + 1), min(served.height, box[3] + 1))
    
    near = fill.filter(ImageFilter.MaxFilter(7))
    stripe, mask = Image.new("RGBA", (1, served.height)), Image.new("L", served.size)
    ImageDraw.Draw(mask).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=255)
    for y in range(served.height):
        row = [served.getpixel((x, y)) for x in range(served.width)]
        edge = max((x for x, p in enumerate(row) if p[3] > 16), default=0)

        background = [p for x, p in enumerate(row) if 0 < x < edge - 1 and not near.getpixel((x, y))
                      and (p[3] > 204 and min(p[:3]) < 64 and max(p[:3]) - min(p[:3]) > 30
                           if badge else 16 < p[3] < 250 and min(p[:3]) > 170)]
        body = mode(background) if background else (0, 0, 0, 0)
        stripe.putpixel((0, y), body)
        for x, p in enumerate(row):
            if not (x > 0 and near.getpixel((x, y)) and p[3] > 16 and p != body):
                continue

            ring = (min(p[:3]) > min(body[:3]) + 8 if badge
                    else x < edge - 1 and p[3] >= body[3] - 3)
            if ring:
                mask.putpixel((x, y), 255)
        if not badge:

            for x in range(served.width):
                if (y == 0 or x == 0 or x >= edge - 1 or row[x][3] < body[3] - 3
                        or x == 1 and row[x][3] < 250):
                    mask.putpixel((x, y), 0)
    return stripe, mask, box

def plate_art(entry: int, item: int, g1t, edit: dict, text: str, scale: int,
              squeeze: bool = False, donors: dict | None = None) -> tuple:

    texture, style = g1t.textures[0], DARK_PLATE[(entry, item)]
    served = rgba(texture)
    spec = X360_PLATES.get((entry, item)) if donors is not None else None
    if spec:
        ens, member, index = spec.reference
        served = rgba(donors[("wo2", ens)][member].textures[index])
    badge = (entry, item) == (4094, 16)
    cover = (lambda r, g, b, a: min(r, g, b) / 255 if a > 204 else 0) if badge else plate_cover
    geometry = row_geometry(served, cover)
    if (entry, item) in TIME_LABEL_LAYOUT:
        _, left, baseline = TIME_LABEL_LAYOUT[(entry, item)]
        geometry = (left, left, baseline)
    piece = edit["rows"][0][0]
    options = menu_render_options(entry, item, 0, piece, style)
    with tempfile.TemporaryDirectory() as tmp:
        label, metrics = header_label(text, scale, Path(tmp) / "label.png", MENU_RASTER, *options)
    label, metrics = squeezed_label(label, metrics, geometry, scale, squeeze)
    if badge:
        alpha = label.getchannel("A")
        label = Image.new("RGBA", label.size, (255, 255, 255, 0))
        label.putalpha(alpha)
    x, y = row_offset(geometry, metrics, scale, "left")
    if (entry, item) in TIME_LABEL_LAYOUT:
        y = round(y)
    right = (x + label.width) / scale
    old_right = ink(served)[2]
    width = max(old_right, 4 * math.ceil((right + (32 if spec else 29)) / 4))
    if badge:
        width = max(old_right, 4 * math.ceil((right + 12) / 4))
    if spec:
        canvas_width = 1 << (max(texture.width, served.width, width) - 1).bit_length()
        background = None
        if getattr(spec, "cleanup_reference", None):
            ens, member, index = spec.cleanup_reference
            background = rgba(donors[("wo2", ens)][member].textures[index])
        clean, mask, cleanup = clean_substrate(
            served, canvas_size=(canvas_width, served.height), minimum_right=width,
            background_reference=background, left_bevel=getattr(spec, "left_bevel", 2),
            restore_fade=getattr(spec, "restore_fade", False),
        )
        if (texture.width, texture.height) != clean.size:
            g1t = g1t.with_texture_resized(0, *clean.size, bytes(4 * canvas_width * served.height))
        native = native_plate(g1t, clean)
        overlay = Image.new("RGBA", (canvas_width * scale, served.height * scale))
        if x + metrics["ink"][0] < 0 or x + metrics["ink"][2] > overlay.width:
            raise ValueError(f"{entry}.{item}: full-width vector leaves its X360 plate")
        overlay.paste(label, (x, y))
        overlay.info["plate"] = dict(reference=list(spec.reference), role=spec.role,
                                    text=text, **cleanup)
        return native, overlay, width
    if width > served.width:
        raise ValueError(f"{entry}.{item}: full-width plate needs a {width}-wide texture")
    stripe, mask, _ = plate_lettering(served, badge)
    clean = served.copy()
    clean.paste(stripe.resize(served.size, Image.Resampling.NEAREST), (0, 0), mask)
    if width > old_right:
        end = 12 if badge else 29
        slant = clean.crop((old_right - end, 0, old_right, served.height))
        clean.paste(stripe.resize((width - old_right, served.height), Image.Resampling.NEAREST),
                    (old_right - end, 0))
        clean.paste(slant, (width - end, 0))
    g1t = native_plate(g1t, clean)
    overlay = Image.new("RGBA", (served.width * scale, served.height * scale))
    if x + metrics["ink"][0] < 0 or x + metrics["ink"][2] > overlay.width:
        raise ValueError(f"{entry}.{item}: full-width vector leaves its plate texture")
    overlay.paste(label, (x, y))
    return g1t, overlay, width

def native_menu_art(entry: int, item: int, served: Image.Image, mapping: dict,
                    donors: dict, scale: int, squeeze: bool = False) -> Image.Image:

    edit = next((e for e in mapping["textures"]
                 if (e["entry"], e.get("item")) == (entry, item)
                 or [entry, item] in e.get("also", [])), None)
    if edit is None:
        edit = next(e for e in mapping["textures"] if e["entry"] == entry
                    and 0 < item - e["item"] < e.get("count", 1))
        edit = dict(edit, donor=[edit["donor"][0], edit["donor"][1] + item - edit["item"], 0])
    edit = dict(edit, entry=entry, item=item)
    layout = native_menu_layout(edit, donors)
    options = ["--style", layout["style"]]
    if layout["role"]:
        options += ["--role", layout["role"]]
    with tempfile.TemporaryDirectory() as tmp:
        label, metrics = header_label(layout["text"], scale, Path(tmp) / "native.png", MENU_RASTER, *options)
    if squeeze:
        label, metrics = squished_native_label(dict(donors, squish=True), layout["style"], layout["text"],
                                               layout["role"], entry=entry, item=item,
                                               room=layout["sampled_window"][2], scale=scale,
                                               label=label, metrics=metrics)
    art = Image.new("RGBA", (served.width * scale, served.height * scale))
    x = round(layout["left"] * scale - metrics["ink"][0])
    y = round(layout["baseline"] * scale - metrics["baseline"])
    box = label.getbbox()
    sampled_height = layout["sampled_window"][3] * scale

    vertical_shift = max(0, y + box[3] - sampled_height) if box else 0
    y -= vertical_shift
    if box and (x + box[0] < 0 or y + box[1] < 0 or y + box[3] > sampled_height):
        raise ValueError(f"{entry}.{item}: complete native overlay leaves its sampled crop")
    art.alpha_composite(label, (x, y))

    box = art.getbbox()

    fits = not box or box[2] <= layout["sampled_window"][2] * scale
    natural_minimum = 0 if fits else 4 * math.ceil((box[2] / scale + 2) / 4)
    art.info["minimum"] = max(natural_minimum, ABILITY_QUAD_MINIMUM.get((entry, item), 0))
    art.info["native_layout"] = dict({k: v for k, v in layout.items() if k != "label"}, hires_placement={
        "scale": scale, "x": x, "y": y, "vertical_shift": vertical_shift,
        "sampled_height": sampled_height, "alpha_bbox": list(box) if box else None,
    })
    return art

def _now(function, *args) -> Future:
    done = Future()
    done.set_result(function(*args))
    return done


def hires_bin(entries: Path, source: Path | None, vector: tuple | None = None,
              squeeze: bool = False, jobs: int = 1) -> bytes:
    """The large-menu sheets. With the native codecs (they let go of the GIL) the texture encoding and the
    LZP2 packing of each sheet run on `jobs` threads while the next sheet is drawn; the bytes are the same."""
    if not (native.module and jobs > 1):
        return _hires_bin(entries, source, vector, squeeze, jobs, _now)
    with ThreadPoolExecutor(jobs) as pool:
        return _hires_bin(entries, source, vector, squeeze, jobs, pool.submit)


def _sheet_g1t(g1t, size, pixels: bytes) -> bytes:
    return rekeyed(g1t.with_texture_resized(0, *size, pixels)).encode()


def _hires_bin(entries: Path, source: Path | None, vector: tuple | None, squeeze: bool, jobs: int, later) -> bytes:
    rows, packages, parsed = [], {}, {}
    plate_sources = entries.parent.parent / "vector-plate-sources"
    if vector:
        plate_sources.mkdir(parents=True, exist_ok=True)
    extras = list(DARK) + (sorted(HIRES_NATIVE_MENU) if vector else [])
    plates = HIRES + [(e, i, 2, 0, 0, 0, 0) for e, i in extras]
    for number, (entry, item, scale, w, h, band, per_col) in enumerate(plates):
        report_progress("art", "drawing the large menu plates", 0.65 + 0.3 * number / len(plates),
               (number, len(plates)))
        if entry not in parsed:  # decode each entry once, however many of its items are used
            data = (entries / f"{entry}.bin").read_bytes()
            parsed[entry] = parse_texture_package(parse_lzp2(data).decoded if data[:4] == b"LZP2" else data)
        package = parsed[entry]
        g1t = package.items[item].g1t
        if vector and (entry, item) in DARK_PLATE:
            original = plate_sources / f"{entry}.{item}.g1t"
            if not original.exists():
                original.write_bytes(g1t.encode())
            g1t = parse_g1t(original.read_bytes())
        served = rgba(g1t.textures[0])
        if not w:  
            w, h, band = served.width, served.height, served.height
        if served.width != w or served.height < h:
            raise ValueError(f"served {entry}.{item} is {served.size}, not {w} x at least {h}")
        if source:
            art = Image.open(source / f"{entry}.{item}.png").convert("RGBA")
            if art.width != w * scale or art.height < h * scale:
                raise ValueError(f"{entry}.{item}.png is {art.size}, not {w * scale} x at least {h * scale}")
        elif (entry, item) in PAGE_LABEL and vector:
            art = page_label_art(scale, w, served, squeeze)
        elif (entry, item) in MAIN and vector:
            art = main_art(served, scale, MAIN[(entry, item)], squeeze)
        elif vector:
            mapping, donors = vector
            if (entry, item) in HIRES_NATIVE_MENU:
                art = native_menu_art(entry, item, served, mapping, donors, scale, squeeze)
                details = entries.parent.parent / "native-menu-layout"
                details.mkdir(parents=True, exist_ok=True)
                (details / f"{entry}.{item}.json").write_text(
                    json.dumps(art.info["native_layout"], indent=2) + "\n", encoding="utf-8")
                edit = None
            else:
                art = None
            edit = None if (entry, item) in HIRES_NATIVE_MENU else next(
                (e for e in mapping["textures"] if (e["entry"], e.get("item")) == HIRES_TWIN.get((entry, item), (entry, item))
                 and ("rows" in e or "draw" in e or (entry, item) in HEADER)), None)
            if (entry, item) in HIRES_NATIVE_MENU:
                pass
            elif (entry, item) in VALUE:
                edit = rows_edit(entry, item, mapping, served, look=VALUE[(entry, item)])
            elif (entry, item) in DARK:
                edit = rows_edit(entry, item, mapping, served, style=DARK[(entry, item)])
            elif not edit:  
                continue
            elif (entry, item) in HEADER:
                edit = header_edit(edit, mapping, served)
            texts = hires_texts(edit, mapping, donors) if edit else []
            if (entry, item) in HIRES_NATIVE_MENU:
                pass
            elif (entry, item) in DARK_PLATE:
                clean, art, minimum = plate_art(entry, item, g1t, edit, texts[0], scale, squeeze, donors)
                packages[entry] = packages.get(entry, package).with_g1t(item, rekeyed(clean))
                w, h = clean.textures[0].width, clean.textures[0].height
                if "plate" in art.info:
                    details = entries.parent.parent / "plate-cleanup"
                    details.mkdir(parents=True, exist_ok=True)
                    (details / f"{entry}.{item}.json").write_text(json.dumps(art.info["plate"], indent=2) + "\n", encoding="utf-8")
            else:
                art = vector_art(edit, served, texts, scale, VECTOR_WINDOW.get((entry, item)), squeeze)
        else:
            art = served.resize((served.width * scale, served.height * scale), Image.Resampling.NEAREST)
        growth = art.info.get("right_growth", 0)
        if growth:
            w += growth
            scale |= HIRES_RIGHT_GROW
        else:

            w = max(w, art.info.get("minimum", 0))
        numeric_scale = scale & 0x3FFFFFFF
        art = art.crop((0, 0, w * numeric_scale, h * numeric_scale))
        sheet = hires_sheet(art, w, band, per_col, numeric_scale)
        if (entry, item) in DARK_PLATE and vector:
            scale |= HIRES_OVERLAY
        else:
            minimum = 0
        if (entry, item) in (set(DARK) - set(DARK_PLATE)) | HIRES_NATIVE_MENU:
            box = art.getbbox()
            minimum = art.info.get("minimum", 4 * math.ceil((box[2] / numeric_scale + 2) / 4) if box else 0)
            if vector and (entry, item) in ATTRIBUTE_QUAD_MINIMUM:
                minimum = max(minimum, ATTRIBUTE_QUAD_MINIMUM[entry, item])
        rows.append((entry, item, scale, w, h, band | (minimum << 16), per_col,
                     later(_sheet_g1t, g1t, sheet.size, sheet.tobytes())))
    if vector and (4069, 2) in {plate[:2] for plate in plates}:  # entry 0: no stock texture, the runtime draws it
        sheet = hires_sheet(option_rows_art(2, squeeze), OPTION_W, OPTION_BAND, 0, 2)
        rows.append((0, 0, 2, OPTION_W, len(OPTION_ROWS) * OPTION_BAND, OPTION_BAND, 0,
                     later(_sheet_g1t, parsed[4069].items[2].g1t, sheet.size, sheet.tobytes())))
    encoded = {entry: package.encode() for entry, package in packages.items()}
    packed = [entry for entry in encoded if (entries / f"{entry}.bin").read_bytes()[:4] == b"LZP2"]
    if jobs > 1 and len(packed) > 1 and not native.module:  # the Python encoder is slow: one entry per process
        with worker_pool(min(jobs, len(packed))) as pool:
            results = dict(zip(packed, pool.map(packed_lzp2, [encoded[entry] for entry in packed]), strict=True))
    else:
        packing = {entry: later(packed_lzp2, encoded[entry]) for entry in packed}
        results = {entry: done.result() for entry, done in packing.items()}
    for entry, data in encoded.items():
        (entries / f"{entry}.bin").write_bytes(results.get(entry, data))
    return hires_blob([(*row[:-1], row[-1].result()) for row in rows])

# Shares of the art stage (measured): loading the donors 0 to 0.5, the entries to 0.65, the large plates to 0.95.
LINKDATA2_FIRST = 4251  

def archive(key: int) -> tuple[Path, object]:

    if key < LINKDATA2_FIRST:
        return SHARED["base_bin"], SHARED["index"].entries[key]
    return SHARED["ld2_bin"], SHARED["ld2_index"].entries[key - LINKDATA2_FIRST]

def edited_lone(data: bytes, edits: list[dict], donors: dict, preview: Path | None = None) -> bytes:

    out = bytearray(data)
    for edit in edits:
        at = edit["g1t_at"]
        stock = parse_g1t(data[at:at + struct.unpack_from("<I", data, at + 8)[0]])
        g1t = rekeyed(stock.with_texture_payload(edit["texture"], payload(stock.textures[edit["texture"]], edit, donors, None)))
        encoded = g1t.encode()
        if len(encoded) != len(stock.encode()):
            raise ValueError(f"G1T at {at} changed size: an in-place edit keeps the stock geometry")
        out[at:at + len(encoded)] = encoded
        if preview:
            preview_png(Path(f"{preview}.{at}.{edit['texture']}.png"), stock.textures[edit["texture"]], g1t.textures[edit["texture"]])
    return bytes(out)

def rebuilt(ordinal: int) -> tuple[int, bytes, int]:

    group = SHARED["edits"][ordinal]
    preview = SHARED["preview"] and SHARED["preview"] / str(ordinal)
    edit = edited_lone if any("g1t_at" in e for e in group) else edited_entry
    base_bin, entry = archive(ordinal)
    with base_bin.open("rb") as stream:
        if entry.record.allocated_bytes:  
            members = read_members(stream, entry)
            data = b"".join(parse_lzp2(m).decoded for m in members)
            if ordinal == FONT_ENTRY:
                data = latin_font(data, SHARED["donors"]["latin"])
            else:
                data = edit(data, group, SHARED["donors"], preview)
            return ordinal, data if SHARED["write"] else b"", len(group)
        data, packed = read_entry(stream, entry)
    data = edit(data, group, SHARED["donors"], preview)
    if not SHARED["write"]:  
        return ordinal, b"", len(group)
    return ordinal, packed_lzp2(data) if packed else data, len(group)

def font_environment() -> dict[str, str]:
    environment = os.environ.copy()
    package_root = str(Path(__file__).resolve().parents[2])
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (package_root, environment.get("PYTHONPATH"))))
    return environment

def donor_stem(stem: Path | None, image: Path | None, identity, destination: Path) -> Path:
    if stem is not None:
        stem = Path(stem)
    elif image is not None:
        extract_donor(image, identity, destination,
                      files=("linkdata_ens.idx", "linkdata_ens.lnk"))
        stem = destination / "linkdata_ens"
    else:
        raise ValueError(f"give the {identity.name} donor archive or disc image")
    for suffix in (".idx", ".lnk"):
        name = "linkdata_ens" + suffix
        member = next(member for member in identity.files if member.path == name)
        verify_file(stem.with_suffix(suffix), member)
    return stem

def main(argv: list[str] | None = None) -> int:
    global FONT_PYTHON, LZP2_CACHE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wo1-donor", type=Path, default=os.environ.get("WOZMOD_WO1_DONOR"),
                        help="WO1 PC Europe linkdata_ens path without its .lnk/.idx suffix")
    parser.add_argument("--wo1-x360-donor", type=Path, default=os.environ.get("WOZMOD_WO1_X360_DONOR"),
                        help="WO1 Xbox 360 USA linkdata_ens path without its .lnk/.idx suffix")
    parser.add_argument("--latin-cells", type=Path, default=os.environ.get("WOZMOD_LATIN_CELLS"),
                        help="24-pixel vector Latin cells; generated when omitted")
    parser.add_argument("--wo2-donor", type=Path, default=os.environ.get("WOZMOD_X360_DONOR"),
                        help="WO2 Xbox 360 USA linkdata_ens path without its .lnk/.idx suffix")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--jobs", type=int, default=os.process_cpu_count(), help="entries rebuilt in parallel")
    parser.add_argument("--preview-dir", type=Path,
                        help="write <entry>.<item>.<texture>.png stock|edited sheets of every edited texture")
    parser.add_argument("--entries", type=int, nargs="+",
                        help="rebuild only these entries for --preview-dir; writes no entries")
    parser.add_argument("--hires", help="write wozmod/hires.bin: 'vector' (rows lettered by the vector "
                        "faces), 'fixture' (2x nearest copy of the served art, for plumbing tests only) or a "
                        "directory of <entry>.<item>.png sheets")
    parser.add_argument("--hires-only", action="store_true", help="with --hires: the entries are already built")
    parser.add_argument("--variant", action="append", default=[], metavar="NAME=VALUE",
                        help="review lettering: help=per-screen|condensed|wide|condensed-camp; "
                        "camp=x360|wo1 (repeat to combine; defaults are per-screen and x360)")
    parser.add_argument("--no-squish", action="store_true",
                        help="developer oracle only: keep full-width vector lettering")
    parser.add_argument("--game", type=Path, default=os.environ.get("WOZMOD_GAME"))
    parser.add_argument("--wo1-iso", type=Path, default=os.environ.get("WOZMOD_WO1_ISO"))
    parser.add_argument("--wo1-x360-iso", type=Path, default=os.environ.get("WOZMOD_WO1_X360_ISO"))
    parser.add_argument("--wo2-iso", type=Path, default=os.environ.get("WOZMOD_X360_ISO"))
    parser.add_argument("--font-python", default=FONT_PYTHON)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args(argv)
    FONT_PYTHON = args.font_python
    working = args.output_dir or args.preview_dir
    if working is not None:
        LZP2_CACHE = args.cache_dir or working / "lzp2-cache"
    try:
        set_review_variants(args.variant)
    except ValueError as error:
        parser.error(str(error))
    if args.variant and args.hires != "vector":
        parser.error("--variant needs --hires vector")
    args.squeeze = not args.no_squish
    if (bool(args.entries) == bool(args.output_dir)) or (args.entries and not args.preview_dir):
        parser.error("give --output-dir, or --entries with --preview-dir (a partial build is never written)")
    if args.hires_only and not (args.hires and args.output_dir):
        parser.error("--hires-only needs --hires and --output-dir")
    if not args.hires_only and args.game is None:
        parser.error("give --game or WOZMOD_GAME")
    if working is not None and (not args.hires_only or args.hires == "vector"):
        try:
            args.wo1_donor = donor_stem(args.wo1_donor, args.wo1_iso, WO1, working / "donors/wo1")
            args.wo1_x360_donor = donor_stem(args.wo1_x360_donor, args.wo1_x360_iso, WO1_X360,
                                             working / "donors/wo1-x360")
            args.wo2_donor = donor_stem(args.wo2_donor, args.wo2_iso, X360, working / "donors/wo2-x360")
        except ValueError as error:
            parser.error(str(error))
    if args.hires_only:
        write_hires(args)
        return 0
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    edits: dict[int, list[dict]] = {}
    for edit in mapping["textures"]:  
        for ordinal, step in expanded(edit):
            edits.setdefault(ordinal, []).append(step)
    stages = mapping["stage_names"]
    for strips in stages["strips"]:
        edits.setdefault(strips["entry"], []).extend(
            {"entry": strips["entry"], "item": strips["item"] + k, "texture": 0, "stage_name": name,
             **({"size": strips["size"]} if "size" in strips else {})}
            for k, name in enumerate(stages["names"][:strips["count"]]))
    titles = stages["titles"]
    edits.setdefault(titles["entry"], []).extend(
        {"item": k, "texture": 0, "stage_title": name, "baseline": titles["baseline"],
         **({"wo1": [titles["donor"], stages["rows"].index(name), 0]} if name in stages["rows"] else {})}
        for k, name in enumerate(stages["names"]))
    flat = [e for group in edits.values() for e in group]
    flat += [piece for e in flat for piece in [*sum(e.get("rows", []), []), *e.get("draw", []), e.get("plate"),
                                               e.get("banner", {}).get("badge"), e.get("card")] if piece]
    flat += [source for face in mapping.get("faces", {}).values() for source in face["rows"]]
    edits[FONT_ENTRY] = []
    if args.entries:  
        if missing := set(args.entries) - edits.keys():
            parser.error(f"entries without edits: {sorted(missing)}")
        edits = {ordinal: edits[ordinal] for ordinal in args.entries}
    donors = wo1_donors(args, {e["donor"][0] for e in flat if "donor" in e} | {stages["font"][0], titles["donor"]})
    if lettering.ACTIVE is not None:
        donors["latin"] = lettering.get(dict(kind="latin"), None)[0]
    elif args.latin_cells is None:
        args.latin_cells = working / "latin-cells.png"
        args.latin_cells.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([FONT_PYTHON, "-m", "wozmod.fonts.raster", str(args.latin_cells), "--px", "24", "--spacing", "legacy"],
                       check=True, env=font_environment(), stdout=subprocess.PIPE, text=True)
    if lettering.ACTIVE is None:
        if not args.latin_cells.is_file():
            parser.error(f"missing Latin cells: {args.latin_cells}")
        donors["latin"] = lettering.get(dict(kind="latin"),
                                       lambda: (Image.open(args.latin_cells).convert("L"), {}))[0]
    donors |= {("wo2", k): wo2_g1ts(v) for k, v in wo1_entries(
        args.wo2_donor.with_suffix(".lnk"), args.wo2_donor.with_suffix(".idx"),
        sorted({e["wo2"][0] for e in flat if "wo2" in e}
               | {s["wo2"][0] for e in flat for s in e.get("draw", []) if "wo2" in s}
               | {3} | native_reference_entries(mapping))).items()}
    donors["squish"] = args.squeeze
    stock = args.game
    base_bin = stock / "LINKDATA1.BIN"
    index = parse_index((stock / "LINKDATA1.IDX").read_bytes(), base_bin.stat().st_size)
    stored: dict[int, bytes] = {}
    with base_bin.open("rb") as stream:
        donors |= {("moz", k): donor_g1ts(read_entry(stream, index.entries[k])[0])
                   for k in sorted({e[key][0] for e in flat for key in ("against", "base") if key in e})}
    donors["faces"] = faces(mapping, donors, args.jobs)
    donors["menu_labels"] = prepare_menu_labels(
        mapping, (args.output_dir / "review/native-menu-labels" if args.output_dir
                  else args.preview_dir / "native-menu-labels"))
    donors["menu_sound_rows"] = {item: sound_menu_rows(mapping, item) for item in (23, 24, 25)}
    if args.preview_dir:
        args.preview_dir.mkdir(parents=True, exist_ok=True)
    if lettering.ACTIVE is None:
        native_width_path = (args.output_dir / "review/native-menu-widths.json" if args.output_dir
                             else args.preview_dir / "native-menu-widths.json")
        native_width_path.parent.mkdir(parents=True, exist_ok=True)
        with lettering.untracked():
            report = native_menu_widths(mapping, donors)
        native_width_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ld2_bin = stock / "LINKDATA2.BIN"
    SHARED.update(base_bin=base_bin, index=index, donors=donors, edits=edits,
                  ld2_bin=ld2_bin, ld2_index=parse_index((stock / "LINKDATA2.IDX").read_bytes(), ld2_bin.stat().st_size),
                  preview=args.preview_dir, write=bool(args.output_dir))

    with worker_pool(args.jobs) as pool:
        futures = [pool.submit(rebuilt, ordinal)
                   for ordinal in sorted(edits, key=lambda ordinal: -archive(ordinal)[1].stored_bytes)]
        try:
            for future in as_completed(futures):
                ordinal, data, count = future.result()
                stored[ordinal] = data
                report_progress("art", "building menu textures", 0.5 + 0.15 * len(stored) / len(edits),
                                (len(stored), len(edits)))
                print(f"entry {ordinal}: {count} textures, {archive(ordinal)[1].stored_bytes} -> {len(data)} bytes",
                      flush=True)
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    if not args.output_dir:
        print(f"{args.preview_dir}: previews of {len(stored)} entries")
        return 0
    entries = args.output_dir / "wozmod/entries"
    entries.mkdir(parents=True, exist_ok=True)
    for ordinal, data in stored.items():
        (entries / f"{ordinal}.bin").write_bytes(data)
    with base_bin.open("rb") as stream:
        prompts = read_entry(stream, index.entries[PROMPT_ENTRY])[0]
    wo2_prompts = wo1_entries(args.wo2_donor.with_suffix(".lnk"), args.wo2_donor.with_suffix(".idx"), [13])[13]
    (args.output_dir / "wozmod/prompts-xbox.bin").write_bytes(xbox_prompts(prompts, wo2_g1ts(wo2_prompts)))
    outlines = working / "glyph-outlines.bin"
    subprocess.run([FONT_PYTHON, "-m", "wozmod.fonts.glyph_outlines", str(outlines)], check=True,
                   env=font_environment(), stdout=subprocess.PIPE, text=True)
    with base_bin.open("rb") as stream:
        atlas = b"".join(parse_lzp2(m).decoded for m in read_members(stream, index.entries[FONT_ENTRY]))
    (args.output_dir / "wozmod/glyphs.bin").write_bytes(glyphs_bin(atlas, outlines.read_bytes()))
    if args.hires:
        write_hires(args)
    write_manifest(args.output_dir)
    print(f"{args.output_dir}: {len(stored)} entries")
    return 0

def write_hires(args) -> None:
    selected = set_review_variants(getattr(args, "variant", None))
    source = None if args.hires in ("fixture", "vector") else Path(args.hires)
    vector = None
    if args.hires == "vector":
        mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
        if not args.hires_only:
            cache = args.output_dir / "vector-plate-sources"
            for old in cache.glob("*.g1t"):
                old.unlink()
        pieces = [p for e in mapping["textures"] if (e["entry"], e.get("item")) in {h[:2] for h in HIRES} | set(DARK)
                  for row in e.get("rows") or [e.get("draw", [])] for p in row]
        donors = wo1_donors(args, {p["donor"][0] for p in pieces if "box" in p and "donor" in p} | {1, 16})
        donors |= {("wo2", k): x360_members(v) for k, v in wo1_entries(
            args.wo2_donor.with_suffix(".lnk"), args.wo2_donor.with_suffix(".idx"),
            sorted(set(X360_PLATE_ENTRIES) | native_reference_entries(mapping))).items()}
        donors["menu_labels"] = prepare_menu_labels(mapping)
        donors["squish"] = args.squeeze
        vector = mapping, donors
    (args.output_dir / "wozmod/hires.bin").write_bytes(hires_bin(
        args.output_dir / "wozmod/entries", source, vector, args.squeeze, getattr(args, "jobs", 1)))
    (args.output_dir / "review-variant.json").write_text(json.dumps({
        "schema": "moz-menu-review-variant-v1", "selection": selected,
        "runtime_edits": "shared", "geometry": "per-bank minima and right-anchor growth",
    }, indent=2) + "\n", encoding="utf-8")
    write_manifest(args.output_dir)
    print(f"{args.output_dir}: hires.bin ({args.hires})")

if __name__ == "__main__":
    raise SystemExit(main())
