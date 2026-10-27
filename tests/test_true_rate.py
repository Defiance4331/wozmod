"""The true frame rate: the 60 Hz phase, the constant transforms and thunks (runtime/true_rate.h), and the site
catalogue (data/true_rate.toml) against the stock executable."""

from __future__ import annotations

import ctypes
import os
import shutil
import struct
from pathlib import Path

import pytest

from wozmod.runtime import true_rate

GLUE = r'''
#include "true_rate.h"
static struct tr_clock c;
void set_rate(unsigned int n) { tr_set_rate(&c, n); }
unsigned int tick(void) { tr_tick(&c); return c.tick60; }
unsigned int frame(double seconds) { tr_frame(&c, seconds); return c.frame60; }
unsigned int skipped(void) { tr_skipped(&c); return c.frame60; }
float frame_dt(void) { return c.frame_dt; }
float step(void) { return c.step; }
double transform(int kind, double x, double s) { return tr_transform(kind, x, s); }
unsigned int gate(unsigned char *out, unsigned int at, const unsigned char *code, const unsigned char *alt,
                  unsigned int back, const unsigned int *sym, unsigned int syms)
{ return tr_gate(out, at, TR_SYM_TICK60, code, alt, back, sym, syms); }
void window(unsigned int k, unsigned int *n, unsigned int *once) { tr_window(&c, k); *n = c.n; *once = c.once; }
unsigned int patch(unsigned char *out, unsigned int at, const unsigned char *code, unsigned int back,
                   const unsigned int *sym, unsigned int syms)
{ return tr_patch(out, at, code, back, sym, syms); }
'''

RATES = (60, 100, 120, 144, 165, 180, 240)


@pytest.fixture(scope="module")
def clock(c_library):
    lib = c_library("true_rate", GLUE)
    lib.step.restype = ctypes.c_float
    lib.transform.restype = ctypes.c_double
    lib.transform.argtypes = [ctypes.c_int, ctypes.c_double, ctypes.c_double]
    lib.frame.argtypes = [ctypes.c_double]
    lib.frame_dt.restype = ctypes.c_float
    return lib


