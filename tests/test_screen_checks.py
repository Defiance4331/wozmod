"""Layout assertions over screen JSON and capture PNGs, on synthetic input in tmp_path."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("screen_checks", REPO / "tools" / "screen_checks.py")
sc = importlib.util.module_from_spec(_spec)
sys.modules["screen_checks"] = sc
_spec.loader.exec_module(sc)


def text(x, y, width, string="abc", caller="0x1"):
    return {"x": x, "y": y, "width": width, "text": string, "caller": caller}


def quad(key, x, y, w, h):
    return {"texture": key, "x": x, "y": y, "width": w, "height": h, "caller": "0x2", "count": 1}


def screen(texts=(), textures=()):
    return {"texts": list(texts), "textures": list(textures)}


def canvas(width=1280, bg=90):
    return Image.new("RGB", (width, width * 9 // 16), (bg, bg, bg))


def test_text_touching_a_quad_passes_and_one_pixel_of_overlap_fails():
    """Bug: `<=` in the intersection test flags a name that starts exactly where the thumbnail ends."""
    q = [quad("1.1", 107, 300, 42, 38)]
    assert sc.text_clear_of_quad(screen([text(149, 304, 100)], q), None, "1.1").ok
    c = sc.text_clear_of_quad(screen([text(148, 304, 100)], q), None, "1.1")
    assert not c.ok and c.numbers["hits"][0]["overlap_w"] == 1


def test_wrapped_second_line_counts_toward_the_text_rect():
    """Bug: rect height from one line only, so a wrapped line that lands on the next block goes unseen."""
    one = screen([text(549, 413, 500, "a"), text(549, 451, 800, "b")])
    two = screen([text(549, 413, 500, "a\nb"), text(549, 451, 800, "c")])
    assert sc.text_clear_of_text(one, {"caller": "0x1"}).ok is True
    assert not sc.text_clear_of_text(two, {"match": "^a"}, {"match": "^c"}).ok


def test_one_text_drawn_by_two_callers_is_not_its_own_overlap():
    """Bug: the runtime logs each text twice (draw site and wrapper); pairing them reports a false collision."""
    s = screen([text(10, 10, 100, "same", "0xa"), text(10, 10, 100, "same", "0xb")])
    assert sc.text_clear_of_text(s, None, allow_empty=True).ok
    assert len(sc.select_texts(s)) == 1


def test_selector_that_matches_nothing_fails_unless_allowed():
    """Bug: a wrong caller on the wrong screen passes every layout check vacuously."""
    s = screen([text(0, 0, 10)], [quad("1.1", 0, 0, 5, 5)])
    assert not sc.text_clear_of_quad(s, {"caller": "0xnone"}, "1.1").ok
    assert not sc.text_clear_of_quad(s, None, "9.9").ok
    assert sc.text_clear_of_quad(s, {"caller": "0xnone"}, "1.1", allow_empty=True).ok


def test_quad_key_wildcard_and_identical_quad_rows_count_once():
    """Bug: a duplicated quad row doubles the hits; `key.*` must cover every member of a package."""
    s = screen([], [quad("4064.5", 0, 0, 1, 1), quad("4064.5", 0, 0, 1, 1), quad("4064.7", 5, 0, 1, 1), quad("4065.5", 0, 0, 1, 1)])
    assert len(sc.select_quads(s, "4064.*")) == 2


def test_text_past_the_edge_is_reported_with_the_excess():
    """Bug: narration drawn as one long line passes when only x is compared to the edge."""
    c = sc.text_within_edge(screen([text(360, 507, 2565)]), None)
    assert not c.ok and c.numbers["worst_over"] == 2565 + 360 - 1280
    assert sc.text_within_edge(screen([text(200, 507, 1080)]), None).ok


def test_squished_text_is_measured_by_its_ink_not_its_reported_width():
    """Bug: the JSON width is taken before the lane squish, so a name squished to end at its lane edge fails
    the lane and the thumbnail checks; trimming must still see a name that is really drawn under the thumbnail."""
    img = canvas(1920)
    d = ImageDraw.Draw(img)
    d.rectangle((300, 760, 300 + 449, 775), fill=(240, 240, 240))  # layout (200,500): ink 300 layout px wide
    reported = screen([text(200, 500, 500)], [quad("1.1", 520, 495, 60, 40)])  # pre-squish width 500 reaches the quad
    assert not sc.text_within_edge(reported, None, edge=520).ok and not sc.text_clear_of_quad(reported, None, "1.1").ok
    c = sc.text_within_edge(reported, None, edge=505, img=img)
    assert c.ok and c.numbers["right_ends"] == [500] and c.numbers["reported_ends"] == [700]
    assert sc.text_clear_of_quad(reported, None, "1.1", img=img).ok
    d.rectangle((300, 760, 300 + 449 + 300, 775), fill=(240, 240, 240))  # unsquished: the ink runs on to the quad
    assert not sc.text_clear_of_quad(reported, None, "1.1", img=img).ok


def test_ink_false_judges_a_typed_out_page_by_its_reported_width():
    """Bug: a narration page captured mid-reveal shows only its first word, so ink-based trimming passes a
    page whose full text runs past the edge."""
    img = canvas(1920)
    ImageDraw.Draw(img).rectangle((300, 760, 449, 775), fill=(240, 240, 240))  # only the start is drawn yet
    page = screen([text(200, 507, 1200)])
    assert sc.text_within_edge(page, None, edge=942, img=img).ok  # the trimmed ink ends at 300
    c = sc.text_within_edge(page, None, edge=942, img=img, ink=False)
    assert not c.ok and c.numbers["right_ends"] == [1400]


def test_text_position_flags_the_prompt_bar_that_slid_and_honours_the_tolerance():
    """Bug: the bar ending at 1184 (the old build) passes because only `x` is read, or a 1 px tolerance is read as exclusive."""
    bar = {"caller": "0x55dfd8"}
    fixed = screen([text(1007, 652, 185, caller="0x55dfd8")])
    old = screen([text(999, 652, 185, caller="0x55dfd8")])
    assert sc.text_position(fixed, bar, y=652, right=1192).ok
    c = sc.text_position(old, bar, y=652, right=1192)
    assert not c.ok and c.numbers["bad"] == [{"x": 999, "y": 652, "right": 1184}]
    assert sc.text_position(screen([text(1007, 653, 186, caller="0x55dfd8")]), bar, y=652, right=1192).ok  # 1 px off
    assert not sc.text_position(screen([text(1007, 654, 185, caller="0x55dfd8")]), bar, y=652).ok


def test_quad_position_flags_the_skill_squares_left_at_the_old_x_and_a_condensed_label():
    """Bug: squares drawn at 886 (the X360 lane) or a label condensed to 56 px pass, because quads are never measured
    or only the first of a row is."""
    row = [quad("4079.82", 1014, 300, 24, 24), quad("4079.82", 1014, 340, 24, 24)]
    assert sc.quad_position(screen(textures=row), "4079.82", x=1014).ok
    c = sc.quad_position(screen(textures=row[:1] + [quad("4079.82", 886, 340, 24, 24)]), "4079.82", x=1014)
    assert not c.ok and c.numbers["bad"] == [{"x": 886, "y": 340, "width": 24, "height": 24}]
    grid = [quad("4079.82", x, y, 14, 14) for x in (1014, 1029) for y in (160, 175)]
    assert sc.quad_position(screen(textures=grid), "4079.82", left=1014).ok
    assert not sc.quad_position(screen(textures=[dict(q, x=q["x"] - 128) for q in grid]), "4079.82", left=1014).ok
    assert not sc.quad_position(screen(textures=[quad("4079.83", 500, 300, 56, 20)]), "4079.83", width=96).ok
    assert sc.quad_position(screen(textures=[quad("4079.83", 500, 300, 97, 20)]), "4079.83", width=96).ok  # 1 px tol


def test_line_count_bounds_use_newlines():
    """Bug: CONDITIONS wrapping to two lines goes unnoticed when width alone is compared."""
    s = screen([text(0, 0, 50, "a\nb")])
    assert not sc.line_count(s, None, max=1).ok
    assert sc.line_count(s, None, max=2).ok


def test_cjk_is_flagged_but_pad_button_glyphs_are_not():
    """Bug: a range that swallows the private-use button icons flags every prompt bar; one that skips kana misses leftovers."""
    kana, kanji, wide = chr(0x30c6), chr(0x6f22), chr(0xff21)
    bad = screen([text(0, 0, 9, kana), text(0, 9, 9, kanji), text(0, 18, 9, wide), text(0, 27, 9, "OK")])
    c = sc.no_cjk(bad)
    assert not c.ok and [b["at"] for b in c.numbers["bad"]] == [(0, 0), (0, 9), (0, 18)]
    assert sc.no_cjk(screen([text(0, 0, 9, "Yew  Elm")])).ok


def test_text_ink_is_looked_for_at_capture_scale():
    """Bug: reading the rect at layout coordinates on a 1.5x capture finds the empty top-left and calls drawn text invisible."""
    img = canvas(1920)
    ImageDraw.Draw(img).rectangle((300, 750, 600, 770), fill=(240, 240, 240))  # layout (200,500)+
    s = screen([text(200, 500, 200)])
    assert sc.text_has_ink(s, img, None).ok
    assert not sc.text_has_ink(screen([text(700, 500, 200)]), img, None).ok  # same size rect elsewhere: nothing


def test_label_ink_on_the_quad_edge_fails_and_a_gap_passes():
    """Bug: comparing the wrong column (x instead of x-1, or outside against outside) misses a label cut at its quad."""
    s = screen([], [quad("4064.5", 100, 100, 200, 40)])
    cut, ok = canvas(), canvas()
    ImageDraw.Draw(cut).rectangle((250, 105, 299, 135), fill=(0, 0, 0))   # touches x=299, the last column
    ImageDraw.Draw(ok).rectangle((250, 105, 290, 135), fill=(0, 0, 0))    # ends 8 px short
    c = sc.ink_clear_of_quad_edge(s, cut, "4064.5")
    assert not c.ok and c.numbers["quads"][0]["cut_rows"] == 31
    assert sc.ink_clear_of_quad_edge(s, ok, "4064.5").ok


def _plate(img, box, ink):
    d = ImageDraw.Draw(img)
    d.rectangle((box[0], 440, box[1], 520), fill=(10, 10, 10))
    d.rectangle((ink[0], 462, ink[1], 480), fill=(240, 240, 240))


def test_name_centre_is_measured_against_the_dark_box_and_overflow_fails():
    """Bug: centring against the text rect instead of the ink/box, or ignoring ink that spills past the plate."""
    s = screen([text(300, 460, 200)])
    centred, shifted, spill = canvas(), canvas(), canvas()
    _plate(centred, (200, 600), (300, 500))
    _plate(shifted, (200, 600), (230, 430))
    _plate(spill, (200, 450), (300, 500))
    c = sc.text_centred_in_box(s, centred, None)
    assert c.ok and c.numbers["texts"][0]["box"] == (200, 600)
    assert not sc.text_centred_in_box(screen([text(230, 460, 200)]), shifted, None).ok
    o = sc.text_centred_in_box(s, spill, None)
    assert not o.ok and o.numbers["texts"][0]["gap_r"] < 0


class Driver:
    def __init__(self, tmp_path, fail_route=None, wait_ok=True):
        self.tmp, self.fail_route, self.wait_ok, self.log = tmp_path, fail_route, wait_ok, []

    def route(self, name, **kw):
        self.log.append(("route", name, kw))
        if name == self.fail_route:
            raise RuntimeError("stuck")

    def keys(self, names):
        self.log.append(("keys", names))

    def notice(self, text):
        self.log.append(("notice", text))

    def wait(self, cond):
        self.log.append(("wait", cond))
        return self.wait_ok

    def screen(self):
        return screen([text(10, 10, 40, "a\nb")], [])

    def capture(self):
        self.log.append(("capture",))
        path = self.tmp / "cap.png"
        canvas().save(path)
        return path


SPEC = [
    {"name": "one", "route": "r1", "assert": [{"kind": "line_count", "max": 1}]},
    {"name": "two", "route": "r2", "assert": [{"kind": "text_has_ink", "text": {"caller": "0x1"}}]},
]


def test_runner_reports_each_screen_and_a_failed_route_does_not_stop_the_rest(tmp_path):
    """Bug: an exception in one route aborts the table, or the capture path is dropped from image checks."""
    d = Driver(tmp_path, fail_route="r1")
    rows = sc.run_spec(SPEC, d)
    assert [(r.screen, r.assertion, r.ok) for r in rows] == [("one", "reach", False), ("two", "text_has_ink", False)]
    assert rows[0].capture is None and rows[1].capture == str(tmp_path / "cap.png")


def test_runner_skips_the_capture_when_no_assertion_reads_pixels_and_stops_on_wait_timeout(tmp_path):
    """Bug: capturing for every screen slows a tour; running assertions on a screen that never appeared reports false numbers."""
    d = Driver(tmp_path)
    rows = sc.run_spec([SPEC[0]], d)
    assert not rows[0].ok and rows[0].numbers["lines"] == [2] and ("capture",) not in d.log
    late = Driver(tmp_path, wait_ok=False)
    rows = sc.run_spec([{**SPEC[0], "wait": {"caller": "0x1"}}], late)
    assert [(r.assertion, r.ok) for r in rows] == [("wait", False)]


def test_runner_presses_a_screens_keys_and_posts_its_notice_after_its_route_and_before_reading_it(tmp_path):
    """Bug: the keys or the notice are dropped or sent before the route, so every gallery stop reads the first
    artwork and the calibration notice is posted outside the battle."""
    d = Driver(tmp_path)
    sc.run_spec([{**SPEC[0], "keys": ["Right", "Down"], "notice": "nnn", "wait": {"caller": "0x1"}}], d)
    assert [e[0] for e in d.log[:4]] == ["route", "keys", "notice", "wait"] and d.log[1][1] == ["Right", "Down"]


def test_spec_loader_rejects_unknown_kinds_and_keys_and_accepts_every_committed_spec(tmp_path):
    """Bug: a typo'd assertion kind or key is silently skipped, so the check never runs."""
    for body in ('[[screen]]\nname="a"\nroute="r"\n[[screen.assert]]\nkind="nope"\n',
                 '[[screen]]\nname="a"\nroute="r"\nrout="x"\n',
                 '[[screen]]\nname="a"\nroute="r"\npress=["Return"]\n'):
        p = tmp_path / "bad.toml"
        p.write_text(body)
        with pytest.raises(ValueError):
            sc.load_spec(p)
    for p in (REPO / "data" / "checks").glob("*.toml"):
        assert sc.load_spec(p)


