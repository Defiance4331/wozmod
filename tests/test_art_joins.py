"""Menu art paired with donor art through the identity both programs' code uses, recomputed from the files."""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import pytest

from wozmod.art.build import MAPPING, expanded
from wozmod.art.stage_titles import title_members, title_sources
from wozmod.formats import pe
from wozmod.formats.linkdata import archive, wo1_entries
from wozmod.runtime.prompts import XBOX_PROMPTS

STOCK = Path(os.environ.get("WOZMOD_GAME", ""))
DONORS = Path(os.environ.get("WOZMOD_DONORS", ""))
X360_BASE = 0x82000000


def stock(name: str) -> Path:
    if not os.environ.get("WOZMOD_GAME") or not (STOCK / name).is_file():
        pytest.skip("set WOZMOD_GAME to the stock game")
    return STOCK / name


def program(disc: str) -> bytes:
    path = DONORS / disc / "default.pe"
    if not os.environ.get("WOZMOD_DONORS") or not path.is_file():
        pytest.skip("set WOZMOD_DONORS to a folder holding the decrypted X360 programs")
    return path.read_bytes()


def test_each_xbox_prompt_takes_the_wo2_item_its_slot_draws_in_both_programs() -> None:
    # Bug: a prompt slot is paired with a neighbouring ENS 13 item, so a menu shows the wrong button. Both programs
    # load the 4079 icons through one 158-entry slot table (MOZ 0x718B60, WO2 X360 0x82047CB8): equal tables make
    # ENS 13 item k the Xbox art for slot k. MOZ repainted slots 11 and 12 (the X360 D-pad halves) as its attack
    # keycaps, which take the face buttons of items 2 and 3.
    exe = stock("OROCHI_Z.exe").read_bytes()
    moz = struct.unpack_from("<158I", exe, pe.file_offset(pe.layout(exe), 0x718B60))
    x360 = struct.unpack_from(">158I", program("wo2-x360"), 0x82047CB8 - X360_BASE)
    assert moz == x360
    repainted = {11: 2, 12: 3}
    assert {slot: item for slot, item in XBOX_PROMPTS.items() if repainted.get(slot, slot) != item} == {}


# Entries MOZ loads through the same handle table as a WO2 X360 entry (MOZ table, WO2 ENS, WO2 table, length): the
# 4079 icons (accessors MOZ 0x58C960 / WO2 0x822D1DE0) and the 4081 results sheet (MOZ 0x58F390 / WO2 0x822D3A60).
SHARED_TABLES = {4079: (0x718B60, 13, 0x82047CB8, 158), 4081: (0x719310, 15, 0x820481F8, 38)}


def served_pointers(donor: str) -> dict[tuple[int, int], set]:
    """Every (entry, item) the art data serves and the donor [ens, member] pointers its recipe names."""
    served: dict[tuple[int, int], set] = {}
    for edit in json.loads(MAPPING.read_text(encoding="utf-8"))["textures"]:
        for entry, step in expanded(edit):
            pieces = [step, *sum(step.get("rows", []), []), *step.get("draw", [])]
            served.setdefault((entry, step.get("item")), set()).update(
                tuple(piece[donor][:2]) for piece in pieces if donor in piece)
    return served


def test_each_wo2_texture_of_a_shared_handle_table_is_the_same_item() -> None:
    # Bug: a WO2 pointer one item off on a sheet both programs index through one table, so a menu shows the
    # neighbouring plate or icon. Equal tables make WO2 item k the art MOZ draws as item k.
    exe, wo2 = stock("OROCHI_Z.exe").read_bytes(), program("wo2-x360")
    served, wrong = served_pointers("wo2-x360"), []
    for entry, (moz_va, ens, wo2_va, count) in SHARED_TABLES.items():
        moz = struct.unpack_from(f"<{count}I", exe, pe.file_offset(pe.layout(exe), moz_va))
        assert moz == struct.unpack_from(f">{count}I", wo2, wo2_va - X360_BASE), entry
        for (at, item), pointers in sorted(served.items()):
            if at == entry and item in moz:
                wrong += [f"{entry}.{item}: serves WO2 {ens}.{member}" for e, member in pointers
                          if e == ens and member != item]
    assert wrong == []


def moz_title(stage: int) -> int:
    """The title index MOZ's stage-name loaders read for a stage (0x58EEA0, entry 4076 at index * 0x8800)."""
    return 124 if 129 <= stage <= 131 else stage - 7 if stage >= 132 else stage - 4 if stage >= 68 else stage


