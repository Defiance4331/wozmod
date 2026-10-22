"""The runtime's glyph renderer (runtime/glyph_raster.h): coverage, stems and heights on whole pixels."""

from __future__ import annotations

import ctypes

import pytest


ZONES = (-185, -12, 655, 669, 860, 872, 965)  # desc, base_over, xh, xh_over, cap, cap_over, asc (the face's)


@pytest.fixture(scope="module")
def raster(c_library):
    return c_library("raster", r'''
#include "glyph_raster.h"
void polygon(const float *pts, int n, int w, int h, unsigned char *out)
{
    float acc[64 * 66] = {0};
    int i;
    for (i = 0; i < n; i++)
        gr_line(acc, w + 2, h, pts[2 * i], pts[2 * i + 1], pts[2 * ((i + 1) % n)], pts[2 * ((i + 1) % n) + 1]);
    gr_fill(acc, w + 2, w, h, out, w);
}
void soften(unsigned char *px, int w, int h, int side)
{
    gr_soften(px, w, h, w, side);
}
float fit(const int *advance, int count, float box, float scale, float factor)
{
    return gr_fit(advance, count, box, scale, factor);
}
int step(int advance, float scale)
{
    return gr_step(advance, scale);
}
void across(const short *stems, int count, float offset, float scale, const float *in, int n, float *out)
{
    struct gr_warp w = gr_across(stems, count, offset, scale);
    int i;
    for (i = 0; i < n; i++)
        out[i] = gr_map(&w, in[i]);
}
void up(const short *z, const short *bars, int count, float scale, const float *in, int n, float *out)
{
    struct gr_zones zones = {z[0], z[1], z[2], z[3], z[4], z[5], z[6]};
    struct gr_warp w = gr_up(&zones, bars, count, scale);
    int i;
    for (i = 0; i < n; i++)
        out[i] = gr_map(&w, in[i]);
}
''')


def floats(values):
    return (ctypes.c_float * len(values))(*values)


def area_coverage(poly, w, h, n=64):
    """Each pixel's covered fraction by n x n point sampling (an oracle independent of the area maths)."""
    def inside(px, py):
        wind = 0
        for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1], strict=True):
            cross = (bx - ax) * (py - ay) - (px - ax) * (by - ay)
            if ay <= py < by and cross > 0:
                wind += 1
            elif by <= py < ay and cross < 0:
                wind -= 1
        return wind != 0
    return [[sum(inside(x + (i + 0.5) / n, y + (j + 0.5) / n) for i in range(n) for j in range(n)) / n / n
             for x in range(w)] for y in range(h)]


@pytest.mark.slow_first
def test_coverage_matches_the_covered_area_of_each_pixel(raster):
    # Catches a wrong area split at a pixel edge or across a sloped edge, which shows as ragged or swollen glyphs.
    w, h = 12, 10
    for poly in ([(1.5, 1.0), (1.5, 8.25), (6.75, 8.25), (6.75, 1.0)],          # a stem with part-covered edges
                 [(2.0, 1.0), (10.6, 8.7), (3.2, 8.7)],                        # sloped sides
                 [(5.0, 0.5), (9.0, 4.5), (5.0, 8.5), (1.0, 4.5)]):            # a diamond: every slope sign
        out = (ctypes.c_ubyte * (w * h))()
        raster.polygon(floats([v for p in poly for v in p]), len(poly), w, h, out)
        want = area_coverage(poly, w, h)
        worst = max(abs(out[y * w + x] / 255 - want[y][x]) for y in range(h) for x in range(w))
        assert worst < 0.02, poly


def test_stems_of_one_width_land_on_whole_pixels_with_one_width_at_every_scale(raster):
    # Catches the bold "n": the same stem drawn 2 px in one letter and 3 px in another because its sides fall
    # at different fractions of a pixel. Stems of one width (123 units; the export evens them) placed as in n, b, d.
    for scale in [0.02 + 0.0025 * i for i in range(17)]:  # 24 px cells at 1x to 3x screens
        widths = set()
        for stems, offset in (((76, 199, 576, 699), 37.5), ((78, 201, 626, 749), 12.0), ((51, 174, 601, 724), 61.0)):
            pairs = (ctypes.c_short * 4)(*stems)
            edges = [v + offset for v in stems]
            out = (ctypes.c_float * 4)()
            raster.across(pairs, 2, ctypes.c_float(offset), ctypes.c_float(scale), floats(edges), 4, out)
            assert all(abs(v - round(v)) < 1e-4 for v in out), (scale, list(out))
            widths |= {round(out[1] - out[0]), round(out[3] - out[2])}
        assert len(widths) == 1 and min(widths) >= 1, (scale, widths)