def test_spec_loader_refuses_a_caller_inside_the_runtime_dll(tmp_path):
    """Bug: a spec selects text by a runtime DLL address, which moves with every build, so the
    check matches nothing on the next build instead of measuring the screen."""
    p = tmp_path / "dll.toml"
    p.write_text('[[screen]]\nname="a"\nroute="r"\n[[screen.assert]]\nkind="line_count"\nmax=1\n'
                 'text={caller="0x680051e3"}\n')
    with pytest.raises(ValueError, match="inside the runtime DLL"):
        sc.load_spec(p)


def test_a_banner_name_is_selected_by_its_plate_and_an_overhead_tag_crossing_the_row_is_not():
    # Catches the banner check reading an overhead officer tag that drifts across the banner's row (it failed with
    # the tag's right end, 1064), or picking up the dialog text elsewhere on screen.
    s = {**screen([text(964, 307, 239, "Name"), text(164, 470, 702, "dialog"), text(875, 288, 191, "Tag above"),
                   text(691, 332, 191, "Tag left")], []),
         "boxes": [{"x": 894, "y": 303, "width": 28, "height": 35, "caller": "0x5501dd", "count": 1}]}
    assert [t["text"] for t in sc._texts(s, {"on_plate": "0x5501dd|0x55006b"})] == ["Name"]
    assert sc._texts(s, {"on_plate": "0x550211"}) == []


