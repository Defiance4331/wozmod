"""Button prompt art for entry 4079, drawn from our own geometry: a modern Xbox set and a modern PlayStation set.

`prompt_art(family)` returns every swapped icon slot at the size the game's texture has. Every shape and letter
here is authored in this file (a small stroke alphabet, discs, pills, arrows), drawn SS times larger and averaged
down, so it runs on the player's machine with Pillow alone. Coordinates are in 1x pixels of the 32x32 cell.

Each icon is designed in a 32-unit cell centred on 16. The game shows only texels 0-29 of a 32-texel slot (it draws
a 30x30 quad with UVs 0 to 30, 0x549AD0), so the canvas maps the design onto that window: 30/32 of a texel per unit,
centred on texel 15, and the last two texels stay clear. `prompt_art(family, scale)` renders the textures `scale`
times larger, for the runtime's sharper copies on large displays. A wide (64x32) slot holds the same icon as a square
slot, at its left edge (texels 0-29), the rest clear.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

SS = 8  # supersampling factor
CELL = 32  # design units of an icon, and texels of a slot
WINDOW = 30  # texels of a 32-texel slot the game shows

# slot -> texture width; every slot is 32 tall
SLOT_WIDTH = {**{slot: 32 for slot in range(16)}, 3: 64, 13: 64, 14: 64, 15: 64, 178: 64, 179: 64}

BODY = (30, 31, 35, 255)  # button body
RIM = (150, 153, 162, 255)  # thin light edge, for the dark prompt bar
INK = (244, 244, 246, 255)  # labels and lit parts
DIM = (78, 81, 90, 255)  # unlit D-pad arms, stick rings
HALO = (0, 0, 0, 150)  # dark edge outside each icon, for bright menu art

XBOX_FACE = {"A": (108, 210, 74, 255), "B": (255, 84, 84, 255), "X": (66, 156, 255, 255), "Y": (255, 203, 46, 255)}
PS_FACE = {
    "triangle": (47, 216, 180, 255),
    "circle": (255, 92, 112, 255),
    "cross": (120, 170, 255, 255),
    "square": (240, 124, 216, 255),
}


def _arc(cx, cy, rx, ry, start, end, steps=24):
    """Points along an ellipse from angle start to end in degrees (y down, so 90 is the bottom)."""
    return [
        (cx + rx * math.cos(math.radians(start + (end - start) * i / steps)),
         cy + ry * math.sin(math.radians(start + (end - start) * i / steps)))
        for i in range(steps + 1)
    ]


# Stroke alphabet: per character, its advance and polylines in a box one unit tall (y down).
def _letters() -> dict[str, tuple[float, list[list[tuple[float, float]]]]]:
    return {
        "A": (0.82, [[(0, 1), (0.41, 0), (0.82, 1)], [(0.16, 0.64), (0.66, 0.64)]]),
        "B": (0.66, [[(0, 0), (0, 1)],
                     [(0, 0), (0.36, 0)] + _arc(0.36, 0.24, 0.24, 0.24, -90, 90) + [(0, 0.48)],
                     [(0, 0.48), (0.38, 0.48)] + _arc(0.38, 0.74, 0.26, 0.26, -90, 90) + [(0, 1)]]),
        "X": (0.78, [[(0, 0), (0.78, 1)], [(0.78, 0), (0, 1)]]),
        "Y": (0.8, [[(0, 0), (0.4, 0.52), (0.8, 0)], [(0.4, 0.52), (0.4, 1)]]),
        "L": (0.56, [[(0, 0), (0, 1), (0.56, 1)]]),
        "R": (0.66, [[(0, 1), (0, 0), (0.36, 0)] + _arc(0.36, 0.26, 0.26, 0.26, -90, 90) + [(0, 0.52)],
                     [(0.3, 0.52), (0.66, 1)]]),
        "T": (0.7, [[(0, 0), (0.7, 0)], [(0.35, 0), (0.35, 1)]]),
        "S": (0.62, [_arc(0.31, 0.25, 0.3, 0.25, -25, -270) + _arc(0.31, 0.75, 0.31, 0.25, -90, 155)]),
        "1": (0.42, [[(0.02, 0.2), (0.32, 0), (0.32, 1)]]),
        "2": (0.62, [_arc(0.31, 0.29, 0.3, 0.29, -165, 15) + [(0, 1), (0.62, 1)]]),
        "3": (0.6, [_arc(0.29, 0.25, 0.28, 0.25, -150, 90), _arc(0.29, 0.74, 0.3, 0.26, -90, 150)]),
    }


LETTERS = _letters()


class _Canvas:
    """A supersampled RGBA texture for one icon; every coordinate and width passed in is in design units, which the
    canvas maps onto the window the game shows, `scale` texels per 1x texel."""

    scale = 1  # set by prompt_art for the textures it renders

    def __init__(self, width=CELL, height=CELL):
        self.f = SS * self.scale * WINDOW / CELL  # canvas pixels per design unit
        self.image = Image.new("RGBA", (width * SS * self.scale, height * SS * self.scale), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)

    def disc(self, cx, cy, r, fill):
        f = self.f
        self.draw.ellipse([(cx - r) * f, (cy - r) * f, (cx + r) * f, (cy + r) * f], fill=fill)

    def ring(self, cx, cy, r, width, fill):
        f = self.f
        self.draw.ellipse([(cx - r) * f, (cy - r) * f, (cx + r) * f, (cy + r) * f], outline=fill,
                          width=round(width * f))

    def rounded(self, box, radius, fill):
        f = self.f
        x0, y0, x1, y1 = box
        self.draw.rounded_rectangle([x0 * f, y0 * f, x1 * f, y1 * f], radius=radius * f, fill=fill)

    def polygon(self, points, fill):
        self.draw.polygon([(x * self.f, y * self.f) for x, y in points], fill=fill)

    def stroke(self, points, width, fill, closed=False):
        """A polyline with round joins and caps."""
        f = self.f
        pts = [(x * f, y * f) for x, y in points] + ([(points[0][0] * f, points[0][1] * f)] if closed else [])
        w = width * f
        for a, b in zip(pts, pts[1:], strict=False):
            self.draw.line([a, b], fill=fill, width=round(w))
        for x, y in pts:
            self.draw.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=fill)

    def text(self, word, cx, cy, height, width, fill):
        """A word of the stroke alphabet, centred on (cx, cy), cap height `height`, stroke `width`."""
        gap = 0.3
        total = sum(LETTERS[ch][0] for ch in word) + gap * (len(word) - 1)
        x = cx - total * height / 2
        top = cy - height / 2
        for ch in word:
            advance, lines = LETTERS[ch]
            for line in lines:
                self.stroke([(x + px * height, top + py * height) for px, py in line], width, fill)
            x += (advance + gap) * height

    def corner_box(self, box, big, small, left: bool, fill):
        """Fill box with corners of radius small, except the outer top corner, of radius big."""
        f = self.f
        scaled = [v * f for v in box]
        tight, round_ = Image.new("L", self.image.size, 0), Image.new("L", self.image.size, 0)
        ImageDraw.Draw(tight).rounded_rectangle(scaled, radius=small * f, fill=255)
        ImageDraw.Draw(round_).rounded_rectangle(scaled, radius=big * f, fill=255)
        x0, y0, x1, _ = scaled
        corner = (x0, y0, x0 + big * f, y0 + big * f) if left else (x1 - big * f, y0, x1, y0 + big * f)
        corner = tuple(round(v) for v in corner)
        tight.paste(round_.crop(corner), corner[:2])
        self.image.paste(Image.new("RGBA", self.image.size, fill), (0, 0), tight)

    def finish(self) -> Image.Image:
        """The texture at its size, with a dark halo one 1x texel wide outside the ink."""
        size = (self.image.width // SS, self.image.height // SS)
        out = self.image.convert("RGBa").resize(size, Image.Resampling.BOX).convert("RGBA")
        k = self.scale
        spread = out.getchannel("A").filter(ImageFilter.MaxFilter(2 * k + 1))
        if k > 1:
            spread = spread.filter(ImageFilter.BoxBlur(k / 2))
        halo = Image.new("RGBA", size, HALO[:3] + (0,))
        halo.putalpha(spread.point(lambda v: v * HALO[3] // 255))
        return Image.alpha_composite(halo, out)


C = CELL / 2  # cell centre
DISC_R = 14.2


def _button(c: _Canvas):
    """The round button body every face button, D-pad and stick sits on."""
    c.disc(C, C, DISC_R, RIM)
    c.disc(C, C, DISC_R - 1.2, BODY)


def _xbox_face(letter: str) -> Image.Image:
    c = _Canvas()
    _button(c)
    c.text(letter, C, C, 13.5, 2.8, XBOX_FACE[letter])
    return c.finish()


def _ps_face(symbol: str) -> Image.Image:
    c = _Canvas()
    _button(c)
    colour, w = PS_FACE[symbol], 2.4
    if symbol == "triangle":
        r = 7.6
        pts = [(C + r * math.cos(math.radians(a)), C + 0.9 + r * math.sin(math.radians(a))) for a in (-90, 30, 150)]
        c.stroke(pts, w, colour, closed=True)
    elif symbol == "circle":
        c.ring(C, C, 6.6, w, colour)
    elif symbol == "cross":
        d = 5.2
        c.stroke([(C - d, C - d), (C + d, C + d)], w, colour)
        c.stroke([(C + d, C - d), (C - d, C + d)], w, colour)
    else:
        d = 5.6
        c.stroke([(C - d, C - d), (C + d, C - d), (C + d, C + d), (C - d, C + d)], w, colour, closed=True)
    return c.finish()


def _xbox_dpad(lit: set[str]) -> Image.Image:
    """A cross on a round base; arms in `lit` ("h" left/right, "v" up/down) are white, the rest dim."""
    c = _Canvas()
    _button(c)
    arm, reach = 4.4, 10.2
    bars = {"v": (C - arm, C - reach, C + arm, C + reach), "h": (C - reach, C - arm, C + reach, C + arm)}
    for key in sorted(bars, key=lambda k: k in lit):  # unlit bar first, lit bar over it
        c.rounded(bars[key], 1.6, INK if key in lit else DIM)
    c.disc(C, C, 1.6, BODY)
    return c.finish()


def _ps_dpad(lit: set[str]) -> Image.Image:
    """Four separate arrow keys pointing at the centre, the DualSense way, on the round base; lit keys white."""
    c = _Canvas()
    _button(c)
    half, end, shoulder, tip = 4.4, 11.4, 5.8, 1.8  # the upper key, pointing down at the centre
    arrow = [(-half, -end), (half, -end), (half, -shoulder), (0, -tip), (-half, -shoulder)]
    for axis, angles in (("v", (0, 180)), ("h", (90, 270))):
        for a in angles:
            ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
            c.polygon([(C + x * ca - y * sa, C + x * sa + y * ca) for x, y in arrow], INK if axis in lit else DIM)
    return c.finish()


def _shoulder(label: str, trigger: bool) -> Image.Image:
    """A bumper (wide, low) or a trigger (tall), its outer top corner rounded, with its label."""
    c = _Canvas()
    left = label[0] == "L"
    if trigger:
        box, big, small = (5.5, 2.5, 26.5, 29.5), 9.0, 2.5
    else:
        box, big, small = (2.5, 7.5, 29.5, 24.5), 7.5, 2.5
    for inset, fill in ((0, RIM), (1.2, BODY)):
        c.corner_box((box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset), big - inset, small, left, fill)
    cy = (box[1] + box[3]) / 2 + (1.0 if trigger else 0.3)
    c.text(label, C, cy, 10.0, 2.1, INK)
    return c.finish()


def _stick(label: str, click: bool) -> Image.Image:
    """A thumbstick seen from above, with its label; a click adds a down arrow over the label."""
    c = _Canvas()
    _button(c)
    c.ring(C, C, 10.4, 1.2, DIM)
    if click:
        c.polygon([(C - 4.8, 5.0), (C + 4.8, 5.0), (C, 9.8)], INK)
        c.text(label, C, C + 3.2, 8.6, 2.0, INK)
    else:
        c.text(label, C, C, 9.6 if len(label) > 1 else 11.0, 2.1, INK)
    return c.finish()


def _stick_direction(label: str, direction: str) -> Image.Image:
    """A thumbstick seen from above with its label, and an arrow on the side it is pushed to."""
    c = _Canvas()
    _button(c)
    c.ring(C, C, 10.4, 1.2, DIM)
    dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[direction]
    tip, base, half = 13.6, 8.4, 4.2  # from the centre: the arrow's tip, its base, half its base
    c.polygon([(C + dx * tip, C + dy * tip), (C + dx * base - dy * half, C + dy * base + dx * half),
               (C + dx * base + dy * half, C + dy * base - dx * half)], INK)
    c.text(label, C - dx * 1.6, C - dy * 1.6, 8.0 if len(label) > 1 else 9.6, 1.9, INK)
    return c.finish()


def stick_art(family: str, scale: int = 1) -> list[Image.Image]:
    """The stick-direction icons the Move List draws, 32x32 (times `scale`): the left stick up, down, left, right,
    then the right stick."""
    labels = {"xbox": ("LS", "RS"), "playstation": ("L", "R")}[family]
    _Canvas.scale = scale
    try:
        return [_stick_direction(label, d) for label in labels for d in ("up", "down", "left", "right")]
    finally:
        _Canvas.scale = 1


def _xbox_menu() -> Image.Image:
    c = _Canvas()
    _button(c)
    for y in (C - 4.2, C, C + 4.2):
        c.stroke([(C - 5.4, y), (C + 5.4, y)], 2.0, INK)
    return c.finish()


def _xbox_view() -> Image.Image:
    c = _Canvas()
    _button(c)
    c.rounded((C - 7, C - 7, C + 3, C + 3), 2.0, INK)
    c.rounded((C - 5.2, C - 5.2, C + 1.2, C + 1.2), 1.0, BODY)
    c.rounded((C - 4.2, C - 4.2, C + 7.6, C + 7.6), 2.4, BODY)
    c.rounded((C - 2.6, C - 2.6, C + 6.6, C + 6.6), 2.0, INK)
    return c.finish()


def _ps_pill(kind: str) -> Image.Image:
    """The DualSense Options or Create button: a small upright pill with its mark."""
    c = _Canvas()
    box = (C - 9.6, 2.0, C + 9.6, 30.0)
    c.rounded(box, 9.6, RIM)
    c.rounded((box[0] + 1.2, box[1] + 1.2, box[2] - 1.2, box[3] - 1.2), 8.4, BODY)
    if kind == "options":
        for y in (C - 4.6, C, C + 4.6):
            c.stroke([(C - 4.2, y), (C + 4.2, y)], 1.9, INK)
    else:
        ox, oy = C, C + 6.4
        for a in (-140, -90, -40):
            ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
            c.stroke([(ox + 3.6 * ca, oy + 3.6 * sa), (ox + 11.0 * ca, oy + 11.0 * sa)], 2.0, INK)
    return c.finish()


def _place(icon: Image.Image, width: int) -> Image.Image:
    if width == icon.width:
        return icon
    out = Image.new("RGBA", (width, icon.height), (0, 0, 0, 0))
    out.paste(icon, (0, 0))
    return out


def prompt_art(family: str, scale: int = 1) -> dict[int, Image.Image]:
    """Every swapped 4079 slot for family "xbox" or "playstation", RGBA at `scale` times the slot's texture size."""
    _Canvas.scale = scale
    try:
        return _icons(family)
    finally:
        _Canvas.scale = 1


