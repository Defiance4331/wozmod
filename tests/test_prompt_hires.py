"""The sharper pad prompts: the runtime's reader (runtime/prompt_hires.h) finds each slot's copy the art build wrote."""

import ctypes

import pytest

from wozmod.art.build import PROMPT_HIRES_SCALES, prompt_hires
from wozmod.art.prompt_glyphs import prompt_art


@pytest.fixture(scope="module")
def find(c_library):
    lib = c_library("prompt_hires", r'''
#include "prompt_hires.h"
unsigned int find(const unsigned char *d, unsigned int n, unsigned int slot, unsigned int scale, unsigned int *w)
{
    return prompt_hires_find(d, n, slot, scale, w);
}
int whole(const unsigned char *d, unsigned int n) { return prompt_hires_whole(d, n); }
''')
    lib.find.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
    lib.find.restype = ctypes.c_uint
    lib.find.whole = lib.whole
    return lib.find


def test_the_reader_finds_each_slots_copy_at_each_scale(find):
    """Bugs: the build writes the copies slot by slot while the runtime reads them scale by scale (or a slot's width
    is wrong), so the game shows another button's icon or torn rows; or a slot without a copy (the 2P strip, 44)
    is found, and garbage replaces its texture."""
    data = prompt_hires("playstation")
    wrong = []
    for scale in PROMPT_HIRES_SCALES:
        for slot, image in prompt_art("playstation", scale).items():
            w = ctypes.c_uint()
            at = find(data, len(data), slot, scale, ctypes.byref(w))
            pixels = image.tobytes("raw", "BGRA")
            if w.value != image.width or data[at:at + len(pixels)] != pixels:
                wrong.append((scale, slot, at, w.value))
    w = ctypes.c_uint()
    assert wrong == [] and find(data, len(data), 44, 4, ctypes.byref(w)) == 0


def test_the_dlls_start_check_takes_a_whole_file_of_copies_and_refuses_a_cut_one(find):
    """Bug: the check the DLL makes at start refuses the copies the art build writes, so wozmod turns itself off for
    every player; or it takes a file cut short, whose last slots then quietly keep their small icons."""
    data = prompt_hires("xbox")
    assert [find.whole(d, len(d)) for d in (data, data[:-4], data[:12], data + b"\0" * 4)] == [1, 0, 0, 0]