def text_line(letters):
    """A 1080p capture of one text line at layout (100, 100): each letter a dark block (x, width, bottom row)
    on a light plate, 28 capture px tall from row 162; the screen JSON for it."""
    img = Image.new("RGB", (1920, 1080), (240, 240, 240))
    d = ImageDraw.Draw(img)
    for x, w, bottom in letters:
        d.rectangle((x, 162, x + w - 1, bottom - 1), fill=(40, 40, 40))
    return screen([text(100, 100, 220, "abcdefgh")], []), img


def test_a_bold_stem_among_regular_ones_is_reported_and_a_one_pixel_difference_is_not():
    # Catches the bold "n": one letter's stems drawn 3 px where the line's are 2 (a 50% jump). A 4 px stem among
    # 3 px ones looks the same to the user and must pass, or every line of today's text fails.
    regular = [(160 + 20 * i, 3, 190) for i in range(8)]
    s, img = text_line(regular[:4] + [(240, 5, 190)] + regular[5:])
    c = sc.text_stems_even(s, img, None)
    assert not c.ok and c.numbers["lines"][0]["odd"] == [5]
    s, img = text_line(regular[:4] + [(240, 4, 190)] + regular[5:])
    assert sc.text_stems_even(s, img, None).ok


def test_a_letter_off_the_line_is_reported_and_a_descender_is_not():
    # Catches the raised "iz": letters ending a pixel or two above or below the rest. A g or p hangs below on
    # purpose and must pass.
    on = [(160 + 20 * i, 10, 190) for i in range(8)]
    s, img = text_line(on[:3] + [(220, 10, 192)] + on[4:])
    c = sc.text_on_one_line(s, img, None)
    assert not c.ok and c.numbers["lines"][0]["off"] == [2.0]
    s, img = text_line(on[:3] + [(220, 10, 197)] + on[4:])
    assert sc.text_on_one_line(s, img, None).ok


