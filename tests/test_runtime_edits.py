"""Behavioral checks for fixed runtime edit assembly."""

from __future__ import annotations

import os
import re
import shutil
import struct
from pathlib import Path

import pytest

from wozmod.formats import pe
from wozmod.runtime import runtime_edits


def test_assembled_calls_use_each_instruction_end_for_rel32() -> None:
    assert runtime_edits.assembled(0x1000, b"\x90\x90", 0x2000, b"\x90", 0x0800) == bytes.fromhex(
        "9090 e8f90f0000 90 e8f3f7ffff"
    )


def test_detour_does_not_pad_past_the_overwritten_stock_span() -> None:
    assert runtime_edits.detour(0x1000, b"\x90" * 7, 0x2000, 0x1007, b"\x50", 0x3000) == [
        (0x1000, b"\x90" * 7, bytes.fromhex("e9fb0f0000 9090")),
        (0x2000, b"\xcc" * 11, bytes.fromhex("50 e8fa0f0000 e9fcefffff")),
    ]


@pytest.mark.parametrize(("rows", "moving_fix", "count", "value_stop", "default"),
                         [(1, False, 7, 5, 6), (2, False, 8, 6, 7), (1, True, 8, 6, 7), (2, True, 9, 7, 8)])
def test_settings_rows_does_not_leave_default_at_the_old_index(
    rows: int, moving_fix: bool, count: int, value_stop: int, default: int,
) -> None:
    # Bug: a release build keeps a cursor slot for a compiled-out row (moving camera fix): an empty row.
    edits = runtime_edits.settings_rows(rows, moving_fix)
    assert [(va, len(stock), len(patched)) for va, stock, patched in edits] == [
        (0x475B20, 7, 7), (0x56B91C, 2, 2), (0x56B2ED, 17, 17), (0x56B60B, 3, 3), (0x56B750, 3, 3),
        (0x56B7E4, 3, 3), (0x56B806, 3, 3), (0x56B86A, 19, 19), (0x56B8AB, 19, 19),
    ]
    assert [edits[index][2][position] for index, position in [(2, 1), (3, 2), (4, 2), (5, 2), (6, 2)]] == [
        count, value_stop, value_stop, default, default,
    ]


@pytest.mark.skipif(os.environ.get("WOZMOD_AUDIO") != "1" or not shutil.which("ffmpeg")
                    or not os.environ.get("WOZMOD_DONORS") or not os.environ.get("WOZMOD_GAME"),
                    reason="set WOZMOD_AUDIO=1, WOZMOD_DONORS and WOZMOD_GAME, with ffmpeg")
def test_sound_pool_holds_every_english_effect_bank_on_top_of_the_stock_ones() -> None:
    # Bug: the English effect banks grow (more clips, another format) past the pool the voice installs size at boot;
    # the pool fills at battle start and the game crashes. Each bank's header and data are pool blocks with a 16-byte
    # header, rounded to 16 (a stock 8,140,732-byte bank takes 8,140,752).
    from wozmod.formats.audio import SE_BANKS, english_se_bank, moz_se_banks
    from wozmod.formats.linkdata import moz_entries, ps2_members
    from wozmod.voices.build import PSP_ENGLISH_SHOUTS, PSP_SHOUTS

    game, donors = Path(os.environ["WOZMOD_GAME"]), Path(os.environ["WOZMOD_DONORS"])
    seidx, se = moz_entries(game / "LINKDATA1.BIN", game / "LINKDATA1.IDX", [4056, 4057]).values()
    psp = ps2_members(donors / "wo2-psp" / PSP_SHOUTS, [PSP_ENGLISH_SHOUTS])[PSP_ENGLISH_SHOUTS]
    bank, _ = english_se_bank(seidx, se, (donors / "wo1-x360/link_sebank.hdx").read_bytes(), donors / "wo1-x360/link_sebank.bdx",
                              (donors / "wo2-x360/link_sebank.hdx").read_bytes(), donors / "wo2-x360/link_sebank.bdx", psp)
    block = lambda size: (size + 16 + 15) // 16 * 16 if size else 0
    growth = 0
    for b, (header, data) in enumerate(moz_se_banks(seidx, se)):
        _, header_size, _, data_size = struct.unpack_from("<4I", bank, 4 + 16 * b)
        if data_size:
            growth += max(0, block(header_size) + block(data_size) - block(len(header)) - block(len(data)))

    assert SE_BANKS == len(moz_se_banks(seidx, se))
    assert runtime_edits.SOUND_POOL >= runtime_edits.STOCK_SOUND_POOL + growth, hex(growth)


