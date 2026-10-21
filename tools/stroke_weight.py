"""Stroke weight of every glyph in our fonts, against the rest of its font (a review tool; the same rule on game
screenshots is the text_stems_even check in tools/screen_checks.py).

A glyph's weight is the median ink across its vertical stems through the middle of its ink (see `weight`). A glyph is flagged when its
weight is off the font's median by more than RATIO or by a whole pixel.

Runs under the font Python (fontforge, cairo, numpy):
    WOZMOD_FONT_PYTHON=/usr/bin/python3; PYTHONPATH=src $WOZMOD_FONT_PYTHON tools/stroke_weight.py OUT_DIR
Writes OUT_DIR/weights.json and one PNG strip per font, flagged glyphs boxed in red.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

RATIO = 0.25  # weight off the font's median by more than this fraction is flagged
PIXEL = 1.0  # or by a whole pixel
FULL = 0.9  # a run counts as a stroke only where some pixel is fully inked
WIDE = 1.8  # runs wider than this many median strokes are bars or curves met edge-on, not stems
DRIFT = 1.0  # px a stem's centre may move over the measured rows; more is a slanted stroke
SCALE = 1.5  # device px per 1x layout px: the 1080p screen


def weight(cov: np.ndarray, typical: float | None = None) -> float | None:
    """Ink across the glyph's thickest vertical stem. Over the middle third of its inked rows, take each column's
    least coverage: a vertical stem keeps its ink (and its anti-aliased edges) in every row, while a crossbar, a
    bowl or a slanted stroke is missing from some row and drops to nothing. A stem is a run of such columns that is
    solid (>= FULL) somewhere; its weight is the run's summed coverage, so the pixel phase does not change it.
    Glyphs with no vertical stem there (S, X, o) are not judged."""
    inked = np.nonzero(cov.max(axis=1) >= FULL)[0]
    if len(inked) < 3:
        return None
    blocks = np.split(inked, np.nonzero(np.diff(inked) > 1)[0] + 1)
    rows = max(blocks, key=len)  # the letter's body: an accent or a dot above it is a separate block
    band = cov[rows[0] + len(rows) // 3: rows[0] + 2 * len(rows) // 3 + 1]
    col = band.min(axis=0)
    found, start = [], None
    for i, v in enumerate(list(col) + [0.0]):
        if v > 0.05 and start is None:
            start = i
        elif v <= 0.05 and start is not None:
            if col[start:i].max() >= FULL and drift(band, start, i) <= DRIFT:
                found.append(float(col[start:i].sum()))
            start = None
    if typical:
        found = [r for r in found if r <= WIDE * typical]
    return max(found) if found else None  # the main stem: a serif face's hairlines and spurs are thinner by design


def drift(band: np.ndarray, start: int, stop: int, both: bool = False) -> float:
    """How far the stroke through columns start..stop moves sideways over the rows (px). Each row's stroke is the
    half-inked run through the stroke's darkest column; a vertical stem keeps one edge in place even where a
    crossbar or a bowl joins the other side (E, H, b), while a slanted stroke (A, V, W) moves both."""
    core = start + int(band[:, start:stop].min(axis=0).argmax())
    lefts, rights = [], []
    for row in band:
        a = b = core
        while a > 0 and row[a - 1] >= 0.5:
            a -= 1
        while b < len(row) - 1 and row[b + 1] >= 0.5:
            b += 1
        lefts.append(a), rights.append(b)
    moves = (max(lefts) - min(lefts), max(rights) - min(rights))
    return float(max(moves) if both else min(moves))  # on screen the band is under the crossbars: both edges hold


def judge(cells: dict[str, np.ndarray]) -> dict[str, dict]:
    cells = {ch: c for ch, c in cells.items() if ch.isalnum()}  # punctuation and signs have no stems to compare
    first = {ch: weight(c) for ch, c in cells.items()}
    typical = statistics.median([w for w in first.values() if w] or [0.0])
    out = {}
    for ch, c in cells.items():
        w = weight(c, typical)
        off = None if w is None else w - typical
        out[ch] = {"weight": None if w is None else round(w, 2), "off": None if off is None else round(off, 2),
                   "flag": off is not None and (abs(off) > RATIO * typical or abs(off) >= PIXEL)}
    return {"median": round(typical, 2), "glyphs": out}


def wo1_cells() -> dict[str, np.ndarray]:
    """The in-game text font exactly as shipped: 24 px cells, legacy spacing."""
    from wozmod.fonts import raster

    strip, _ = raster.cells(24, "legacy")
    return {ch: strip[:, i * 24:(i + 1) * 24] for i, ch in enumerate(raster.CHARS) if ch.strip()}


def outline_cells(glyphs: dict, cap: float, s: float = SCALE) -> dict[str, np.ndarray]:
    """Plain fills of a lettering face's outlines (no gradient, halo or edge) at screen size."""
    from wozmod.fonts.menu_effects import coverage, path

    out = {}
    for ch, (cs, box, *_) in glyphs.items():
        if not ch.strip() or not cs:
            continue
        x0, y0, x1, y1 = box
        pad = 3
        w, h = math.ceil((x1 - x0) * s) + 2 * pad, math.ceil((y1 - y0) * s) + 2 * pad
        out[ch] = coverage(lambda ctx: path(ctx, cs, pad - x0 * s, pad - y0 * s, s), w, h)
    return out