def test_every_height_lands_on_its_nearest_row_overshoot_included(raster):
    # Catches the baseline, x-height or cap height left between rows (round letters then read a pixel low, the
    # raised "iz" in "Seize"), and an overshoot bent away instead of rounded like any other length, which makes
    # round letters shorter than the font draws them.
    zones = (ctypes.c_short * 7)(*ZONES)
    probes = [0, -12, 655, 669, 860, 872]
    for scale in (0.02, 0.025, 0.03, 0.035, 0.045, 0.06, 0.09):  # 24 px cells at 1x to 4.5x
        out = (ctypes.c_float * len(probes))()
        raster.up(zones, None, 0, ctypes.c_float(scale), floats(probes), len(probes), out)
        xh, cap = round(655 * scale), round(860 * scale)
        assert list(out) == [0, -round(12 * scale), xh, xh + round(14 * scale), cap, cap + round(12 * scale)], scale


def test_a_round_letters_bottom_and_top_strokes_keep_one_thickness(raster):
    # Catches the thin bottom of s and a: a bottom stroke whose outer edge moves to a row while its inner edge stays,
    # taking the difference off the stroke, so the bottom reads lighter than the top.
    zones = (ctypes.c_short * 7)(*ZONES)
    s_bars = (ctypes.c_short * 4)(-10, 97, 558, 665)  # the s: bottom and top strokes, 107 units each
    flat = (ctypes.c_short * 4)(0, 113, 748, 861)     # an E: bottom and top arms on the line and the cap height
    for scale in (0.02, 0.025, 0.03, 0.035, 0.045, 0.06, 0.09):
        out = (ctypes.c_float * 4)()
        raster.up(zones, s_bars, 2, ctypes.c_float(scale), floats([-10, 97, 558, 665]), 4, out)
        assert out[1] - out[0] == out[3] - out[2] == max(1, round(107 * scale)), (scale, list(out))
        assert out[0] == -round(10 * scale) and out[3] == round(655 * scale) + round(10 * scale)
        flat_out = (ctypes.c_float * 2)()
        raster.up(zones, flat, 2, ctypes.c_float(scale), floats([0, 861]), 2, flat_out)
        assert flat_out[0] == 0 and flat_out[1] == round(860 * scale)


def test_softening_spreads_ink_evenly_without_moving_or_losing_it(raster):
    # Catches a blur that shifts every glyph a pixel one way (an off-by-one in the running neighbours) or thins or
    # thickens the text (ink lost or gained), which would undo the even weight the softer levels must keep.
    w, h = 9, 7
    for side in (38, 64):
        stem = [255 if x == 4 and 1 <= y <= 5 else 0 for y in range(h) for x in range(w)]
        px = (ctypes.c_ubyte * (w * h))(*stem)
        raster.soften(px, w, h, side)
        rows = [[px[y * w + x] for x in range(w)] for y in range(h)]
        assert all(r[3] == r[5] > 0 and r[2] == r[6] == 0 for r in rows[2:5]), side  # spread evenly both ways
        assert abs(sum(px) - sum(stem)) <= len(stem) // 4, side                       # rounding only
    px = (ctypes.c_ubyte * (w * h))(*stem)
    raster.soften(px, w, h, 0)
    assert list(px) == stem


def test_a_step_is_the_games_advance_in_whole_pixels_with_halves_split_both_ways(raster):
    # Catches steps that round every half the same way, which makes every line that much longer (or shorter) than
    # the game laid it out: text then runs past its box, or bunches up.
    raster.step.restype = ctypes.c_int
    f = lambda advance, scale: raster.step(advance, ctypes.c_float(scale))
    assert [f(a, 1.5) for a in (14, 15, 16, 17, 7, 9)] == [21, 22, 24, 26, 10, 14]  # 22.5 -> 22, 25.5 -> 26
    halves = [a for a in range(4, 24) if (a * 1.5) % 1]
    assert sum(f(a, 1.5) - a * 1.5 for a in halves) == 0  # as many halves round up as down


def test_a_squished_line_fits_its_box_as_the_glyphs_step_through_it(raster):
    # Catches the weapon-attribute line drawn 5 px past its box: the squish chosen from the exact width while each
    # letter steps by a whole pixel, which here rounds up for most letters.
    raster.fit.restype = raster.step.restype = ctypes.c_float
    raster.step.restype = ctypes.c_int
    advances = [13] * 37  # each exact step 16.58 px: every one rounds up
    box, scale = 409, 1.5
    exact = box / sum(advances)
    steps = lambda f: sum(raster.step(a, ctypes.c_float(scale * f)) for a in advances)
    factor = raster.fit((ctypes.c_int * len(advances))(*advances), len(advances), ctypes.c_float(box),
                        ctypes.c_float(scale), ctypes.c_float(exact))
    assert steps(exact) > box * scale  # the exact-width squish overflows
    assert steps(factor) <= box * scale and factor > 0.97 * exact
    assert raster.fit((ctypes.c_int * 3)(10, 10, 10), 3, ctypes.c_float(400), ctypes.c_float(scale),
                      ctypes.c_float(1.0)) == 1.0  # room to spare: no squish