def _options_byte_after(code: bytes, value: int) -> int:
    """The options byte after a patched sequence's leading byte operations: and/or al, imm8 (24, 0C) on the byte in
    al, or and/or r/m8, imm8 (80 /4, 80 /1 with a SIB and disp32) on the byte in memory. Stops at anything else."""
    at = 0
    while at < len(code):
        if code[at] in (0x24, 0x0C):
            value = value & code[at + 1] if code[at] == 0x24 else value | code[at + 1]
            at += 2
        elif code[at] == 0x80 and code[at + 1] in (0xA4, 0x8C):  # and/or byte [reg + reg + disp32], imm8
            value = value & code[at + 7] if code[at + 1] == 0xA4 else value | code[at + 7]
            at += 8
        else:
            break
    return value


def test_new_saves_and_controller_default_start_the_horizontal_camera_at_standard() -> None:
    """Bug: a new save or the Controller page's DEFAULT leaves CAMERA - HORIZONTAL at the stock INVERSE (bit 0x1
    clear), sets the vertical bit (0x2) instead, misses player 2, or DEFAULT also changes the other options."""
    edits = {va: after for va, _, after in runtime_edits.STANDARD_CAMERA}
    # The two first-run sites jump into the cave; the cave code is what runs on the options byte in al.
    caves = {site: edits[site + 5 + struct.unpack_from("<i", edits[site], 1)[0]] for site in (0x47544C, 0x475468)}
    wrong = []
    for value in range(256):
        for site, cave in caves.items():  # first run: horizontal set, vertical and vibration clear, the rest kept
            if _options_byte_after(cave, value) != (value & 0xF8) | 0x01:
                wrong.append((hex(site), value))
        if _options_byte_after(edits[0x570C98], value) != value | 0x01:  # DEFAULT: only the horizontal bit set
            wrong.append(("default", value))
    assert not wrong[:4]


def test_xbox_defaults_bind_each_camera_action_to_its_right_stick_half() -> None:
    """Bug: an edit lands on a neighbouring push (the flag's, a shifted offset) that holds the same stock byte, or
    swaps a direction, so a camera action stays unbound or turns the wrong way; read back by setter call target."""
    exe = Path(os.environ.get("WOZMOD_GAME") or "", "OROCHI_Z.exe")
    if not exe.is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    stock = exe.read_bytes()
    layout = pe.layout(stock)
    start, end = 0x5E378D, 0x5E3891  # layout 3's camera block, up to its return
    code = bytearray(stock[pe.file_offset(layout, start):pe.file_offset(layout, end)])
    for va, before, after in runtime_edits.PAD_CAMERA:
        assert code[va - start:va - start + len(before)] == before
        code[va - start:va - start + len(after)] = after
    setter = {0x5E3D10: "action", 0x5E3E50: "axis", 0x5E3E00: "direction", 0x5E3E20: "index"}
    bindings: dict[int, dict[str, int]] = {}
    for at in range(len(code) - 4):
        target = start + at + 5 + struct.unpack_from("<i", code, at + 1)[0]
        if code[at] != 0xE8 or target not in setter:
            continue
        if setter[target] == "action":  # push n; mov ecx, esi; call
            action = code[at - 3]
            bindings[action] = {}
        else:  # push 1 (the pad binding); push value; mov ecx, edi or esi; call
            bindings[action][setter[target]] = struct.unpack("b", code[at - 3:at - 2])[0]
    # Direction 1 keeps the negative half; axis 4 is the right stick's Y, axis 3 its X (lRx copied into lRz).
    assert bindings == {
        17: {"axis": 1, "direction": 1, "index": 4},  # Camera Up
        18: {"axis": 1, "direction": 0, "index": 4},  # Camera Down
        19: {"axis": 1, "direction": 1, "index": 3},  # Camera Left
        20: {"axis": 1, "direction": 0, "index": 3},  # Camera Right
    }



