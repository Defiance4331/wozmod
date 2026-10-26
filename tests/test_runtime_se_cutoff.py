"""Version 1.01's falling-rock cutoff in the sound-effect poll, for version 1.00 (runtime/se_cutoff.h)."""

from __future__ import annotations

import ctypes
import os
import re
from pathlib import Path

import pytest

SLOTS, TABLE, STRIDE = 240, 0x1E0C, 0x14
ROCK = {"bank": 10, "group": 3, "index": 20, "channel": 1}  # the sound 1.01 cuts off (0x5D60F0), on map 115
MAP = 115
FREED = 0x5A  # what the test's free returns, so the poll's return value can be told from its own 1

PLAYING = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
STOP = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint))
RELEASE = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)


@pytest.fixture(scope="module")
def cutoff(c_library):
    lib = c_library("se_cutoff", r'''
#include <string.h>
#include "se_cutoff.h"
static struct se_cutoff c;
void reset(void) { memset(&c, 0, sizeof c); }
unsigned int map_at(void) { return SE_MAP_AT; }
unsigned int poll(unsigned char *m, unsigned int map, int (*playing)(unsigned char *, unsigned int),
                  void (*stop)(unsigned char *, const unsigned int *),
                  unsigned int (*release)(unsigned char *, unsigned int), unsigned int *cut)
{
    struct se_stock s = {playing, stop, release};
    return se_poll(&c, m, map, &s, cut);
}
''')
    lib.poll.argtypes = [ctypes.c_void_p, ctypes.c_uint, PLAYING, STOP, RELEASE, ctypes.POINTER(ctypes.c_uint)]
    lib.poll.restype = ctypes.c_uint
    lib.map_at.restype = ctypes.c_uint
    return lib


class Manager:
    """The stock sound manager's slot table as 0x5D5580 fills it and 0x5D5DC0 frees it, and the middleware's answer
    to whether a handle still plays."""

    def __init__(self, lib):
        lib.reset()
        self.lib, self.m = lib, (ctypes.c_ubyte * (TABLE + SLOTS * STRIDE))()
        self.playing, self.asked, self.stopped, self.freed = set(), [], [], []
        self._callbacks = (PLAYING(self._playing), STOP(self._stop), RELEASE(self._release))

    def slot(self, i):
        return (ctypes.c_uint * 5).from_buffer(self.m, TABLE + STRIDE * i)

    def start(self, i, handle, bank, group, index, channel):
        self.slot(i)[:] = [handle, bank, group, index, channel]
        self.playing.add(handle)

    def _playing(self, m, handle):
        self.asked.append(handle)
        return int(handle in self.playing)

    def _stop(self, m, slot):  # 0x5D5D80 by (bank, group, index, channel)
        self.stopped.append(tuple(slot[k] for k in range(1, 5)))
        self.playing.discard(next(s[0] for i in range(SLOTS) if tuple((s := self.slot(i))[1:]) ==
                                  tuple(slot[k] for k in range(1, 5))))

    def _release(self, m, i):
        self.freed.append(i)
        self.slot(i)[:] = [0, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF]
        return FREED

    def poll(self, game_map=MAP):
        cut = ctypes.c_uint()
        returned = self.lib.poll(self.m, game_map, *self._callbacks, ctypes.byref(cut))
        return returned, cut.value

    def polls_until_stopped(self, most):
        """How many polls stop a sound, or None when `most` polls stop none."""
        for n in range(1, most + 1):
            self.poll()
            if self.stopped:
                return n
        return None


def test_the_rock_sound_on_map_115_stops_on_its_180th_playing_poll(cutoff):
    # Bug: an off-by-one in the count (1.01 compares 0xB4 with jae after the add), or a stop that does not free the
    # slot and return what the free returns, as 1.01 does.
    game = Manager(cutoff)
    game.start(5, 0x10005, **ROCK)
    assert {game.poll() for _ in range(179)} == {(1, SLOTS)} and not game.stopped
    assert game.poll() == (FREED, 5)
    assert game.stopped == [(10, 3, 20, 1)] and game.freed == [5] and game.slot(5)[0] == 0


