"""The weapon texture table the DLL owns in place of the stock 36 slots (runtime/weapon_slots.h, issue 18)."""

from __future__ import annotations

import os
import re
import struct
from pathlib import Path

import pytest

from wozmod.runtime import layout

STOCK, STOCK_COUNT, SLOT = 0x8C07A0, 36, 20
STOCK_END = STOCK + STOCK_COUNT * SLOT
TABLE = 0x10000000  # any address the DLL's table could have
MODEL_TABLE, MODEL_RECORDS, MODEL_RECORD = 0x793930, 580, 20  # the weapon model table 0x4BDA80 indexes


@pytest.fixture(scope="module")
def slots(c_library):
    lib = c_library("weapon_slots", r'''
#include "weapon_slots.h"
unsigned int count(void) { return WEAPON_SLOT_COUNT; }
unsigned int moved(unsigned int stock, unsigned int table) { return weapon_slot_moved(stock, table); }
''')
    return lib


@pytest.fixture(scope="module")
def stock():
    exe = Path(os.environ.get("WOZMOD_GAME") or "", "OROCHI_Z.exe")
    if not exe.is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    from wozmod.formats import pe
    image = bytearray(exe.read_bytes())
    return image, pe.layout(bytes(image))


def _numbers(op_str: str) -> list[int]:
    return [int(n, 16) for n in re.findall(r"0x[0-9a-f]+", op_str)]


def test_every_scan_of_the_weapon_texture_table_covers_the_dlls_whole_table(slots, stock):
    # Bug: a site left out of weapon_slot_sites, or an end moved like a base: that scan still stops at the 36th slot,
    # or runs into the stock table, so a battle that needs a 37th texture still crashes at 0x4B86DF or loses a slot.
    capstone = pytest.importorskip("capstone")
    from wozmod.formats import pe
    image, pe_layout = stock
    for va, value in layout.c_array("weapon_slot_sites"):
        at = pe.file_offset(pe_layout, va)
        assert struct.unpack_from("<I", image, at)[0] == value, f"{va:#x} does not hold {value:#x} in stock"
        struct.pack_into("<I", image, at, slots.moved(value, TABLE))
    text = next(s for s in pe_layout["sections"] if s["name"] == b".text")
    x86 = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    x86.skipdata = True
    stale, scans, start = [], [], None
    end = TABLE + slots.count() * SLOT
    for address, _, mnemonic, op_str in x86.disasm_lite(bytes(image[text["raw"]:text["raw"] + text["raw_size"]]),
                                                        pe_layout["image_base"] + text["rva"]):
        numbers = _numbers(op_str)
        if any(STOCK <= n < STOCK_END for n in numbers) or mnemonic == "cmp" and {STOCK_END, STOCK_END + 0x10} & {
                *numbers}:
            stale.append(f"{address:#x} {mnemonic} {op_str}")
        if mnemonic == "mov" and any(TABLE <= n < TABLE + SLOT for n in numbers):
            start = next(n for n in numbers if TABLE <= n < TABLE + SLOT)
        if mnemonic == "cmp" and any(TABLE <= n <= end + SLOT for n in numbers):
            bound = next(n for n in numbers if TABLE <= n <= end + SLOT)
            scans.append((hex(address), bound - start if start is not None else None))
    assert stale == []
    assert len(scans) == 9  # two scans in the lookup and in each of its two copies, three reset loops
    assert [(at, span) for at, span in scans if span != slots.count() * SLOT] == []


def test_the_table_holds_every_texture_the_weapon_model_table_names(slots, stock):
    # Bug: the table sized to the demand seen (37) or a round number below the model table's: a battle whose officers
    # name more distinct weapon textures than that still runs out. Claimed slots hold distinct keys, and each key is
    # field +0 of a model record, so the model table's distinct keys are the most a battle can claim.
    from wozmod.formats import pe
    image, pe_layout = stock
    at = pe.file_offset(pe_layout, MODEL_TABLE)
    keys = {struct.unpack_from("<I", image, at + MODEL_RECORD * i)[0] for i in range(MODEL_RECORDS)} - {0xFFFF}
    assert slots.count() == len(keys)
