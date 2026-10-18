"""Mainraster rendering support."""
import argparse
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from . import mainvec as V
from .buttonraster import Label, blur, coverage, path
from .word_key import word_key

RING = 3.0
HALO_R, HALO_SIGMA, HALO_K = 6.5, 2.53, 0.775

STREAK_D = np.arange(20, 53, 2.0)
STREAK_Y = [0.018, 0.163, 0.301, 0.403, 0.464, 0.489, 0.496, 0.503, 0.509, 0.505, 0.487, 0.447, 0.377, 0.26,
            0.11, 0.001, 0.0]
STREAK_U = np.arange(0, 91, 10.0)
STREAK_X = [1.0, 0.927, 0.801, 0.652, 0.494, 0.34, 0.199, 0.081, 0.004, 0.0]


STREAK_L, STREAK_LEAN, STREAK_W = -4.07, 0.957, 12.2
FILL_D = np.arange(0, 47, 2.0)
STYLES = {
    "lit": dict(edge=(33, 36, 57), halo=(16, 9, 49), streak=(16, 9, 24), fill=[
        (202, 205, 220), (197, 202, 215), (198, 201, 214), (193, 193, 214), (188, 185, 211), (183, 171, 205),
        (172, 153, 201), (161, 134, 198), (153, 122, 198), (143, 105, 199), (136, 101, 201), (120, 117, 204),
        (107, 161, 207), (111, 179, 206), (122, 185, 207), (135, 191, 209), (149, 196, 208), (166, 201, 211),
        (181, 207, 214), (198, 211, 214), (207, 214, 215), (215, 215, 215), (214, 215, 214), (216, 216, 215)]),
    "dim": dict(edge=(57, 60, 74), halo=(46, 42, 68), streak=(46, 42, 54), fill=[
        (167, 170, 180), (165, 168, 177), (165, 167, 177), (164, 162, 173), (160, 157, 173), (154, 148, 171),
        (147, 136, 166), (141, 123, 165), (136, 115, 165), (130, 105, 165), (125, 102, 165), (115, 112, 166),
        (107, 140, 172), (108, 153, 172), (114, 156, 170), (124, 161, 173), (133, 165, 173), (145, 167, 173),
        (155, 171, 174), (165, 173, 175), (171, 175, 175), (175, 177, 178), (177, 177, 177), (176, 176, 176)]),
    "lit2": dict(edge=(33, 32, 57), halo=(8, 0, 49), streak=(8, 0, 21), fill=[
        (238, 242, 255), (232, 237, 254), (231, 237, 255), (228, 227, 251), (222, 217, 248), (214, 200, 244),
        (200, 178, 237), (188, 155, 231), (176, 138, 232), (165, 121, 234), (156, 112, 239), (136, 133, 238),
        (121, 188, 241), (128, 209, 241), (140, 215, 244), (157, 225, 247), (172, 231, 247), (194, 238, 249),
        (213, 243, 252), (232, 248, 254), (244, 251, 255), (254, 255, 255), (254, 254, 255), (253, 251, 253)]),
    "dim2": dict(edge=(49, 48, 66), halo=(35, 29, 60), streak=(35, 29, 41), fill=[
        (184, 187, 199), (181, 184, 196), (181, 183, 196), (178, 177, 192), (173, 170, 189), (168, 159, 188),
        (158, 146, 183), (151, 131, 181), (146, 120, 181), (138, 108, 181), (132, 102, 182), (117, 116, 184),
        (108, 151, 189), (111, 166, 189), (119, 169, 189), (131, 176, 189), (140, 180, 189), (156, 185, 190),
        (166, 187, 192), (181, 191, 195), (189, 194, 197), (196, 195, 197), (196, 195, 196), (193, 196, 194)]),
}


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
    letters = text.replace(" ", "")
    for ch in letters:
        if ch not in g:
            raise KeyError(f"mainvec has no glyph for {ch!r} in {text!r}")
    ink = V.SETS.get(word_key(text))
    if ink is None:
        ink, pen, prev = [], 0.0, None
        for ch in text:
            if ch == " ":
                pen += V.SPACE
                continue
            if prev is not None:
                pen += V.BEARINGS[prev][1] + V.KERN.get((prev, ch), 0.0) + V.BEARINGS[ch][0]
            ink.append(pen)
            pen += g[ch][1][2] - g[ch][1][0]
            prev = ch
    if not letters:
        return [], V.SPACE * len(text)
    ls, last = V.BEARINGS[letters[0]][0], letters[-1]
    out = [(ch, ls + x - g[ch][1][0]) for ch, x in zip(letters, ink, strict=True)]
    return out, ls + ink[-1] + (g[last][1][2] - g[last][1][0]) + V.BEARINGS[last][1]