def _run(code: bytes, at: int):
    """Walk straight-line code: its instructions other than push imm and add esp, imm, each [esp+n] in them made
    relative to esp at the start; each call's target and the value it finds at [esp]; and where it jumps, with esp's
    offset there (None when it runs off the end, and the offset at the end)."""
    import capstone

    x86, depth, stack, ops, calls = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32), 0, {}, [], []
    for i in x86.disasm(code, at):
        text = f"{i.mnemonic} {i.op_str}"
        if i.mnemonic == "push" and re.fullmatch(r"(0x)?[0-9a-f]+", i.op_str):
            depth -= 4
            stack[depth] = int(i.op_str, 0)
        elif i.mnemonic == "add" and i.op_str.startswith("esp, "):
            depth += int(i.op_str[5:], 0)
        elif i.mnemonic == "jmp":
            return ops, calls, (int(i.op_str, 0), depth)
        else:
            if i.mnemonic == "call":
                calls.append((int(i.op_str, 0), stack.get(depth)))
            ops.append(re.sub(r"\[esp(?: \+ ((?:0x)?[0-9a-f]+))?\]",
                              lambda m: f"[esp{int(m[1] or '0', 0) + depth:+}]", text))
    return ops, calls, (None, depth)


@pytest.mark.parametrize("site", [0x536868, 0x582F24], ids=hex)
def test_each_draw_state_reset_runs_the_moved_code_at_its_stack_depth_and_returns_past_it(site) -> None:
    """Bug: the reset's argument left on the stack (or one too many taken off), so the moved [esp+n] reads and the
    function's own cleanup are off by 4; a moved instruction or call dropped; the reset called with another argument;
    or the return lands inside the overwritten span. 1.01 queues 0x58E4D0(0) (1.00's 0x58E370) at both places."""
    edits = {va: (before, after) for va, before, after in runtime_edits.V101_FIXES}
    stock, jump = edits[site]
    cave = site + 5 + struct.unpack_from("<i", jump, 1)[0]
    ops, calls, back = _run(edits[cave][1], cave)
    stock_ops, stock_calls, (_, stock_depth) = _run(stock, site)
    reset = f"call {runtime_edits.DRAW_STATE:#x}"
    assert calls == [*stock_calls, (runtime_edits.DRAW_STATE, 0)]
    assert [op for op in ops if op != reset] == stock_ops
    assert back == (site + len(stock), stock_depth)


def _shape(code: bytes, start: int) -> list[str]:
    """The function's instructions with what moves between versions taken out: a branch inside it as its offset from
    the start, any other address as ADDR."""
    import capstone

    def number(m):
        value = int(m[0], 16)
        return f"+{value - start:#x}" if start <= value < start + len(code) else "ADDR" if value >= 0x400000 else m[0]

    x86 = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    return [re.sub(r"0x[0-9a-f]+", number, f"{i.mnemonic} {i.op_str}") for i in x86.disasm(code, start)]


