"""Layout rules learned from a stock frame and checked on ours, on synthetic frames."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for name in ("screen_checks", "layout_rules"):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
lr = sys.modules["layout_rules"]


def text(x, y, width, caller="0x500000"):
    return {"x": x, "y": y, "width": width, "text": "abc", "caller": caller}


def box(x, y, w, h, caller="0x500100"):
    return {"x": x, "y": y, "width": w, "height": h, "caller": caller, "count": 1}


def quad(key, x, y, w, h, caller="0x500200"):
    return {"texture": key, "x": x, "y": y, "width": w, "height": h, "caller": caller, "count": 1}


def frame(texts=(), boxes=(), textures=()):
    return {"texts": list(texts), "boxes": list(boxes), "textures": list(textures)}


def broken(stock, ours, rule):
    return [v for v in lr.check(ours, lr.learn(stock)) if v["rule"] == rule]


def test_a_box_sized_to_its_text_keeps_the_stock_gap_after_english():
    # Catches a banner sized the Japanese way (bytes times a glyph width), leaving a wide gap after a short name.
    stock = frame([text(900, 400, 80)], [box(890, 395, 100, 34)])
    hugging = frame([text(900, 400, 150)], [box(890, 395, 170, 34)])
    padded = frame([text(900, 400, 60)], [box(890, 395, 300, 34)])
    fixed = frame([text(900, 400, 40)], [box(890, 395, 100, 34)])  # a fixed plate is not sized to its text
    assert broken(stock, hugging, "hug") == [] and broken(stock, fixed, "hug") == []
    assert [v["gap_ours"] for v in broken(stock, padded, "hug")] == [230]


def test_a_cursor_box_keeps_its_item_inside():
    # Catches a highlight placed for art of another size, so it no longer surrounds the artwork.
    stock = frame(textures=[quad("4064.5", 104, 104, 92, 92)], boxes=[box(100, 100, 100, 100)])
    ours = frame(textures=[quad("4064.5", 124, 104, 92, 92)], boxes=[box(100, 100, 100, 100)])
    assert [v["box"] for v in broken(stock, ours, "inside")] == ["box@0x500100#0"]
    assert broken(stock, stock, "inside") == []


def test_a_centred_name_stays_centred_however_long():
    # Catches a name drawn from the box's left edge instead of its centre once the English is a different width.
    stock = frame([text(470, 300, 60)], [box(400, 290, 200, 40)])
    longer = frame([text(430, 300, 140)], [box(400, 290, 200, 40)])
    left = frame([text(410, 300, 140)], [box(400, 290, 200, 40)])
    assert broken(stock, longer, "centred") == []
    assert [round(v["offset"]) for v in broken(stock, left, "centred") if v["axis"] == "x"] == [-20]


def test_a_longer_text_may_not_grow_into_something_it_stayed_clear_of():
    # Catches a banner or label that runs into the minimap or a neighbouring element.
    stock = frame([text(900, 500, 120)], textures=[quad("4060.1", 1100, 480, 160, 160)])
    ours = frame([text(900, 500, 230)], textures=[quad("4060.1", 1100, 480, 160, 160)])
    assert [(v["el"], v["other"]) for v in broken(stock, ours, "clear")] == [("4060.1@0x500200#0", "text@0x500000#0")]
    assert broken(stock, frame([text(900, 500, 190)], textures=[quad("4060.1", 1100, 480, 160, 160)]), "clear") == []


def test_a_resized_element_keeps_a_left_centre_or_right_anchor():
    # Catches a menu that drifted sideways when it grew, instead of growing from its anchored edge or centre.
    stock = frame(boxes=[box(540, 200, 200, 300)])
    grown_centred = frame(boxes=[box(490, 200, 300, 300)])
    drifted = frame(boxes=[box(700, 200, 300, 300)])
    assert broken(stock, grown_centred, "place") == []
    assert len(broken(stock, drifted, "place")) == 1
    assert lr.check(drifted, lr.learn(stock), allow={("place", "box@0x500100#0")}) == []  # moved on purpose


def test_an_element_on_screen_stays_on_screen():
    # Catches a text or plate pushed past the screen edge.
    stock = frame([text(1100, 50, 100)])
    assert broken(stock, frame([text(1100, 50, 180)]), "on_screen") == []
    assert [v["rect"][2] for v in broken(stock, frame([text(1100, 50, 240)]), "on_screen")] == [1340]


def test_a_repeated_cursor_placed_differently_from_the_rest_is_reported():
    # Catches one gallery thumbnail whose highlight sits off it while the others line up.
    frames = [frame(textures=[quad("4064.5", 100 + 120 * n, 100, 92, 92)], boxes=[box(96 + 120 * n, 96, 100, 100)])
              for n in range(4)]
    frames[2]["boxes"] = [box(96 + 240 + 20, 96, 100, 100)]
    assert [v["frame"] for v in lr.siblings(frames, "box@", "4064.5@")] == [2]


def test_a_label_in_a_plate_sized_to_it_keeps_its_place_in_the_plate():
    # Catches anchoring a child to the screen: the in-battle label plate moves as it grows with its label, and the
    # label moves with it; the label drifting inside the plate is wrong.
    stock = frame([text(1056, 277, 72)], [box(1036, 270, 112, 40)])
    grown = frame([text(1080, 277, 123)], [box(1060, 270, 163, 40)])
    drifted = frame([text(1100, 277, 123)], [box(1060, 270, 163, 40)])
    assert [v for v in broken(stock, grown, "place") if v["el"].startswith("text@")] == []
    assert [v["el"] for v in broken(stock, drifted, "place") if v["el"].startswith("text@")] == ["text@0x500000#0"]


def test_a_panel_and_the_bar_under_it_stay_lined_up():
    # Catches moving a panel without the bar or arrow drawn along its edge (the gallery description bar left at
    # the stock x under the moved thumbnail panel).
    stock = frame(boxes=[box(484, 136, 584, 360, "0x557553"), box(484, 510, 584, 50, "0x557ab4")])
    moved = frame(boxes=[box(570, 136, 584, 360, "0x557553"), box(570, 510, 584, 50, "0x557ab4")])
    half = frame(boxes=[box(570, 136, 584, 360, "0x557553"), box(484, 510, 584, 50, "0x557ab4")])
    assert broken(stock, moved, "aligned") == []
    assert sorted(v["edge"] for v in broken(stock, half, "aligned")) == ["left", "right"]
