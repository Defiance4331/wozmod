"""The volume options on a 3 dB curve (runtime/volume_curve.h, english_runtime.VOLUME), the SE gain at full
precision (runtime_edits.SE_PRECISION) and a stage line's fixed duck (runtime/stage_duck.h, runtime_edits.STAGE_DUCK)."""

from __future__ import annotations

import ctypes
import math
import os
import re
import struct
from pathlib import Path

import pytest

from wozmod.formats import pe
from wozmod.runtime import english_runtime as runtime
from wozmod.runtime.layout import DEFINES
from wozmod.runtime.runtime_edits import SE_PRECISION, STAGE_DUCK

MUSIC, EFFECTS, VOICE = 0x2CCC, 0x3FFF, 0x2EEE  # each category's stock ceiling: its gain at level 15 (0x3FFF = 1.0)
# How the callers turn an option level into the level they pass, with the edits: the option itself, or a duck of it,
# with the stock integer rounding (all values are 0-15, so the shifts and magic divides are floors).
DUCKS = {"option": lambda o: o, "3/4": lambda o: 3 * o // 4, "1/2": lambda o: o // 2}


def _curve(c_library):
    lib = c_library("volume_curve", '#include "volume_curve.h"\n'
                                    "unsigned scaled(unsigned v, unsigned level) { return volume_scaled(v, level); }\n")
    lib.scaled.argtypes, lib.scaled.restype = [ctypes.c_uint, ctypes.c_uint], ctypes.c_uint
    return lib.scaled


def test_level_15_and_every_duck_keep_their_stock_gain_ratio(c_library):
    """Bugs: a curve indexed by the level the caller passed instead of the option (the event-scene music duck at
    level 15 goes from -2.7 to -12 dB), a scale that is not exactly 1 at level 15 (the default changes), rounding the
    stock gain again instead of keeping the game's (a duck at level 15 moves by one step). Every ceiling, option and
    duck the stock callers use; each failing row is reported."""
    scaled = _curve(c_library)
    wrong = []
    for ceiling in (MUSIC, EFFECTS, VOICE):
        for option in range(16):
            full = scaled(ceiling * option // 15, option)
            for duck, level in DUCKS.items():
                stock = ceiling * level(option) // 15
                gain = scaled(stock, option)
                if option == 15 and gain != stock:
                    wrong.append((hex(ceiling), option, duck, "not stock at 15", gain, stock))
                elif option and abs(gain - stock * full / (ceiling * option // 15)) > 1:
                    wrong.append((hex(ceiling), option, duck, "duck ratio", gain, stock))
    assert wrong == []


def test_each_level_is_3_db_under_the_next_and_level_0_is_silent(c_library):
    """Bugs: an off-by-one in the table (every level a step too loud or too quiet), a wrong entry (level 1 silent, or
    left at the stock -23.5 dB). The owner's curve: -3 * (15 - L) dB of the stock ceiling, level 0 silent."""
    scaled = _curve(c_library)
    wrong = [(hex(ceiling), option, scaled(ceiling * option // 15, option))
             for ceiling in (MUSIC, EFFECTS, VOICE) for option in range(16)
             if abs(scaled(ceiling * option // 15, option)
                    - (ceiling * 10 ** (-3 * (15 - option) / 20) if option else 0)) > 1]
    assert wrong == []


# ---- the stock code the hooks and edits sit in ----

REGISTERS = ("eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi")
PARTS = {**{f"{r}x": (f"e{r}x", 0, 16) for r in "abcd"}, **{f"{r}l": (f"e{r}x", 0, 8) for r in "abcd"},
         **{f"{r}h": (f"e{r}x", 8, 8) for r in "abcd"}, **{r[1:]: (r, 0, 16) for r in ("esi", "edi", "ebp", "esp")},
         **{r: (r, 0, 32) for r in REGISTERS}}


def _image():
    exe = Path(os.environ.get("WOZMOD_GAME") or "", "OROCHI_Z.exe")
    if not exe.is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    image = exe.read_bytes()
    return image, pe.layout(image)


class _Machine:
    """Straight-line x86 for the few instructions these level and gain computations use, over the stock image with
    edits applied; any other instruction fails the test instead of being skipped."""

    def __init__(self, image: bytes, layout: dict, edits: list = ()):
        import capstone
        from capstone import x86

        self.image, self.layout, self.x86 = image, layout, x86
        self.edited = {va + i: byte for va, _, after in edits for i, byte in enumerate(after)}
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.md.detail = True
        self.regs, self.memory = {r: 0 for r in REGISTERS}, {}

    def code(self, va: int, size: int = 16) -> bytes:
        at = pe.file_offset(self.layout, va)
        return bytes(self.edited.get(va + i, self.image[at + i]) for i in range(size))

    def read(self, addr: int, size: int) -> int:
        return int.from_bytes(bytes(self.memory.get(addr + i, 0) for i in range(size)), "little")

    def write(self, addr: int, size: int, value: int) -> None:
        data = (value & (1 << 8 * size) - 1).to_bytes(size, "little")
        self.memory.update(zip(range(addr, addr + size), data, strict=True))

    def reg(self, name: str) -> int:
        parent, shift, bits = PARTS[name]
        return self.regs[parent] >> shift & (1 << bits) - 1

    def set_reg(self, name: str, value: int) -> None:
        parent, shift, bits = PARTS[name]
        mask = (1 << bits) - 1 << shift
        self.regs[parent] = self.regs[parent] & ~mask | (value << shift & mask)

    def address(self, op) -> int:
        mem = op.mem
        base = self.reg(self.md.reg_name(mem.base)) if mem.base else 0
        index = self.reg(self.md.reg_name(mem.index)) * mem.scale if mem.index else 0
        return base + index + mem.disp & 0xFFFFFFFF

    def get(self, op) -> int:
        if op.type == self.x86.X86_OP_REG:
            return self.reg(self.md.reg_name(op.reg))
        if op.type == self.x86.X86_OP_IMM:
            return op.imm & (1 << 8 * op.size) - 1
        return self.read(self.address(op), op.size)

    def put(self, op, value: int) -> None:
        if op.type == self.x86.X86_OP_REG:
            self.set_reg(self.md.reg_name(op.reg), value)
        else:
            self.write(self.address(op), op.size, value)

    def push(self, value: int) -> None:
        self.regs["esp"] -= 4
        self.write(self.regs["esp"], 4, value)

    def run(self, start: int, stop: int) -> None:
        """From start to the instruction at stop, which is not run."""
        eip = start
        while eip != stop:
            [ins] = self.md.disasm(self.code(eip), eip, count=1)
            eip, ops, name = ins.address + ins.size, ins.operands, ins.mnemonic
            if name in ("mov", "movzx"):
                self.put(ops[0], self.get(ops[1]))
            elif name == "lea":
                self.put(ops[0], self.address(ops[1]))
            elif name in ("add", "and"):
                a, b = self.get(ops[0]), self.get(ops[1])
                self.put(ops[0], a + b if name == "add" else a & b)
            elif name in ("shr", "sar"):
                bits = ops[0].size * 8
                value = self.get(ops[0]) if name == "shr" else signed(self.get(ops[0]), bits)
                self.put(ops[0], value >> (self.get(ops[1]) if len(ops) > 1 else 1))
            elif name == "imul" and len(ops) == 3:
                self.put(ops[0], signed(self.get(ops[1]), 32) * signed(self.get(ops[2]), 32))
            elif name == "imul":
                product = signed(self.regs["eax"], 32) * signed(self.get(ops[0]), 32)
                self.regs["eax"], self.regs["edx"] = product & 0xFFFFFFFF, product >> 32 & 0xFFFFFFFF
            elif name == "cdq":
                self.regs["edx"] = 0xFFFFFFFF if self.regs["eax"] >> 31 else 0
            elif name == "push":
                self.push(self.get(ops[0]))
            elif name == "jmp":
                eip = ops[0].imm
            elif name != "nop":
                raise AssertionError(f"{ins.address:#x}: {name} {ins.op_str} is outside this machine")


def signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value >> bits - 1 else value


def _direct_branches(image: bytes, layout: dict, targets: set[int]) -> dict[int, int]:
    """{source: target} of every call e8 or jmp e9 in .text to one of targets."""
    text = next(s for s in layout["sections"] if s["name"] == b".text")
    base, body = layout["image_base"] + text["rva"], image[text["raw"]:text["raw"] + text["raw_size"]]
    found = {}
    for m in re.finditer(rb"[\xe8\xe9]", body[:-4]):
        source = base + m.start()
        if (target := source + 5 + struct.unpack_from("<i", body, m.start() + 1)[0]) in targets:
            found[source] = target
    return found


# Each VOLUME hook: where its gain computation starts, the stock ceiling it multiplies by, and where the level comes
# from (the setter's argument, or the option byte itself).
SITES = {
    0x5141E7: (0x5141D0, MUSIC, "argument"),   # set_bgm_volume
    0x514217: (0x514200, MUSIC, "argument"),   # fade_bgm_level
    0x513087: (0x513070, EFFECTS, "argument"),  # set_se_level
    0x512D57: (0x512D40, VOICE, "argument"),   # set_voice_level
    0x5138AD: (0x51388A, EFFECTS, "option"),   # init_se_channels
    0x513A31: (0x513A18, EFFECTS, "option"),   # set_se_channels_muted, unmute
}


def test_each_volume_hook_finds_the_stock_gain_of_its_level_in_edx():
    """Bugs: a hook on another copy of the same five bytes (the setters share them), or a hook before the divide
    finishes, so the curve scales a value that is not the gain; an inline site said to take an option it does not
    read. Runs the stock code from each computation's start to the hook."""
    pytest.importorskip("capstone")
    image, layout = _image()
    assert sorted(SITES) == sorted(va for va, *_ in runtime.VOLUME)
    wrong = []
    for va, _, _, option in runtime.VOLUME:
        start, ceiling, source = SITES[va]
        for level in range(16):
            machine = _Machine(image, layout)
            machine.regs.update(dict.fromkeys(REGISTERS, 0x5A5A5A5A), esp=0x100000)
            machine.write(option, 1, level if source == "option" else 15 - level)
            machine.write(0x100004, 4, level if source == "argument" else 15 - level)  # [esp+4] at the entry
            machine.run(start, va)
            if machine.regs["edx"] != ceiling * level // 15:
                wrong.append((hex(va), level, machine.regs["edx"]))
    assert wrong == []


# Every direct call of a level setter in the stock game: the first instruction of the level it passes, and that
# level as the setter's option byte (VOLUME) makes it at the call.
CALLERS = {
    0x4754A2: (0x475499, "option"),  # set_controller_defaults 0x475440: 15, stored first
    0x4754B0: (0x4754A7, "option"),
    0x4754BE: (0x4754B5, "option"),
    0x47566C: (0x475662, "option"),  # apply_vibration_options 0x475620: the saved levels
    0x475679: (0x475671, "option"),
    0x475686: (0x47567E, "option"),
    0x4B2DB5: (0x4B2DAB, "option"),  # 0x4B2AA0: music and effects back after a pause-like screen
    0x4B2DC2: (0x4B2DBA, "option"),
    0x4B32B7: (0x4B32AD, "option"),  # 0x4B31E0: the same
    0x4B32C4: (0x4B32BC, "option"),
    0x4F4138: (0x4F4130, "option"),  # 0x4F4130: music back
    0x4F42AE: (0x4F4297, "3/4"),     # start_event_scene 0x4F4230: music ducked
    0x4F4FEF: (0x4F4FD5, "option"),  # start_stage_line_voice 0x4F4F80: stock 3L/4 and 4L/5, the full level with
    0x4F5010: (0x4F4FF4, "option"),  # STAGE_DUCK (stage_duck.h sets the depth)
    0x4F5123: (0x4F511B, "option"),  # open_stage_dialog_window 0x4F5090: both back
    0x4F5130: (0x4F5128, "option"),
    0x53F914: (0x53F90A, "1/2"),     # setup_battle_state 0x53F650: music halved
    0x53FB4C: (0x53FB44, "option"),  # 0x53FAD0: music back
    0x5812C8: (0x5812BE, "1/2"),     # 0x581160: music halved
    0x58136F: (0x581367, "option"),  # music back
    0x57227A: (0x572271, "option"),  # the Sound page's defaults (before draw_sound_page 0x5722A0): 15, stored first
    0x572288: (0x57227F, "option"),
    0x572296: (0x57228D, "option"),
    0x572A36: (0x572A30, "option"),  # build_bgm_title_list 0x572940's level setter: stored first
    0x572A90: (0x572A89, "option"),  # a music step
    0x572AA6: (0x572AA0, "option"),  # the SE level setter 0x572AA0
    0x572B00: (0x572AF9, "option"),  # step_se_option 0x572AB0
    0x572B16: (0x572B10, "option"),  # the Voice level setter 0x572B10
    0x572B70: (0x572B69, "option"),  # the Voice step 0x572B20
    0x572CD9: (0x572CD0, "option"),  # run_sound_page 0x572BE0, defaults: 15, stored first
    0x572CE7: (0x572CDE, "option"),
    0x572CF5: (0x572CEC, "option"),
    0x572F6B: (0x572F64, "option"),  # run_sound_page, a music step
    0x572FC1: (0x572FBA, "option"),  # run_sound_page, an effects step
}
SETTERS = {0x5141D0: 0x5141E7, 0x514200: 0x514217, 0x513070: 0x513087, 0x512D40: 0x512D57}  # entry: its hook


def test_every_caller_of_a_level_setter_passes_its_option_or_a_duck_of_it():
    """Bugs: a caller that passes a level not made from the setter's option byte at the call (a level from another
    option, or one stored after the call), whose gain the curve would scale by the wrong option; a call added or
    misread; a STAGE_DUCK edit that leaves part of the stock factor (4L, the x4 kept: louder than the option, or 3L).
    The curve needs level <= option, which every derivation here keeps. Each call runs from its level's first
    instruction twice, with the edits, the option byte and the registers disagreeing, so a level from neither is
    caught."""
    pytest.importorskip("capstone")
    image, layout = _image()
    option_of = {va: option for va, _, _, option in runtime.VOLUME}
    calls = _direct_branches(image, layout, set(SETTERS))
    wrong = [("not in CALLERS", hex(va)) for va in sorted(set(calls) - set(CALLERS))]
    wrong += [("not a call of a setter", hex(va)) for va in sorted(set(CALLERS) - set(calls))]
    for call, (start, duck) in CALLERS.items():
        if call not in calls:
            continue
        option = option_of[SETTERS[calls[call]]]
        for level in range(16):
            for registers in (level, (level + 7) % 16):
                machine = _Machine(image, layout, SE_PRECISION + STAGE_DUCK)
                machine.regs.update(dict.fromkeys(REGISTERS, 0x5A5A5A00 | registers), esp=0x100000)
                machine.write(option, 1, level)
                machine.run(start, call)
                passed, stored = machine.read(machine.regs["esp"], 1), machine.read(option, 1)
                if passed != DUCKS[duck](stored):
                    wrong.append((hex(call), level, registers, passed, stored))
    assert wrong == []


# The functions that set a category's gain, below the setters: who may call them with a gain made from no option.
SINKS = (0x5E1520, 0x5E20C0, 0x5E20E0, 0x5E1FE0)  # music, SE (register 0x10), voice, an SE register
NOT_FROM_AN_OPTION = {0x5E11BD: "the sound start-up, SE at full 0x3FFF", 0x5E11D5: "the same, voice at 0x2EEE"}


def test_every_gain_from_an_option_goes_through_a_volume_hook():
    """Bug: code that turns an option into a gain on its own and calls the music, SE or voice setter below the
    level setters, so that category keeps the stock straight line at that call."""
    image, layout = _image()
    functions = [(start, start + 0x30) for start in SETTERS] + [(0x513880, 0x513940), (0x513A10, 0x513AE0)]
    wrong = [hex(source) for source in _direct_branches(image, layout, set(SINKS))
             if source not in NOT_FROM_AN_OPTION and not any(lo <= source < hi for lo, hi in functions)
             and not 0x5E20C0 <= source < 0x5E20E0]  # set_se_volume's own call of set_audio_register
    assert wrong == []


# ---- the SE gain on its way to a channel ----

PUSHES = {  # each way V reaches push_channel_volume 0x5E1980: its start, where the pushed dword is ready, and how
    # V enters (a register or the last-V word 0x140A6AC); then where the dword lands as 0x5E1980's second argument
    "set_audio_register 0x5E1FE0": (0x5E2011, 0x5E2029, "ebx"),  # then its store into the pushed slot, 0x5E202C
    "its fade tick (step_fade_ramp 0x5E0E80)": (0x5E0FCC, 0x5E0FEB, "ax"),
    "repush_se_channel_volume 0x5E1C90": (0x5E1C90, 0x5E1CAD, "last"),
}
LAST_V = 0x140A6AC


def _channel_gain(image: bytes, layout: dict, edits: list, path: str, v: int) -> float:
    """The float gain 0x5E1980 sends a channel for SE gain v, along one path, with edits applied: the stock bytes run
    through the machine, 0x5E1980's movzx and fdiv decoded from the (edited) bytes and its divisor read from the
    image."""
    start, ready, entry = PUSHES[path]
    machine = _Machine(image, layout, edits)
    machine.regs.update(dict.fromkeys(REGISTERS, 0x5A5A5A5A), esp=0x100000)
    if entry == "last":
        machine.write(LAST_V, 2, v)
    else:
        machine.set_reg(entry, v)
    machine.run(start, ready)
    if path.startswith("set_audio_register"):
        machine.regs["esp"] = 0x100000 - 0x18  # the slot [esp+0x18] is the pushed argument
        machine.run(0x5E202C, 0x5E2030)
        argument = machine.read(0x100000, 4)
    else:
        argument = machine.regs["eax"]
    return _push_channel_volume(machine, argument)


def _push_channel_volume(machine: _Machine, argument: int) -> float:
    """0x5E1980's gain for its second argument: its read of the argument (byte or word) over the qword divisor its
    fdiv names, as the float32 it stores."""
    [read] = machine.md.disasm(machine.code(0x5E19A7, 5), 0x5E19A7)
    [divide] = machine.md.disasm(machine.code(0x5E19C0, 6), 0x5E19C0)
    assert (read.mnemonic, divide.mnemonic) == ("movzx", "fdiv")
    divisor_at = pe.file_offset(machine.layout, divide.operands[0].mem.disp)
    divisor = struct.unpack("<d", machine.image[divisor_at:divisor_at + 8])[0]
    return struct.unpack("<f", struct.pack("<f", (argument & (1 << 8 * read.operands[1].size) - 1) / divisor))[0]


def _mb(gain: float) -> int:
    """0x670E80: the gain in millibels, truncated, clamped to -10000..0."""
    return -10000 if gain <= 1e-5 else max(-10000, min(0, int(2000 * math.log10(gain))))


def test_the_se_gain_reaches_a_channel_at_full_precision():
    """Bugs: a push left quantising to 7 bits (levels 1 and 2, 3 and 4 sound the same, and V under 129 is silent);
    a divisor other than 16383.0 (every effect off its level); a byte store or byte read left in the chain (the
    channel hears V mod 256). On each path: the 128 stock 7-bit steps (V = 129k) send the stock float, full V sends
    1.0, and every V in 1..128 sounds."""
    pytest.importorskip("capstone")
    image, layout = _image()
    wrong = []
    for path in PUSHES:
        for v in range(0, 16384, 129):
            stock, edited = (_channel_gain(image, layout, e, path, v) for e in ((), SE_PRECISION))
            if stock != edited:
                wrong.append((path, v, "off the stock step", stock, edited))
        full = _channel_gain(image, layout, SE_PRECISION, path, 0x3FFF)
        if full != 1.0 or _mb(full) != 0:
            wrong.append((path, 0x3FFF, "full is not 0 mB", full))
        wrong += [(path, v, "silent") for v in range(1, 129)
                  if _mb(_channel_gain(image, layout, SE_PRECISION, path, v)) == -10000]
        wrong += [(path, v, "not V / 16383") for v in (130, 367, 1092, 6553, 16382)
                  if _channel_gain(image, layout, SE_PRECISION, path, v) != struct.unpack(
                      "<f", struct.pack("<f", v / 16383))[0]]
    assert wrong == []


def test_the_channel_reset_still_sends_full_gain():
    """Bug: the reset 0x5E1EB0 left pushing 127, which 0x5E1980 now reads as 127/16383 (-42 dB): every effect
    channel quiet until the SE option is applied again. Follows the reset's jl (always taken) to its push."""
    capstone = pytest.importorskip("capstone")
    image, layout = _image()
    gains = []
    for edits in ((), SE_PRECISION):
        machine = _Machine(image, layout, edits)
        [jl] = machine.md.disasm(machine.code(0x5E1F0D, 2), 0x5E1F0D)
        assert jl.mnemonic == "jl"
        push = list(machine.md.disasm(machine.code(jl.operands[0].imm, 16), jl.operands[0].imm, count=3))
        assert [i.mnemonic for i in push] == ["push", "push", "call"] and push[2].operands[0].imm == 0x5E1980
        assert push[1].op_str == "edi" and push[0].operands[0].type == capstone.x86.X86_OP_IMM
        gains.append(_push_channel_volume(machine, push[0].operands[0].imm))
    assert gains == [1.0, 1.0]


# ---- a stage line's duck ----

def _duck(c_library):
    lib = c_library("stage_duck", '#include "volume_curve.h"\n#include "stage_duck.h"\n'
                                  "static struct stage_duck d;\n"
                                  "void line(int voiced) { stage_duck_line(&d, voiced); }\n"
                                  "void release(int spoken) { stage_duck_release(&d, spoken); }\n"
                                  "int restore(void) { return stage_duck_restore(&d); }\n"
                                  "unsigned ducked(unsigned stock, unsigned level, int music) {\n"
                                  "    return stage_duck_gain(&d, volume_scaled(stock, level),\n"
                                  "                           music ? STAGE_DUCK_MUSIC : STAGE_DUCK_EFFECTS); }\n")
    lib.ducked.argtypes, lib.ducked.restype = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], ctypes.c_uint
    lib.restore.restype = ctypes.c_int
    return lib


def test_a_voiced_stage_line_ducks_music_and_effects_by_the_owners_depth_at_every_level(c_library):
    """Bugs: a depth made from 3/4 instead of the stock 11/15 heard at full sliders (music 0.19 dB off), the music
    and effects depths swapped, a whole-level duck left in (the depth moves with the slider, level 1 mutes), a duck on
    a line with no voice, level 0 no longer silent. The owner's depth, ledger row 42: the stock drop at full sliders
    (music 11/15, effects 12/15) and 4 dB more, on the curve's gain at every option; a line with no voice keeps it."""
    lib = _duck(c_library)
    wrong = []
    for music, ceiling, stock_duck in ((1, MUSIC, 11 / 15), (0, EFFECTS, 12 / 15)):
        depth = 20 * math.log10(stock_duck) - 4
        for level in range(16):
            curve = ceiling * 10 ** (-3 * (15 - level) / 20) if level else 0
            for voiced in (1, 0):
                lib.line(voiced)
                gain = lib.ducked(ceiling * level // 15, level, music)
                expected = curve * 10 ** (depth / 20) if voiced else curve
                if abs(gain - expected) > 1.5 or (level and not gain):
                    wrong.append(("music" if music else "effects", level, voiced, gain, round(expected, 1)))
    assert wrong == []


def test_a_released_spoken_window_is_restored_by_its_ticks_end_unless_a_line_takes_over(c_library):
    """Bugs: a restore between two chained lines (the bed swells for a moment under every word-5 chain), a restore
    left pending (made every tick, or at the next battle's first tick), a notice box's close restoring (undoing an
    event scene's own music level), the stock leak kept (an event that ends on word 5 = 1 stays ducked). A tick is
    0x4F5090 (the release), then the window commands (a line), then 0x4ED6D0 (the restore check)."""
    lib = _duck(c_library)
    ticks = {  # each tick's events before its end: the restores due at each end
        "chained voiced line": ([["release 1", "line 1"]], [0]),
        "chained unvoiced load": ([["release 1", "line 0"]], [0]),  # its own setter calls set the full level
        "the event ends on word 5 = 1": ([["release 1"], []], [1, 0]),
        "a voiceless line follows": ([["release 1"]], [1]),  # index < 0 or kind 3: it calls no setter
        "the notice box closes": ([["release 0"]], [0]),
        "a line, no release": ([["line 1"], []], [0, 0]),
    }
    wrong = []
    for case, (events, expected) in ticks.items():
        lib.restore()
        got = []
        for tick in events:
            for event in tick:
                name, value = event.split()
                getattr(lib, name)(int(value))
            got.append(lib.restore())
        if got != expected:
            wrong.append((case, got, expected))
    assert wrong == []


def test_the_stage_duck_hooks_sit_on_the_line_its_two_setter_calls_and_each_ticks_end():
    """Bugs: a return address taken from a call's own address (the duck never applies), the music and effects returns
    swapped (music ducked by the effects depth), a line hook that does not see the load's result in edi, a caller of
    the window commands whose tick never reaches the restore check (a released duck kept on, the stock leak)."""
    image, layout = _image()
    hooks = {hook_id: va for va, _, hook_id in runtime.STAGE_DUCK_HOOKS}
    sites = _direct_branches(image, layout, {0x5137F0, 0x514200, 0x513070, 0x4EECC0, 0x4ED6D0})

    def call_before(va: int) -> int | None:
        return sites.get(va - 5)

    at = pe.file_offset(layout, hooks[156])
    wrong = []
    if call_before(hooks[156] - 2) != 0x5137F0 or image[at - 2:at] != bytes.fromhex("8bf8"):
        wrong.append("the line hook is not right after the load and its mov edi, eax")
    if call_before(DEFINES["STAGE_LINE_MUSIC"]) != 0x514200:
        wrong.append("STAGE_LINE_MUSIC is not the return of fade_bgm_level")
    if call_before(DEFINES["STAGE_LINE_EFFECTS"]) != 0x513070:
        wrong.append("STAGE_LINE_EFFECTS is not the return of set_se_level")
    if not hooks[156] < DEFINES["STAGE_LINE_MUSIC"] < DEFINES["STAGE_LINE_EFFECTS"] < 0x4F5052:
        wrong.append("the setter calls are not the line's own, between its load and its load test")
    commands = sorted(va for va, target in sites.items() if target == 0x4EECC0)
    ends = sorted(va for va, target in sites.items() if target == 0x4ED6D0)
    if hooks[158] != 0x4ED6D0 or [va + 5 for va in commands] != ends:
        wrong.append(("the window commands and the tick's end", [hex(v) for v in commands], [hex(v) for v in ends]))
    assert wrong == []