def streak(xr, d, width, room=None):


    u = xr - width
    if room is not None and width + STREAK_U[-1] > room:
        u = u * STREAK_U[-1] / max(room - width, 1.0)
    xh = STREAK_L - STREAK_LEAN * (np.clip(d, 26, 42) - 34)
    return (np.interp(d, STREAK_D, STREAK_Y, left=0, right=0) * np.clip((xr - xh) / STREAK_W + 0.5, 0, 1)
            * np.interp(u, STREAK_U, STREAK_X, left=1.0, right=0.0))


def render(text, scale=1.0, style="lit", room=None):


    g = glyphs()
    placed, advance = layout(text)
    s, st = scale, STYLES[style]
    pad = math.ceil((HALO_R + 3 * HALO_SIGMA) * s) + 1
    left = math.ceil((STREAK_W / 2 - STREAK_L + 8 * STREAK_LEAN) * s)
    asc = max(V.CAP - b[1] for _, b in g.values())
    desc = max(b[3] - V.CAP for _, b in g.values())
    baseline = pad + math.ceil(asc * s)
    h = baseline + math.ceil(desc * s) + pad
    if placed:
        xmin = min(x + g[ch][1][0] for ch, x in placed)
        xmax = max(x + g[ch][1][2] for ch, x in placed)
        ymin = min(g[ch][1][1] for ch, _ in placed) - V.CAP
        ymax = max(g[ch][1][3] for ch, _ in placed) - V.CAP
    else:
        xmin = xmax = ymin = ymax = 0.0
    width = xmax - xmin
    tail = STREAK_U[-1] if room is None else min(STREAK_U[-1], max(room - width, 1.0))
    lpad = max(pad, left)
    ox = lpad - xmin * s
    w = math.ceil(width * s) + lpad + max(pad, math.ceil(tail * s) + 1)
    cap = baseline - V.CAP * s

    def draw(ctx):
        for ch, x in placed:
            path(ctx, g[ch][0], ox + x * s, cap, s)

    F = coverage(draw, w, h)
    O = coverage(draw, w, h, RING * s)
    H = np.clip(HALO_K * blur(coverage(draw, w, h, HALO_R * s), HALO_SIGMA * s), 0, 1)
    d = ((np.arange(h) + 0.5 - cap) / s)[:, None]
    xr = ((np.arange(w) + 0.5 - lpad) / s)[None, :]
    S = streak(xr, d, width, room) if placed else np.zeros((h, w))
    fill = np.stack([np.interp(d[:, 0], FILL_D, [c[i] for c in st["fill"]]) for i in range(3)], 1)[:, None, :]
    layers = [(F, fill), (O, np.array(st["edge"], float)), (H, np.array(st["halo"], float)),
              (S, np.array(st["streak"], float))]
    alpha, pre, free = np.zeros((h, w)), np.zeros((h, w, 3)), np.ones((h, w))
    for a, c in layers:
        pre += (free * a)[..., None] * c
        alpha += free * a
        free = free * (1 - a)
    rgb = pre / np.maximum(alpha, 1e-9)[..., None]
    rgba = np.dstack([np.clip(rgb + 0.5, 0, 255), np.clip(alpha * 255 + 0.5, 0, 255)]).astype(np.uint8)
    return Label(Image.fromarray(rgba, "RGBA"), baseline,
                 (lpad, baseline + ymin * s, ox + xmax * s, baseline + ymax * s), ox, advance * s)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text")
    parser.add_argument("output", type=Path)
    parser.add_argument("--scale", type=float, default=1.0, help="device px per official sheet texel")
    parser.add_argument("--style", choices=STYLES, default="lit", help="the official sheet's colours")
    parser.add_argument("--room", type=float, help="official px from the ink's left edge the streak must end within")
    args = parser.parse_args()
    label = render(args.text, args.scale, args.style, args.room)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    label.image.save(args.output)
    print(json.dumps(dict(size=label.image.size, baseline=label.baseline, ink=[round(v, 3) for v in label.ink],
                          pen=round(label.pen, 3), advance=round(label.advance, 3))))
