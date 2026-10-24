"""Behavioral checks for fixed runtime edit assembly."""

from __future__ import annotations

import os
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


@pytest.mark.parametrize(("rows", "distance", "moving_fix", "count", "value_stop", "default"),
                         [(1, False, False, 7, 4, 6), (2, False, False, 8, 5, 7), (1, True, False, 8, 5, 7),
                          (1, False, True, 8, 5, 7), (2, True, True, 10, 7, 9)])
def test_settings_rows_does_not_leave_default_at_the_old_index(
    rows: int, distance: bool, moving_fix: bool, count: int, value_stop: int, default: int,
) -> None:
    # Bug: a release build keeps a cursor slot for a compiled-out row (render distance, moving camera fix): an empty row.
    edits = runtime_edits.settings_rows(rows, distance, moving_fix)
    assert [(va, len(stock), len(patched)) for va, stock, patched in edits] == [
        (0x56B2ED, 17, 17), (0x56B60B, 3, 3), (0x56B750, 3, 3),
        (0x56B7E4, 3, 3), (0x56B806, 3, 3), (0x56B86A, 19, 19), (0x56B8AB, 19, 19),
    ]
    assert [edits[index][2][position] for index, position in [(0, 1), (1, 2), (2, 2), (3, 2), (4, 2)]] == [
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
