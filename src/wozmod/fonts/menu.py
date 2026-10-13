"""Menu rendering support."""
import argparse
from dataclasses import replace
import json
import math
from collections.abc import Mapping
from pathlib import Path

import cairo
from PIL import Image

from . import menusans
from .menu_effects import coverage, paint, path
from .menu_styles import STYLES, style_for

__all__ = ["render"]


def _style(style):
    if isinstance(style, str):
        if style not in STYLES:
            raise ValueError(f"unknown menu style: {style!r}")
        style = STYLES[style]
    if not isinstance(style, Mapping):
        raise TypeError("menu style must be a style name or a mapping")
    st = dict(style)
    fields = {"face", "axes", "cap", "width", "scale", "track", "space", "layout", "fill", "bands", "gradient",
              "tracking_overrides", "ring", "ring_fill", "ring_alpha", "ring_mode", "sigma", "glow", "glow_sigma", "glow_k", "glow_fill",
              "shadow_offset", "shadow_sigma", "shadow_fill", "shadow_alpha"}
    unknown = st.keys() - fields
    if unknown:
        raise ValueError(f"unknown menu style fields: {', '.join(sorted(unknown))}")
    if st.get("face") != "menusans":
        raise ValueError(f"unknown menu face: {st.get('face')!r}")
    axes = dict(st.get("axes", {}))
    for key in ("pen", "pinch", "g_pen"):
        if key in axes:
            axes[key] = tuple(axes[key])
    st["axes"] = menusans.Axes(**axes)
    for key in ("cap", "width", "scale"):
        value = st.get(key, 1.0)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"menu style {key} must be finite and positive")
    if not math.isfinite(st.get("track", 0.0)):
        raise ValueError("menu tracking must be finite")
    overrides = st.get("tracking_overrides", {})
    if (not isinstance(overrides, Mapping)
            or any(not isinstance(word, str) or not isinstance(value, (int, float))
                   or not math.isfinite(value) for word, value in overrides.items())):
        raise ValueError("menu word tracking must map words to finite values")
    if not math.isfinite(st.get("space", 7.0)) or st.get("space", 7.0) < 0:
        raise ValueError("menu word space must be finite and nonnegative")
    if st.get("ring_mode", "outside") not in {"outside", "over"}:
        raise ValueError("unknown menu ring coverage mode")
    if "shadow_alpha" in st:
        if not math.isfinite(st["shadow_alpha"]) or not 0 <= st["shadow_alpha"] <= 1:
            raise ValueError("menu shadow opacity must be between zero and one")
        offset = st.get("shadow_offset", (0, 0))
        if len(offset) != 2 or any(not math.isfinite(value) for value in offset):
            raise ValueError("menu shadow offset must have two finite coordinates")
        sigma = st.get("shadow_sigma", 0)
        if not math.isfinite(sigma) or sigma < 0:
            raise ValueError("menu shadow blur must be finite and nonnegative")
        color = st.get("shadow_fill")
        if color is None or len(color) != 3 or any(not math.isfinite(v) or not 0 <= v <= 255 for v in color):
            raise ValueError("menu shadow colour must have three channels between zero and 255")
    if "layout" in st:
        layout = st["layout"]
        if not isinstance(layout, Mapping) or layout.get("kind") != "fixed_cells":
            raise ValueError("unknown menu layout")
        if layout.keys() - {"kind", "pitch", "spans", "offsets", "glyph_map"}:
            raise ValueError("unknown fixed-cell layout fields")
        pitch = layout.get("pitch", 0)
        if not math.isfinite(pitch) or pitch <= 0:
            raise ValueError("menu cell pitch must be finite and positive")
        for span in layout.get("spans", {}).values():
            if not isinstance(span, int) or span < 1:
                raise ValueError("menu cell spans must be positive integers")
        if any(not math.isfinite(offset) for offset in layout.get("offsets", {}).values()):
            raise ValueError("menu cell offsets must be finite")
        if any(not isinstance(source, str) or len(source) != 1
               or not isinstance(target, str) or len(target) != 1
               for source, target in layout.get("glyph_map", {}).items()):
            raise ValueError("menu cell glyph map must contain single characters")
    return st


