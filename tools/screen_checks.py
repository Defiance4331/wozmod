"""Layout assertions over one runtime `screen` JSON and its capture PNG.

Pure functions: no game, no harness. Text and quads are in 1280x720 layout
pixels; the capture is `scale = width / 1280` times larger (1.5 at 1920x1080).
A text entry has no height, so its rect is one glyph row (`TEXT_H`) plus
`LINE_PITCH` per extra line, measured on the real captures. Its `width` is the
width before the runtime squishes it into a lane, so the rect-based checks take
the capture and shrink the rect to the ink actually drawn (`drawn_texts`).

Every check returns a `Check` with the measured numbers. A selector that
matches nothing fails the check (a vacuous pass on the wrong screen hides a
bug); pass `allow_empty=True` to accept it.

`run_spec` drives a spec (see `data/checks/`) through a driver with the
methods `route`, `wait`, `screen`, `capture` and returns result rows.
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from wozmod.runtime.english_runtime import DLL_BASE

LAYOUT_W = 1280
TEXT_H = 28  # one glyph row, layout px
LINE_PITCH = 36  # baseline step of a wrapped line, layout px
CJK = re.compile(r"[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")


@dataclass
class Check:
    ok: bool
    numbers: dict[str, Any] = field(default_factory=dict)


@dataclass
class Row:
    screen: str
    assertion: str
    ok: bool
    numbers: dict[str, Any]
    capture: str | None


# ---- selectors -------------------------------------------------------------

def select_texts(screen: dict, caller: str | None = None, match: str | None = None,
                 x: int | None = None, y: int | None = None) -> list[dict]:
    """Texts by draw caller, regex and/or draw position. Rows repeated by a second caller
    (same x, y, text) count once, keeping the first."""
    seen, out = set(), []
    for t in screen.get("texts", []):
        if caller and t["caller"].lower() != caller.lower():
            continue
        if (x is not None and t["x"] != x) or (y is not None and t["y"] != y):
            continue
        if match and not re.search(match, t["text"]):
            continue
        key = (t["x"], t["y"], t["text"])
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def select_quads(screen: dict, key: str) -> list[dict]:
    """Texture quads by `key.member`; fnmatch wildcards allowed (`4064.*`). Identical quads count once."""
    seen, out = set(), []
    for q in screen.get("textures", []):
        k = (q["texture"], q["x"], q["y"], q["width"], q["height"])
        if k not in seen and fnmatch.fnmatchcase(q["texture"], key):
            seen.add(k)
            out.append(q)
    return out


def _texts(screen: dict, sel: dict | None) -> list[dict]:
    sel = sel or {}
    return select_texts(screen, sel.get("caller"), sel.get("match"), sel.get("x"), sel.get("y"))


def text_rect(t: dict) -> tuple[int, int, int, int]:
    lines = t["text"].count("\n") + 1
    return t["x"], t["y"], t["x"] + t["width"], t["y"] + TEXT_H + (lines - 1) * LINE_PITCH


def _quad_rect(q: dict) -> tuple[int, int, int, int]:
    return q["x"], q["y"], q["x"] + q["width"], q["y"] + q["height"]


def _inter(a, b) -> tuple[int, int, int, int] | None:
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return r if r[0] < r[2] and r[1] < r[3] else None


def _empty(what: str, allow_empty: bool) -> Check:
    return Check(allow_empty, {"matched": 0, "selector": what})


# ---- image helpers ---------------------------------------------------------

def _scale(img: Image.Image) -> float:
    return img.width / LAYOUT_W


def _cap(rect, s: float, img: Image.Image) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = (round(v * s) for v in rect)
    return max(x0, 0), max(y0, 0), min(x1, img.width), min(y1, img.height)


class _Gray:
    """Luminance of a capture, indexed in capture px."""

    def __init__(self, img: Image.Image):
        self.w, self.h = img.size
        self.b = img.convert("L").tobytes()

    def at(self, x: int, y: int) -> int:
        return self.b[y * self.w + x]

    def box(self, x0: int, y0: int, x1: int, y1: int) -> list[list[int]]:
        return [list(self.b[y * self.w + x0:y * self.w + x1]) for y in range(y0, y1)]


def drawn_texts(texts: list[dict], img: Image.Image | None, delta: int = 60, gap: int = 18) -> list[dict]:
    """Copies of `texts` whose `width` is the ink drawn, never more than the reported width.
    The screen JSON measures a text before the runtime squishes it into its lane, so a squished
    name still reports its full width. Ink: capture pixels more than `delta` from the rect's median
    luminance; it ends at the first blank run of `gap` capture px (wider than a space). Without a
    capture, or with no ink found, the reported width stands.
    Shortcut: a busy background or a neighbour closer than `gap` keeps the reported width (a
    conservative false overlap); upgrade to the runtime reporting the squished width."""
    if img is None:
        return texts
    g, s, out = _Gray(img), _scale(img), []
    for t in texts:
        x0, y0, x1, y1 = _cap(text_rect(t), s, img)
        box = g.box(x0, y0, x1, y1) if x1 > x0 and y1 > y0 else []
        px = sorted(v for row in box for v in row)
        med, last, blank = (px[len(px) // 2] if px else 0), None, 0
        for i in range(x1 - x0):
            if any(abs(row[i] - med) > delta for row in box):
                last, blank = i, 0
            else:
                blank += 1
                if last is not None and blank >= gap:
                    break
        width = t["width"] if last is None else min(t["width"], round((x0 + last + 1) / s) - t["x"])
        out.append({**t, "width": width, "reported_width": t["width"]})
    return out


# ---- assertions ------------------------------------------------------------

def text_clear_of_quad(screen: dict, text: dict | None, quad: str, allow_empty: bool = False, *,
                       img: Image.Image | None = None) -> Check:
    """No selected text rect intersects a quad (weapon thumbnail over a name,
    RECORD TIME plate over the army line). With `img`, a text counts as wide as its drawn ink."""
    ts, qs = drawn_texts(_texts(screen, text), img), select_quads(screen, quad)
    if not ts or not qs:
        return _empty(f"texts={len(ts)} quads={len(qs)}", allow_empty)
    hits = [
        {"text_at": (t["x"], t["y"]), "quad": q["texture"], "overlap": r, "overlap_w": r[2] - r[0], "overlap_h": r[3] - r[1]}
        for t in ts for q in qs if (r := _inter(text_rect(t), _quad_rect(q)))
    ]
    return Check(not hits, {"texts": len(ts), "quads": len(qs), "hits": hits})


def text_clear_of_text(screen: dict, text: dict | None, other: dict | None = None, allow_empty: bool = False, *,
                       img: Image.Image | None = None) -> Check:
    """Rects of two text selections do not intersect (CONDITIONS over RULES).
    `other` defaults to `text`, so each pair inside one selection is checked.
    With `img`, a text counts as wide as its drawn ink."""
    a = drawn_texts(_texts(screen, text), img)
    b = a if other is None else drawn_texts(_texts(screen, other), img)
    if not a or not b:
        return _empty(f"a={len(a)} b={len(b)}", allow_empty)
    hits = []
    for i, t in enumerate(a):
        for j, u in enumerate(b):
            if other is None and j <= i:
                continue
            if t is u:
                continue
            if r := _inter(text_rect(t), text_rect(u)):
                hits.append({"a_at": (t["x"], t["y"]), "b_at": (u["x"], u["y"]), "overlap_h": r[3] - r[1], "overlap_w": r[2] - r[0]})
    return Check(not hits, {"a": len(a), "b": len(b), "hits": hits})


def line_count(screen: dict, text: dict | None = None, *, max: int, min: int = 1, allow_empty: bool = False) -> Check:
    """Each selected text has between `min` and `max` lines (newlines + 1)."""
    ts = _texts(screen, text)
    if not ts:
        return _empty("texts=0", allow_empty)
    counts = [t["text"].count("\n") + 1 for t in ts]
    return Check(all(min <= c <= max for c in counts), {"lines": counts, "min": min, "max": max})


def text_within_edge(screen: dict, text: dict | None, edge: int = LAYOUT_W, allow_empty: bool = False, *,
                     img: Image.Image | None = None, ink: bool = True) -> Check:
    """Text ends at or before `edge` (narration drawn as one long line; a lane's right end).
    With `img`, a text ends where its drawn ink ends; `ink=False` keeps the reported width, for a text
    the game reveals letter by letter (the capture then shows only the start of it)."""
    ts = drawn_texts(_texts(screen, text), img if ink else None)
    if not ts:
        return _empty("texts=0", allow_empty)
    ends = [t["x"] + t["width"] for t in ts]
    return Check(max(ends) <= edge, {"right_ends": ends, "edge": edge, "worst_over": max(0, max(ends) - edge),
                                     "reported_ends": [t["x"] + t.get("reported_width", t["width"]) for t in ts]})


def text_position(screen: dict, text: dict | None, x: int | None = None, y: int | None = None,
                  right: int | None = None, tol: int = 1, allow_empty: bool = False) -> Check:
    """Every selected text sits at the given layout `x`, `y` or reported right end (`x + width`), within `tol`
    (the prompt bar at y 652 ending at 1192; a name column at its row x). Skipped fields are not checked."""
    ts = _texts(screen, text)
    if not ts:
        return _empty("texts=0", allow_empty)
    want = {"x": x, "y": y, "right": right}
    seen = [{"x": t["x"], "y": t["y"], "right": t["x"] + t["width"]} for t in ts]
    bad = [s for s in seen if any(v is not None and abs(s[k] - v) > tol for k, v in want.items())]
    return Check(not bad, {"texts": len(ts), "want": {k: v for k, v in want.items() if v is not None}, "tol": tol,
                           "bad": bad})


def quad_position(screen: dict, quad: str, x: int | None = None, y: int | None = None, width: int | None = None,
                  height: int | None = None, left: int | None = None, tol: int = 1, allow_empty: bool = False) -> Check:
    """Every quad of texture `quad` sits at the given layout `x`, `y` and has the given `width`, `height`, within
    `tol` (a strip title at its row; a tag at its size). `left` is the leftmost quad's x, for a grid such as the
    weapon row's skill squares (columns from x 1014). Skipped fields are not checked."""
    qs = select_quads(screen, quad)
    if not qs:
        return _empty("quads=0", allow_empty)
    want = {"x": x, "y": y, "width": width, "height": height}
    bad = [{k: q[k] for k in want} for q in qs if any(v is not None and abs(q[k] - v) > tol for k, v in want.items())]
    leftmost = min(q["x"] for q in qs)
    ok = not bad and (left is None or abs(leftmost - left) <= tol)
    want["left"] = left
    return Check(ok, {"quads": len(qs), "want": {k: v for k, v in want.items() if v is not None}, "tol": tol,
                      "bad": bad, "left": leftmost})


def no_cjk(screen: dict, text: dict | None = None, allow_empty: bool = False) -> Check:
    """No Japanese or fullwidth character in drawn text. Reports where, not what."""
    ts = _texts(screen, text)
    if not ts:
        return _empty("texts=0", allow_empty)
    bad = [{"at": (t["x"], t["y"]), "caller": t["caller"], "chars": len(CJK.findall(t["text"]))} for t in ts if CJK.search(t["text"])]
    return Check(not bad, {"texts": len(ts), "bad": bad})


def text_has_ink(screen: dict, img: Image.Image, text: dict | None, delta: int = 60, min_pixels: int = 20,
                 allow_empty: bool = False) -> Check:
    """Something is drawn inside each reported text rect: at least `min_pixels`
    capture pixels differ from the rect's median luminance by more than `delta`.
    Shortcut: a busy scene behind the text can fake ink; use on plain panels."""
    ts = _texts(screen, text)
    if not ts:
        return _empty("texts=0", allow_empty)
    g, s = _Gray(img), _scale(img)
    counts = []
    for t in ts:
        x0, y0, x1, y1 = _cap(text_rect(t), s, img)
        px = sorted(v for row in g.box(x0, y0, x1, y1) for v in row)
        med = px[len(px) // 2] if px else 0
        counts.append(sum(1 for v in px if abs(v - med) > delta))
    return Check(all(c >= min_pixels for c in counts), {"ink_pixels": counts, "min_pixels": min_pixels})


_SIDES = {  # side -> (inside point, outside point) of edge pixel `e` at position `p` along it
    "right": lambda r, p, k: ((r[2] - 1, p), (r[2] - 1 + k, p)),
    "left": lambda r, p, k: ((r[0], p), (r[0] - k, p)),
    "bottom": lambda r, p, k: ((p, r[3] - 1), (p, r[3] - 1 + k)),
    "top": lambda r, p, k: ((p, r[1]), (p, r[1] - k)),
}


def ink_clear_of_quad_edge(screen: dict, img: Image.Image, quad: str, side: str = "right", delta: int = 60,
                           gap: int = 4, min_rows: int = 3, allow_empty: bool = False) -> Check:
    """Art in a quad does not run into its `side` edge (label cut at its quad).
    A pixel row counts as cut when the last pixel inside differs from the pixel
    `gap` capture px outside by more than `delta`. Fails at `min_rows` such rows."""
    qs = select_quads(screen, quad)
    if not qs:
        return _empty("quads=0", allow_empty)
    g, s = _Gray(img), _scale(img)
    cut = []
    for q in qs:
        r = _cap(_quad_rect(q), s, img)
        along = range(r[1], r[3]) if side in ("left", "right") else range(r[0], r[2])
        n = 0
        for p in along:
            (ix, iy), (ox, oy) = _SIDES[side](r, p, gap)
            if 0 <= ox < g.w and 0 <= oy < g.h and abs(g.at(ix, iy) - g.at(ox, oy)) > delta:
                n += 1
        cut.append({"quad_at": (q["x"], q["y"]), "cut_rows": n})
    return Check(all(c["cut_rows"] < min_rows for c in cut), {"side": side, "quads": cut, "min_rows": min_rows})


def text_centred_in_box(screen: dict, img: Image.Image, text: dict | None, tol: float = 3.0, dark: int = 40,
                        ink: int = 120, pad: int = 4, allow_empty: bool = False) -> Check:
    """Text ink is centred in its plate's dark box (+-`tol` layout px) and inside it.
    Ink: luminance > `ink` inside the text rect. Box: the widest run of pixels
    darker than `dark` that holds the ink, on the rows `pad` capture px above
    and below the ink (the slanted plate ends drop out of the intersection)."""
    ts = _texts(screen, text)
    if not ts:
        return _empty("texts=0", allow_empty)
    g, s = _Gray(img), _scale(img)
    out = []
    for t in ts:
        x0, y0, x1, y1 = _cap((t["x"], t["y"], t["x"] + t["width"], t["y"] + TEXT_H), s, img)
        cols = [x for x in range(x0, x1) if any(v > ink for v in (g.at(x, y) for y in range(y0, y1)))]
        rows = [y for y in range(y0, y1) if any(g.at(x, y) > ink for x in range(x0, x1))]
        if not cols:
            out.append({"at": (t["x"], t["y"]), "error": "no ink", "ok": False})
            continue
        i0, i1, yt, yb = cols[0], cols[-1], rows[0] - pad, rows[-1] + pad
        if yt < 0 or yb >= g.h:
            out.append({"at": (t["x"], t["y"]), "error": "no rows around ink", "ok": False})
            continue
        runs: list[list[int]] = []
        for x in range(g.w):
            if g.at(x, yt) < dark and g.at(x, yb) < dark:
                if runs and x == runs[-1][1] + 1:
                    runs[-1][1] = x
                else:
                    runs.append([x, x])
        runs = [r for r in runs if r[0] <= (i0 + i1) // 2 <= r[1]] or [r for r in runs if r[0] <= i1 and r[1] >= i0]
        if not runs:
            out.append({"at": (t["x"], t["y"]), "error": "no dark box", "ok": False})
            continue
        b0, b1 = max(runs, key=lambda r: r[1] - r[0])
        off = ((i0 + i1) - (b0 + b1)) / 2 / s
        row = {"at": (t["x"], t["y"]), "box": (b0, b1), "ink": (i0, i1), "gap_l": i0 - b0, "gap_r": b1 - i1,
               "off_layout": round(off, 1), "ok": abs(off) <= tol and i0 >= b0 and i1 <= b1}
        out.append(row)
    return Check(all(o["ok"] for o in out), {"tol": tol, "texts": out})


# kind -> (function, needs the capture)
ASSERTIONS: dict[str, tuple[Callable[..., Check], bool]] = {
    "text_clear_of_quad": (text_clear_of_quad, False),
    "text_clear_of_text": (text_clear_of_text, False),
    "line_count": (line_count, False),
    "text_within_edge": (text_within_edge, False),
    "text_position": (text_position, False),
    "quad_position": (quad_position, False),
    "no_cjk": (no_cjk, False),
    "text_has_ink": (text_has_ink, True),
    "ink_clear_of_quad_edge": (ink_clear_of_quad_edge, True),
    "text_centred_in_box": (text_centred_in_box, True),
}
INK_WIDTH = {"text_clear_of_quad", "text_clear_of_text", "text_within_edge"}  # take the capture as `img=`
SCREEN_KEYS = {"name", "route", "fixture", "battle", "script", "wait", "assert"}


# ---- spec and runner -------------------------------------------------------

def load_spec(path: Path) -> list[dict]:
    """Read a check file and reject unknown keys or assertion kinds before any run."""
    screens = tomllib.loads(Path(path).read_text(encoding="utf-8")).get("screen", [])
    for sc in screens:
        extra = set(sc) - SCREEN_KEYS
        if extra or "name" not in sc or "route" not in sc:
            raise ValueError(f"screen {sc.get('name')!r}: needs name and route, unknown keys {sorted(extra)}")
        for a in sc.get("assert", []):
            if a.get("kind") not in ASSERTIONS:
                raise ValueError(f"screen {sc['name']!r}: unknown assertion kind {a.get('kind')!r}")
            for sel in (a.get("text"), a.get("other")):
                # callers inside our DLL move with every build; a spec must name stock code or a position
                if isinstance(sel, dict) and int(sel.get("caller", "0"), 16) >= DLL_BASE:
                    raise ValueError(f"screen {sc['name']!r}: caller {sel['caller']} is inside the runtime DLL; "
                                     "select by a stock caller or by x/y")
    return screens


def run_spec(screens: list[dict], driver) -> list[Row]:
    """Reach each screen through the driver, capture it, run its assertions.
    Driver: route(name, fixture=None, battle=None, script=None),
    wait(cond: dict) -> bool, screen() -> dict, capture() -> path."""
    rows: list[Row] = []
    for sc in screens:
        name = sc["name"]
        try:
            driver.route(sc["route"], fixture=sc.get("fixture"), battle=sc.get("battle"), script=sc.get("script"))
            if sc.get("wait") and not driver.wait(dict(sc["wait"])):
                rows.append(Row(name, "wait", False, {"wait": sc["wait"]}, None))
                continue
            data, png = driver.screen(), None
            if any(ASSERTIONS[a["kind"]][1] or a["kind"] in INK_WIDTH for a in sc.get("assert", [])):
                png = str(driver.capture())
        except Exception as e:  # a failed route must not hide the other screens
            rows.append(Row(name, "reach", False, {"error": f"{type(e).__name__}: {e}"}, None))
            continue
        img = Image.open(png).convert("RGB") if png else None
        for a in sc.get("assert", []):
            params = {k: v for k, v in a.items() if k != "kind"}
            fn, needs_img = ASSERTIONS[a["kind"]]
            c = fn(data, img, **params) if needs_img else fn(data, **params, **({"img": img} if a["kind"] in INK_WIDTH else {}))
            rows.append(Row(name, a["kind"], c.ok, c.numbers, png if needs_img or a["kind"] in INK_WIDTH else None))
    return rows


def format_table(rows: list[Row]) -> str:
    return "\n".join(f"{'PASS' if r.ok else 'FAIL'}  {r.screen}  {r.assertion}  {r.numbers}" for r in rows)
