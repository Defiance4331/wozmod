"""Advances for the in-game text font from the shape of each letter's sides.

A side's room is set so the white area between the side's profile and a vertical line, over the letter's body, is
the same for every side: a straight side (n, H) sits flush and gets the full room, a round side (o, c) curves away
and gets less, a diagonal or open side (v, T) gets little. The profile is measured over the body only (the x-height
for lowercase, the cap height for the rest) and each row's depth is capped, so a deep opening (inside c) counts as
a shallow one. Pure: contours in font units (1200 to the 24-unit cell), y up from the baseline.
"""

from __future__ import annotations

ROWS = 48  # profile samples over the body
CELL_UNIT = 50  # font units to one cell unit (one layout px at the game's 24 px text)
CAP_DEPTH = 40  # font units a side's profile may count as depth (deeper openings count as this): lower evens the
# gaps between neighbours, 0 would be one gap for every letter
# English letter frequencies (percent): the room is calibrated so text of this mix keeps the width it had with the
# official WO1 PC advances, so lines, boxes and squish stay close to the game's layout.
FREQUENCY = dict(e=12.7, t=9.1, a=8.2, o=7.5, i=7.0, n=6.7, s=6.3, h=6.1, r=6.0, d=4.3, l=4.0, c=2.8, u=2.8, m=2.4,
                 w=2.4, f=2.2, g=2.0, y=2.0, p=1.9, b=1.5, v=1.0, k=0.8, j=0.15, x=0.15, q=0.1, z=0.07)


def _spans(contours: list[list[tuple[float, float]]], y: float) -> list[float]:
    """x of every crossing of the horizontal line y with the outline."""
    xs = []
    for pts in contours:
        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1], strict=True):
            if (ay <= y < by) or (by <= y < ay):
                xs.append(ax + (y - ay) * (bx - ax) / (by - ay))
    return xs


def side_depths(contours, body: tuple[float, float], cap: float) -> tuple[float, float]:
    """Mean depth of the left and right profiles from the ink's extremes over `body` (y low, y high), each row's
    depth capped at `cap` (a row with no ink counts as `cap`)."""
    xs = [p[0] for c in contours for p in c]
    lo, hi = min(xs), max(xs)
    left = right = 0.0
    for k in range(ROWS):
        y = body[0] + (body[1] - body[0]) * (k + 0.5) / ROWS
        row = _spans(contours, y)
        left += min(cap, min(row) - lo) if row else cap
        right += min(cap, hi - max(row)) if row else cap
    return left / ROWS, right / ROWS


def place(contours, body, cap: float, room: float) -> tuple[int, float]:
    """(advance in cell units, font units from the advance's start to the ink's left edge). Each side's room is
    `room` less its mean depth (at least 0); the advance rounds to whole cell units, the rounding split between
    the sides."""
    xs = [p[0] for c in contours for p in c]
    depth_left, depth_right = side_depths(contours, body, cap)
    left, right = max(0.0, room - depth_left), max(0.0, room - depth_right)
    exact = (max(xs) - min(xs) + left + right) / CELL_UNIT
    advance = max(1, round(exact))
    return advance, left + (advance - exact) * CELL_UNIT / 2


def calibrate(glyphs: dict[str, tuple], weights: dict[str, float], target: float, cap: float) -> float:
    """The room at which the weighted mean advance of `glyphs` (char -> (contours, body)), unrounded, equals
    `target` cell units: typical text keeps its overall width."""
    def mean(room):
        total = 0.0
        for ch, w in weights.items():
            contours, body = glyphs[ch]
            xs = [p[0] for c in contours for p in c]
            dl, dr = side_depths(contours, body, cap)
            total += w * (max(xs) - min(xs) + max(0.0, room - dl) + max(0.0, room - dr)) / CELL_UNIT
        return total / sum(weights.values())
    lo, hi = 0.0, 400.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if mean(mid) < target else (lo, mid)
    return (lo + hi) / 2


def font_spacing() -> dict[str, tuple[int, float]]:
    """char -> (advance, ink's left edge from the advance start, font units) for the in-game text font (wo1vec);
    needs fontforge. Lowercase letters are measured over the x-height, everything else over the cap height."""
    from wozmod.runtime.latin_font import OFFICIAL
    from . import glyph_outlines, raster, wo1vec

    font = wo1vec.make()
    outlines = raster.outlines(font)
    glyphs = {}
    for ch in raster.CHARS:
        if ch.strip() and ch in outlines and outlines[ch][0]:
            contours = [glyph_outlines.flatten(c) for c in outlines[ch][0]]
            glyphs[ch] = (contours, (0, font[ord("x")].boundingBox()[3]) if ch.islower() else
                          (0, font[ord("H")].boundingBox()[3]))
    target = sum(w * OFFICIAL[raster.CHARS.index(c)] for c, w in FREQUENCY.items()) / sum(FREQUENCY.values())
    room = calibrate(glyphs, FREQUENCY, target, CAP_DEPTH)
    return {ch: place(contours, body, CAP_DEPTH, room) for ch, (contours, body) in glyphs.items()}


if __name__ == "__main__":
    from wozmod.runtime.latin_font import OFFICIAL
    from . import raster

    spacing = font_spacing()
    table = bytes(spacing[ch][0] if ch in spacing else OFFICIAL[i] for i, ch in enumerate(raster.CHARS))
    print(table.hex())