def test_only_bank_10_index_20_on_map_115_is_counted(cutoff):
    # Bug: the bank, group or index read from the wrong slot offset, or the map compared with another value: either
    # stops other sounds or never stops the rock sound. Every row is checked and each wrong one reported.
    rows = {
        "rock": ((10, 3, 20, 1), MAP, 180),
        "other map": ((10, 3, 20, 1), MAP - 1, None),
        "other bank": ((11, 3, 20, 1), MAP, None),
        "other index": ((10, 3, 21, 1), MAP, None),
        "bank and index swapped": ((20, 3, 10, 1), MAP, None),
        "index in the group": ((10, 20, 3, 1), MAP, None),
        "bank in the channel": ((7, 3, 20, 10), MAP, None),
    }
    wrong = {}
    for name, (ids, game_map, expected) in rows.items():
        game = Manager(cutoff)
        game.start(9, 0x20009, *ids)
        for n in range(1, 401):
            game.poll(game_map)
            if game.stopped:
                break
        stopped_at = n if game.stopped else None
        if stopped_at != expected:
            wrong[name] = stopped_at
    assert wrong == {}


@pytest.mark.parametrize("how", ["another handle in the slot", "the poll freed it, the same handle again"])
def test_a_slot_that_holds_a_new_sound_counts_it_from_zero(cutoff, how):
    # Bug: the new sound inherits the count of the one the slot held before and is cut off early (1.01 zeroes the
    # count when it allocates or frees the slot).
    game = Manager(cutoff)
    game.start(3, 0x30003, **ROCK)
    for _ in range(150):
        game.poll()
    if how == "another handle in the slot":  # freed and allocated again between two polls, by code the poll is not
        game.start(3, 0x40003, **ROCK)
    else:  # the sound ends, the poll frees the slot, and the middleware hands out the same handle again
        game.playing.clear()
        assert game.poll() == (FREED, SLOTS)
        game.start(3, 0x30003, **ROCK)
    assert game.polls_until_stopped(400) == 180


def test_a_poll_ends_at_the_first_finished_slot(cutoff):
    # Bug: the poll goes on past the slot it frees, so the slots after it are asked and counted twice as often as in
    # 1.00 and 1.01, which both return there.
    game = Manager(cutoff)
    game.start(2, 0x50002, 1, 0, 4, 0)
    game.start(7, 0x60007, **ROCK)
    game.playing.discard(0x50002)  # slot 2's sound has ended
    assert game.poll() == (FREED, SLOTS)
    assert game.asked == [0x50002] and game.freed == [2]
    assert game.polls_until_stopped(400) == 180  # slot 7 was not counted by the first poll


def test_the_map_is_the_dword_the_game_sets_from_the_stage_record(cutoff):
    # Bug: 1.01's address of the map (0x801AC4) kept: in 1.00 that is part of another table, and the rock sound
    # would never stop. 1.00 stores byte 0 of the stage's 0x26-byte record there (0x5486F4: imul by 0x26, movzx).
    capstone = pytest.importorskip("capstone")
    from wozmod.formats import pe

    exe = Path(os.environ.get("WOZMOD_GAME") or "", "OROCHI_Z.exe")
    if not exe.is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    image = exe.read_bytes()
    layout = pe.layout(image)
    text = next(s for s in layout["sections"] if s["name"] == b".text")
    code, base = image[text["raw"]:text["raw"] + text["raw_size"]], layout["image_base"] + text["rva"]
    at = cutoff.map_at()
    x86 = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    from_record = []
    for store in re.finditer(rb"[\x89][\x05\x0d\x15\x1d\x2d\x35\x3d]" + re.escape(at.to_bytes(4, "little")), code):
        for back in range(8, 33):  # find the decoding that ends on the store
            ops = [f"{i.mnemonic} {i.op_str}" for i in x86.disasm(code[store.start() - back:store.end()],
                                                                     base + store.start() - back)]
            if ops and ops[-1].startswith("mov dword ptr [") and len(ops) >= 4:
                tail = ops[-4:-1]
                if any(o.startswith("movzx") and "byte ptr" in o for o in tail) and any(
                        o.startswith("imul") and o.endswith(", 0x26") for o in tail):
                    from_record.append(hex(base + store.start()))
                    break
    assert from_record