def faces() -> dict[str, dict[str, np.ndarray]]:
    from wozmod.fonts import buttonraster, headerraster, labelraster, mainraster, valueraster
    from wozmod.fonts import valuevec
    from wozmod.fonts.menu_effects import outline_table

    found = {"wo1vec 24px (in-game text)": wo1_cells()}
    for name, mod in (("mainvec", mainraster), ("labelvec", labelraster), ("buttonvec", buttonraster)):
        found[name] = outline_cells(mod.glyphs(), 0)
    H = headerraster.V  # italic: measured upright, or every stem would read as a slanted stroke
    found["headervec upright"] = outline_cells(outline_table(H, lambda ch: H.layer(ch, None, slant=0.0)), 0)
    for face in ("BOLD", "REGULAR", "CONFIRM_FACE", "LIGHT"):
        found[f"valuevec {face.removesuffix('_FACE').lower()}"] = outline_cells(valueraster.glyphs(face), getattr(valuevec, face).CAP)
    return found


def strip(cells: dict[str, np.ndarray], verdict: dict) -> Image.Image:
    h = max(c.shape[0] for c in cells.values()) + 16
    w = sum(c.shape[1] + 6 for c in cells.values())
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d, x = ImageDraw.Draw(im), 0
    for ch, c in cells.items():
        glyph = Image.fromarray((255 - c * 255).astype(np.uint8)).convert("RGB")
        if verdict["glyphs"][ch]["weight"] is None:  # no vertical stem: not judged
            glyph = Image.blend(glyph, Image.new("RGB", glyph.size, (255, 255, 255)), 0.7)
        im.paste(glyph, (x + 3, 3))
        if verdict["glyphs"][ch]["flag"]:
            d.rectangle((x + 1, 1, x + c.shape[1] + 4, c.shape[0] + 4), outline=(220, 0, 0), width=2)
        x += c.shape[1] + 6
    return im


def main(argv: list[str]) -> int:
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    for name, cells in faces().items():
        report[name] = judge(cells)
        cells = {ch: c for ch, c in cells.items() if ch in report[name]["glyphs"]}
        strip(cells, report[name]).save(out / f"{name.split()[0]}-{name.split()[1] if ' ' in name else 'all'}.png")
        flagged = {ch: g["off"] for ch, g in report[name]["glyphs"].items() if g["flag"]}
        print(f"{name}: median {report[name]['median']} px, {len(cells)} glyphs, flagged {flagged}")
    (out / "weights.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
