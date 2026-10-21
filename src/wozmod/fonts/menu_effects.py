"""Menu effects rendering support."""
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field

import cairo
import numpy as np
from PIL import Image

from .skeleton import contours


def composite(fill, colors, rings, glow=None, *, ring_mode="outside"):


    alpha = fill.copy()
    pre = fill[..., None] * colors
    for mask, color in rings:
        outer = np.maximum(alpha, mask)


        amount = (1 - fill) * mask if ring_mode == "over" else outer - alpha
        pre += amount[..., None] * np.asarray(color)
        alpha = outer
    if glow is not None:
        mask, color = glow
        outer = np.maximum(alpha, mask)
        pre += (outer - alpha)[..., None] * np.asarray(color)
        alpha = outer
    rgb = pre / np.maximum(alpha, 1e-9)[..., None]
    return np.dstack([np.clip(rgb + 0.5, 0, 255),
                      np.clip(alpha * 255 + 0.5, 0, 255)]).astype(np.uint8)


@dataclass
class Label:
    image: Image.Image
    baseline: int
    ink: tuple
    pen: float
    advance: float
    fill_profile: list = field(default_factory=list)  # the fill's coverage per column (buttons; see lettering.fill_edges)

    def emit(self, output):
        """Save the image to `output` and print its metrics as JSON: what each raster's command line answers."""
        output.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(output)
        print(json.dumps(dict(size=self.image.size, baseline=self.baseline, ink=[round(v, 3) for v in self.ink],
                              pen=round(self.pen, 3), advance=round(self.advance, 3))))


def outline_table(face, layer, cap=None, upright=None):
    """face.BUILD's letters as {letter: (paths, box, span)} in cap units, y down from the cap line: the paths of
    layer(letter) for `path`, its ink box (x0, top, x1, bottom), and the (x0, x1) its spacing measures, taken from
    upright(letter) when given (a slanted face is spaced upright)."""
    cap = face.CAP if cap is None else cap
    out = {}
    for ch in face.BUILD:
        l = layer(ch)
        x0, y0, x1, y1 = l.boundingBox()
        u = (upright(ch) if upright else l).boundingBox()
        out[ch] = (contours(l, cap, face.U), (x0 / face.U, cap - y1 / face.U, x1 / face.U, cap - y0 / face.U),
                   (u[0] / face.U, u[2] / face.U))
    return out


def _check(text, face, g):
    for ch in text.replace(" ", ""):
        if ch not in g:
            raise KeyError(f"{face.__name__.rsplit('.', 1)[-1]} has no glyph for {ch!r} in {text!r}")


def ink_layout(text, face, g, ink=None):
    """text's letters placed by `ink`, each letter's ink start (a row fitted to its donor), or else by their spans
    in the table `g` joined by face.BEARINGS, a space adding face.SPACE. Returns ([(letter, x)], advance)."""
    _check(text, face, g)
    letters = text.replace(" ", "")
    if ink is None:
        ink, pen, prev = [], 0.0, None
        for ch in text:
            if ch == " ":
                pen += face.SPACE
                continue
            if prev is not None:
                pen += face.BEARINGS[prev][1] + face.BEARINGS[ch][0]
            ink.append(pen)
            pen += g[ch][2][1] - g[ch][2][0]
            prev = ch
    if not letters:
        return [], face.SPACE * len(text)
    ls, last = face.BEARINGS[letters[0]][0], letters[-1]
    out = [(ch, ls + x - g[ch][2][0]) for ch, x in zip(letters, ink, strict=True)]
    return out, ls + ink[-1] + (g[last][2][1] - g[last][2][0]) + face.BEARINGS[last][1]


def pen_layout(text, face, g, track=0.0, space=None):
    """text's letters set by a pen: each letter's face.BEARINGS around its ink box in `g`, `track` more after every
    character, a space adding `space` (face.SPACE by default). Returns ([(letter, x)], advance)."""
    _check(text, face, g)
    space = face.SPACE if space is None else space
    pen, out = 0.0, []
    for k, ch in enumerate(text):
        pen += track if k else 0.0
        if ch == " ":
            pen += space
            continue
        (ls, rs), (x0, _, x1, _) = face.BEARINGS[ch], g[ch][1]
        out.append((ch, pen + ls - x0))
        pen += ls + (x1 - x0) + rs
    return out, pen