def stage_records(data: bytes) -> list[bytes]:
    """A stage table (MOZ LINKDATA1 4075, WO1 X360 ENS 13): a u32 count, then 38-byte records."""
    return [data[4 + 38 * k:42 + 38 * k] for k in range((len(data) - 4) // 38)]


# The record bytes MOZ kept from WO1 for the stages it took over; the others it renumbered (byte 0, the stage-art id,
# by a constant 85).
KEPT = (5, 9, 10, *range(12, 26), 34, 35, 36, 37)


def test_each_wo1_stage_title_comes_from_the_wo1_stage_with_the_same_stage_data() -> None:
    # Bug: a title takes the WO1 picture of a neighbouring stage (the old rule took the name's row in the stage-name
    # font's atlas, an order nothing in the code reads), so the battle intro names the wrong battle. The join reads
    # the WO1 program's stage-name table (wozmod.art.stage_titles); this checks it against the stage records: the MOZ
    # stage a title belongs to has the stage data of a WO1 stage whose record byte 0 is the member the title takes.
    folder = DONORS / "wo1-x360"
    names = json.loads(MAPPING.read_text(encoding="utf-8"))["stage_names"]["names"]
    members = title_members(names, program("wo1-x360"))
    wo1 = stage_records(wo1_entries(folder / "linkdata_ens.lnk", folder / "linkdata_ens.idx", [13])[13])
    moz = stage_records(archive(stock("LINKDATA1.BIN").with_suffix(""))[4075])
    assert (len(moz), len(wo1)) == (144, 60)
    kept = lambda record: bytes(record[i] for i in KEPT)
    wrong, checked = [], 0
    for title, member in enumerate(members):
        stages = [s for s in range(len(moz)) if moz_title(s) == title]
        if member is None or title < 68:  # 0-67: WO2 stages; a WO1 name there is the same battle's name, not its data
            continue
        checked += 1
        if not any(kept(moz[s]) == kept(r) for s in stages for r in wo1 if r[0] == member):
            wrong.append(f"title {title}: member {member}")
    assert (checked, wrong) == (57, [])


# WO1 X360 art drawn by the same call in both programs. Each program fetches an entry's textures through raw(item) or
# handle(h) = raw(table[h]): MOZ 4079 raw 0x58C940, handle 0x58C960 / 0x58E180, table 0x718B60 (158); MOZ 4080 raw
# 0x58E870, handle 0x58E890 / 0x58EF50, table 0x718F88 (23); WO1 ENS 16 raw 0x82287BB8, handle 0x82287BD0, table
# 0x8203B078 (113); WO1 ENS 17 raw 0x82288530, handle 0x822885D0, table 0x8203B270 (21). The call sites below pair
# by code-address order within the shared screen code (the two programs keep their source files in the same order),
# and 4079.219/220 by the same quad position and size. Each row: MOZ entry, how the call reaches its item ("raw",
# "handle", "base" for a loop index plus a constant), the VA of the instruction holding the constant; the same for
# WO1; then the MOZ items the call draws. A base call draws its constant plus a per-screen index whose range the
# code takes from game data (attributes, list rows): the items listed are those the art data serves.
MOZ_TABLES = {4079: (0x718B60, 158), 4080: (0x718F88, 23)}
WO1_TABLES = {16: (0x8203B078, 113), 17: (0x8203B270, 21)}
SAME_DRAW = [
    (4079, "handle", 0x55407E, 16, "handle", 0x8226AC00, [20]),
    (4079, "base", 0x54E644, 16, "base", 0x82266F7C, [20, 21]),
    (4079, "raw", 0x594D06, 16, "raw", 0x8228C948, [35]),
    (4079, "raw", 0x566AFC, 16, "raw", 0x82271FBC, [52]),
    (4079, "raw", 0x52C2EE, 16, "raw", 0x8224F664, [53]),
    (4079, "base", 0x5958C3, 16, "base", 0x8228D458, list(range(60, 78))),
    (4079, "raw", 0x595413, 16, "raw", 0x8228D03C, [80]),
    (4079, "base", 0x52CB15, 16, "base", 0x8224FE0C, list(range(83, 97))),
    (4079, "raw", 0x591EC4, 16, "raw", 0x8228AD90, [151]),
    (4079, "base", 0x591D94, 16, "base", 0x8228AC50, list(range(154, 158))),
    (4079, "base", 0x591DD6, 16, "base", 0x8228ACE4, list(range(159, 163))),
    (4079, "base", 0x591E54, 16, "base", 0x8228AD2C, list(range(164, 168))),
    (4079, "base", 0x59026C, 16, "base", 0x82288E34, list(range(172, 177))),
    (4079, "raw", 0x5669D7, 16, "raw", 0x82271F6C, [181]),
    (4079, "handle", 0x53C1EC, 17, "handle", 0x8225B8E8, [219]),
    (4079, "raw", 0x53C26C, 17, "handle", 0x8225B990, [220]),
    (4080, "handle", 0x535F12, 17, "handle", 0x82255F7C, [5]),
]


def moz_constant(exe: bytes, va: int) -> int:
    """The constant of a push imm, add reg, imm or lea reg, [reg + disp] at va."""
    import capstone

    x86 = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    x86.detail = True
    at = pe.file_offset(pe.layout(exe), va)
    instruction = next(x86.disasm(exe[at:at + 15], va))
    operand = instruction.operands[-1]
    return operand.imm if operand.type == capstone.x86.X86_OP_IMM else operand.mem.disp


def wo1_constant(program: bytes, va: int) -> int:
    """The signed immediate of an li or addi at va."""
    word = struct.unpack_from(">I", program, va - X360_BASE)[0]
    assert word >> 26 == 14, f"{va:#x} is not li/addi"
    return (word & 0xFFFF) - 0x10000 * (word & 0x8000 != 0)


def test_each_wo1_menu_texture_comes_from_the_wo1_member_the_same_draw_call_fetches() -> None:
    # Bug: a WO1 pointer one member off (the piecewise shifts -1, -3, -4 rested on one visual audit), so a menu shows
    # a neighbouring icon or label. Reads the constant each paired call passes in both programs.
    exe, wo1 = stock("OROCHI_Z.exe").read_bytes(), program("wo1-x360")
    moz_table = {e: struct.unpack_from(f"<{n}I", exe, pe.file_offset(pe.layout(exe), va))
                 for e, (va, n) in MOZ_TABLES.items()}
    wo1_table = {e: struct.unpack_from(f">{n}I", wo1, va - X360_BASE) for e, (va, n) in WO1_TABLES.items()}
    served, wrong = served_pointers("wo1-x360"), []
    for entry, how, moz_va, ens, wo1_how, wo1_va, items in SAME_DRAW:
        base = moz_constant(exe, moz_va)
        base = moz_table[entry][base] if how == "handle" else base
        member = wo1_constant(wo1, wo1_va)
        member = wo1_table[ens][member] if wo1_how == "handle" else member
        for item in items:
            want = {(ens, member + item - base)}
            if served.get((entry, item)) != want:
                wrong.append(f"{entry}.{item}: serves {sorted(served.get((entry, item), ()))}, the draw call fetches {want}")
    assert wrong == []


# The record bytes WO2 X360 ENS 10 and MOZ 4075 share for WO2's stages (the rest are byte-swapped words).
WO2_KEPT = (0, 2, 3, 7, *range(9, 26), 34, 35, 36, 37)


def test_each_wo2_stage_title_comes_from_the_wo2_stage_with_the_same_stage_data() -> None:
    # Bug: a WO2 stage's title takes the picture of another WO2 stage (ENS 11 is one title per WO2 stage id), so the
    # battle intro names the wrong battle. The MOZ stage a title belongs to must have the stage data of the WO2 stage
    # whose ENS 11 member it takes.
    folder = DONORS / "wo2-x360"
    names = json.loads(MAPPING.read_text(encoding="utf-8"))["stage_names"]["names"]
    sources = title_sources(names, program("wo1-x360"))
    wo2 = stage_records(wo1_entries(folder / "linkdata_ens.lnk", folder / "linkdata_ens.idx", [10])[10])
    moz = stage_records(archive(stock("LINKDATA1.BIN").with_suffix(""))[4075])
    kept = lambda record: bytes(record[i] for i in WO2_KEPT)
    taken = [(title, source["wo2-x360"]) for title, source in enumerate(sources) if "wo2-x360" in source]
    wrong = [f"title {title}: member {member}" for title, member in taken
             if not any(kept(moz[s]) == kept(wo2[member]) for s in range(len(moz)) if moz_title(s) == title)]
    assert (len(taken), wrong) == (68, [])