def _layout(text, glyphs, track, space, layout=None):
    placed, pen, previous = [], 0.0, None
    if layout is not None:
        pitch = layout["pitch"]
        for original in text:


            ch = layout.get("glyph_map", {}).get(
                original, menusans.ALIASES.get(original, original))
            if ch != " ":
                if ch not in glyphs:
                    raise ValueError(f"menusans has no glyph for {original!r}")
                box = glyphs[ch][1]


                x = pen + pitch / 2 + layout.get("offsets", {}).get(ch, 0)
                placed.append((ch, x - (box[0] + box[2]) / 2))
            pen += pitch * layout.get("spans", {}).get(ch, 1)
        return placed, pen
    for i, original in enumerate(text):
        ch = menusans.ALIASES.get(original, original)
        if i:
            pen += track
        if ch == " ":
            pen += space
            previous = None
            continue
        if ch not in glyphs:
            raise ValueError(f"menusans has no glyph for {original!r}")
        box = glyphs[ch][1]
        if previous is not None:
            pen += menusans.KERNING.get(previous + ch, 0.0)
        bearing, advance = menusans.spacing(ch, box)
        placed.append((ch, pen + bearing - box[0]))
        pen += advance
        previous = ch
    return placed, pen


def _placed_bounds(placed, glyphs, slant=0):

    boxes = {}
    for ch, _ in placed:
        if ch in boxes:
            continue
        paths, box = glyphs[ch]
        if not slant:
            boxes[ch] = box
            continue
        points = []
        transform = lambda p: (p[0] + slant * (menusans.CAP - p[1]), p[1])
        for contour in paths:
            start = transform(contour[0])
            points.append(start)
            for segment in contour[1:]:
                controls = [start, *(transform(p) for p in segment[1:])]
                points.append(controls[-1])
                if segment[0] == "C":
                    for axis in (0, 1):
                        p0, p1, p2, p3 = (p[axis] for p in controls)
                        a = -p0 + 3*p1 - 3*p2 + p3
                        b = 2*(p0 - 2*p1 + p2)
                        c = p1 - p0
                        if abs(a) < 1e-12:
                            roots = [] if abs(b) < 1e-12 else [-c/b]
                        else:
                            d = b*b - 4*a*c
                            roots = [] if d < 0 else [(-b-math.sqrt(d))/(2*a),
                                                      (-b+math.sqrt(d))/(2*a)]
                        for t in roots:
                            if 0 < t < 1:
                                points.append(tuple((1-t)**3*controls[0][k]
                                                    + 3*(1-t)**2*t*controls[1][k]
                                                    + 3*(1-t)*t*t*controls[2][k]
                                                    + t**3*controls[3][k]
                                                    for k in (0, 1)))
                start = controls[-1]
        boxes[ch] = (min(p[0] for p in points), min(p[1] for p in points),
                     max(p[0] for p in points), max(p[1] for p in points))
    return [(x+boxes[ch][0], boxes[ch][1], x+boxes[ch][2], boxes[ch][3])
            for ch, x in placed]


def _draw_line(ctx, glyphs, placed, sx, sy, ox, baseline, slant=0):

    ctx.save()
    ctx.translate(ox, baseline - menusans.CAP * sy)
    ctx.scale(sx / sy, 1)
    ctx.transform(cairo.Matrix(1, 0, -slant, 1, slant * menusans.CAP * sy, 0))
    for ch, x in placed:
        path(ctx, glyphs[ch][0], x * sy, 0, sy)
    ctx.restore()


