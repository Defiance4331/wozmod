"""Raster rendering support."""
import argparse
from pathlib import Path

import cairo
import numpy as np

from wozmod.runtime.latin_font import LATIN_CHARS


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


CHARS = list(LATIN_CHARS)


GAP = 3.5
SPACE = 8


def spacing(font):
    """Each Latin glyph's left bearing and advance: its ink centred in ink + GAP pixels."""
    out = {}
    for g in font.glyphs():
        if g.unicode < 0 or chr(g.unicode) not in CHARS or chr(g.unicode) == ' ':
            continue
        x0, _, x1, _ = (v / 50 for v in g.boundingBox())
        advance = round(x1 - x0 + GAP)
        out[chr(g.unicode)] = ((advance - (x1 - x0)) / 2 - x0, advance)
    return out


def cells(px):

    from . import wo1vec
    font = wo1vec.make()
    glyphs, placed = outlines(font), spacing(font)
    ink = [mask(glyphs, [c], px, px, px, placed[c][0] * px / 24, wo1vec.BASE * px / 24, 0) if c in placed
           else np.zeros((px, px)) for c in CHARS]
    return np.hstack(ink), bytes(placed[c][1] if c in placed else SPACE if c == ' ' else 24 for c in CHARS)


if __name__ == '__main__':
    from PIL import Image
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('output', type=Path)
    parser.add_argument('--px', type=int, default=24, help='cell size in pixels; the game reads 24')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    strip, _ = cells(args.px)
    Image.fromarray((strip * 255).round().astype(np.uint8), 'L').save(args.output)
