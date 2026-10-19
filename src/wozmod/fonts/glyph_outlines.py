"""The in-game text font as outlines for the runtime's own glyph renderer (wozmod/glyphs.bin).

The runtime draws each Latin glyph from these outlines at its exact size on screen, fitting stems and heights to
whole pixels. Per glyph: its placement in the game's 24-unit cell, the vertical stems (straight sides
and the sides of round letters, as left/right x pairs), the horizontal bars (bottom/top y pairs, the round
letters' tops and bottoms among them) and the flattened contours. Heights the renderer fits: the
baseline, x-height and cap height, each with the overshoot of round letters, and the deepest and tallest ink.

All lengths are font units: 1200 to the 24-unit cell, y up from the baseline. `extract` (fontforge) runs under
the font Python and writes the outlines as JSON, which lettering.zip carries to the installer; `from_json` and
`encode` need no fontforge:
    PYTHONPATH=src $WOZMOD_FONT_PYTHON -m wozmod.fonts.glyph_outlines OUT.json
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

MAGIC = b"WZGO"
VERSION = 2
CELL = 1200  # font units to the game's 24-unit glyph cell
TOLERANCE = 1.0  # font units a flattened curve may stray from the true one
EDGE = 1.0  # font units a stem side may lean and still count as vertical
STEM_RANGE = (60, 260)  # font units a stem may be wide (the face's stems are 105 to 133)
STEM_LENGTH = 100  # font units a straight side must run to count as a stem
SAME_WIDTH = 2  # font units between stem widths that are one width


def flatten(contour: list) -> list[tuple[float, float]]:
    """A contour as raster.outlines gives it (a start point, then line or cubic segments) as a closed polyline,
    without the repeated start point."""
    points = [contour[0]]
    for seg in contour[1:]:
        if len(seg) == 3:
            points += _cubic(points[-1], *seg)
        else:
            points.append(seg[-1])
    if len(points) > 1 and points[-1] == points[0]:
        points.pop()
    return points


def _cubic(p0, p1, p2, p3, depth=0):
    """Points after p0 up to p3, split until each piece lies within TOLERANCE of its chord."""
    def off(p):
        (ax, ay), (bx, by) = p0, p3
        length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        if not length:
            return ((p[0] - ax) ** 2 + (p[1] - ay) ** 2) ** 0.5
        return abs((bx - ax) * (ay - p[1]) - (ax - p[0]) * (by - ay)) / length
    if depth > 12 or max(off(p1), off(p2)) <= TOLERANCE:
        return [p3]
    mid = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    a, b, c = mid(p0, p1), mid(p1, p2), mid(p2, p3)
    d, e = mid(a, b), mid(b, c)
    m = mid(d, e)
    return _cubic(p0, a, d, m, depth + 1) + _cubic(m, e, c, p3, depth + 1)


def _inside(contours, x, y) -> bool:
    """Nonzero winding of the point among all contours."""
    wind = 0
    for pts in contours:
        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1], strict=True):
            if ay <= y < by and (bx - ax) * (y - ay) - (x - ax) * (by - ay) > 0:
                wind += 1
            elif by <= y < ay and (bx - ax) * (y - ay) - (x - ax) * (by - ay) < 0:
                wind -= 1
    return wind != 0


def stems(contours: list[list[tuple[float, float]]]) -> list[tuple[int, int]]:
    """Vertical stems as (left x, right x): pairs of a left side (ink to its right) and the nearest right side
    across ink, overlapping in height. Sides are straight vertical runs at least STEM_LENGTH long, or the
    leftmost/rightmost points of a contour (the sides of o, c, O)."""
    sides = []  # (x, y low, y high)
    for pts in contours:
        n = len(pts)
        for i in range(n):
            (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
            if abs(ax - bx) <= EDGE and abs(ay - by) >= STEM_LENGTH:
                sides.append(((ax + bx) / 2, min(ay, by), max(ay, by)))
        for pick in (min, max):
            x = pick(p[0] for p in pts)
            ys = [p[1] for p in pts if abs(p[0] - x) <= EDGE]
            sides.append((x, min(ys), max(ys)))
    lefts, rights = [], []
    for x, lo, hi in sides:
        mid = (lo + hi) / 2
        ink_right, ink_left = _inside(contours, x + 2, mid), _inside(contours, x - 2, mid)
        if ink_right and not ink_left:
            lefts.append((x, lo, hi))
        elif ink_left and not ink_right:
            rights.append((x, lo, hi))
    found = []  # (height, left, right)
    for x, lo, hi in sorted(set(lefts)):
        across = [r for r in rights if STEM_RANGE[0] <= r[0] - x <= STEM_RANGE[1] and r[1] <= hi and lo <= r[2]
                  and _inside(contours, (x + r[0]) / 2, (max(lo, r[1]) + min(hi, r[2])) / 2)]
        if across:
            r = min(across, key=lambda r: r[0])
            found.append((min(hi, r[2]) - max(lo, r[1]), round(x), round(r[0])))
    out = []  # the tallest of stems that overlap across x (the stem of i, not its dot)
    for _, left, right in sorted(found, reverse=True):
        if all(right <= a or left >= b for a, b in out):
            out.append((left, right))
    return sorted(out)


def bars(contours: list[list[tuple[float, float]]]) -> list[tuple[int, int]]:
    """Horizontal strokes as (bottom y, top y), found as stems() finds vertical ones on the outline turned on its
    side: straight bars (e's crossbar, the arms of E, z) and the bottoms and tops of round letters (s, a, o)."""
    return stems([[(y, x) for x, y in c] for c in contours])


def even_stems(glyphs: dict) -> None:
    """Give stems whose widths differ by no more than SAME_WIDTH units (outline rounding, not design) one width,
    their group's median, keeping each stem's left (or bottom) side: otherwise 122 and 123 round to different whole
    pixels at some sizes and the same stroke reads bold in one letter. Distinct design widths (the thin m, W) stay
    apart. Vertical stems and horizontal bars are grouped apart."""
    for kind in ("stems", "bars"):
        widths = sorted({r - l for g in glyphs.values() for l, r in g.get(kind, [])})
        groups, current = [], []
        for w in widths:
            if current and w - current[-1] > SAME_WIDTH:
                groups.append(current)
                current = []
            current.append(w)
        if current:
            groups.append(current)
        even = {w: round(sorted(group)[len(group) // 2]) for group in groups for w in group}
        for g in glyphs.values():
            if kind in g:
                g[kind] = [(l, l + even[r - l]) for l, r in g[kind]]


def zones(glyphs: dict[str, dict]) -> dict[str, int]:
    """The heights fitted to whole pixels, from the face's own letters."""
    lo = lambda chars: min(glyphs[c]["ymin"] for c in chars if c in glyphs)
    hi = lambda chars: max(glyphs[c]["ymax"] for c in chars if c in glyphs)
    return {"desc": lo(glyphs), "base_over": lo("oecsaOCGS"), "xh": glyphs["x"]["ymax"],
            "xh_over": hi("oecsz"), "cap": glyphs["H"]["ymax"], "cap_over": hi("OCGS"), "asc": hi(glyphs)}


ZONES = ("desc", "base_over", "xh", "xh_over", "cap", "cap_over", "asc")


def encode(glyphs: dict[int, dict], zone: dict[str, int], base: int) -> bytes:
    """glyphs: game glyph number (0 = the cell after LATIN_GLYPH) -> {offset, stems, bars, contours}. Layout:
    'WZGO', u32 version, 7 x i16 zones, u16 baseline (font units down from the cell top), u16 count, then per glyph
    u16 number, i16 offset, u16 stems, u16 bars, u16 contours, u32 data offset; data: stems as i16 left, right
    pairs, bars as i16 bottom, top pairs, contour ends as u16 point counts (cumulative), points as i16 x, y."""
    head = MAGIC + struct.pack("<I7hHH", VERSION, *(zone[k] for k in ZONES), base, len(glyphs))
    table, data = b"", b""
    start = len(head) + 14 * len(glyphs)
    for number in sorted(glyphs):
        g = glyphs[number]
        pts = [p for c in g["contours"] for p in c]
        bar = g.get("bars", [])
        table += struct.pack("<HhHHHI", number, round(g["offset"]), len(g["stems"]), len(bar), len(g["contours"]),
                             start + len(data))
        ends, total = [], 0
        for c in g["contours"]:
            total += len(c)
            ends.append(total)
        data += struct.pack(f"<{2 * len(g['stems'])}h", *(v for s in g["stems"] for v in s))
        data += struct.pack(f"<{2 * len(bar)}h", *(v for s in bar for v in s))
        data += struct.pack(f"<{len(ends)}H", *ends)
        data += struct.pack(f"<{2 * len(pts)}h", *(round(v) for p in pts for v in p))
    return head + table + data


def extract() -> bytes:
    """The face as JSON for `from_json`, each glyph placed in its advance as the game's width table spaces it
    (side_spacing: room for each side by the side's shape). Lengths are rounded as `encode` stores them."""
    from . import raster, side_spacing, wo1vec

    spacing = side_spacing.font_spacing()
    font = wo1vec.make()
    outlines = raster.outlines(font)
    glyphs = {}
    for number, ch in enumerate(raster.CHARS):
        if not ch.strip() or ch not in outlines or not outlines[ch][0]:
            continue
        contours = [flatten(c) for c in outlines[ch][0]]
        xs, ys = [p[0] for c in contours for p in c], [p[1] for c in contours for p in c]
        glyphs[number] = {"ch": ch, "offset": spacing[ch][1] - min(xs),
                          "contours": contours,
                          "stems": stems(contours), "bars": bars(contours),
                          "ymin": round(min(ys)), "ymax": round(max(ys))}
    even_stems(glyphs)
    zone = zones({g["ch"]: g for g in glyphs.values()})
    stored = {number: {"offset": round(g["offset"]), "stems": g["stems"], "bars": g["bars"],
                       "contours": [[[round(v) for v in p] for p in c] for c in g["contours"]]}
              for number, g in glyphs.items()}
    return json.dumps({"zone": zone, "base": round(wo1vec.BASE * CELL / 24), "glyphs": stored},
                      sort_keys=True, separators=(",", ":")).encode("ascii")


def from_json(data: bytes) -> bytes:
    """glyphs.bin outline data from `extract`'s JSON."""
    face = json.loads(data)
    return encode({int(number): g for number, g in face["glyphs"].items()}, face["zone"], face["base"])


if __name__ == "__main__":
    Path(sys.argv[1]).write_bytes(extract())
