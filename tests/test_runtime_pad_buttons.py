"""The runtime's pad buttons: held follows the pad's state, and the buffered events still count (runtime/pad_buttons.h)."""

from __future__ import annotations

import ctypes

import pytest

Floats = ctypes.c_float * 4


@pytest.fixture(scope="module")
def sync(c_library):
    lib = c_library("pad_buttons", r'''
#include "pad_buttons.h"
void sync(float *held, float *press, float *release, const float *was, const unsigned char *state, unsigned int n)
{
    pad_buttons_sync(held, press, release, was, state, n);
}
''')
    lib.sync.argtypes = [ctypes.POINTER(ctypes.c_float)] * 4 + [ctypes.c_char_p, ctypes.c_uint]
    return lib


def test_held_follows_the_pad_state_and_keeps_the_events_edges(sync) -> None:
    """Bugs: a release the event buffer dropped keeps the button held (the guard that never lets go); a dropped press
    gives no press; or held taken from the state alone loses a tap that went down and up within one frame."""
    # per button: (held before, held, press, release as the events left them this frame), the state's bit
    rows = {
        "release dropped":   ((1, 1, 0, 0), 0x00),
        "press dropped":     ((0, 0, 0, 0), 0x80),
        "tap within frame":  ((0, 0, 1, 1), 0x00),
        "still held":        ((1, 1, 0, 0), 0x80),
    }
    expected = {  # held, press, release after the merge
        "release dropped": (0, 0, 1), "press dropped": (1, 1, 0), "tap within frame": (0, 1, 1), "still held": (1, 0, 0),
    }
    was, held, press, release = (Floats(*[float(r[0][k]) for r in rows.values()]) for k in range(4))
    state = bytes(bit for _, bit in rows.values())
    sync.sync(held, press, release, was, state, 4)
    got = {name: (held[i], press[i], release[i]) for i, name in enumerate(rows)}
    assert got == expected
