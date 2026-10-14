"""Layout rules learned from the stock game's frames and checked on ours.

A frame is the harness `screen` JSON (texts, textures, plain boxes). Each element gets an id from what drew it
(its texture, or a plain box or a text, plus the stock caller) and its rank among elements with that id, top to
bottom then left to right. A stock frame and ours pair element by element that way although the text differs.

English is wider than Japanese, so sizes never carry over. `learn` keeps only relations that hold whatever the
text length, and `check` applies them to our frame:

- inside: an element stays inside the smallest box that held it
- centred: an element centred in that box stays centred
- hug: a box that changes size with its text keeps the stock gap after the text
- clear: elements that did not touch still do not
- aligned: panels and art whose left or right edges lined up still do (a bar under a panel, an arrow at its edge)
- place: an element that changes size keeps its left edge, centre or right edge (in its box, if it has one)
- on_screen: an element on screen stays on screen

`siblings` compares repeats of one element against each other across frames (a cursor box over each item of a
list) and reports the ones placed differently from the rest.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from screen_checks import LAYOUT_W, TEXT_H, LINE_PITCH, drawn_texts
from wozmod.runtime.english_runtime import DLL_BASE

LAYOUT_H = 720
EDGE = 1  # layout px of slack for an element sitting on its box's edge
CENTRED = 1.5  # stock offset that counts as centred
TOL = 3  # layout px of drift allowed in our frame
HUG_SLACK = 12  # extra gap after the text a resized box may keep
BACKDROP = 0.5  # a box covering more of the screen than this is a backdrop, never a parent


def _rect(x, y, w, h):
    return (x, y, x + w, y + h)


def elements(frame: dict, img=None) -> dict[str, tuple[int, int, int, int]]:
    """Element id -> layout rect. Texts drawn by our DLL repeat a stock draw, so only stock callers count; with
    the capture, a text is as wide as its drawn ink (the reported width is before the runtime squishes it)."""
    found: dict[str, list] = {}
    texts = [t for t in frame.get("texts", []) if int(t["caller"], 16) < DLL_BASE]
    for t in drawn_texts(texts, img):
        lines = t["text"].count("\n") + 1
        found.setdefault(f"text@{t['caller']}", []).append(
            (t["x"], t["y"], t["x"] + t["width"], t["y"] + TEXT_H + (lines - 1) * LINE_PITCH))
    for q in frame.get("textures", []):
        found.setdefault(f"{q['texture']}@{q['caller']}", []).append(_rect(q["x"], q["y"], q["width"], q["height"]))
    for b in frame.get("boxes", []):
        found.setdefault(f"box@{b['caller']}", []).append(_rect(b["x"], b["y"], b["width"], b["height"]))
    out = {}
    for name, rects in found.items():
        for rank, r in enumerate(sorted(set(rects), key=lambda r: (r[1], r[0], r[2], r[3]))):
            out[f"{name}#{rank}"] = r
    return out


def _area(r):
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def _holds(outer, inner, slack=EDGE):
    return outer[0] - slack <= inner[0] and outer[1] - slack <= inner[1] and inner[2] <= outer[2] + slack \
        and inner[3] <= outer[3] + slack


def _touch(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _centre(r, axis):
    return (r[axis] + r[axis + 2]) / 2


def learn(frame: dict, img=None) -> list[dict]:
    """Relations of a stock frame that do not depend on text length, as JSON-ready dicts."""
    els = elements(frame, img)
    boxes = {k: r for k, r in els.items() if not k.startswith("text@") and _area(r) < BACKDROP * LAYOUT_W * LAYOUT_H}
    out, parent = [], {}
    for k, r in els.items():
        holders = [(b, _area(br)) for b, br in boxes.items() if b != k and _area(br) > _area(r) and _holds(br, r)]
        if holders:
            p = min(holders, key=lambda h: h[1])[0]
            parent[k] = p
            pr = boxes[p]
            out.append({"rule": "inside", "el": k, "box": p})
            for axis, name in ((0, "x"), (1, "y")):
                if abs(_centre(r, axis) - _centre(pr, axis)) <= CENTRED:
                    out.append({"rule": "centred", "el": k, "box": p, "axis": name})
            if k.startswith("text@"):
                out.append({"rule": "hug", "el": k, "box": p, "box_width": pr[2] - pr[0], "gap": pr[2] - r[2]})
        if 0 <= r[0] and 0 <= r[1] and r[2] <= LAYOUT_W and r[3] <= LAYOUT_H:
            out.append({"rule": "on_screen", "el": k})
        out.append({"rule": "place", "el": k, "rect": list(r), **({"box": parent[k], "box_rect": list(boxes[parent[k]])}
                                                                  if k in parent else {})})
    keys = sorted(els)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            ra, rb = els[a], els[b]
            related = parent.get(a) == b or parent.get(b) == a or _holds(ra, rb) or _holds(rb, ra)
            if not related and not _touch(ra, rb) and (a.startswith("text@") or b.startswith("text@")):
                out.append({"rule": "clear", "el": a, "other": b})
            if a in boxes and b in boxes:
                out += [{"rule": "aligned", "el": a, "other": b, "edge": edge}
                        for edge, i in (("left", 0), ("right", 2)) if abs(ra[i] - rb[i]) <= EDGE]
    return out


def check(frame: dict, relations: list[dict], img=None, allow: set[tuple[str, str]] = frozenset()) -> list[dict]:
    """Relations our frame breaks, with the numbers. `allow` holds (rule, element) pairs changed on purpose.
    An element missing from our frame is not checked: the screen may differ in what it shows."""
    els, bad = elements(frame, img), []
    for rel in relations:
        k = rel["el"]
        if (rel["rule"], k) in allow or k not in els:
            continue
        r = els[k]
        if rel["rule"] == "on_screen":
            if r[0] < 0 or r[1] < 0 or r[2] > LAYOUT_W or r[3] > LAYOUT_H:
                bad.append({**rel, "rect": r})
        elif rel["rule"] == "place":
            s = rel["rect"]
            if (s[2] - s[0], s[3] - s[1]) != (r[2] - r[0], r[3] - r[1]):
                # inside a box, the anchor is to the box: a plate sized to its label moves with it
                sb, b = (rel["box_rect"], els.get(rel["box"])) if "box" in rel else ((0, 0, 0, 0), (0, 0, 0, 0))
                if b is None:
                    continue
                kept = [abs((r[0] - b[0]) - (s[0] - sb[0])) <= TOL,
                        abs((_centre(r, 0) - _centre(b, 0)) - (_centre(s, 0) - _centre(sb, 0))) <= TOL,
                        abs((r[2] - b[2]) - (s[2] - sb[2])) <= TOL]
                if not any(kept):
                    bad.append({**rel, "stock": s, "ours": r})
        elif rel["rule"] == "aligned":
            o, i = els.get(rel["other"]), 0 if rel["edge"] == "left" else 2
            if o and abs(r[i] - o[i]) > TOL:
                bad.append({**rel, "rect": r, "other_rect": o})
        elif rel["rule"] == "clear":
            o = els.get(rel["other"])
            if o and _touch(r, o):
                bad.append({**rel, "rect": r, "other_rect": o})
        elif (b := els.get(rel["box"])) is not None:
            if rel["rule"] == "inside" and not _holds(b, r):
                bad.append({**rel, "rect": r, "box_rect": b})
            elif rel["rule"] == "centred":
                axis = 0 if rel["axis"] == "x" else 1
                if abs(off := _centre(r, axis) - _centre(b, axis)) > TOL:
                    bad.append({**rel, "offset": off})
            elif rel["rule"] == "hug" and abs((b[2] - b[0]) - rel["box_width"]) > EDGE:  # the box follows its text
                if (gap := b[2] - r[2]) > rel["gap"] + HUG_SLACK:
                    bad.append({**rel, "gap_ours": gap, "gap_stock": rel["gap"]})
    return bad


def siblings(frames: list[dict], box: str, el: str) -> list[dict]:
    """Across frames, the offset of element id prefix `el` inside the box id prefix `box` (a cursor over each
    item in turn); frames whose offset differs from the median by more than TOL."""
    seen = []
    for n, frame in enumerate(frames):
        els = elements(frame)
        for kb, b in ((k, r) for k, r in els.items() if k.startswith(box)):
            for ke, e in ((k, r) for k, r in els.items() if k.startswith(el) and _touch(r, b)):
                seen.append((n, ke, e[0] - b[0], e[1] - b[1], e[2] - b[2], e[3] - b[3]))
    if len(seen) < 3:
        return []
    med = [statistics.median(s[i] for s in seen) for i in range(2, 6)]
    return [{"frame": n, "el": ke, "offsets": offs, "median": med} for n, ke, *offs in seen
            if any(abs(o - m) > TOL for o, m in zip(offs, med, strict=True))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    one = sub.add_parser("learn", help="stock tour report -> structure file")
    one.add_argument("tour", type=Path)
    one.add_argument("output", type=Path)
    two = sub.add_parser("check", help="our tour report against a structure file")
    two.add_argument("tour", type=Path)
    two.add_argument("structure", type=Path)
    args = parser.parse_args(argv)
    report = json.loads(args.tour.read_text())
    if args.action == "learn":
        structure = {stop["name"]: learn(stop["screen"]) for stop in report["stops"]}
        args.output.write_text(json.dumps(structure, indent=1) + "\n")
        print(f"{sum(map(len, structure.values()))} relations over {len(structure)} screens")
        return 0
    structure, found = json.loads(args.structure.read_text()), 0
    for stop in report["stops"]:
        for v in check(stop["screen"], structure.get(stop["name"], [])):
            found += 1
            print(f"{stop['name']}  {json.dumps(v)}")
    print(f"{found} broken relations")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
