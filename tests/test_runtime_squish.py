"""Synthetic checks of the runtime's text-fit geometry."""

from __future__ import annotations

import ctypes

import pytest


@pytest.fixture(scope="module")
def fit_runtime(c_library):
    fit = c_library("fit", r'''
#include "text_fit.h"
double names(unsigned int width, unsigned int fixed, unsigned int room)
{
    return text_fit_names(width, fixed, room);
}
double factor(unsigned int text, unsigned int box, unsigned int align, int enabled)
{
    return text_fit(text, text, box, align, enabled).factor;
}
unsigned int point(unsigned int packed, double anchor, unsigned int text, unsigned int box,
    unsigned int align, int enabled, double pixels)
{
    return text_fit_x(packed, anchor, text_fit(text, text, box, align, enabled), pixels);
}
unsigned int point_line(unsigned int packed, double anchor, unsigned int run, unsigned int own,
    unsigned int box, unsigned int align, double pixels)
{
    return text_fit_x(packed, anchor, text_fit(run, own, box, align, 1), pixels);
}
int latin(const char *value)
{
    return text_fit_latin(value);
}
int drop(const char *value, char *out, unsigned int size)
{
    return text_drop_bad_icons(value, out, size) == value;
}
void quad(unsigned int *data, int four, double anchor, unsigned int text, unsigned int box,
    unsigned int align, int enabled, double pixels)
{
    text_fit_quad(data, four, anchor, text_fit(text, text, box, align, enabled), pixels);
}
''')
    fit.factor.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
    fit.factor.restype = ctypes.c_double
    fit.names.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
    fit.names.restype = ctypes.c_double
    fit.point.argtypes = [ctypes.c_uint, ctypes.c_double, ctypes.c_uint, ctypes.c_uint,
                         ctypes.c_uint, ctypes.c_int, ctypes.c_double]
    fit.point.restype = ctypes.c_uint
    fit.point_line.argtypes = [ctypes.c_uint, ctypes.c_double, ctypes.c_uint, ctypes.c_uint,
                              ctypes.c_uint, ctypes.c_uint, ctypes.c_double]
    fit.point_line.restype = ctypes.c_uint
    fit.latin.argtypes = [ctypes.c_char_p]
    fit.latin.restype = ctypes.c_int
    fit.drop.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    fit.drop.restype = ctypes.c_int
    fit.quad.argtypes = [ctypes.POINTER(ctypes.c_uint), ctypes.c_int, ctypes.c_double,
                        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_double]
    fit.quad.restype = None
    return fit


def test_fitting_off_or_unknown_boxes_do_not_stretch_glyphs(fit_runtime):
    for text, box, enabled in [(40, 80, 1), (80, 80, 1), (100, 0, 1), (100, 80, 0), (0, 80, 1)]:
        assert fit_runtime.factor(text, box, 0, enabled) == 1
        assert fit_runtime.point(0x12348123, 0x8000, text, box, 0, enabled, 16) == 0x12348123
    assert fit_runtime.factor(100, 80, 0, 1) == pytest.approx(0.8)


def test_condensing_keeps_the_left_centre_or_right_anchor(fit_runtime):
    # A 100-pixel run begins at 200 and must occupy an 80-pixel box.
    anchor = 0x8000 + 200 * 16
    original = [anchor, anchor + 24 * 16, anchor + 100 * 16]
    expected = [[200, 219.2, 280], [210, 229.2, 290], [220, 239.2, 300]]
    for align, positions in enumerate(expected):
        observed = [fit_runtime.point(x, anchor, 100, 80, align, 1, 16) for x in original]
        assert observed == [int(0x8000 + x * 16 + 0.5) for x in positions]


def test_a_shorter_caption_line_stays_centred_when_the_widest_line_sets_the_factor(fit_runtime):
    # A 900-pixel line of a caption whose widest line is 1500, in the 1200 box centred at 640.
    # The stock centres the line at x 190; at factor 0.8 it must span 280 to 1000.
    anchor = 0x8000 + 190 * 16
    ends = [fit_runtime.point_line(0x8000 + x * 16, anchor, 1500, 900, 1200, 1, 16) for x in (190, 1090)]
    assert ends == [0x8000 + x * 16 for x in (280, 1000)]


def test_non_native_condensing_does_not_use_native_scale_or_drop_fractional_rounding(fit_runtime):
    # Centred 101-pixel run in a 73-pixel box, with a 17-pixel advance, at 1920x1080.
    anchor = 0x8000 + 300 * 24
    source = [anchor, anchor + 24 * 24, anchor + 17 * 24, anchor + 41 * 24]
    result = [fit_runtime.point(x, anchor, 101, 73, 1, 1, 24) for x in source]
    assert result == [0x9D70, 0x9F10, 0x9E97, 0xA037]


