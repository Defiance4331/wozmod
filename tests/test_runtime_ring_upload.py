"""The ground rings built in system memory and uploaded once a pass (runtime/ring_upload.h)."""

from __future__ import annotations

import ctypes

import pytest

RING = 0x48000  # the engine's ring size (288 KiB)
DYNAMIC, WRITEONLY, DISCARD = 0x200, 0x8, 0x2000
VERTEX = 36
FILL = 0xEE  # the fake buffer's bytes before any upload


class Fake(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_void_p), ("usage", ctypes.c_uint), ("size", ctypes.c_uint), ("locks", ctypes.c_uint),
                ("unlocks", ctypes.c_uint), ("last_size", ctypes.c_uint), ("last_flags", ctypes.c_uint),
                ("mem", ctypes.c_ubyte * RING)]


@pytest.fixture(scope="module")
def rings(c_library):
    lib = c_library("ring_upload", r'''
#include <stdlib.h>
#include <string.h>
#include "ring_upload.h"
/* A vertex buffer: its Lock hands out its own memory and counts the call. */
struct fake { void **vt; unsigned usage, size, locks, unlocks, last_size, last_flags; unsigned char mem[0x48000]; };
static long fake_lock(void *b, unsigned offset, unsigned size, void **out, unsigned flags)
{
    struct fake *f = b;
    f->locks++, f->last_size = size, f->last_flags = flags, *out = f->mem + offset;
    return 0;
}
static long fake_unlock(void *b) { return ((struct fake *)b)->unlocks++, 0; }
static long fake_desc(void *b, unsigned *d)
{
    struct fake *f = b;
    d[2] = f->usage, d[3] = f->usage & 0x200 ? 0 : 1, d[4] = f->size;
    return 0;
}
static void *vt[14];
static void *grow(void *old, unsigned size) { return realloc(old, size); }
static struct ring_upload u;
void reset(void) { memset(&u, 0, sizeof u), u.resize = grow; }
struct fake *make(unsigned usage, unsigned size)
{
    struct fake *f = calloc(1, sizeof *f);
    vt[11] = (void *)fake_lock, vt[12] = (void *)fake_unlock, vt[13] = (void *)fake_desc;
    f->vt = vt, f->usage = usage, f->size = size;
    memset(f->mem, 0xEE, sizeof f->mem);
    return f;
}
long lock(struct fake *f, unsigned size, void **out, unsigned flags) { return ru_lock(&u, f, size, out, flags); }
long unlock(struct fake *f) { return ru_unlock(&u, f); }
''')
    lib.make.restype = ctypes.POINTER(Fake)
    lib.make.argtypes = [ctypes.c_uint, ctypes.c_uint]
    lib.lock.argtypes = [ctypes.POINTER(Fake), ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    lib.unlock.argtypes = [ctypes.POINTER(Fake)]
    lib.lock.restype = lib.unlock.restype = ctypes.c_long
    return lib


class Slot:
    """An engine ring slot as 0x5B4100 locks it and 0x5B8D60 allocates from it: [1] the base the Lock hands back,
    [2] the ring's end, [3] the last tile's start, [4] the write cursor: reset to the base after the Lock, and moved past
    each tile's vertices."""

    def __init__(self, lib):
        self.lib, self.fields = lib, (ctypes.c_void_p * 8)()

    def lock(self, buffer, flags=0):
        out = ctypes.addressof(self.fields) + ctypes.sizeof(ctypes.c_void_p)
        assert self.lib.lock(buffer, RING, out, flags) == 0
        self.fields[2], self.fields[4] = self.fields[1] + RING, self.fields[1]

    def write(self, data: bytes):
        for at in range(0, len(data), 4 * VERTEX):
            tile = data[at:at + 4 * VERTEX]
            self.fields[3] = self.fields[4]
            ctypes.memmove(self.fields[4], tile, len(tile))
            assert ctypes.string_at(self.fields[4], len(tile)) == tile  # the bounding box reads them straight back
            self.fields[4] += len(tile)

    def unlock(self, buffer):
        assert self.lib.unlock(buffer) == 0


def tiles(count: int, seed: int) -> bytes:
    return bytes((seed + 7 * i) % 251 for i in range(4 * VERTEX * count))


def test_a_pass_reaches_the_buffer_once_with_exactly_what_it_wrote(rings):
    """Bugs: the cursor read from the wrong slot field, a copy one tile short or long, a second pass's size from the
    first, a Lock that still waits on the GPU (flags 0), or the engine's own Lock reaching the buffer mid-pass."""
    rings.reset()
    buffer, slot = rings.make(DYNAMIC | WRITEONLY, RING), Slot(rings)
    for count, seed in ((179, 1), (60, 2)):
        locks = buffer.contents.locks
        slot.lock(buffer)
        slot.write(tiles(count, seed))
        assert buffer.contents.locks == locks
        slot.unlock(buffer)
        size = 4 * VERTEX * count
        fake = buffer.contents
        assert (fake.locks - locks, fake.last_size, fake.last_flags) == (1, size, DISCARD)
        assert bytes(fake.mem[:size]) == tiles(count, seed)
    assert buffer.contents.unlocks == 2


def test_a_managed_ring_keeps_its_own_lock(rings):
    """Bug: a MANAGED ring (kind 0) staged too, whose DISCARD Lock the runtime refuses, so its ground never draws."""
    rings.reset()
    buffer, slot = rings.make(0, RING), Slot(rings)
    slot.lock(buffer)
    assert slot.fields[1] == ctypes.addressof(buffer.contents.mem)
    slot.write(tiles(3, 5))
    slot.unlock(buffer)
    fake = buffer.contents
    assert (fake.locks, fake.last_flags, fake.unlocks) == (1, 0, 1)


def test_after_a_reset_a_slot_uploads_into_its_new_buffer(rings):
    """Bug: a slot that keeps the buffer it first locked uploads into the one the Reset released."""
    rings.reset()
    old, new, slot = rings.make(DYNAMIC | WRITEONLY, RING), rings.make(DYNAMIC | WRITEONLY, RING), Slot(rings)
    slot.lock(old)
    slot.write(tiles(2, 3))
    slot.unlock(old)
    slot.lock(new)
    slot.write(tiles(4, 4))
    slot.unlock(new)
    assert (old.contents.locks, new.contents.locks) == (1, 1)
    assert bytes(new.contents.mem[:4 * VERTEX * 4]) == tiles(4, 4)