def test_the_icon_nudge_makes_both_upgrade_skills_panels_as_version_101_draws_them() -> None:
    """Bug: a nudge value other than 1.01's (x +52, first row y 173 and +7), a nudge site missed, or an edit on the
    wrong instruction. Differential: each 1.00 panel function with the nudge applied against the same function in
    the 1.01 exe (0x140 later), instruction by instruction, with moved addresses taken out."""
    pytest.importorskip("capstone")
    exe = Path(os.environ.get("WOZMOD_GAME") or "", "OROCHI_Z.exe")
    update = Path(os.environ.get("WOZMOD_V101_EXE") or "")
    if not exe.is_file() or not update.is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder and WOZMOD_V101_EXE to the 1.01 OROCHI_Z.exe")
    images = {name: (image := path.read_bytes(), pe.layout(image)) for name, path in (("1.00", exe), ("1.01", update))}

    def code(name, start, end):
        image, layout = images[name]
        return bytearray(image[pe.file_offset(layout, start):pe.file_offset(layout, end)])

    differ = {}
    for start, end in ((0x527690, 0x5279C0), (0x5279C0, 0x527F60)):
        nudged = code("1.00", start, end)
        for va, before, after in runtime_edits.V101_FIXES:
            if start <= va < end:
                assert nudged[va - start:va - start + len(before)] == before
                nudged[va - start:va - start + len(after)] = after
        ours, theirs = _shape(bytes(nudged), start), _shape(bytes(code("1.01", start + 0x140, end + 0x140)), start + 0x140)
        differ[hex(start)] = [(a, b) for a, b in zip(ours, theirs, strict=True) if a != b]
    assert differ == {"0x527690": [], "0x5279c0": []}


def test_3d_effects_start_at_full_volume_and_lose_only_the_mute_at_max_distance() -> None:
    """Bugs: the issue 11 edit drops DSBCAPS_CTRL3D with the mute (every enemy-side effect then fails its 3D interface
    and goes silent), or leaves DSBCAPS_MUTE3DATMAXDISTANCE set (a unit's hit past 2250 units stays silent on Vista
    and later); the start gain is written as anything but the float 1.0 (an integer 1 reads as a denormal: silence).
    Flag values from dsound.h: CTRL3D 0x10, MUTE3DATMAXDISTANCE 0x20000."""
    capstone = pytest.importorskip("capstone")
    disassembler = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    edits = dict((va, (stock, patched)) for va, stock, patched in runtime_edits.SOUND_3D)
    assert sorted(edits) == [0x67ACF4, 0x72D6A0]
    flags = []
    for code in edits[0x67ACF4]:
        [ins] = disassembler.disasm(code, 0x67ACF4)
        assert (ins.mnemonic, ins.op_str.rsplit(", ", 1)[0]) == ("or", "dword ptr [esp + 0x1c]")
        flags.append(int(ins.op_str.rsplit(", ", 1)[1], 16))
    assert flags == [0x10 | 0x20000, 0x10]
    assert [round(struct.unpack("<f", gain)[0], 6) for gain in edits[0x72D6A0]] == [0.52, 1.0]


def _step_x86(code: bytes, start: int, memory: dict[int, int], regs: dict[str, int]) -> None:
    """Run code from start to its ret on a byte memory, for the few instructions a cell-list build's early exits use;
    any other instruction fails the test instead of being skipped."""
    import capstone
    from capstone import x86 as cx

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    flags = {"zf": False, "cf": False}

    def read(addr: int, size: int) -> int:
        return int.from_bytes(bytes(memory.get(addr + i, 0) for i in range(size)), "little")

    def write(addr: int, size: int, value: int) -> None:
        for i, byte in enumerate((value & (1 << 8 * size) - 1).to_bytes(size, "little")):
            memory[addr + i] = byte

    def address(op) -> int:
        mem = op.mem
        index = regs[md.reg_name(mem.index)] * mem.scale if mem.index else 0
        return (regs[md.reg_name(mem.base)] if mem.base else 0) + index + mem.disp & 0xFFFFFFFF

    def get(op) -> int:
        if op.type == cx.X86_OP_REG:
            return regs[md.reg_name(op.reg)]
        if op.type == cx.X86_OP_IMM:
            return op.imm & 0xFFFFFFFF
        return read(address(op), op.size)

    def put(op, value: int) -> None:
        if op.type == cx.X86_OP_REG:
            regs[md.reg_name(op.reg)] = value & 0xFFFFFFFF
        else:
            write(address(op), op.size, value)

    eip = start
    while True:
        [ins] = md.disasm(code[eip - start:eip - start + 16], eip, count=1)
        eip = ins.address + ins.size
        ops, name = ins.operands, ins.mnemonic
        if name in ("mov", "movzx"):
            put(ops[0], get(ops[1]))
        elif name == "lea":
            put(ops[0], address(ops[1]))
        elif name in ("cmp", "sub", "add"):
            a, b = get(ops[0]), get(ops[1])
            result = a - b if name != "add" else a + b
            flags.update(zf=result & 0xFFFFFFFF == 0, cf=b > a if name != "add" else result > 0xFFFFFFFF)
            if name != "cmp":
                put(ops[0], result)
        elif name == "test":
            flags.update(zf=get(ops[0]) & get(ops[1]) == 0, cf=False)
        elif name == "push":
            regs["esp"] -= 4
            write(regs["esp"], 4, get(ops[0]))
        elif name == "pop":
            put(ops[0], read(regs["esp"], 4))
            regs["esp"] += 4
        elif name in ("je", "jne", "jbe"):
            taken = {"je": flags["zf"], "jne": not flags["zf"], "jbe": flags["zf"] or flags["cf"]}[name]
            eip = get(ops[0]) if taken else eip
        elif name == "ret":
            regs["eip"] = read(regs["esp"], 4)
            regs["esp"] += 4
            return
        else:
            raise AssertionError(f"{ins.address:#x}: {name} {ins.op_str} is outside this stepper")


