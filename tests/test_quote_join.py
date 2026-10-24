"""The battle-quote joins, checked against the stock and donor files: stock quote slot k with donor slot k
(script_alignment.QUOTE_SLOTS) on WO2 X360, WO1 X360 and WO2 PSP, and the select voices of officers 92 and 93."""

import json
import os
import re
import struct
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not (os.environ.get("WOZMOD_GAME") and os.environ.get("WOZMOD_DONORS")),
                                reason="set WOZMOD_GAME and WOZMOD_DONORS")

X360_BASE = 0x82000000


def _suffix(line: bytes) -> int | None:
    return int(found[1]) if (found := re.search(rb"@([0-9]+)$", line)) else None


def _donor_table(t: int, insert: tuple[int, int]) -> int | None:
    """The donor table of stock table t: format tables 0 and 1 as they are, officer tables by officer id."""
    first, count = insert
    officer = t - 2
    if t < 2 or officer < first:
        return t
    return None if officer < first + count else t - count


@pytest.fixture(scope="module")
def files():
    from wozmod.donors import DonorsFolder
    from wozmod.formats.linkdata import archive
    from wozmod.text.script_alignment import QUOTES, moz_scripts, wo2_x360

    game, folders = Path(os.environ["WOZMOD_GAME"]), DonorsFolder(Path(os.environ["WOZMOD_DONORS"]))
    wo1, wo2 = folders.folder("wo1-x360"), folders.folder("wo2-x360")
    if not (wo1 / "linkdata_ens.idx").is_file() or not (wo2 / "linkdata_ens.idx").is_file():
        pytest.skip("the WO1 and WO2 X360 donors are not extracted")
    wo1_ens, wo2_donor = archive(wo1 / "linkdata_ens"), wo2_x360(wo2 / "linkdata_ens")
    entry, wo1_entry, wo2_entry = QUOTES
    return {"game": game, "folders": folders, "moz": moz_scripts(game / "LINKDATA2"), "wo1_dir": wo1,
            "wo2_donor": wo2_donor, "stock": archive(game / "LINKDATA1")[entry], "wo1": wo1_ens[wo1_entry],
            "wo2": wo2_donor.members([wo2_entry])[wo2_entry]}


@pytest.fixture(scope="module")
def maps(files):
    """The installer's two X360 voice maps, built as voices.build builds them."""
    from wozmod.text.script_alignment import WO1_QUOTE_INSERT, paired_quote_tables, wo1_scripts
    from wozmod.voices.build import table_rows
    from wozmod.voices.maps import dialogue_voice_maps, shown_lines

    return dialogue_voice_maps(
        files["moz"], wo1_scripts(files["wo1_dir"] / "linkdata_ens"), files["wo2_donor"].scripts(),
        paired_quote_tables(files["stock"], files["wo1"], ">", insert=WO1_QUOTE_INSERT),
        paired_quote_tables(files["stock"], files["wo2"], files["wo2_donor"].order),
        shown_lines(files["moz"], table_rows()))


