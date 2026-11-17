"""An event line's mouths follow its voice as heard (runtime/lip_clock.h): the lip slot handed a speak's block takes its
time from the samples its voice's stream has played, not from the event clock."""

from __future__ import annotations

import ctypes

import pytest

ID, FLAGS = 0xC + 0x10, 0xC + 0x1C  # buffer 0 (0x40 bytes a buffer) of the voice manager
NO_VOICE, NOBODY = 0xFFFFFFFF, 0xFFFFFFFF
NO_WAITS = (NO_VOICE, NO_VOICE)  # voice_waits: the id whose play waits on each buffer, none here
# A buffer's flags, as the stock voice manager leaves them: loaded (bits 1 and 3), played (bit 3 cleared, bit 4 set),
# started by the voice frame (bit 1 cleared, bit 2 set), quiet (bit 2 cleared, id -1), loaded again with stale bits.
LOADED, PLAYED, STARTED, QUIET, LOADED_STALE = 0x0A, 0x12, 0x14, 0x10, 0x1E
STOPPED, PLAYING = 0, 1  # the stream player's state
LINE, OTHER, OLD = 0xB75, 0xB76, 0xB74
RATE, LENGTH = 48000, 241  # the clip's sample rate; the block's frame count
SCENE, LATER_SCENE = 0x1000, 0x2000  # the scene's lip time array

GLUE = r'''
#include "lip_clock.h"
static unsigned int state[2], played[2], rate[2], lengths[8], reports;
static float times[8];
static char texts[4][96];
static unsigned int stream(unsigned int b, unsigned int *p, unsigned int *r)
{
    *p = played[b];
    if (!*r)
        *r = rate[b];
    return state[b];
}
static float *slot(unsigned int s, unsigned int *length)
{
    if (s >= 8)
        return 0;
    *length = lengths[s];
    return &times[s];
}
static void report(const struct lip_line *l) { lip_text(l, texts[reports++ & 3]); }
static double clock;
static double now(void) { return clock; }
static const struct lip_game game = {stream, slot, report, now};
static struct lip_clock lc;
void reset(void) { lc = (struct lip_clock){{{0}}}; reports = 0; clock = 0; }
void wait(double seconds) { clock += seconds; }
void set_stream(unsigned int b, unsigned int s, unsigned int p, unsigned int r) { state[b] = s, played[b] = p, rate[b] = r; }
float *slot_times(void) { return times; }
unsigned int *slot_lengths(void) { return lengths; }
void hand(unsigned char *m, unsigned int s, unsigned int scene, unsigned int id, unsigned int w0, unsigned int w1)
{
    const unsigned int waits[2] = {w0, w1};
    lip_hand(&lc, &game, m, s, scene, id, waits);
}
void run_pass(unsigned char *m, unsigned int scene, unsigned int w0, unsigned int w1)
{
    const unsigned int waits[2] = {w0, w1};
    lip_pass(&lc, &game, m, scene, waits);
}
unsigned int report_count(void) { return reports; }
const char *report_text(unsigned int i) { return texts[i & 3]; }
const char *text(unsigned int id, float gap, float drift)
{
    static char out[96];
    struct lip_line l = {LIP_HEARD, 0, id, 0, 0, gap, drift, 0.0, -1.0};
    return lip_text(&l, out);
}
'''


@pytest.fixture(scope="module")
def library(c_library):
    lib = c_library("lip_clock", GLUE)
    lib.slot_times.restype = ctypes.POINTER(ctypes.c_float)
    lib.slot_lengths.restype = ctypes.POINTER(ctypes.c_uint)
    lib.report_text.restype = ctypes.c_char_p
    lib.text.restype = ctypes.c_char_p
    lib.text.argtypes = [ctypes.c_uint, ctypes.c_float, ctypes.c_float]
    return lib


@pytest.fixture
def lips(library):
    return Scene(library)


