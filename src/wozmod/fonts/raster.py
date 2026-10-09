"""Raster rendering support."""
import argparse
import math
from pathlib import Path

import cairo
import numpy as np
import fontforge

from wozmod.runtime.latin_font import ADVANCES


def outlines(font):

    out = {}
    for g in font.glyphs():
        if g.unicode < 0:
            continue
        cs = []
        for c in g.foreground:
            pts = list(c)
            k = next(i for i, p in enumerate(pts) if p.on_curve)
            pts = pts[k:] + pts[:k] + [pts[k]]
            segs, ctl = [(pts[0].x, pts[0].y)], []
            for p in pts[1:]:
                if p.on_curve:
                    segs.append((*ctl, (p.x, p.y)) if ctl else ((p.x, p.y),))
                    ctl = []
                else:
                    ctl.append((p.x, p.y))
            cs.append(segs)
        out[chr(g.unicode)] = (cs, g.width)
    return out


def draw(glyphs, text, x, base, scale, ctx):

    for ch in text:
        cs, w = glyphs.get(ch, glyphs[' '])
        for segs in cs:
            ctx.move_to(x + segs[0][0] * scale, base - segs[0][1] * scale)
            for seg in segs[1:]:
                p = [(x + a * scale, base - b * scale) for a, b in seg]
                if len(p) == 3:
                    ctx.curve_to(*p[0], *p[1], *p[2])
                else:
                    ctx.line_to(*p[-1])
            ctx.close_path()
        x += w * scale
    return x


def mask(glyphs, lines, px, w, h, x0, base0, leading):

    surf = cairo.ImageSurface(cairo.FORMAT_A8, w, h)
    ctx = cairo.Context(surf)
    ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
    for i, line in enumerate(lines):
        draw(glyphs, line, x0, base0 + i * leading, px / 1200, ctx)
    ctx.fill()
    surf.flush()
    a = np.ndarray((h, surf.get_stride()), np.uint8, surf.get_data())[:, :w]
    return a / 255.


def load(path):
    f = fontforge.open(str(path))
    try:
        return outlines(f)
    finally:
        f.close()


CHARS = [chr(c) for c in range(0x20, 0x7E)] + list('ŌōŪūï')


GAP = 3.5
SPACE = 8


PREFERRED_ADVANCES = ADVANCES[:94]


def spacing(font, mode="baseline"):

    if mode not in {"baseline", "legacy"}:
        raise ValueError(f"unknown Latin spacing: {mode!r}")

    out = {}
    for g in font.glyphs():
        if g.unicode < 0 or chr(g.unicode) not in CHARS or chr(g.unicode) == ' ':
            continue
        x0, _, x1, _ = (v / 50 for v in g.boundingBox())
        ink, c = x1 - x0, g.unicode
        advance = (min(max(PREFERRED_ADVANCES[c - 0x20], math.ceil(ink + 2)), math.floor(ink + 5))
                   if mode == "baseline" and c < 0x7E else round(ink + GAP))
        out[chr(g.unicode)] = ((advance - (x1 - x0)) / 2 - x0, advance)
    return out


def cells(px, spacing_mode="baseline"):

    from . import wo1vec
    font = wo1vec.make()
    glyphs, placed = outlines(font), spacing(font, spacing_mode)
    ink = [mask(glyphs, [c], px, px, px, placed[c][0] * px / 24, wo1vec.BASE * px / 24, 0) if c in placed
           else np.zeros((px, px)) for c in CHARS]
    return np.hstack(ink), bytes(placed[c][1] if c in placed else SPACE if c == ' ' else 24 for c in CHARS)


if __name__ == '__main__':
    from PIL import Image
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('output', type=Path)
    parser.add_argument('--px', type=int, default=24, help='cell size in pixels; the game reads 24')
    parser.add_argument('--spacing', choices=('baseline', 'legacy'), default='baseline',
                        help='Latin advance rule')
    parser.add_argument('--advances', action='store_true', help='also print the 99 advances as hex')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    strip, advances = cells(args.px, args.spacing)
    Image.fromarray((strip * 255).round().astype(np.uint8), 'L').save(args.output)
    if args.advances:
        print(advances.hex())
