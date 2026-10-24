from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import pytest

from wozmod.text import program_join
from wozmod.movies import movie_rows
from wozmod.text.build import PART_TABLES as BUILD_PARTS
from wozmod.text.program_join import DONORS, X360_BASE, program_join as join, stock_tables

TABLE = Path(__file__).resolve().parents[1] / "data/text/table.json"
PART_TABLES = {table for table, *_ in BUILD_PARTS}

# Rows the join disputes that the owner reviewed and kept as shipped (text ledger rows 19 and 20, from the cards in
# work/review/wording-*.json). Stock string hash prefix: why the shipped words stay.
REVIEWED = {
    "8b6bf8eab0ba": "officer attacks 16, 28 and 81 share one stock line: only the general official line fits all three",
    "9cb1c195470d": "music 16: WO1's title adds a battle the Japanese title does not name",
    "2ea135ca4579": "music 31: MOZ lists it beside its second version (53), as WO2's numbered title does",
    "c03cdc011541": "music 32: MOZ lists it beside its second version (54), as WO2's numbered title does",
    "6b9656787713": "element effect 12: the same words; WO1's string only adds a trailing space",
    "21546233b709": "army 50: the bare name, as WO2 X360 and the PSP give it; WO1's Troop suffix is not adopted",
    "8de3ceb368b4": "army 76: bare like the other officer-named units; WO1's Troop suffix is not adopted",
    "7a2183c3e54a": "army 85: the Japanese has the army word, which the PSP name keeps and WO2's drops",
    # Text ledger row 5: Z replaced Cygnus with this officer, so the donors' slot 887 names Cygnus.
    "5d649510f287": "name parts 563 and 887: one officer's name in MOZ; the donors' slot 887 is the officer Z replaced",
}


def test_immediates_are_read_from_each_instruction_form_that_sets_a_message_id():
    # Bug: the decoder reads a lea's displacement from the wrong byte when an index register (SIB byte) is present
    # or the displacement is one byte, takes an unsigned immediate as signed, or reads a PSP word at its address
    # instead of its file offset, so an anchor joins the wrong message.
    code = bytes.fromhex("6880020000" "6a12" "05ee000000" "81c716010000" "83c039" "8d8f2f030000" "8d842d7a010000"
                         "8d6e6a")
    exe = bytearray(0x200)
    struct.pack_into("<I", exe, 0x3C, 0x40)
    exe[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", exe, 0x44, 0x14C, 1, 0, 0, 0, 0xE0, 0)
    struct.pack_into("<I", exe, 0x58 + 28, 0x400000)
    struct.pack_into("<8sIIII", exe, 0x58 + 0xE0, b".text", 0x100, 0x1000, 0x100, 0x100)
    exe[0x100:0x100 + len(code)] = code
    layout = program_join.pe.layout(bytes(exe))
    starts = [0, 5, 7, 12, 18, 21, 27, 34]
    assert [program_join._moz_immediate(bytes(exe), layout, 0x401000 + at) for at in starts] == [
        0x280, 0x12, 0xEE, 0x116, 0x39, 0x32F, 0x17A, 0x6A]
    program = bytearray(16)
    struct.pack_into(">II", program, 0, 0x386002E4, 0x386BFFBC)  # li r3, 0x2e4; addi r3, r11, -0x44
    x360 = program_join.Program("wo2-x360", bytes(program))
    assert [x360.immediate(X360_BASE + at) for at in (0, 4)] == [0x2E4, -0x44]
    prx = bytearray(0x68)
    struct.pack_into("<I", prx, 0x1C, 0x34)  # e_phoff
    struct.pack_into("<III", prx, 0x34, 1, 0x60, 0)  # PT_LOAD at file offset 0x60, address 0
    struct.pack_into("<II", prx, 0x60, 0x2442013D, 0x2463FFFC)  # addiu v0, v0, 0x13d; addiu v1, v1, -4
    assert [program_join.Program("wo2-psp", bytes(prx)).immediate(at) for at in (0, 4)] == [0x13D, -4]


@pytest.mark.skipif(not (os.environ.get("WOZMOD_GAME") and os.environ.get("WOZMOD_DONORS")),
                    reason="set WOZMOD_GAME and WOZMOD_DONORS")
def test_every_program_string_row_takes_the_donor_string_its_twin_code_reads():
    # Bug: a pe row points at a neighbour of its string (a shifted id, the old position pairing), at a later donor
    # than the text order allows, or at PSP text for a string an Xbox 360 program has; or a name part override
    # names another officer; or one row serves slots the donors name apart (two weapons that share a Japanese
    # name), where a per-index part override must give each slot its own words. Rows the join cannot reach are not
    # judged.
    from wozmod.text.resolver import DonorResolver
    from wozmod.text.stock import stock_key

    resolver = DonorResolver(Path(os.environ["WOZMOD_DONORS"]))
    exe = (Path(os.environ["WOZMOD_GAME"]) / "OROCHI_Z.exe").read_bytes()
    joined = join(exe, {donor: resolver.donors.program(donor) for donor in DONORS}, movie_rows(),
                  program_join.stages(Path(os.environ["WOZMOD_GAME"]), resolver))
    data = json.loads(TABLE.read_text(encoding="utf-8"))
    overrides = {(row["table"], row["index"]): row for row in data["parts"]}
    slots: dict[str, list[tuple[str, int]]] = {}
    for table, strings in stock_tables(exe).items():
        for index, value in enumerate(strings):
            if (keyed := stock_key(value)) and (table, index) not in overrides:
                slots.setdefault(keyed[0], []).append((table, index))
    items = [(row["key"], row, slots.get(row["key"], [])) for row in data["rows"]]
    # Part keys stay short: disputes are keyed by their first 12 characters.
    items += [(f"{table}#{index}", row, [(table, index)]) for (table, index), row in overrides.items()]

    def english(ref: list) -> str | None:  # a few joined slots hold filler that is not donor English
        try:
            return resolver.resolve(ref)
        except ValueError:
            return None

    disputes = {}
    for key, row, where in items:
        found = {donor: {text for slot in where for ref in joined.get(slot, {}).get(donor, [])
                         if (text := english(ref)) is not None} for donor in DONORS}
        first = next((donor for donor in DONORS if found[donor]), None)
        if first is None or "ref" not in row or row["source"] == "authored":
            continue
        donor, shown = row["ref"][0], resolver.resolve(row["ref"])
        label = f"{key[:12]} {where[:3]}"
        if donor not in DONORS or DONORS.index(donor) > DONORS.index(first):
            disputes[key[:12]] = f"{label}: {donor} although {first} has it"
        elif found[donor] and shown not in found[donor]:
            disputes[key[:12]] = f"{label}: not the string the join gives"
        # A part slot (program_join.PART_TABLES) takes the words every donor that joins it agrees on: an override
        # its own, and a row the words its slots share, else each slot needs its own override.
        agreed = {}
        for slot in where:
            if slot[0] in PART_TABLES:
                for donor in DONORS:
                    if words := {english(ref) for ref in joined.get(slot, {}).get(donor, [])} - {None}:
                        agreed[slot] = agreed.get(slot, words) & words
        if any(agreed.values()) and shown not in set.intersection(*agreed.values()):
            disputes.setdefault(key[:12], f"{label}: its slots' donors agree on other words, or name them apart")
    assert sorted(value for key, value in disputes.items() if key not in REVIEWED) == []
    assert sorted(REVIEWED.keys() - disputes.keys()) == []  # a stale entry would hide the next dispute
