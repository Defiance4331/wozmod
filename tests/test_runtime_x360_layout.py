"""Check the runtime's layout values against the immediates of the X360 build of the same screens."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from wozmod.runtime import layout, runtime_edits

X360_BASE = 0x82000000

# Our edit VA -> the X360 instruction (or data word) that holds the same value.
EDITS = {
    0x59C728: 0x822DDCE8, 0x59C723: 0x822DDCF0, 0x59C71C: 0x822DDCFC, 0x59C717: 0x822DDD00,
    0x59C433: 0x822DD940, 0x59C4A2: 0x822DDA20,
    0x53A631: 0x82294AEC, 0x53A62C: 0x82294AE8, 0x53A625: 0x82294ADC, 0x53A663: 0x82294B24,
    0x53A65E: 0x82294B20, 0x53A676: 0x82294B30, 0x53A764: 0x82294B30, 0x53A68B: 0x82294B6C,
    0x53A73D: 0x82294C44, 0x539F94: 0x82294200, 0x53A686: 0x82294B5C,
    0x53A7AC: 0x82294CBC, 0x53A7A7: 0x82294CB8, 0x53A7A0: 0x82294CA8, 0x53A79E: 0x82294CA0,
    0x49EABF: 0x822EA288, 0x49EAFE: 0x822EA2C8, 0x49EB39: 0x822EA330}
# Edits whose X360 value is a cell width: ours is 24/20 of it.
CELL_EDITS = {0x49EAA1: 0x822EA2E8, 0x538C4E: 0x822935C0, 0x550821: 0x822A622C}

# Panel edits (X360_PANELS) -> the X360 instruction that holds the same value.
PANEL_EDITS = {
    0x59A712: 0x822DDCE8, 0x59A706: 0x822DDCFC,
    0x595AC1: 0x822D91B8, 0x595B3D: 0x822D924C, 0x595B6E: 0x822D9278, 0x595B71: 0x822D927C,
    0x595B83: 0x822D9280, 0x595BB6: 0x822D92B4, 0x556D81: 0x822A973C,
    0x556E7A: 0x822A9834, 0x55B55A: 0x822AB3B8, 0x55B55E: 0x822AB3BC,
    0x55B578: 0x822AB3D4, 0x55B5DA: 0x822AB450, 0x55B612: 0x822AB484,
    0x55B656: 0x822AB4F8, 0x56A7A1: 0x822BA428, 0x56A7B5: 0x822BA430,
    0x52959B: 0x82282064, 0x5295B0: 0x82282080, 0x5295C4: 0x82282088, 0x5295DC: 0x82282094,
    0x52D757: 0x822880C4, 0x52D76B: 0x822880D0, 0x52D77E: 0x822880E0, 0x52D792: 0x822880EC,
    0x52D7A4: 0x822880F8, 0x527705: 0x822830DC, 0x527719: 0x822830E4, 0x527722: 0x822830EC,
    0x52776D: 0x82283154, 0x5277C7: 0x822831C0, 0x527882: 0x82283298, 0x527890: 0x822832A4,
    0x5278A8: 0x822832AC, 0x55742F: 0x822A9B18, 0x557443: 0x822A9B20, 0x557456: 0x822A9B2C,
    0x557506: 0x822A9C2C, 0x557529: 0x822A9C3C, 0x55BAF0: 0x822AC104, 0x55BB04: 0x822AC10C,
    0x55BB15: 0x822AC004, 0x595ADE: 0x822D91EC, 0x595AF2: 0x822D9208, 0x595B0F: 0x822D9224,
    0x556D9F: 0x822A9768, 0x556DB7: 0x822A9784, 0x556DD5: 0x822A97A0, 0x55B57B: 0x822AB3EC,
    0x55B5AC: 0x822AB424, 0x56A859: 0x822BA4C4, 0x529666: 0x82282114,
    0x52D82A: 0x82288180, 0x527832: 0x822831FC, 0x5574F0: 0x822A9BC8,
    0x52E5CD: 0x82289EA0, 0x52F9A2: 0x8228B370,
    0x52F9BA: 0x8228B3A4, 0x52F9CE: 0x8228B3B0, 0x52FA17: 0x8228B3FC, 0x5332F8: 0x8228E38C,
    0x53343A: 0x8228E530, 0x533315: 0x8228E3B8, 0x53332F: 0x8228E3D4, 0x533349: 0x8228E3F0,
    0x533372: 0x8228E400, 0x527418: 0x82282E24, 0x52742C: 0x82282E2C, 0x527464: 0x82282E6C,
    0x528F05: 0x82284AE4, 0x528F19: 0x82284AEC, 0x528F27: 0x82284AF8, 0x536654: 0x822903F4,
    0x536675: 0x82290410, 0x53669A: 0x8229044C, 0x536694: 0x82290440, 0x5366FD: 0x82290534,
    0x53670E: 0x8229054C, 0x536791: 0x822905CC, 0x536828: 0x822906AC, 0x5368C4: 0x8229073C,
    0x5368F3: 0x8229078C, 0x536970: 0x82290874, 0x536984: 0x8229087C, 0x53699B: 0x82290888,
    0x535E6C: 0x8228FD74, 0x535E5D: 0x8228FD64, 0x535EDB: 0x8228FDB8, 0x538CB0: 0x8229362C,
    0x538CA3: 0x82293614, 0x538C9C: 0x8229360C, 0x59C3DF: 0x822DD854, 0x59C3E6: 0x822DD7D4,
    0x59C41F: 0x822DD79C, 0x59C42A: 0x822DD958, 0x59C42C: 0x822DD950, 0x56A160: 0x822B9C14,
    0x56A1A8: 0x822B9C1C}
# Panel values computed from X360 values; x reads an X360 immediate.
DERIVED = {
    0x529587: lambda x: x(0x82282094) + x(0x822D91B8),  # slide travel = resting x + panel width
    0x55BC0A: lambda x: x(0x822AC004) + x(0x822AB3BC) + 43,  # weapon panel after the list, stock 43 px gap
    0x55BBE7: lambda x: 1280 - x(0x822AC004) - x(0x822AB3BC) - 43,  # its travel from the right edge
    0x52F9A9: lambda x: x(0x8228B374) - x(0x8228B370),  # resting alpha 0x80 from the resting x
    0x54FE19: lambda x: x(0x822A5990) - 0x800,  # banner backing corner without the X360 pack bias
}
# Detour sites -> cave, and the cave values: (offset in the cave, size, value).
DETOURS = {0x55B58C: 0x5F5E00, 0x55BBA3: 0x5F5E20, 0x59C421: 0x5F5E40, 0x49EAE4: 0x5F5F00, 0x49EB1F: 0x5F5F40}
CAVES = {
    0x5F5E00: [(1, 1, lambda x: x(0x822AB404)), (5, 4, lambda x: x(0x822AB408))],
    0x5F5E20: [(1, 4, lambda x: x(0x822AC0D4)), (7, 4, lambda x: x(0x822AC09C))],
    0x5F5E40: [(1, 4, lambda x: x(0x822DD798))],
    0x5F5F00: [(6, 1, lambda x: round(x(0x822EA258) * 24 / 20))],
    0x5F5F40: [(6, 1, lambda x: round(x(0x822EA2E8) * 24 / 20))]}
# The officer card's reading-line call, which the X360 card does not make.
NOPS = {0x535EC1}

# Page site: X360 draw cell w, cell h, pitch, x, y, wrap width, and the cell w it wraps at.
PAGES = {
    "STILL_DRAW": (0x822DDD3C, 0x822DDD40, 0x822DDD44, 0x822DDD94, 0x822DDD90, 0x822DDD7C, 0x822DDD3C),
    "STILL_Z_DRAW": (0x822DDD3C, 0x822DDD40, 0x822DDD44, 0x822DDD94, 0x822DDD90, 0x822DDD7C, 0x822DDD3C)}
# Site: the X360 wrap width and the cell w it wraps at; the box is that width unless our art moves it.
WRAPS = {
    0x595F93: (0x822D974C, 0x822D972C, True), 0x5975C6: (0x822DB240, 0x822DB224, False),
    0x593B98: (0x822DB318, 0x822DB224, True), 0x595679: (0x822D8D30, 0x822D8D20, True),
    0x5956FA: (0x822D8D30, 0x822D8D20, True), 0x53C225: (0x82296084, 0x82296070, True),
    0x53C2A5: (0x8229611C, 0x82296108, True), 0x526BE8: (0x822824E0, 0x822824B8, False),
    0x539F9F: (0x822941E4, 0x822941D4, True), 0x598125: (0x822DBA6C, 0x822DBA3C, True),
    0x59818B: (0x822DBB0C, 0x822DBAC8, True),
    0x49DACE: (0x822EA300, 0x822EA2E8, True), 0x49DB4C: (0x822EA300, 0x822EA2E8, True)}


@pytest.fixture(scope="module")
def x360() -> bytes:
    old_repo = os.environ.get("WOZMOD_OLD_REPO")
    program = Path(old_repo or "", "work/donors/wo2-x360/default.pe")
    if not old_repo or not program.is_file():
        pytest.skip("set WOZMOD_OLD_REPO to a checkout holding the decrypted X360 program")
    return program.read_bytes()


def immediate(program: bytes, va: int) -> int:
    """Read li/addi, subfic or cmplwi immediates, or a data word."""
    word = struct.unpack_from(">I", program, va - X360_BASE)[0]
    opcode, value = word >> 26, word & 0xFFFF
    if opcode in (7, 8, 11, 14):
        return value - 0x10000 * (value >= 0x8000)
    return value if opcode == 10 else word


def ours(edit: tuple) -> int:
    """The signed immediate or displacement that ends the patched instruction."""
    va, stock, patched = edit
    if patched[0] in (0x6A, 0xB0) or patched[:2] == b"\xc6\x05" or (patched[0] in (0x83, 0x8D) and len(patched) <= 4):
        return struct.unpack("<b", patched[-1:])[0]
    return struct.unpack("<i", patched[-4:])[0]


def test_layout_values_do_not_drift_from_the_x360_screens(x360: bytes) -> None:
    panels = {edit[0]: edit for edit in runtime_edits.X360_LAYOUT + runtime_edits.X360_PANELS}
    edits = {va: ours(edit) for va, edit in panels.items()}
    rows = [(f"edit {va:#x}", edits[va], immediate(x360, at)) for va, at in EDITS.items()]
    rows += [(f"edit {va:#x}", edits[va], round(immediate(x360, at) * 24 / 20)) for va, at in CELL_EDITS.items()]
    pages, boxes, wraps = layout.pages(), layout.sites(), layout.reflow_rules()
    for name, refs in PAGES.items():
        w, h, pitch, x, y, width, cell = (immediate(x360, at) for at in refs)
        ours_w = round(w * 24 / 20)
        expected = (ours_w, h, pitch, x, y, round(width * 20 / cell * ours_w / 24), round(width * w / cell))
        rows.append((name, pages[layout.DEFINES[name]], expected))
    for site, (width_at, cell_at, boxed) in WRAPS.items():
        width, cell = immediate(x360, width_at), immediate(x360, cell_at)
        rows.append((f"wrap {site}", wraps.get(site, boxes[site][0]), round(width * 20 / cell)))
        if boxed:
            rows.append((f"box {site}", boxes[site][0], width))
    x = lambda at: immediate(x360, at)
    rows += [(f"panel {va:#x}", ours(panels[va]), x(at)) for va, at in PANEL_EDITS.items()]
    rows += [(f"panel {va:#x}", ours(panels[va]), value(x)) for va, value in DERIVED.items()]
    for site, cave in DETOURS.items():
        patched = panels[site][2]
        rows.append((f"detour {site:#x}", (patched[0], site + 5 + struct.unpack_from("<i", patched, 1)[0]), (0xE9, cave)))
        for at, size, value in CAVES[cave]:
            code = panels[cave][2][at:at + size]
            rows.append((f"cave {cave:#x}+{at}", int.from_bytes(code, "little", signed=True), value(x)))
    rows += [(f"nop {va:#x}", panels[va][2], bytes.fromhex("0f1f440000")) for va in NOPS]
    assert [row for row in rows if row[1] != row[2]] == []
    assert set(panels) == set(EDITS) | set(CELL_EDITS) | set(PANEL_EDITS) | set(DERIVED) | set(DETOURS) | set(CAVES) | NOPS