def test_quote_voices_and_rows_follow_the_slot_the_quote_pickers_read(files, maps) -> None:
    # Bugs: a quote slot paired one off, or a table paired with another officer's, plays and shows another line; a
    # row points at a donor line other than the one at its slot. Oracle: each game reads slot k of the officer's
    # table for the same event (script_alignment.QUOTE_SLOTS), so the stock line at slot k plays the donor clip at
    # slot k (a WO1 clip where its English is the X360 line's) and its row shows that donor line's words. A row keyed
    # by a string several slots show passes when it shows one of their lines.
    from wozmod.formats.audio import WO1_RECORD_BASE, WO2_CLIP_BASE, quote_voice
    from wozmod.formats.scripts import quote_tables, quote_text
    from wozmod.text.script_alignment import QUOTE_INSERT, QUOTE_SLOTS, WO1_QUOTE_INSERT
    from wozmod.text.stock import stock_key
    from wozmod.voices.build import TEXT_TABLE

    stock = quote_tables(files["stock"])
    donors = {"wo2-x360": (quote_tables(files["wo2"], ">"), QUOTE_INSERT, WO2_CLIP_BASE, 17),
              "wo1-x360": (quote_tables(files["wo1"], ">"), WO1_QUOTE_INSERT, WO1_RECORD_BASE, 21)}
    wo1_map, wo2_map = maps

    def joined(donor: str, t: int, k: int) -> bytes:
        tables, insert, _, _ = donors[donor]
        at = _donor_table(t, insert)
        return tables[at][k] if at is not None and at < len(tables) and k < len(tables[at]) else b""

    failing, shown, voiced = [], {}, 0
    rows = {row["key"]: row for row in json.loads(TEXT_TABLE.read_text(encoding="utf-8"))["rows"]}
    for t, table in enumerate(stock):
        for k, line in enumerate(table):
            if not line or (t >= 2 and k not in QUOTE_SLOTS):
                continue
            key = next((found[0] for raw in (line[2:], quote_text(line)) if (found := stock_key(raw))
                        and found[0] in rows), None)  # as the runtime looks it up (voiced_line, then the text)
            if key:
                shown.setdefault(key, []).append((t, k))
            if (suffix := _suffix(line)) is None:
                continue
            voice, x360, wo1 = quote_voice(suffix), joined("wo2-x360", t, k), joined("wo1-x360", t, k)
            if (clip := _suffix(x360)) is not None:
                voiced += 1
                if wo2_map.get(voice) != WO2_CLIP_BASE + clip - 1:
                    failing.append(f"quote {t}.{k} voice {voice:#x}: X360 {wo2_map.get(voice)}, "
                                   f"slot {WO2_CLIP_BASE + clip - 1}")
            if (clip := _suffix(wo1)) is not None and x360 and quote_text(wo1) == quote_text(x360):
                if wo1_map.get(voice) != WO1_RECORD_BASE + clip - 1:
                    failing.append(f"quote {t}.{k} voice {voice:#x}: WO1 {wo1_map.get(voice)}, "
                                   f"slot {WO1_RECORD_BASE + clip - 1}")
    checked = 0
    for key, slots in shown.items():
        ref = rows[key].get("ref")
        if not ref or ref[0] not in donors or ref[1:4] != ["ens", donors[ref[0]][3], "quotes"]:
            continue
        words = quote_text(donors[ref[0]][0][ref[4]][ref[5]])
        answers = {quote_text(line) for t, k in slots if (line := joined(ref[0], t, k))}
        checked += 1
        if answers and words not in answers:
            failing.append(f"row {key[:12]} at {', '.join(f'quote {t}.{k}' for t, k in slots)}: {ref}")
    assert voiced > 4000 and checked > 4000, (voiced, checked)
    assert not failing, "\n".join(failing)