LINK_LIST_BUILD = (0x4A9560, 0x4A963D)  # 0x4A9560 build_cell_link_list: ebx = the list wrapper {cell, head, built}


def test_a_path_cell_list_with_no_cell_ends_empty_like_a_cell_with_no_links() -> None:
    """Bugs: the crash at 0x4A96B9, a null cell (a point off the path grid or on an empty slot) that leaves the
    list head as stack garbage; an edit that writes only built, or the head at a wrong offset; a moved push without
    its pop (the function returns to a wrong address). Differential: the patched build on a null cell against the
    stock build on a real cell whose link count is 0, the state the search already handles as an empty list."""
    pytest.importorskip("capstone")
    exe = Path(os.environ.get("WOZMOD_GAME") or "", "OROCHI_Z.exe")
    if not exe.is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    image = exe.read_bytes()
    start, end = LINK_LIST_BUILD
    offset = pe.file_offset(pe.layout(image), start)
    stock = bytes(image[offset:offset + end - start])
    patched = bytearray(stock)
    for va, before, after in runtime_edits.PATH_CELLS:
        assert patched[va - start:va - start + len(before)] == before
        patched[va - start:va - start + len(after)] = after

    wrapper, cell, cell_data, garbage = 0x20000, 0x30000, 0x40000, 0x3F800000

    def build(code: bytes, cell_pointer: int, built: int = 0) -> tuple[int, int, bool]:
        memory: dict[int, int] = {}
        for addr, value in ((wrapper, cell_pointer), (wrapper + 4, garbage), (wrapper + 8, built),
                            (cell, cell_data), (0xFFF00, 0xDEAD0000), (0xFFF04, 0x50000)):
            memory.update(zip(range(addr, addr + 4), value.to_bytes(4, "little"), strict=True))
        memory[cell_data + 0x1D] = 0  # the cell's link count
        saved = {"ebx": wrapper, "ebp": 0x11111111, "edi": 0x22222222, "esi": 0x33333333}
        regs = {"eax": 0, "ecx": 0, "edx": 0, "esp": 0xFFF00, **saved}
        _step_x86(code, start, memory, regs)
        head, flag = (int.from_bytes(bytes(memory[wrapper + k + i] for i in range(4)), "little") for k in (4, 8))
        kept = {name: regs[name] for name in saved} == saved and regs["esp"] == 0xFFF04 and regs["eip"] == 0xDEAD0000
        return head, flag, kept

    assert build(stock, 0)[:2] == (garbage, 0)  # the stock bug, so the stepper sees what it guards
    no_links = build(stock, cell)
    assert no_links == (0, 1, True)
    assert build(bytes(patched), 0) == no_links
    assert build(bytes(patched), cell) == no_links
    assert build(bytes(patched), 0, built=1) == (garbage, 1, True)  # a list already built is left alone
