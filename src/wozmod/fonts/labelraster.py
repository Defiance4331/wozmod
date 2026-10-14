"""Labelraster rendering support."""
import argparse
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from . import labelvec as V
from .buttonraster import Label, blur, coverage, path

FILL_A = 0.908100490

FILL_GREY = [26.70312, 26.70312, 28.75937, 35.95625, 45.20938, 55.49062, 65.77187, 68.85625, 79.1375,
             80.16562, 75.025, 71.94062, 61.65937, 55.49062, 46.2375, 38.0125, 34.92813, 30.81562]
HALO_R, HALO_SIGMA, HALO_K, HALO_GREY = 0.84, 1.28, 0.82, 249


@lru_cache(maxsize=1)
def glyphs():

    out = {}
    for ch in V.BUILD:
        l = V.layer(ch)
        x0, y0, x1, y1 = l.boundingBox()
        out[ch] = V.contours(l), (x0 / V.U, V.CAP - y1 / V.U, x1 / V.U, V.CAP - y0 / V.U)
    return out


def layout(text):


    g = glyphs()


    lefts = V.SETS.get(text)
    if lefts is not None:
        chars = text.replace(" ", "")
        ls = V.BEARINGS[chars[0]][0]
        placed = [(ch, ls + x - g[ch][1][0]) for ch, x in zip(chars, lefts, strict=True)]
        last = chars[-1]
        advance = ls + lefts[-1] + g[last][1][2] - g[last][1][0] + V.BEARINGS[last][1]
        return placed, advance
    track, space = V.ROWS.get(text, (0.0, V.SPACE))
    pen, out = 0.0, []
    for k, ch in enumerate(text):
        pen += track if k else 0.0
        if ch == " ":
            pen += space
            continue
        if ch not in g:
            raise KeyError(f"labelvec has no glyph for {ch!r} in {text!r}")
        (ls, rs), (x0, _, x1, _) = V.BEARINGS[ch], g[ch][1]
        out.append((ch, pen + ls - x0))
        pen += ls + (x1 - x0) + rs
    return out, pen


def render(text, scale=1.0):


    g = glyphs()
    placed, advance = layout(text)
    s = scale
    pad = math.ceil((HALO_R + 3 * HALO_SIGMA) * s) + 1
    asc, desc = 17.5, 0.3
    baseline = pad + math.ceil(asc * s)
    h = baseline + math.ceil(desc * s) + pad
    xmin = min(x + g[ch][1][0] for ch, x in placed)
    xmax = max(x + g[ch][1][2] for ch, x in placed)
    ymin = min(g[ch][1][1] for ch, _ in placed) - V.CAP
    ymax = max(g[ch][1][3] for ch, _ in placed) - V.CAP
    ox = pad - xmin * s
    w = math.ceil((xmax - xmin) * s) + 2 * pad

    def draw(ctx):
        for ch, x in placed:
            path(ctx, g[ch][0], ox + x * s, baseline - V.CAP * s, s)

    F = coverage(draw, w, h)
    H = np.clip(HALO_K * blur(coverage(draw, w, h, HALO_R * s), HALO_SIGMA * s), 0, 1)
    depth = ((np.arange(h) + 0.5) - (baseline - V.CAP * s)) / s
    grey = np.interp(depth, np.arange(len(FILL_GREY)) + 0.1, FILL_GREY)[:, None]
    alpha = FILL_A * F + (1 - F) * H
    pre = FILL_A * F * grey + (1 - F) * H * HALO_GREY
    rgb = pre / np.maximum(alpha, 1e-9)
    rgba = np.dstack([np.clip(rgb + 0.5, 0, 255)] * 3 + [np.clip(alpha * 255 + 0.5, 0, 255)]).astype(np.uint8)
    return Label(Image.fromarray(rgba, "RGBA"), baseline,
                 (pad, baseline + ymin * s, ox + xmax * s, baseline + ymax * s), ox, advance * s)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text")
    parser.add_argument("output", type=Path)
    parser.add_argument("--scale", type=float, default=1.0, help="device px per official 1x texel")
    args = parser.parse_args()
    label = render(args.text, args.scale)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    label.image.save(args.output)
    print(json.dumps(dict(size=label.image.size, baseline=label.baseline, ink=[round(v, 3) for v in label.ink],
                          pen=round(label.pen, 3), advance=round(label.advance, 3))))
