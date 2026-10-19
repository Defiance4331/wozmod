"""wozmod/glyphs.bin: the in-game font's outlines and the runtime glyph sheet's G1T header."""

from __future__ import annotations

import ctypes
import json
import struct

import pytest

from wozmod.art.build import glyph_sheet_header
from wozmod.fonts.glyph_outlines import bars, encode, even_stems, from_json, stems
from wozmod.formats.textures import G1T_HEADER, G1T_TEXTURE_HEADER, PIXEL_FORMAT_BGRA8, parse_g1t

ZONE = {"desc": -250, "base_over": -36, "xh": 655, "xh_over": 669, "cap": 860, "cap_over": 865, "asc": 880}


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


@pytest.fixture(scope="module")
def reader(c_library):
    return c_library("reader", r'''
#include "glyph_raster.h"
int head(const unsigned char *blob, unsigned int size, int *out)
{
    struct gr_font f;
    if (!gr_open(&f, blob, size))
        return 0;
    out[0] = f.base, out[1] = f.count, out[2] = f.zones.desc, out[3] = f.zones.base_over, out[4] = f.zones.xh;
    out[5] = f.zones.xh_over, out[6] = f.zones.cap, out[7] = f.zones.cap_over, out[8] = f.zones.asc;
    return 1;
}
int outline(const unsigned char *blob, unsigned int size, unsigned int number, int *out)
{
    struct gr_font f;
    struct gr_outline o;
    unsigned int i, n = 0;
    if (!gr_open(&f, blob, size) || !gr_outline(&f, number, &o))
        return 0;
    out[n++] = o.offset, out[n++] = o.stems, out[n++] = o.bars, out[n++] = o.contours, out[n++] = o.points;
    for (i = 0; i < 2 * o.stems; i++)
        out[n++] = o.stem[i];
    for (i = 0; i < 2 * o.bars; i++)
        out[n++] = o.bar[i];
    for (i = 0; i < o.contours; i++)
        out[n++] = o.end[i];
    for (i = 0; i < 2 * o.points; i++)
        out[n++] = o.point[i];
    return (int)n;
}
''')


def test_the_runtime_reads_back_every_field_the_build_writes(reader):
    # Catches a field written in one place and read from another (a shifted offset in the table or the data),
    # which the runtime would draw as garbage glyphs or none.
    glyphs = {33: {"offset": 37.5, "stems": [(76, 199), (576, 699)], "bars": [(546, 654)],
                   "contours": [rect(76, 0, 199, 655), rect(576, 0, 699, 655)]},
              71: {"offset": -12, "stems": [], "bars": [(-35, 72), (558, 665)],
                   "contours": [[(10, -36), (300, 669), (590, -20)]]}}
    blob = encode(glyphs, ZONE, 965)
    data = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    out = (ctypes.c_int * 64)()
    assert reader.head(data, len(blob), out)
    assert list(out[:9]) == [965, 2] + [ZONE[k] for k in ("desc", "base_over", "xh", "xh_over", "cap", "cap_over", "asc")]
    for number, g in glyphs.items():
        n = reader.outline(data, len(blob), number, out)
        points = [v for c in g["contours"] for p in c for v in p]
        ends = [sum(len(c) for c in g["contours"][:i + 1]) for i in range(len(g["contours"]))]
        assert list(out[:n]) == [round(g["offset"]), len(g["stems"]), len(g["bars"]), len(g["contours"]),
                                 len(points) // 2, *[v for s in g["stems"] for v in s],
                                 *[v for b in g["bars"] for v in b], *ends, *points]
    assert reader.outline(data, len(blob), 34, out) == 0
    assert reader.outline(data, len(blob) - 4, 71, out) == 0  # cut short: refused, not read past the end


def test_the_glyph_sheet_header_and_its_pixels_make_one_blank_texture(tmp_path):
    # Catches a header the game's texture loader would misread: a wrong file size, count or texture size.
    textures = [G1T_TEXTURE_HEADER.pack(1 << 4, PIXEL_FORMAT_BGRA8, 0x33, 0) + bytes(8 * 8 * 4) for _ in range(2)]
    table = struct.pack("<II", 8, 8 + len(textures[0]))
    body = table + b"".join(textures)
    atlas = G1T_HEADER.pack(b"GT1G", b"0600", 0x20 + len(body), 0x20, 2, 0, 0, 0) + body
    header = glyph_sheet_header(atlas, size=16)
    sheet = parse_g1t(header + bytes(16 * 16 * 4))
    assert len(sheet.textures) == 1
    t = sheet.textures[0]
    assert (t.width, t.height, t.pixel_format, t.mipmap_count) == (16, 16, PIXEL_FORMAT_BGRA8, 1)


def test_stems_are_the_straight_and_round_sides_once_each():
    # Catches a stem missed (that letter's strokes are not fitted and read soft or bold), or the dot of i taken
    # as a second stem over the first, which bends the outline two ways.
    n = [rect(76, 0, 199, 655), rect(576, 0, 699, 655), rect(199, 540, 576, 655)]
    ring = [[(300, -30), (600, 330), (300, 690), (0, 330)], [(300, 90), (120, 330), (300, 570), (480, 330)]]
    i = [rect(176, 0, 299, 655), rect(160, 760, 315, 900)]
    assert stems(n) == [(76, 199), (576, 699)]
    assert stems(i) == [(176, 299)]
    assert stems(ring) == [(0, 120), (480, 600)]
    assert bars(n) == [(540, 655)] and bars(ring) == [(-30, 90), (570, 690)]


def test_stem_widths_a_unit_apart_become_one_width_and_design_widths_stay_apart():
    # Catches outline rounding (122 against 123 units) splitting one stroke into 5 and 6 px at some sizes, and the
    # opposite: the deliberately thin stems of m merged into the regular ones.
    glyphs = {0: {"stems": [(76, 198), (576, 699)]}, 1: {"stems": [(78, 201)]}, 2: {"stems": [(85, 200)]}}
    even_stems(glyphs)
    widths = {g: [r - l for l, r in glyphs[g]["stems"]] for g in glyphs}
    assert widths[0] == [123, 123] and widths[1] == [123] and widths[2] == [115]


def test_outlines_shipped_as_json_keep_the_glyphs_in_number_order():
    # Bug: glyph numbers come back from JSON as strings, so glyph 10 sorts before glyph 3 and the runtime's table,
    # which it searches in number order, misses glyphs.
    square = {"offset": 5, "stems": [[0, 100]], "bars": [[0, 100]], "contours": [[[0, 0], [100, 0], [100, 100]]]}
    data = from_json(json.dumps({"zone": ZONE, "base": 950, "glyphs": {"10": square, "3": square}}).encode())
    head = struct.calcsize("<4sI7hHH")
    assert struct.unpack_from("<4sI7hHH", data)[-1] == 2
    assert [struct.unpack_from("<H", data, head + 14 * row)[0] for row in range(2)] == [3, 10]