def test_psp_quote_voices_and_rows_follow_the_slot_both_pickers_read(files) -> None:
    # Bugs: a PSP table paired with another officer's (an insert, as X360 needs), or a slot paired one off, plays and
    # shows another line. Oracle: MOZ and the PSP read slot k of officer table t for the same event, with no table
    # inserted (script_alignment.PSP_QUOTE_INSERT), so the stock line at slot k plays the clip of PSP slot k's @NNNN
    # and a PSP row shows PSP slot k's words (any one of them, for a string several slots show).
    from wozmod.formats.audio import PSP_CLIP_BASE, quote_voice
    from wozmod.formats.linkdata import ps2_members
    from wozmod.formats.scripts import quote_tables, quote_text
    from wozmod.text.psp_alignment import psp_quote_text
    from wozmod.text.script_alignment import PSP_QUOTES, QUOTE_SLOTS
    from wozmod.text.stock import stock_key
    from wozmod.voices.build import PSP_TEXT, TEXT_TABLE, psp_text_voice_map

    fns = files["folders"].folder("wo2-psp") / PSP_TEXT
    if not fns.is_file():
        pytest.skip("the WO2 PSP donor is not extracted")
    stock, psp = quote_tables(files["stock"]), quote_tables(ps2_members(fns, [PSP_QUOTES])[PSP_QUOTES])
    voices = psp_text_voice_map(files["moz"], files["stock"], fns)
    rows = {row["key"]: row for row in json.loads(TEXT_TABLE.read_text(encoding="utf-8"))["rows"]}

    def joined(t: int, k: int) -> bytes:
        return psp[t][k] if t < len(psp) and k < len(psp[t]) else b""

    failing, shown, voiced = [], {}, 0
    for t, table in enumerate(stock):
        for k, line in enumerate(table):
            if not line or (t >= 2 and k not in QUOTE_SLOTS):
                continue
            if key := next((found[0] for raw in (line[2:], quote_text(line)) if (found := stock_key(raw))
                            and found[0] in rows), None):
                shown.setdefault(key, []).append((t, k))
            if (suffix := _suffix(line)) is not None and (clip := _suffix(joined(t, k))) is not None:
                voiced += 1
                if voices.get(quote_voice(suffix)) != PSP_CLIP_BASE + clip - 1:
                    failing.append(f"quote {t}.{k}: PSP {voices.get(quote_voice(suffix))}, slot {PSP_CLIP_BASE + clip - 1}")
    checked = 0
    for key, slots in shown.items():
        ref = rows[key].get("ref")
        if not ref or ref[:4] != ["wo2-psp", "fns", PSP_QUOTES, "quotes"]:
            continue
        checked += 1
        if psp_quote_text(psp[ref[4]][ref[5]]) not in {psp_quote_text(joined(t, k)) for t, k in slots}:
            failing.append(f"row {key[:12]} at {', '.join(f'quote {t}.{k}' for t, k in slots)}: {ref}")
    assert voiced > 100 and checked > 0, (voiced, checked)
    assert not failing, "\n".join(failing)


def test_officers_92_and_93_play_the_select_voices_both_programs_give_them(files, maps) -> None:
    # Bug: the two officers' select voices swapped or left to a quote slot. Oracle: the voice MOZ 0x512B20 plays for
    # each and the clip WO2 X360 0x8229C89C plays for it, read from the two programs.
    from wozmod.formats import pe

    exe = (files["game"] / "OROCHI_Z.exe").read_bytes()
    program = files["folders"].program("wo2-x360")
    layout = pe.layout(exe)

    def x86(va: int, opcode: bytes, size: int) -> int:
        at = pe.file_offset(layout, va)
        assert exe[at:at + len(opcode)] == opcode, f"MOZ {va:#x}"
        return int.from_bytes(exe[at + len(opcode):at + len(opcode) + size], "little")

    def ppc(va: int, high: int) -> int:
        word = struct.unpack_from(">I", program, va - X360_BASE)[0]
        assert word >> 16 == high, f"X360 {va:#x}"
        return word & 0xFFFF

    cmp_eax, push, cmpwi_r29, li_r3 = b"\x83\xf8", b"\x68", 0x2F1D, 0x3860
    moz = {x86(0x512B24, cmp_eax, 1): x86(0x512B29, push, 4), x86(0x512B39, cmp_eax, 1): x86(0x512B3E, push, 4)}
    first = ppc(0x8229C89C, cmpwi_r29)  # equal: 0x8229C8A0; otherwise, below the bound 0x5E, 0x8229C8A8
    x360 = {first: ppc(0x8229C8A0, li_r3), first + 1: ppc(0x8229C8A8, li_r3)}
    assert moz.keys() == x360.keys() == {92, 93}
    _, wo2_map = maps
    assert {voice: wo2_map.get(voice) for voice in moz.values()} == {moz[o]: x360[o] for o in (92, 93)}