def _render(text, style, *, origin=None, canvas=None):
    st = _style(style)
    glyphs = menusans.glyphs(st["axes"])
    track = st.get("tracking_overrides", {}).get(text, st.get("track", 0.0))
    placed, advance = _layout(text, glyphs, track, st.get("space", 7.0),
                              st.get("layout"))
    scale = st.get("scale", 1.0)
    sy = scale * st["cap"] / menusans.CAP
    sx = sy * st.get("width", 1.0)
    ring = st.get("ring", 0.0) * scale
    sigma = st.get("sigma", 0.0) * scale
    glow = st.get("glow", 0.0) * scale
    glow_sigma = st.get("glow_sigma", 0.0) * scale
    shadow_extent = 0
    if st.get("shadow_alpha", 0):
        shadow_extent = (max(abs(v) for v in st.get("shadow_offset", (0, 0)))
                         + 4 * st.get("shadow_sigma", 0)) * scale
    pad = math.ceil(max(ring + 4 * sigma, glow + 4 * glow_sigma,
                        max(ring, glow) + shadow_extent)) + 1
    asc = max(menusans.CAP - b[1] for _, b in glyphs.values())
    desc = max(b[3] - menusans.CAP for _, b in glyphs.values())
    baseline = pad + math.ceil(asc * sy)
    height = baseline + math.ceil(desc * sy) + pad
    if placed:
        bounds = _placed_bounds(placed, glyphs, st["axes"].slant)
        xmin = min(b[0] for b in bounds)
        xmax = max(b[2] for b in bounds)
        ymin = min(b[1] for b in bounds) - menusans.CAP
        ymax = max(b[3] for b in bounds) - menusans.CAP
    else:
        xmin = xmax = ymin = ymax = 0.0
    ox = pad - xmin * sx
    width = max(1, math.ceil((xmax - xmin) * sx) + 2 * pad)
    if origin is not None:


        ox = origin[0] - xmin * sx
        baseline = origin[1]
        width = max(width, math.ceil(ox + xmax * sx) + pad)
        height = max(height, math.ceil(baseline + desc * sy) + pad)
    if canvas is not None:
        width, height = max(width, canvas[0]), max(height, canvas[1])

    def draw(ctx):
        _draw_line(ctx, glyphs, placed, sx, sy, ox, baseline, st["axes"].slant)


    reference_glyphs = None
    if (st["axes"].g_form == "single" and st["axes"].g_hook == "foot"
            and any(ch == "g" for ch, _ in placed)):
        reference_glyphs = menusans.glyphs(
            replace(st["axes"], g_hook="symmetric", g_pen=(1.0, 1.0)))


    def mask(dilation):
        def supersampled(ctx):
            ctx.scale(8, 8)
            draw(ctx)
        a = coverage(supersampled, width * 8, height * 8, dilation * 8)
        fresh = a.reshape(height, 8, width, 8).mean((1, 3))
        if reference_glyphs is None:
            return fresh

        def reference(ctx):
            ctx.scale(8, 8)
            _draw_line(ctx, reference_glyphs, placed, sx, sy, ox, baseline,
                       st["axes"].slant)
        a = coverage(reference, width * 8, height * 8, dilation * 8)
        result = a.reshape(height, 8, width, 8).mean((1, 3))
        for ch, x in placed:
            if ch == "g":
                boxes = [_placed_bounds([(ch, x)], face, st["axes"].slant)[0]
                         for face in (glyphs, reference_glyphs)]
                left = max(0, math.floor(ox + min(b[0] for b in boxes) * sx - dilation))
                right = min(width, math.ceil(ox + max(b[2] for b in boxes) * sx + dilation))
                result[:, left:right] = fresh[:, left:right]
        return result

    fill = mask(0)
    rgba = paint(fill, mask, st, baseline, sy, menusans.CAP)
    image = Image.fromarray(rgba, "RGBA")
    image.info.update(baseline=baseline,
                      ink=(ox + xmin * sx, baseline + ymin * sy, ox + xmax * sx, baseline + ymax * sy),
                      pen=ox, advance=advance * sx)
    return image, fill


def render(text: str, style: str | Mapping) -> Image.Image:

    return _render(text, style)[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text")
    parser.add_argument("output", type=Path)
    parser.add_argument("--style", choices=STYLES, required=True)
    parser.add_argument("--role", help="semantic row colour role, where the style has variants")
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()
    image = render(args.text, dict(style_for(args.style, role=args.role), scale=args.scale))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)
    print(json.dumps(dict(size=image.size, **image.info)))
