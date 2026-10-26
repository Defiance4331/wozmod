"""A saved input map whose pad bindings the stock load cleared gets them back on the player's pad (runtime/pad_map.h)."""

from __future__ import annotations

import ctypes
import struct

import pytest

NONE = 10


@pytest.fixture(scope="module")
def relink(c_library):
    lib = c_library("pad_map", r'''
#include "pad_map.h"
unsigned int relink(unsigned char *map, unsigned int joy)
{
    return pad_map_relink(map, joy);
}
''')
    lib.relink.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    return lib


def input_map(pad_bindings: dict[int, tuple[int, int, int, int]]) -> ctypes.Array:
    """21 actions of 0x28 bytes; each pad binding (device, isAxis, direction, index) at +0x18, the keyboard's left 0."""
    data = bytearray(21 * 0x28)
    for action, (device, is_axis, direction, index) in pad_bindings.items():
        struct.pack_into("<IBxxxII", data, action * 0x28 + 0x18, device, is_axis, direction, index)
    return ctypes.create_string_buffer(bytes(data), len(data))


def devices(buffer: ctypes.Array) -> list[int]:
    return [struct.unpack_from("<I", buffer.raw, action * 0x28 + 0x18)[0] for action in range(21)]


def test_only_a_map_with_nothing_on_the_players_pad_gets_its_cleared_bindings_back(relink) -> None:
    """Bugs: a map that still has a binding on the pad is changed, bringing back a binding the player cleared with F12;
    a never-bound camera action is bound to button 0; a cleared binding on button 0 is left off; the bindings go to
    pad 0 instead of the player's pad; the device is read at the wrong offset."""
    # (isAxis, direction, index) of each action's pad binding, as the Xbox defaults leave it; actions 19 and 20 are
    # the stock never-bound camera (all 0), and action 6 is bound to button 0 (also all 0, but not a camera action)
    fields = {action: (0, 0, action % 12) for action in range(17)}
    fields |= {0: (1, 1, 1), 6: (0, 0, 0), 17: (1, 1, 4), 18: (1, 0, 4), 19: (0, 0, 0), 20: (0, 0, 0)}

    def lost(joy):
        return {action: (NONE, *f) for action, f in fields.items()}, [joy] * 19 + [NONE] * 2

    cases = {  # name: (pad bindings, PLAYER_JOY, the devices expected after)
        "lost, pad 0": (*lost(0)[:1], 0, lost(0)[1]),
        "lost, pad 1": (*lost(1)[:1], 1, lost(1)[1]),
        "one binding left on the pad": ({**lost(0)[0], 3: (0, *fields[3])}, 0, [NONE] * 3 + [0] + [NONE] * 17),
        "no pad": (lost(0)[0], NONE, [NONE] * 21),
    }
    got = {}
    for name, (bindings, joy, _) in cases.items():
        buffer = input_map(bindings)
        relink.relink(buffer, joy)
        got[name] = devices(buffer)
    assert got == {name: expected for name, (_, _, expected) in cases.items()}