@pytest.mark.parametrize("rate", RATES)
def test_sixty_periods_a_second_spread_evenly(clock, rate):
    clock.set_rate(rate)
    marks = [clock.tick() for _ in range(rate * 3)]
    assert sum(marks) == 180
    assert clock.step() == pytest.approx(60 / rate)
    for start in range(0, rate * 2, 7):  # any run of k ticks holds k * 60 / N periods, to within one
        for k in (1, 2, 5, rate // 3):
            assert abs(sum(marks[start:start + k]) - k * 60 / rate) < 1.0 + 1e-9


@pytest.mark.parametrize("rate", RATES)
def test_the_frame_time_is_the_seconds_of_the_ticks_since_the_last_present(clock, rate):
    """Bug: a step the game takes once a drawn frame (particle effects) advances one tick's time whatever the pass
    ran, so after a catch-up of k ticks its effects are k - 1 ticks behind and pile up; or the time outlives the
    present, so a frame that repeats (its pass ran no tick) still moves them."""
    clock.set_rate(rate)
    for k in (1, 4, 0, 2, 0, 60, 1):
        for _ in range(k):
            clock.tick()
        assert clock.frame_dt() == pytest.approx(k / rate)
        clock.frame(1.0 / rate)


@pytest.mark.parametrize("rate", (120, 240))
@pytest.mark.parametrize("presents", (None, 240, 184, 100, 40))
def test_a_frame_count_steps_sixty_times_a_second_whatever_the_passes(clock, rate, presents):
    """Once-a-frame counts (play time, item buffs, fades, popups) stand for 1/60 s each. A clock stepped once per
    present ran them at 60 * presents / N a second: the play time at 46 a second at 240 with 184 presents.

    Three seconds of passes of jittered length, each running the ticks its time pays for (the dispatcher), one per
    stand-in after the first: the pass's steps (the draw, the frame end) step 60 times a second while no pass is
    longer than 1/60 s (a longer one steps once, as a stock frame does); the fader, which runs in the frame end and
    every stand-in, 60 times a second at any pass rate. presents None: a pause, passes at the rate and no tick."""
    clock.set_rate(rate)
    passes = presents or rate
    jitter = [0.6, 1.4, 0.9, 1.1, 1.3, 0.7]
    debt = seconds = 0.0
    pass_steps = fader_steps = count = 0
    while seconds < 3.0:
        dt = jitter[count % len(jitter)] / passes
        drawn = clock.frame(dt)  # the pump, at the end of a pass
        pass_steps += drawn
        fader_steps += drawn  # the next pass's frame end
        debt += dt * rate if presents else 0
        ticks = 0 if debt < 0.5 else max(1, int(debt))
        debt -= ticks
        fader_steps += sum(clock.skipped() for _ in range(ticks - 1))  # its catch-up's stand-ins
        seconds += dt
        count += 1
    periods = int(seconds * 60)
    if max(jitter) / passes <= 1 / 60:
        assert abs(pass_steps - periods) <= 1
    assert abs(fader_steps - periods) <= 1


@pytest.mark.parametrize("rate", RATES)
@pytest.mark.parametrize("k", [1, 6])
def test_a_character_updated_every_k_ticks_counts_as_at_sixty(clock, rate, k):
    """Over a second, the windows of a k-tick update schedule hold 60 periods (n) and 60 / k stock calls (once)."""
    clock.set_rate(rate)
    n, once = ctypes.c_uint(), ctypes.c_uint()
    total_n = total_once = 0
    for t in range(1, rate * 6 + 1):
        clock.tick()
        if t % k == 0:
            clock.window(k, ctypes.byref(n), ctypes.byref(once))
            total_n += n.value
            total_once += once.value
    assert total_n == 360
    assert total_once == 360 // k


@pytest.mark.parametrize("kind,x", [(1, 3.5), (2, 0.9), (2, 0.25), (2, 1.02), (3, 0.1), (3, 0.5), (4, 30.0)])
@pytest.mark.parametrize("rate", RATES)
def test_transforms_match_the_closed_forms(clock, kind, x, rate):
    s = 60 / rate
    want = {1: x * s, 2: x ** s, 3: 1 - (1 - x) ** s, 4: x / s}[kind]
    assert clock.transform(kind, x, s) == pytest.approx(want, rel=1e-12)


@pytest.mark.parametrize("rate", RATES)
@pytest.mark.parametrize("f", [0.5, 0.9])
def test_a_decaying_speed_covers_the_stock_distance_at_each_stock_tick(clock, rate, f):
    """pos += v * step; v *= f a stock tick: with v *= f^s and the step times decay_sum, the position at every 60 Hz
    period is the stock position."""
    s = 60 / rate
    fs, k = clock.transform(2, f, s), clock.transform(5, f, s)
    stock = [0.0]
    v = 100.0
    for _ in range(4):
        stock.append(stock[-1] + v)
        v *= f
    pos, v = 0.0, 100.0
    for tick in range(1, 4 * rate // 60 + 1):
        pos += v * s * k
        v *= fs
        if (tick * 60) % rate == 0:
            assert pos == pytest.approx(stock[tick * 60 // rate], rel=1e-9)


@pytest.mark.parametrize("rate", RATES)
def test_a_decay_over_a_second_is_the_stock_decay(clock, rate):
    """v *= f each tick: f^(60/N) over N ticks multiplies by f^60, as 60 stock ticks do."""
    v = 1.0
    f = clock.transform(2, 0.93, 60 / rate)
    for _ in range(rate):
        v *= f
    assert v == pytest.approx(0.93 ** 60, rel=1e-9)


def _disasm(code: bytes, at: int):
    capstone = pytest.importorskip("capstone")
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    return [(i.address, i.mnemonic, i.op_str) for i in md.disasm(code, at)]


def test_a_gate_runs_its_code_on_a_sixty_hertz_tick_and_its_alternative_otherwise(clock):
    code = bytes([true_rate.TR_BYTES, 7, 0x66, 0xFF, 0x88, 0x02, 0x0F, 0x00, 0x00, true_rate.TR_END])  # dec word [eax+0xf02]
    alt = bytes([true_rate.TR_BYTES, 8, 0x66, 0x83, 0x80, 0x02, 0x0F, 0x00, 0x00, 0x00, true_rate.TR_END])  # add word [eax+0xf02], 0
    sym = (ctypes.c_uint * 3)(0x10000000, 0x10000010, 0x10000020)
    out = (ctypes.c_ubyte * 64)()
    n = clock.gate(out, 0x20000000, code, alt, 0x40DE33, sym, 3)
    listing = _disasm(bytes(out[:n]), 0x20000000)
    assert [m for _, m, _ in listing] == ["cmp", "je", "dec", "jmp", "add", "jmp"]
    assert listing[0][2] == "byte ptr [0x10000010], 0"
    assert int(listing[1][2], 16) == listing[4][0]  # je lands on the alternative
    assert int(listing[3][2], 16) == int(listing[5][2], 16) == 0x40DE33


def test_a_patch_resolves_its_symbols_and_branches(clock):
    code = bytes([true_rate.TR_BYTES, 2, 0xD8, 0x0D, true_rate.TR_ABS, 0,
                  true_rate.TR_BYTES, 1, 0xE8, true_rate.TR_REL]) + struct.pack("<I", 0x69CAB0) + bytes([true_rate.TR_END])
    sym = (ctypes.c_uint * 3)(0x10000000, 0x10000010, 0x10000020)
    out = (ctypes.c_ubyte * 64)()
    n = clock.patch(out, 0x20000000, code, 0x412449, sym, 3)
    listing = _disasm(bytes(out[:n]), 0x20000000)
    assert listing == [(0x20000000, "fmul", "dword ptr [0x10000000]"), (0x20000006, "call", "0x69cab0"),
                       (0x2000000B, "jmp", "0x412449")]


def test_the_catalogue_loads_with_known_kinds_and_classes():
    sites = true_rate.load()
    assert sites, "the catalogue is empty"
    vas = [s.va for s in sites]
    assert vas == sorted(set(vas))
    for s in sites:
        assert s.what, f"{s.va:#x} says nothing about what it steps"


@pytest.mark.skipif(shutil.which(true_rate.ASSEMBLER) is None, reason="no MinGW assembler")
def test_catalogue_code_assembles_without_stray_branches():
    """Literal bytes branch only within themselves: a branch out of a thunk must be {call:VA} or {jmp:VA}."""
    capstone = pytest.importorskip("capstone")
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    for s in true_rate.load():
        for text in (s.code, s.alt):
            stream = true_rate.records(text, {}) if text and "{slot:" not in text else b""
            i = 0
            while i < len(stream) and stream[i] != true_rate.TR_END:
                if stream[i] == true_rate.TR_BYTES:
                    chunk = stream[i + 2:i + 2 + stream[i + 1]]
                    for insn in md.disasm(chunk, 0):
                        if insn.mnemonic == "call" or insn.mnemonic.startswith("j"):
                            # only a branch within the same assembled piece
                            assert insn.op_str[:1].isdigit() and int(insn.op_str, 0) <= len(chunk), (
                                f"{s.va:#x}: relative branch out of literal code")
                    i += 2 + stream[i + 1]
                else:
                    i += 2 if stream[i] == true_rate.TR_ABS else 5


@pytest.mark.skipif(not os.environ.get("WOZMOD_GAME"), reason="WOZMOD_GAME unset")
def test_catalogue_matches_the_stock_executable():
    exe = Path(os.environ["WOZMOD_GAME"], "OROCHI_Z.exe")
    if not exe.is_file():
        pytest.skip("no stock OROCHI_Z.exe")
    stock = exe.read_bytes()
    true_rate.check(stock, true_rate.load())
    if shutil.which(true_rate.ASSEMBLER):
        assert "TR_SITES" in true_rate.header(stock)


@pytest.mark.skipif(not os.environ.get("WOZMOD_GAME"), reason="WOZMOD_GAME unset")
def test_each_rewritten_span_is_whole_instructions_that_nothing_jumps_into():
    """A patch or gate replaces whole instructions, and no branch in the code around it lands inside its span (only at
    its start), or the jump into its thunk would cut an instruction in two."""
    capstone = pytest.importorskip("capstone")
    from wozmod.formats import pe
    exe = Path(os.environ["WOZMOD_GAME"], "OROCHI_Z.exe")
    if not exe.is_file():
        pytest.skip("no stock OROCHI_Z.exe")
    data = exe.read_bytes()
    layout = pe.layout(data)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.skipdata = True
    md.detail = True
    text = data[pe.file_offset(layout, 0x401000):pe.file_offset(layout, 0x401000) + 0x2CB000]
    for s in true_rate.load():
        if s.kind == "operand":
            continue
        at = pe.file_offset(layout, s.va)
        ends = [i.address + i.size for i in md.disasm(data[at:at + len(s.stock) + 15], s.va)]
        assert s.va + len(s.stock) in ends, f"{s.va:#x}: the span ends inside an instruction"
        lo = s.va - 0x3000
        window = text[lo - 0x401000:s.va + 0x3000 - 0x401000]
        for insn in md.disasm(window, lo):
            if insn.id == 0:  # data skipped between functions
                continue
            if insn.group(capstone.CS_GRP_JUMP) or insn.group(capstone.CS_GRP_CALL):
                op = insn.operands[0] if insn.operands else None
                if op is not None and op.type == capstone.x86.X86_OP_IMM:
                    assert not s.va < op.imm < s.va + len(s.stock), (
                        f"{s.va:#x}: {insn.address:#x} branches into the span")


def test_catalogue_sites_stay_clear_of_the_runtimes_hooks_and_edits():
    """A site over a hook's jump or an edit's bytes would be refused as not stock, and take the mode down with it."""
    from wozmod.runtime import english_runtime as runtime
    from wozmod.runtime.runtime_edits import (CODE_EDITS, FONT_ROUTE, PAD_CAMERA, STANDARD_CAMERA, VOICE_EDITS,
                                              settings_rows)
    taken = [(va, va + len(prologue)) for va, prologue, *_ in runtime.hook_sites(("ENRT_TEST", "ENRT_MOVING_FIX"))]
    for edits in (CODE_EDITS, FONT_ROUTE, PAD_CAMERA, STANDARD_CAMERA, VOICE_EDITS, settings_rows(1, True, True),
                  settings_rows(2, True, True)):
        taken += [(va, va + len(before)) for va, before, _ in edits]
    for s in true_rate.load():
        for lo, hi in taken:
            assert s.va + len(s.stock) <= lo or hi <= s.va, f"site {s.va:#x} overlaps {lo:#x}"
