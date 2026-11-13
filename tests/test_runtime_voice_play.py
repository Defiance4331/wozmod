"""The runtime's wait for the archive reader before a voice line's start or load (runtime/voice_play.h)."""

from __future__ import annotations

import ctypes

import pytest


ID, FLAGS, WAITING, WAITING_ID = 0xC + 0x10, 0xC + 0x1C, 0xB0, 0xB4  # buffer 0 (0x40 bytes a buffer), the waiting load
LOADED, NONE, END = 0x8, 2, 0x150  # END: the voice mode's end marker, no line
OLD, LINE, OTHER = 0x97D, 0x97F, 0x980  # what the buffer held before, the waiting line, another clip


class Start(ctypes.Structure):
    _fields_ = [("play", ctypes.c_uint), ("next", ctypes.c_uint), ("since", ctypes.c_uint)]


class Kept(ctypes.Structure):
    _fields_ = [("slot", ctypes.c_uint), ("id", ctypes.c_uint), ("arg", ctypes.c_uint * 3),
                ("held", ctypes.c_uint), ("held_id", ctypes.c_uint),
                ("wait", Start * 2), ("waits", ctypes.c_uint), ("frame", ctypes.c_uint)]


IDLE = ctypes.CFUNCTYPE(ctypes.c_int)
START = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint, ctypes.c_uint)
NOTE = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.POINTER(Start), ctypes.c_uint)


class Stock(ctypes.Structure):
    _fields_ = [("idle", IDLE), ("start", START), ("note", NOTE)]


@pytest.fixture(scope="module")
def voice(c_library):
    lib = c_library("voice", r'''
#include "voice_play.h"
void start(struct voice_play *k, unsigned int play, unsigned int next, unsigned int end, const struct voice_stock *s)
{
    voice_start(k, play, next, end, s);
}
void keep(struct voice_play *k, unsigned char *m, unsigned int slot, const unsigned int *arg)
{
    voice_keep(k, m, slot, arg);
}
int frame(struct voice_play *k, unsigned char *m, const struct voice_stock *s) { return voice_frame(k, m, s); }
void stop(struct voice_play *k, unsigned int end, const struct voice_stock *s) { voice_stop(k, end, s); }
''')
    lib.start.argtypes = [ctypes.POINTER(Kept), ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(Stock)]
    lib.keep.argtypes = [ctypes.POINTER(Kept), ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
    lib.frame.argtypes = [ctypes.POINTER(Kept), ctypes.c_void_p, ctypes.POINTER(Stock)]
    lib.stop.argtypes = [ctypes.POINTER(Kept), ctypes.c_uint, ctypes.POINTER(Stock)]
    return lib


class Game:
    """The stock voice path as traced in OROCHI_Z.exe, with the runtime's hooks on it: two clip buffers (the clip each
    holds, its loaded bit and voice id, how long it plays), the waiting load, and the archive reader, which is free once
    its read is finished and reaped and drops a read made while it is not."""

    def __init__(self, voice, read=1, lengths=None, waiting=(NONE, 0)):
        self.voice, self.read, self.lengths = voice, read, lengths or {}
        self.m = (ctypes.c_ubyte * 0x100)()
        self.word(WAITING).value, self.word(WAITING_ID).value = waiting
        self.kept = Kept(NONE, 0, (0, 0, 0), NONE, 0)
        self.data, self.until, self.cur, self.busy, self.now = [OLD, OLD], [0, 0], 0, 0, 0
        self.heard, self.notes = [], []
        self.expected, self.order = END, []
        self._calls = (IDLE(self.free), START(self.start), NOTE(self.note))  # kept alive while C holds them
        self.stock = Stock(*self._calls)

    def word(self, at):
        return ctypes.c_uint.from_buffer(self.m, at)

    def free(self):
        return int(self.busy <= 0)

    def note(self, what, start, frames):
        self.notes.append(("dropped" if b"dropped" in what else "made", start.contents.play, frames))

    def load(self, slot, line):  # 0x5D8540: marks the buffer loaded; the reader drops the read while it is busy
        self.word(FLAGS + 0x40 * slot).value |= LOADED
        self.word(ID + 0x40 * slot).value = line
        if self.free():
            self.data[slot], self.busy = line, self.read

    def play(self, slot):  # 0x5D7BF0 behind hook 109: only a loaded buffer starts, and then it is no longer loaded
        self.voice.keep(ctypes.byref(self.kept), self.m, slot, (ctypes.c_uint * 3)(0, 0, 0))
        flags = self.word(FLAGS + 0x40 * slot)
        if flags.value & LOADED:
            flags.value &= ~LOADED
            self.heard.append(self.data[slot])
            self.until[slot] = self.now + self.lengths.get(self.data[slot], 1)

    def start(self, play, next_):  # 0x513720 as stock: nothing done while the reader is busy
        if not self.free():
            return 0
        if play < END:
            self.play(self.cur)
        self.cur ^= 1
        if next_ < END:
            if self.now < self.until[self.cur]:  # its buffer still plays: the load waits (0x5D7500)
                self.word(WAITING).value, self.word(WAITING_ID).value = self.cur, next_
            else:
                self.load(self.cur, next_)
        return 1

    def scene(self, order):  # 0x4B2AA0: the first line is expected and its clip loaded
        self.expected, self.order = order[0], list(order[1:])
        self.voice.start(ctypes.byref(self.kept), END, self.expected, END, ctypes.byref(self.stock))  # hook 135

    def speak(self, line):  # 0x4A6BC0: only the expected line starts; the runtime's start (hook 135) returns 1
        if line == self.expected:
            self.expected = self.order.pop(0) if self.order else END
            self.voice.start(ctypes.byref(self.kept), line, self.expected, END, ctypes.byref(self.stock))

    def stop(self):  # 0x5D82A0 behind hook 136: both buffers stop and are no longer loaded
        self.voice.stop(ctypes.byref(self.kept), END, ctypes.byref(self.stock))
        for slot in (0, 1):
            self.word(FLAGS + 0x40 * slot).value &= ~LOADED
            self.until[slot] = self.now

    def frame(self, now, events):
        self.now = now
        if self.voice.frame(ctypes.byref(self.kept), self.m, ctypes.byref(self.stock)):  # hook 110
            self.play(self.kept.slot)
            self.kept.slot = NONE  # the runtime makes the play and lets it go
        slot = self.word(WAITING).value  # 0x5D8590's waiting load: made once its buffer is quiet
        if slot != NONE and now >= self.until[slot]:
            self.load(slot, self.word(WAITING_ID).value)
            self.word(WAITING).value, self.word(WAITING_ID).value = NONE, 0xFFFFFFFF
        events(self, now)
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
    ("the voice stopped first",    (1, LINE),   0,           3,     {1: 1},              {2: "stop"},                  []),
]


@pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
def test_a_line_whose_load_waited_past_its_play_is_heard(voice, row):
    # Bug: release 1 lost stage 93's line 139: its load waited behind the longer English line 137 past the line's play,
    # so the play found nothing loaded, and the load, made after the next line's, was dropped by the unreaped reader.
    # A play kept after the voice stopped (a skipped scene) would be heard after its scene.
    _, waiting, busy, quiet, plays, waits, heard = row
    game = Game(voice, waiting=waiting)
    game.busy, game.until[waiting[0] % 2] = busy, quiet

    def events(game, frame):
        if frame in plays:
            game.play(plays[frame])
        if waits.get(frame) == "stop":
            game.stop()
        elif frame in waits:
            game.word(WAITING).value, game.word(WAITING_ID).value = waits[frame]

    for frame in range(10):
        game.frame(frame, events)
    assert game.heard == heard


A, B, C, D = 0x10, 0x11, 0x12, 0x13  # an event sub-scene's lines, in play order

# Each row plays one event sub-scene from frame 0: the scene start (its first clip loaded), then the speaks at their
# frames. read: frames each load keeps the reader busy. Expected: the lines heard, and the runtime log's lines: a
# start made once the reader was free, or dropped, with its line and the frames it waited.
#  name                                      order         read  lengths  speaks {frame: line}  stop  heard      log
SCENES = [
    ("212.1: a speak as the first clip loads", [A, B, C],    3,    {},      {0: A, 65: B, 130: C},  None, [A, B, C], [("made", A, 3)]),
    ("a slow disk at a tick-10 speak",       [A, B],       30,   {},      {10: A, 500: B},      None, [A, B],    [("made", A, 20)]),
    ("a free reader: no wait",               [A, B],       3,    {},      {10: A, 70: B},       None, [A, B],    []),
    ("a newer speak while one waits",        [A, B],       100,  {},      {10: A, 50: B},       None, [B],
     [("dropped", A, 40), ("made", END, 90), ("made", B, 150)]),
    ("the voice stops while one waits",      [A, B],       100,  {},      {10: A},              20,   [],        [("dropped", A, 10)]),
]


@pytest.mark.parametrize("row", SCENES, ids=[r[0] for r in SCENES])
def test_a_start_that_finds_the_reader_busy_still_voices_the_scene(voice, row):
    # Bug: v0.2's 212.1 was silent: its first speak, moved to tick 0, met the read of the first clip, the stock voice
    # start gave up on the busy reader, and the speak then ended the voice of the whole sub-scene. A slower disk does the
    # same at Koei's tick 10. A start that waits must run once, never after the voice stopped, and only the newest
    # waiting line may play (an older one's subtitle is gone).
    _, order, read, lengths, speaks, stop, heard, log = row
    game = Game(voice, read=read, lengths=lengths)

    def events(game, frame):
        if frame == 0:
            game.scene(order)
        if frame in speaks:
            game.speak(speaks[frame])
        if frame == stop:
            game.stop()

    for frame in range(800):
        game.frame(frame, events)
    assert (game.heard, game.notes) == (heard, log)


def test_a_start_made_as_a_waiting_load_falls_due_leaves_the_reader_to_that_load(voice):
    # Bug: were the waiting start made after the voice frame's reader check, the reader would look free, the start's
    # read would begin, and the game's waiting load (line C, behind a long English line) would be dropped by the busy
    # reader, so C's play would sound the buffer's old clip.
    game = Game(voice, read=3, waiting=(1, C))
    game.cur, game.busy, game.until[1] = 1, 5, 5  # C's buffer falls quiet as the reader frees, at frame 5

    def events(game, frame):
        if frame == 1:  # C's speak finds the reader busy
            game.voice.start(ctypes.byref(game.kept), C, D, END, ctypes.byref(game.stock))

    for frame in range(20):
        game.frame(frame, events)
    assert (game.heard, game.notes) == ([C], [("made", C, 4)])