class Scene:
    """An event scene as the runtime sees it: the voice manager's two buffers, each buffer's stream, and the lip slots,
    which the stock stepper (0x4A1C40) advances by half the frame step before each pass, a sixtieth of a second of
    wall clock a tick."""

    def __init__(self, lib):
        self.lib = lib
        lib.reset()
        self.m = (ctypes.c_ubyte * 0x100)()
        self.times, self.lengths = lib.slot_times(), lib.slot_lengths()
        for s in range(8):
            self.times[s], self.lengths[s] = 0.0, LENGTH
        for b in (0, 1):
            self.buffer(b, NOBODY, QUIET)
            self.stream(b, STOPPED, 0)
        self.waits = NO_WAITS

    def buffer(self, b, voice, flags):
        ctypes.c_uint.from_buffer(self.m, ID + 0x40 * b).value = voice
        ctypes.c_uint.from_buffer(self.m, FLAGS + 0x40 * b).value = flags

    def stream(self, b, state, played, rate=RATE):  # rate: as the runtime reads it from the engine
        self.lib.set_stream(b, state, played, rate)

    def speak(self, slot, voice):  # hook 71 at the hand-off; voice NO_VOICE for a block no voice goes with
        self.lib.hand(self.m, slot, SCENE, voice, *self.waits)
        self.times[slot] = 0.0  # 0x4A1710 zeroes the slot's lip time

    def pass_(self, ticks=1, scene=SCENE, seconds=None):  # seconds of wall clock, when not those of the ticks
        for s in range(8):
            self.times[s] += 0.5 * ticks
        self.lib.wait(ctypes.c_double(ticks / 60 if seconds is None else seconds))
        self.lib.run_pass(self.m, scene, *self.waits)
        return self.times


def test_mouths_hold_until_the_voice_is_heard_then_follow_its_samples(lips):
    """Bugs: the mouths move before the voice is heard (while its play waits for the voice frame, or its stream still
    opens and keeps the previous clip's total), follow the event clock through a slow pass, snap to 0 when the clip
    stops, run past the block's last frame, or keep being written once the buffer's channel falls quiet (a stuck
    mouth)."""
    lips.buffer(1, LINE, PLAYED)
    lips.stream(1, STOPPED, 90000)  # the previous clip's total, until the stream opens this one
    lips.speak(3, LINE)
    #   buffer flags, stream state, samples played, ticks this pass, slot 3's lip time after the pass
    rows = [
        (PLAYED,  STOPPED, 90000,   1, 0.0),    # the voice frame has not started it
        (STARTED, STOPPED, 90000,   1, 0.0),    # handed to the engine; the channel thread still opens the stream
        (STARTED, PLAYING, 480,     1, 0.3),
        (STARTED, PLAYING, 10080,   6, 6.3),    # a slow pass: the event clock gained 3 lip frames, the voice 6
        (STARTED, PLAYING, 24000,   1, 15.0),
        (STARTED, STOPPED, 432000,  1, 240.0),  # the clip ended at 9 s, past the block: its last frame
        (QUIET,   STOPPED, 432000,  1, 240.5),  # the line is over: the stock stepper carries on
        (QUIET,   STOPPED, 432000,  1, 241.0),
    ]
    got = []
    for flags, state, played, ticks, _ in rows:
        lips.buffer(1, LINE if flags != QUIET else NOBODY, flags)
        lips.stream(1, state, played)
        got.append(round(lips.pass_(ticks)[3], 3))
    assert got == [row[-1] for row in rows]


