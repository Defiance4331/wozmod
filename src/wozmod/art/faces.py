"""The donor lettering faces: the strokes found in donor art, the Face that sets text from them, and its fills."""
from __future__ import annotations

import unicodedata
from concurrent.futures import as_completed
from statistics import fmean, median, mode

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from wozmod.progress import report as report_progress
from wozmod.donors import WO1_X360, WO2_X360
from .imaging import keyed, rgba, unglowed
from .packing import worker_pool


def text_lines(layer: Image.Image, baselines) -> list[tuple[Image.Image, int]]:

    lines, src = [Image.new("RGBA", layer.size) for _ in baselines], layer.load()
    for piece in strokes(layer, lambda p: p[3] > 0, 1):
        middle = (min(y for _, y in piece) + max(y for _, y in piece)) / 2
        px = lines[next((k for k, b in enumerate(baselines) if middle <= b), len(baselines) - 1)].load()
        for point in piece:
            px[point] = src[point]
    return [(line.crop(line.getbbox()), line.getbbox()[1]) for line in lines if line.getbbox()]

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

def parted(found: list[list[tuple[int, int]]]) -> list[list[tuple[int, int]]]:
    """The strokes with the widest one cut in two where it has the least fill, away from its ends."""
    widest = max(found, key=lambda stroke: max(x for x, _ in stroke) - min(x for x, _ in stroke))
    left, right = min(x for x, _ in widest), max(x for x, _ in widest)
    fill = {x: 0 for x in range(left, right + 1)}
    for x, _ in widest:
        fill[x] += 1
    margin = (right - left) // 5
    cut = min(range(left + margin, right - margin + 1), key=lambda x: (fill[x], abs(2 * x - left - right)))
    return sorted([stroke for stroke in found if stroke is not widest] + [[p for p in widest if p[0] < cut],
                  [p for p in widest if p[0] >= cut]], key=lambda stroke: min(x for x, _ in stroke))

def glyph_fills(row: Image.Image, text: str, is_fill=yellow, least: int = 3,
                counts: dict | None = None) -> list[list[tuple[int, int]]]:

    found, glyphs = strokes(row, is_fill, least), []
    wanted = sum((counts or {}).get(char, 2 if char in TWO_STROKE else 1) for char in text.replace(" ", ""))
    while 0 < len(found) < wanted:  # letters whose serifs touch in the donor art: part the widest at its thinnest column
        found = parted(found)
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
                      for row in face_rows(rgba(donors[titles[WO1_X360.name]][k].textures[0]), [text])])
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


def used_faces(edits) -> set[str]:
    """The faces edits letter with: each piece's "face", and the title face for a stage title."""
    if isinstance(edits, dict):
        found = {edits["face"]} if isinstance(edits.get("face"), str) else set()
        return found | ({"title"} if "stage_title" in edits else set()) | used_faces(list(edits.values()))
    if isinstance(edits, list):
        return set().union(*map(used_faces, edits))
    return set()


def faces(mapping: dict, donors: dict, jobs: int = 1, used: set[str] | None = None) -> dict:
    """The lettering faces (only those in used, when given), one worker process each when jobs > 1 (the longest step
    before the worker pool)."""
    global FACE_WORK
    # a words-only face lends its sheets' words (labels, hi-res rows) and letters nothing, so its art is never read
    names = ["stage", "title", *(name for name, face in mapping.get("faces", {}).items() if not face.get("words_only"))]
    names = [name for name in names if used is None or name in used]
    FACE_WORK = mapping, donors
    report_progress("art", "reading the lettering faces", 0.05, (0, len(names)))
    found = {}
    if jobs > 1 and len(names) > 1:  # an entry may letter with one face or none
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

def source_image(spec: dict, donors: dict, stock: Image.Image) -> Image.Image:

    if "moz" in spec:
        item, index = spec["moz"]
        return rgba(donors["stock"].items[item].g1t.textures[index])
    if WO2_X360.name in spec:
        entry, item, index = spec[WO2_X360.name]
        return rgba(donors[WO2_X360.name, entry][item].textures[index])
    if WO1_X360.name in spec:
        entry, item, index = spec[WO1_X360.name]
        return rgba(donors[entry][item].textures[index])
    return stock

def stock_image(ref, donors: dict) -> Image.Image:

    entry, item, index = ref
    return rgba(donors["moz", entry][item].textures[index])

def gaps(text: str) -> int:

    return len(text) - 1