def test_quad_condensing_does_not_shift_packed_stride_or_corrupt_uv_y_depth(fit_runtime):
    anchor = 0x8000 + 200 * 16
    cases = [([12, 16], [0, 24], [0, 19.2], 0),
             ([12, 18, 24, 30], [0, 24, 17, 41], [0, 19.2, 13.6, 32.8], 1)]
    for indices, offsets, condensed, four in cases:
        original = [0x12340000 + index * 101 for index in range(36)]
        expected = original.copy()
        for index, offset, fitted in zip(indices, offsets, condensed, strict=True):
            original[index] = anchor + offset * 16
            expected[index] = int(anchor + fitted * 16 + 0.5)
        packed = (ctypes.c_uint * len(original))(*original)
        fit_runtime.quad(packed, four, anchor, 100, 80, 0, 1, 16)
        assert list(packed) == expected


def test_latin_eligibility_does_not_reject_safe_icons_or_squish_truncated_special_tokens(fit_runtime):
    valid = [b"", b"Authored 42"]
    valid += [bytes([0x84, selector]) for selector in range(0x40, 0x45)]
    valid += [bytes([0xf0, selector]) for selector in range(0x40, 0x5a) if selector != 0x48]
    invalid = [None, b"\x84", b"\xf0", b"\x84\x3f", b"\x84\x45", b"\x85\x40",
               b"\xf0\x3f", b"\xf0\x5a", b"\xf0\x48", b"a\x84\x45b"]
    cases = [(value, 1) for value in valid] + [(value, 0) for value in invalid]
    assert [(value, fit_runtime.latin(value)) for value, _ in cases] == cases


def test_icon_codes_the_stock_draw_cannot_handle_are_dropped_without_misreading_tokens(fit_runtime):
    """Icon 0x56 from donor text sent the stock draw through its unhandled branch and crashed."""
    cases = [
        (b"Press \xf0U\xf0V now.", b"Press \xf0U now."),
        (b"\xf0H\xf0Ione  \xf0Atwo", None),                 # F0 48 takes F0 49 as its argument
        (b"\xf0K\xf0Z\xf0\x7f\xf0@", b"\xf0@"),         # 0x4B, 0x5A and above are unhandled too
        (b"\x82\xf0V", None),                              # 0xF0 is a trail byte here, V is plain text
        (b"A\xf0", b"A"),                                   # a cut-off token reaches the same branch
        (b"\xf0@\xf0Y", None),
    ]
    out = ctypes.create_string_buffer(64)
    got = []
    for value, expected in cases:
        same = fit_runtime.drop(value, out, len(out))
        got.append((value, None if same else out.value))
    assert got == cases


def test_a_squeezed_line_past_2048_pixels_stays_in_place_on_wide_screens(fit_runtime):
    """At 2560x1440 the 2D layout is 2560 px wide. The game stores a glyph corner's X as a whole dword,
    (x + 2048) * 16 (0x54A5A4), so past 2048 px it is just larger; a squeezed glyph there must not land
    4096 px to the left (the cut-off names and the white streaks)."""
    anchor = (1940 + 2048) * 16  # the line starts at 1940 px and is squeezed to two thirds
    got, want = [], []
    for left in range(1940, 2400, 24):  # one quad per glyph, two corners each
        quad = (ctypes.c_uint * 20)(*[0] * 20)
        quad[12], quad[16] = (left + 2048) * 16, (left + 24 + 2048) * 16
        fit_runtime.quad(quad, 0, anchor, 1500, 1000, 0, 1, 16)
        got += [quad[12], quad[16]]
        want += [int(anchor + ((x + 2048) * 16 - anchor) * 1000 / 1500 + 0.5) for x in (left, left + 24)]
    assert got == want


def test_names_sharing_a_lane_with_icons_shrink_alone_and_end_inside_it(fit_runtime):
    """The scenario army line ran under the RECORD rows; the icons and gaps between names keep their width."""
    room = 507
    for names, fixed in [([220, 214, 212], [68]),          # the three widest army names at cell 18
                         ([165, 160], [34, 90]),            # one name the game cannot condense
                         ([142, 188, 198], [68])]:          # a line that already fits
        f = fit_runtime.names(sum(names), sum(fixed), room)
        drawn = sum(int(w * f) for w in names) + sum(fixed)
        assert f == 1 if sum(names) + sum(fixed) <= room else room - len(names) <= drawn <= room
    assert fit_runtime.names(300, room, room) == 1     # nothing left to shrink into: drawn as is