# Each row: the voice buffers at the speak's hand-off (id, flags), the ids whose play waits on each buffer (voice_waits),
# the voice the hand-off names. Expected: whether slot 3 then follows that voice (its lip time held at 0 while the voice
# is not heard) or the event clock (0.5).
HANDS = [
    ("played, the voice frame to start it", [(LINE, PLAYED), (OLD, STARTED)],  NO_WAITS,   LINE,     True),
    ("played on the other buffer",          [(OLD, STARTED), (LINE, PLAYED)],  NO_WAITS,   LINE,     True),
    ("kept: its clip waits to load",        [(OLD, STARTED), (OLD, STARTED)],  (NO_VOICE, LINE), LINE, True),
    ("its start waits for the reader",      [(LINE, LOADED), (OLD, STARTED)],  (LINE, NO_VOICE), LINE, True),
    ("loaded, not played (stale bits)",     [(LINE, LOADED_STALE), (OLD, STARTED)], NO_WAITS, LINE,  False),
    ("loaded, not played",                  [(LINE, LOADED), (OLD, STARTED)],  NO_WAITS,   LINE,     False),
    ("another clip played",                 [(OTHER, PLAYED), (OLD, STARTED)], NO_WAITS,   LINE,     False),
    ("no voice started",                    [(NOBODY, QUIET), (NOBODY, QUIET)], NO_WAITS,  LINE,     False),
    ("a face block, a voice waiting",       [(LINE, PLAYED), (OLD, STARTED)],  NO_WAITS,   NO_VOICE, False),
]


@pytest.mark.parametrize("buffers, waits, voice, follows", [row[1:] for row in HANDS], ids=[row[0] for row in HANDS])
def test_a_hand_off_follows_the_voice_its_speak_started(lips, buffers, waits, voice, follows):
    """Bugs: a face block or a speak whose voice did not start is captured by a voice (its mouths frozen at 0 for a
    voice that is not theirs); a preloaded clip with a stale started bit is read as the line's voice; a line whose clip
    loads after its play, or whose start waits for the archive reader, keeps the event clock and runs ahead of its late
    voice."""
    for b, (voice_id, flags) in enumerate(buffers):
        lips.buffer(b, voice_id, flags)
    lips.waits = waits
    lips.speak(3, voice)
    assert lips.pass_()[3] == (0.0 if follows else 0.5)


def test_a_line_whose_clip_loads_after_its_play_waits_through_the_load(lips):
    """Bug: the line ends when its late clip loads (the buffer then shows loaded), and the mouths take the event clock
    ahead of the voice."""
    lips.buffer(1, OLD, STARTED)
    lips.waits = (NO_VOICE, LINE)
    lips.speak(3, LINE)
    lips.buffer(1, LINE, LOADED)  # the load made once the old clip fell quiet; the kept play not made yet
    assert lips.pass_()[3] == 0.0
    lips.waits = NO_WAITS
    lips.buffer(1, LINE, PLAYED)
    assert lips.pass_()[3] == 0.0
    lips.buffer(1, LINE, STARTED)
    lips.stream(1, PLAYING, 4800)
    assert lips.pass_()[3] == 3.0


def test_a_slot_handed_another_block_leaves_the_voice_it_followed(lips):
    """Bugs: a slot keeps following the previous line's voice, still playing out on the other buffer, after a later
    speak or a face block replaced its block; a face block on another slot is driven by that voice."""
    lips.buffer(0, LINE, PLAYED)
    lips.speak(3, LINE)
    lips.buffer(0, LINE, STARTED)
    lips.stream(0, PLAYING, 48000)
    assert lips.pass_()[3] == 30.0
    lips.speak(5, NO_VOICE)  # a face block on slot 5
    lips.buffer(1, OTHER, PLAYED)
    lips.speak(3, OTHER)     # the next line, on slot 3 again
    lips.stream(0, PLAYING, 52800)
    times = lips.pass_()
    assert (times[3], times[5]) == (0.0, 0.5)
    lips.buffer(1, OTHER, STARTED)
    lips.stream(1, PLAYING, 2400)
    assert lips.pass_()[3] == 1.5
    lips.speak(3, NO_VOICE)  # a face block replaces that line too: the event clock again
    lips.stream(1, PLAYING, 4800)
    assert lips.pass_()[3] == 0.5


