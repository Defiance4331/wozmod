"""The runtime's fit of the 2D layout and the HUD into each listed display size (runtime/display_fit.h)."""

from __future__ import annotations

import ctypes
import shutil
import subprocess

import pytest

from wozmod.runtime.english_runtime import C_SOURCE


class Fit(ctypes.Structure):
    _fields_ = [("layout_w", ctypes.c_uint), ("layout_h", ctypes.c_uint), ("scale", ctypes.c_uint),
                ("x", ctypes.c_int), ("y", ctypes.c_int), ("hud", ctypes.c_int)]


@pytest.fixture(scope="module")
def display(tmp_path_factory):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("a native C compiler is unavailable")
    directory = tmp_path_factory.mktemp("display-fit")
    (directory / "fit.c").write_text(r'''
#include "display_fit.h"
struct display_fit fit(unsigned int w, unsigned int h) { return display_fit(w, h); }
int size_index(unsigned int w, unsigned int h) { return display_size_index(w, h); }
void shift(unsigned int *p, unsigned int qwc, int d) { display_packet_shift(p, qwc, d); }
void clip(unsigned int w, unsigned int h, unsigned int *c) { struct display_fit f = display_fit(w, h); display_clip(&f, w, h, c); }
unsigned int sizes(unsigned short *out) { unsigned int i; for (i = 0; i < DISPLAY_SIZES; i++)
    out[2 * i] = display_sizes[i][0], out[2 * i + 1] = display_sizes[i][1]; return DISPLAY_SIZES; }
''')
    library = directory / "fit.so"
    subprocess.run([compiler, "-shared", "-fPIC", "-O2", "-I", str(C_SOURCE.parent), str(directory / "fit.c"),
                    "-o", str(library)], check=True)
    lib = ctypes.CDLL(str(library))
    lib.fit.restype = Fit
    return lib


# Hand-derived: the centred 16:9 area (height-bound when wider than 16:9), halved when taller than 2047 px; the HUD
# reaches the edges of the screen, at most a centred 12:5 area.
#  size          layout       scale  x     y   hud
FITS = {
    (1280, 720): (1280, 720, 1, 0, 0, 0),
    (1280, 800): (1280, 720, 1, 0, 40, 0),
    (1366, 768): (1365, 768, 1, 0, 0, 0),
    (1920, 1080): (1920, 1080, 1, 0, 0, 0),
    (1920, 1200): (1920, 1080, 1, 0, 60, 0),
    (2560, 1080): (1920, 1080, 1, 320, 0, 320),
    (2560, 1440): (2560, 1440, 1, 0, 0, 0),
    (2560, 1600): (2560, 1440, 1, 0, 80, 0),
    (3200, 1800): (3200, 1800, 1, 0, 0, 0),
    (3440, 1440): (2560, 1440, 1, 440, 0, 440),
    (3840, 1080): (1920, 1080, 1, 960, 0, 336),
    (3840, 1600): (2844, 1600, 1, 498, 0, 498),
    (3840, 2160): (1920, 1080, 2, 0, 0, 0),
    (5120, 1440): (2560, 1440, 1, 1280, 0, 448),
    (5120, 2160): (1920, 1080, 2, 640, 0, 320),
}


def test_every_listed_size_fits_its_layout_and_hud(display):
    """Bug: an off-by-one offset, a 2160-high layout left over the 16-bit limit, or a HUD past its 12:5 area."""
    wrong = {}
    for (w, h), expected in FITS.items():
        f = display.fit(w, h)
        got = (f.layout_w, f.layout_h, f.scale, f.x, f.y, f.hud)
        if got != expected:
            wrong[(w, h)] = got
    assert not wrong


def test_the_list_is_the_decided_one(display):
    """Bug: a size dropped from, or a 4:3 size left in, the decided list; or a lookup that misses one."""
    out = (ctypes.c_ushort * 64)()
    listed = {(out[2 * i], out[2 * i + 1]) for i in range(display.sizes(out))}
    decided = {(1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2560, 1440), (3200, 1800), (3840, 2160),
               (1280, 800), (1440, 900), (1680, 1050), (1920, 1200), (2560, 1600),
               (2560, 1080), (3440, 1440), (3840, 1600), (5120, 2160), (3840, 1080), (5120, 1440)}
    assert listed == decided
    assert sorted(display.size_index(w, h) for w, h in decided) == list(range(len(decided)))
    assert display.size_index(1600, 1200) == -1


#  size          clip in layout px (x0, x1, y0, y1)   in output px
CLIPS = [
    ((3440, 1440), (0, 2559, 0, 1439), (0, 3439, 0, 1439)),       # the whole layout: the whole output
    ((3440, 1440), (100, 200, 50, 60), (540, 640, 50, 60)),       # a list's clip: moved with the layout
    ((1920, 1200), (100, 200, 50, 60), (100, 200, 110, 120)),
    ((3840, 2160), (0, 1919, 0, 1079), (0, 3839, 0, 2159)),
    ((5120, 2160), (100, 200, 50, 60), (840, 1040, 100, 120)),    # half-size layout: scaled, then moved
]


def test_clips_follow_the_layout_and_the_whole_layout_clips_nothing(display):
    """Bug: a clip left in layout px (a scrolling list cut short on 4K) or the whole-layout clip kept to the 16:9
    area, which cuts an edge HUD element moved outward."""
    wrong = {}
    for (w, h), clip, expected in CLIPS:
        c = (ctypes.c_uint * 4)(*clip)
        display.clip(w, h, c)
        if tuple(c) != expected:
            wrong[(w, h, clip)] = tuple(c)
    assert not wrong


def test_packet_shift_moves_only_vertex_x_of_a_sprite(display):
    """Bug: a shift that also moves a UV, a colour, a Y or a packed clip, or misses the second vertex of a sprite."""
    # A sprite packet as 0x5DDAB0 builds it (5 qwords): reglist tag, 8 registers PRIM, CLAMP, RGBAQ, UV, XYZ3, UV,
    # XYZ2, NOP (nibbles 0, 7, 1, 3, D, 3, 5, F from the low end); then a packed A+D clip (SCISSOR, 0x40).
    words = [0x00008001, 0x84000000, 0xF53D3170, 0]
    words += [0x11 * i for i in range(1, 17)]                   # 8 entries of 2 words: X words at index 4 and 6
    words += [0x00008001, 0x10000000, 0xE, 0, 5, 6, 0x40, 0]    # packed tag, 1 A+D entry
    packet = (ctypes.c_uint * len(words))(*words)
    display.shift(packet, len(words) // 4, 440 * 16)
    moved = {i for i, (a, b) in enumerate(zip(words, packet, strict=True)) if a != b}
    assert moved == {4 + 2 * 4, 4 + 2 * 6} and packet[12] - words[12] == 440 * 16
