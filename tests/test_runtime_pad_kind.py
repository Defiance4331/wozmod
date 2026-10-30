"""Which DirectInput pads are XInput pads, by the IG_ mark in their device path (runtime/pad_kind.h)."""

from __future__ import annotations

import ctypes

import pytest

MAX_PATH = 260
HID = "{4d1e55b2-f16f-11cf-88cb-001111000030}"


@pytest.fixture(scope="module")
def xinput(c_library):
    lib = c_library("pad_kind", r'''
#include "pad_kind.h"
int xinput(const unsigned short *path, unsigned int size) { return pad_path_xinput(path, size); }
''')
    lib.xinput.argtypes = [ctypes.POINTER(ctypes.c_ushort), ctypes.c_uint]
    return lib.xinput


def test_only_paths_with_the_xinput_mark_are_xbox_pads_in_either_case(xinput) -> None:
    # Made-up paths in the shapes Windows and Wine give: the mark is VID_vvvv&PID_pppp&IG_nn in the device ID part.
    paths = {
        rf"\\?\hid#vid_045e&pid_02ea&ig_00#8&1a2b3c4d&0&0000#{HID}": 1,  # Windows, lower case
        rf"\\?\HID#VID_2DC8&PID_3106&IG_00#1&2D595CA7&0&0000#{HID}": 1,  # Wine's winexinput gamepad
        rf"\\?\{{00001124-0000-1000-8000-00805f9b34fb}}_vid&0002045e_pid&0b13&ig_00#9&1f2e3d4c&0&0000#{HID}": 1,
        rf"\\?\hid#vid_054c&pid_05c4#7&3a4b5c6d&0&0000#{HID}": 0,  # a DualShock 4
        rf"\\?\HID#VID_057E&PID_2009&MI_00#7&3A4B5C6D&0&0000#{HID}": 0,  # an interface of a composite device
    }
    seen = {}
    for path in paths:
        buffer = (ctypes.c_ushort * MAX_PATH)(*map(ord, path))
        seen[path] = xinput(buffer, MAX_PATH)
    assert seen == paths
