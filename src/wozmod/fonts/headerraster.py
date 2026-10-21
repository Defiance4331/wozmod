"""Headerraster rendering support."""
import argparse
import math
from functools import lru_cache
from pathlib import Path

import cairo
import numpy as np
from PIL import Image

from . import headervec as V
from .menu_effects import Label, coverage, ink_layout, outline_table, path
from .word_key import word_key

FILL = ((0.00, (255.3, 254.0, 235.2)), (0.10, (250.1, 246.0, 220.5)), (0.19, (247.1, 242.2, 212.8)),
        (0.30, (242.8, 236.4, 201.0)), (0.41, (238.9, 230.3, 188.4)), (0.51, (233.5, 221.5, 175.4)),
        (0.61, (231.0, 216.0, 162.1)), (0.70, (225.7, 210.2, 150.7)), (0.79, (221.2, 203.5, 139.8)),
        (0.89, (217.4, 200.3, 130.6)), (1.00, (213.3, 192.0, 116.3)))
EDGE_RGB = (2, 0, 33)
EDGE = 2.925
EDGE_Y = 3.025


@lru_cache(maxsize=2)
def glyphs(text=None):


    if text is not None and word_key(text) not in V.ROW_PARAMS:
        return glyphs()
    params = V.ROW_PARAMS.get(word_key(text), {}) if text is not None else {}
    return outline_table(V, lambda ch: V.layer(ch, params.get(ch)),
                         upright=lambda ch: V.layer(ch, params.get(ch), slant=0.0))


def layout(text, ref=None):


    return ink_layout(text, V, glyphs(text), V.SETS.get(f"{word_key(text)}@{ref}", V.SETS.get(word_key(text))))


def edge_coverage(draw, w, h, rx, ry):

    surf = cairo.ImageSurface(cairo.FORMAT_A8, w, h)
    ctx = cairo.Context(surf)
    ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
    draw(ctx)
    ctx.fill_preserve()
    ctx.scale(1, ry / rx)
    ctx.set_line_width(2 * rx)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.stroke()
    surf.flush()
    return np.ndarray((h, surf.get_stride()), np.uint8, surf.get_data())[:, :w] / 255.0


def render(text, scale=1.0, hscale=1.0, ref=None):


    g = glyphs(text)
    placed, advance = layout(text, ref)
    s, sx = scale, scale * hscale
    px, py = (math.ceil(e * s) + 2 for e in (EDGE, EDGE_Y))
    asc = max(V.CAP - b[1] for _, b, _ in g.values())
    desc = max(b[3] - V.CAP for _, b, _ in g.values())
    baseline = py + math.ceil(asc * s)
    h = baseline + math.ceil(desc * s) + py
    xmin = min(x + g[ch][1][0] for ch, x in placed)
    xmax = max(x + g[ch][1][2] for ch, x in placed)
    ymin = min(g[ch][1][1] for ch, _ in placed) - V.CAP
    ymax = max(g[ch][1][3] for ch, _ in placed) - V.CAP
    ox = px - xmin * sx
    w = math.ceil((xmax - xmin) * sx) + 2 * px

    def draw(ctx):
        for ch, x in placed:
            path(ctx, g[ch][0], ox + x * sx, baseline - V.CAP * s, sx, s)

    F = coverage(draw, w, h)
    O = np.maximum(edge_coverage(draw, w, h, EDGE * s, EDGE_Y * s), F)
    depth = ((np.arange(h) + 0.5) - (baseline - V.CAP * s)) / s / V.CAP
    fill = np.stack([np.interp(depth, [d for d, _ in FILL], [c[k] for _, c in FILL]) for k in range(3)], -1)
    pre = F[..., None] * fill[:, None, :] + (O - F)[..., None] * np.array(EDGE_RGB, float)
    rgb = pre / np.maximum(O, 1e-9)[..., None]
    rgba = np.dstack([np.clip(rgb + 0.5, 0, 255), np.clip(O * 255 + 0.5, 0, 255)]).astype(np.uint8)
    return Label(Image.fromarray(rgba, "RGBA"), baseline,
                 (px, baseline + ymin * s, ox + xmax * sx, baseline + ymax * s), ox, advance * sx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text")
    parser.add_argument("output", type=Path)
    parser.add_argument("--scale", type=float, default=1.0, help="device px per official 1x texel")
    parser.add_argument("--hscale", type=float, default=1.0, help="horizontal condensing, for A/B only")
    parser.add_argument("--ref", help="official sheet, e.g. 1.13 or 17.34")
    args = parser.parse_args()
    label = render(args.text, args.scale, args.hscale, args.ref)
    label.emit(args.output)