def test_a_border_running_under_the_text_does_not_join_its_letters():
    # Catches the prompt bar's bottom border, inside the text's rect, joining every letter into one run, so the
    # line has no letters to judge and a low letter goes unreported.
    on = [(160 + 20 * i, 10, 190) for i in range(8)]
    s, img = text_line(on[:3] + [(220, 10, 192)] + on[4:])
    ImageDraw.Draw(img).rectangle((140, 193, 480, 194), fill=(180, 180, 180))  # a faint border in the rect, under the text
    c = sc.text_on_one_line(s, img, None)
    assert not c.ok and c.numbers["lines"][0]["off"] == [2.0]


def test_a_caption_line_is_checked_centred_by_the_width_table_not_by_a_fixed_start():
    # Catches a centring check pinned to a start x worked out with old letter widths: it fails a correctly centred
    # line once the widths change, and passes an off-centre one that happens to start there.
    from wozmod.runtime.latin_font import ADVANCES
    order = [chr(c) for c in range(0x20, 0x7E)]
    line = "Opening Movie [02Two]"
    width = sum(ADVANCES[order.index(c)] * 28 // 24 for c in "Opening Movie Two")  # a 28 px cell, each rounded down
    centred = screen([text(640 - width // 2, 616, 0, line, "0x56a116")], [])
    shifted = screen([text(640 - width // 2 + 9, 616, 0, line, "0x56a116")], [])
    assert sc.text_position(centred, {"caller": "0x56a116"}, centre=640, cell=28).ok
    assert not sc.text_position(shifted, {"caller": "0x56a116"}, centre=640, cell=28).ok
    assert not sc.text_position(centred, {"caller": "0x56a116"}, centre=640).ok  # measured at the wrong cell


def test_a_summary_is_checked_at_its_letter_cell_not_by_how_many_lines_it_fills():
    # Catches a site drawn at a smaller cell than the stock one: its lines still fit the column, and how many lines a
    # summary fills depends on the letter widths, so only the width the game reports at the cell can tell.
    from wozmod.runtime.latin_font import ADVANCES
    order = [chr(c) for c in range(0x20, 0x7E)]
    lines = ["Mm Mm Mm", "ii"]
    width = {cell: max(sum(ADVANCES[order.index(c)] * cell // 24 for c in line) for line in lines) for cell in (21, 24)}
    stock = screen([text(980, 408, width[24], "\n".join(lines))])
    smaller = screen([text(980, 408, width[21], "\n".join(lines))])
    assert sc.text_cell(stock, None, cell=24).ok
    assert not sc.text_cell(smaller, None, cell=24).ok


@pytest.mark.parametrize("size, area", [((3440, 1440), (440, 0, 2560, 1440)), ((1920, 1200), (0, 60, 1920, 1080)),
                                        ((3840, 2160), (0, 0, 3840, 2160))])
def test_checks_measure_the_centred_16_9_area_of_a_wider_or_taller_capture(size, area):
    """Bug: an ultrawide or 16:10 capture is measured from its corner, so every rect lands one bar off."""
    x, y, w, h = area
    img = Image.new("RGB", size, (0, 0, 0))
    ImageDraw.Draw(img).rectangle((x, y, x + w - 1, y + h - 1), fill=(90, 90, 90))
    cropped = sc.layout_area(img)
    assert cropped.size == (w, h) and cropped.getextrema() == ((90, 90),) * 3
