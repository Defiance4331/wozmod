"""The runtime's COM method hooks (runtime/device_hooks.h): native d3d9 gives the game's two devices their own vtables."""

from __future__ import annotations

import ctypes

import pytest


@pytest.fixture(scope="module")
def devices(c_library):
    return c_library("device_hooks", r'''
#include "device_hooks.h"
/* Two devices with their own vtables, as native d3d9 made them on Windows 10; slot 1 stands for Reset. */
typedef int (*method)(void *);
static int first_reset(void *d) { (void)d; return 1; }
static int second_reset(void *d) { (void)d; return 2; }
static void *first_vtable[2] = {0, (void *)first_reset}, *second_vtable[2] = {0, (void *)second_reset};
static void **first = first_vtable, **second = second_vtable;
static struct hooks hooks;
static int ours(void *d) { return 100 + ((method)hook_real(&hooks, d, 1))(d); }
static int write(void **at, void *value) { *at = value; return 1; }
/* Hook as the runtime does at each CreateDevice (the first device, the second, then the first again), and call
   each device's Reset through its own vtable. */
void run(int *got)
{
    hook_put(&hooks, &first, 1, (void *)ours, write);
    hook_put(&hooks, &second, 1, (void *)ours, write);
    hook_put(&hooks, &first, 1, (void *)ours, write);
    got[0] = ((method)first[1])(&first), got[1] = ((method)second[1])(&second);
}
''')


def test_each_device_reaches_the_hook_then_its_own_original(devices):
    # Red when only the first device's vtable is hooked (the second skips the hook: 2), when one original serves every
    # vtable (the second calls the first's: 101), or when hooking a vtable again records the hook as its original.
    got = (ctypes.c_int * 2)()
    devices.run(got)
    assert list(got) == [101, 102]
