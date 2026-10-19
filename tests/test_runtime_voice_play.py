"""The runtime's repair of a voice line whose load had to wait past its play (runtime/voice_play.h)."""

from __future__ import annotations

import ctypes

import pytest


ID, FLAGS, WAITING, WAITING_ID = 0xC + 0x10, 0xC + 0x1C, 0xB0, 0xB4  # buffer 0 (0x40 bytes a buffer), the waiting load
LOADED, NONE = 0x8, 2
OLD, LINE, OTHER = 0x97D, 0x97F, 0x980  # what the buffer held before, the waiting line, another clip


class Kept(ctypes.Structure):
    _fields_ = [("slot", ctypes.c_uint), ("id", ctypes.c_uint), ("arg", ctypes.c_uint * 3),
                ("held", ctypes.c_uint), ("held_id", ctypes.c_uint)]


IDLE = ctypes.CFUNCTYPE(ctypes.c_int)


@pytest.fixture(scope="module")
def voice(c_library):
    lib = c_library("voice", r'''
#include "voice_play.h"
void keep(struct voice_play *k, unsigned char *m, unsigned int slot, const unsigned int *arg)
{
    voice_keep(k, m, slot, arg);
}
int frame(struct voice_play *k, unsigned char *m, int (*idle)(void)) { return voice_frame(k, m, idle); }
''')
    lib.keep.argtypes = [ctypes.POINTER(Kept), ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
    lib.frame.argtypes = [ctypes.POINTER(Kept), ctypes.c_void_p, IDLE]
    return lib


class Game:
    """The stock voice manager as traced in OROCHI_Z.exe: the clip a buffer holds, its loaded bit and voice id, the waiting
    load, and the archive reader, free once a finished read is reaped."""

    def __init__(self, waiting, busy):
        self.m = (ctypes.c_ubyte * 0x100)()
        self.data = [OLD, OLD]
        self.word(WAITING).value, self.word(WAITING_ID).value = waiting
        self.busy = busy  # frames until the reader's read is finished; reaping then frees it
        self.heard = []

    def word(self, at):
        return ctypes.c_uint.from_buffer(self.m, at)

    def reap(self):
        return int(self.busy <= 0)

    def play(self, slot):  # 0x5D7BF0: only a loaded buffer starts, and then it is no longer loaded
        flags = self.word(FLAGS + 0x40 * slot)
        if flags.value & LOADED:
            flags.value &= ~LOADED
            self.heard.append(self.data[slot])

    def voice_frame(self, quiet):  # 0x5D8590's waiting load: made once its buffer is quiet; dropped by a busy reader
        slot = self.word(WAITING).value
        if slot != NONE and quiet:
            self.word(FLAGS + 0x40 * slot).value |= LOADED
            self.word(ID + 0x40 * slot).value = self.word(WAITING_ID).value
            if self.reap():
                self.data[slot], self.busy = self.word(WAITING_ID).value, 1
            self.word(WAITING).value, self.word(WAITING_ID).value = NONE, 0xFFFFFFFF
        self.busy -= 1


# Each row runs frames: the runtime's frame start, the game's voice frame (its waiting buffer quiet from frame `quiet`),
# then the scene's plays and the game's own new waits. Expected: the clips the player hears, in order.
#  name                           waits for    reader busy  quiet  plays {frame: slot}  new wait {frame: (slot, id)}  heard
ROWS = [
    ("stage 93 line 139",          (1, LINE),   2,           3,     {1: 1},              {},                           [LINE]),
    ("loaded before its play",     (1, LINE),   0,           0,     {2: 1},              {},                           [LINE]),
    ("reader busy for long",       (1, LINE),   6,           1,     {0: 1},              {},                           [LINE]),
    ("nothing waits",              (NONE, 0),   0,           0,     {1: 0},              {},                           []),
    ("another clip took the wait", (1, LINE),   4,           1,     {0: 1},              {2: (1, OTHER)},              []),
]


@pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
def test_a_line_whose_load_waited_past_its_play_is_heard(voice, row):
    # Bug: release 1 lost stage 93's line 139: its load waited behind the longer English line 137 past the line's play,
    # so the play found nothing loaded, and the load, made after the next line's, was dropped by the unreaped reader.
    _, waiting, busy, quiet, plays, waits, heard = row
    game, kept = Game(waiting, busy), Kept(NONE, 0, (0, 0, 0), NONE, 0)
    idle = IDLE(game.reap)
    for frame in range(10):
        if voice.frame(ctypes.byref(kept), game.m, idle):
            game.play(kept.slot)
            kept.slot = NONE  # the runtime makes the play and lets it go
        game.voice_frame(frame >= quiet)
        if frame in plays:
            voice.keep(ctypes.byref(kept), game.m, plays[frame], (ctypes.c_uint * 3)(0, 0, 0))
            game.play(plays[frame])
        if frame in waits:
            game.word(WAITING).value, game.word(WAITING_ID).value = waits[frame]
    assert game.heard == heard