def _icons(family: str) -> dict[int, Image.Image]:
    if family == "xbox":
        face = {name: _xbox_face(name) for name in "ABXY"}
        dpad = _xbox_dpad
        icons = {
            0: face["B"], 1: face["A"], 2: face["Y"], 3: face["X"],
            7: _shoulder("LB", False), 8: _shoulder("LT", True),
            9: _shoulder("RB", False), 10: _shoulder("RT", True),
            13: _xbox_menu(), 14: _xbox_view(),
            15: _stick("RS", False), 178: _stick("RS", True), 179: _stick("LS", True),
        }
    elif family == "playstation":
        face = {name: _ps_face(name) for name in PS_FACE}
        dpad = _ps_dpad
        icons = {
            0: face["circle"], 1: face["cross"], 2: face["triangle"], 3: face["square"],
            7: _shoulder("L1", False), 8: _shoulder("L2", True),
            9: _shoulder("R1", False), 10: _shoulder("R2", True),
            13: _ps_pill("options"), 14: _ps_pill("create"),
            15: _stick("R", False), 178: _stick("R3", True), 179: _stick("L3", True),
        }
    else:
        raise ValueError(f"unknown prompt family {family!r}")
    icons.update({4: dpad({"h", "v"}), 5: dpad({"h"}), 6: dpad({"v"}), 11: icons[2], 12: icons[3]})
    return {slot: _place(icons[slot], width * _Canvas.scale) for slot, width in SLOT_WIDTH.items()}