def path(ctx, cs, x, y, s, sy=None):
    """Add the paths `cs` to ctx at (x, y), scaled by s (by sy vertically when given)."""
    sy = s if sy is None else sy
    for segs in cs:
        ctx.move_to(x + segs[0][0] * s, y + segs[0][1] * sy)
        for seg in segs[1:]:
            pts = [(x + a * s, y + b * sy) for a, b in seg[1:]]
            if seg[0] == "L":
                ctx.line_to(*pts[0])
            else:
                ctx.curve_to(*pts[0], *pts[1], *pts[2])
        ctx.close_path()


def coverage(draw, w, h, dilate=0.0):


    surf = cairo.ImageSurface(cairo.FORMAT_A8, w, h)
    ctx = cairo.Context(surf)
    ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
    ctx.save()  # draw's transform must not scale the dilation stroke below
    draw(ctx)
    ctx.restore()
    if dilate:
        ctx.set_line_width(2 * dilate)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.fill_preserve()
        ctx.stroke()
    else:
        ctx.fill()
    surf.flush()
    return np.ndarray((h, surf.get_stride()), np.uint8, surf.get_data())[:, :w] / 255.0


def blur(a, sigma):
    r = int(math.ceil(4 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    win = np.lib.stride_tricks.sliding_window_view
    a = win(np.pad(a, r), 2 * r + 1, axis=1) @ k
    return win(a, 2 * r + 1, axis=0) @ k


def translated(a: np.ndarray, dx: float, dy: float) -> np.ndarray:

    result = np.zeros_like(a)
    x0, y0 = math.floor(dx), math.floor(dy)
    fx, fy = dx - x0, dy - y0
    h, w = a.shape
    for x, wx in ((x0, 1 - fx), (x0 + 1, fx)):
        for y, wy in ((y0, 1 - fy), (y0 + 1, fy)):
            if not wx * wy or abs(x) >= w or abs(y) >= h:
                continue
            left, top = max(0, x), max(0, y)
            right, bottom = min(w, w + x), min(h, h + y)
            result[top:bottom, left:right] += (
                wx * wy * a[top - y:bottom - y, left - x:right - x])
    return result


def paint(fill: np.ndarray, mask: Callable[[float], np.ndarray], st: dict,
          baseline: float, sy: float, cap_design: float) -> np.ndarray:

    scale = st.get("scale", 1.0)
    ring = st.get("ring", 0.0) * scale
    sigma = st.get("sigma", 0.0) * scale
    glow = st.get("glow", 0.0) * scale
    glow_sigma = st.get("glow_sigma", 0.0) * scale
    colors = np.empty((fill.shape[0], 3))
    if "gradient" in st:
        depth = ((np.arange(fill.shape[0]) + 0.5) - (baseline - cap_design * sy)) / sy
        stops = [(d * cap_design / 20, c) for d, c in st["gradient"]]
        colors = np.stack([np.interp(depth, [d for d, _ in stops],
                                    [c[k] for _, c in stops]) for k in range(3)], -1)
    elif "bands" in st:
        hy = (baseline - (np.arange(fill.shape[0]) + 0.5)) * 20 / (st["cap"] * scale)

        for bottom, color in st["bands"]:
            colors[hy >= bottom] = color
        colors[hy < st["bands"][0][0]] = st["bands"][0][1]
    else:
        colors[:] = st["fill"]
    rings = []
    if ring:
        r = mask(ring)
        if sigma:
            r = blur(r, sigma)
        rings.append((st.get("ring_alpha", 1.0) * r, st["ring_fill"]))
    halo = None
    if glow:
        g = mask(glow)
        if glow_sigma:
            g = blur(g, glow_sigma)
        halo = (np.minimum(1.0, st.get("glow_k", 1.0) * g), st["glow_fill"])
    rgba = composite(fill, colors[:, None, :], rings, halo,
                     ring_mode=st.get("ring_mode", "outside"))
    if st.get("shadow_alpha", 0):
        foreground = rgba.astype(float) / 255
        alpha = foreground[..., 3]
        shadow = alpha
        sigma = st.get("shadow_sigma", 0) * scale
        if sigma:
            shadow = blur(shadow, sigma)
        dx, dy = st.get("shadow_offset", (0, 0))
        shadow = translated(shadow, dx * scale, dy * scale) * st["shadow_alpha"]
        behind = shadow * (1 - alpha)
        combined = alpha + behind
        premultiplied = (foreground[..., :3] * alpha[..., None]
                         + np.asarray(st["shadow_fill"]) / 255 * behind[..., None])
        rgb = premultiplied / np.maximum(combined, 1e-9)[..., None]
        rgba = np.dstack((np.clip(rgb * 255 + .5, 0, 255),
                         np.clip(combined * 255 + .5, 0, 255))).astype(np.uint8)
    return rgba