def test_a_line_writes_only_into_its_own_scene(lips):
    """Bug: a line from a skipped scene, its voice still in a buffer, writes into the same slot of the next scene's
    arrays (or another context's tick)."""
    lips.buffer(0, LINE, PLAYED)
    lips.speak(3, LINE)
    lips.buffer(0, LINE, STARTED)
    lips.stream(0, PLAYING, 4800)
    assert lips.pass_(scene=LATER_SCENE)[3] == 0.5
    assert lips.pass_()[3] == 3.0


# Each row: the rate the runtime reads for the clip, whether the clock drives the slot, the report expected.
DRIFTS = [
    ("in time through a stall",             RATE,     True,  None),
    ("rate read twice the clip's",          2 * RATE, True,  "start gap 0.0, end drift -18.5"),
    ("left to the event clock (op 16)",     RATE,     False, "start gap 0.0, end drift -7.5"),
]


@pytest.mark.parametrize("rate, driven, text", [row[1:] for row in DRIFTS], ids=[row[0] for row in DRIFTS])
def test_a_line_is_reported_only_when_its_mouths_ended_off_its_voice(lips, rate, driven, text):
    """Bugs: the drift is measured against the samples the lip time is made from, so a rate read wrong (here twice
    the clip's, as a byte rate would be) runs the mouths at half speed unreported; a slot the clock does not drive
    goes unreported though a stall left its event clock behind the voice; or a line in time is reported."""
    lips.lengths[2] = LENGTH if driven else 0
    lips.buffer(0, LINE, PLAYED)
    lips.speak(2, LINE)
    lips.buffer(0, LINE, STARTED)
    played = 0
    for k in range(1, 61):  # 800 samples of the 48 kHz voice a tick; pass 30 stalls 0.25 s on one tick
        stall = 0.25 if k == 30 else 0.0
        played += 800 + int(RATE * stall)
        lips.stream(0, PLAYING, played, rate)
        lips.pass_(seconds=1 / 60 + stall)
    lips.buffer(0, NOBODY, QUIET)
    lips.pass_()
    reports = [lips.lib.report_text(i).decode() for i in range(lips.lib.report_count())]
    assert reports == ([] if text is None else [f"lips off their voice: id B75, {text} lip frames"])


@pytest.mark.parametrize("voice, gap, drift, text", [
    (0xB94, 0.0, -5.06, "id B94, start gap 0.0, end drift -5.1"),
    (0x8F4, 0.94, 1234.5, "id 8F4, start gap 0.9, end drift 1234.5"),
    (0x10A3D, -0.04, -12.0, "id 10A3D, start gap 0.0, end drift -12.0"),
])
def test_report_text(lips, voice, gap, drift, text):
    """Bugs: a lost sign or leading zero (".9" for 0.9), a digit shifted past the point, a voice id padded or cut."""
    assert lips.lib.text(voice, gap, drift).decode() == f"lips off their voice: {text} lip frames"


def test_a_line_is_measured_while_its_voice_plays(lips):
    """Bug: the end drift is read after the clip has ended, while the channel is still busy, so a slot the clock does
    not drive (as op 16 leaves them) is reported for the stock stepper running on past the voice, though its mouths
    kept time with it while it played."""
    lips.lengths[2] = 0
    lips.buffer(0, LINE, PLAYED)
    lips.speak(2, LINE)
    lips.buffer(0, LINE, STARTED)
    for k in range(1, 40):  # in step: the stepper's half lip frame a pass, 800 samples a pass of the voice
        lips.stream(0, PLAYING, 800 * k)
        lips.pass_()
    for _ in range(20):  # the clip has ended; the channel falls quiet a few passes later
        lips.stream(0, STOPPED, 800 * 39)
        lips.pass_()
    lips.buffer(0, NOBODY, QUIET)
    lips.pass_()
    assert lips.lib.report_count() == 0
