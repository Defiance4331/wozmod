"""The voice blocks the installer maps by count (formats.audio *_EVENT_VOICES, *_NARRATION_VOICES) and the story
record bases, joined line by line with what the donor programs play for the same page, scene, record or stage."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from wozmod.formats import pe
from wozmod.formats.audio import (
    MOZ_EVENT_LINES, WO1_EVENT_VOICES, WO1_NARRATION_VOICES, WO1_RECORD_BASE, WO2_CLIP_BASE, WO2_EVENT_VOICES, WO2_NARRATION_VOICES,
    block_voice_map,
)
from wozmod.formats.linkdata import archive
from wozmod.formats.scripts import mesc_blocks
from wozmod.text.contexts import DONOR_MESC, mesc_records
from wozmod.text.narration_voices import (
    PAGE_TABLE, SCENE_TABLE, STILL_LINES, Z_NARRATION_BASE, Z_STILL_BASE, briefing_voices, camp_voices, container,
    line_voices, page_voices,
)
from wozmod.text.script_alignment import BRIEFINGS, X360_ENS

X360_BASE = 0x82000000
pytestmark = pytest.mark.skipif(not (os.environ.get("WOZMOD_DONORS") and os.environ.get("WOZMOD_GAME")),
                                reason="set WOZMOD_DONORS and WOZMOD_GAME")


def _word(program: bytes, va: int) -> int:
    return struct.unpack_from(">I", program, va - X360_BASE)[0]


def _imm(program: bytes, va: int, opcode: int) -> int:
    """The signed 16-bit immediate of the instruction at va, which must have the primary opcode given (addi 14,
    cmpwi 11)."""
    word = _word(program, va)
    assert word >> 26 == opcode, f"{va:#x} is not opcode {opcode}"
    return (word & 0xFFFF) - 0x10000 * (word & 0x8000 > 0)


def _call(program: bytes, va: int) -> int:
    word = _word(program, va)
    assert word >> 26 == 18 and word & 3 == 1, f"{va:#x} is not a bl"
    return va + ((word & 0x03FFFFFC) ^ 0x02000000) - 0x02000000


def _stack_table(program: bytes, va: int) -> tuple[list[int], int]:
    """A function that stores `li r9, imm` / `stw r9, d(r1)` pairs, indexes them from `addi r10, r1, first` and
    returns entry + base (`addi r3, r11, base`): the entries in order, and base."""
    stored, value, first, at = {}, 0, None, va
    while (word := _word(program, at)) != 0x4E800020:  # blr
        rd, ra, imm = word >> 21 & 31, word >> 16 & 31, (word & 0xFFFF) - 0x10000 * (word & 0x8000 > 0)
        if word >> 26 == 14 and ra == 0:
            value = imm
        elif word >> 26 == 14 and ra == 1:
            first = imm
        elif word >> 26 == 36 and ra == 1:
            stored[imm] = value
        elif word >> 26 == 14 and rd == 3:
            base = imm
        at += 4
    return [stored[first + 4 * i] for i in range(len(stored))], base


@pytest.fixture(scope="module")
def inputs():
    donors, game = Path(os.environ["WOZMOD_DONORS"]), Path(os.environ["WOZMOD_GAME"])
    return {"exe": (game / "OROCHI_Z.exe").read_bytes(),
            "stock": archive(game / "LINKDATA1").read([4071, 4073, 4103, 4105, BRIEFINGS[0]]),
            "wo1": (donors / "wo1-x360/default.pe").read_bytes(), "wo2": (donors / "wo2-x360/default.pe").read_bytes(),
            "wo1_ens": archive(donors / "wo1-x360/linkdata_ens").read(e for s, e in DONOR_MESC.values() if s == "wo1-x360"),
            "wo2_ens": archive(donors / "wo2-x360/linkdata_ens").read(e for s, e in DONOR_MESC.values() if s == "wo2-x360"),
            "counts": {name: struct.unpack_from(">I", (donors / name / "linkdata_ans.idx").read_bytes())[0]
                       for name in ("wo1-x360", "wo2-x360")}}


def test_story_and_event_voice_bases_are_the_ones_the_x360_players_add(inputs) -> None:
    # Bug: a record base or a voiced-line limit differs from the X360 player's (or MOZ's) (WO2_CLIP_BASE off by one moves every
    # WO2 story, Dream and quote voice to the next line's; an event count above 0xF1 maps ids no X360 line plays).
    # The voiced-subtitle contexts reuse these constants, so only the program can catch them.
    wo1, wo2, exe = inputs["wo1"], inputs["wo2"], inputs["exe"]
    image = pe.layout(exe)

    def mov(va: int, opcode: int) -> int:  # mov reg, imm32
        at = pe.file_offset(image, va)
        assert exe[at] == opcode, hex(va)
        return struct.unpack_from("<I", exe, at + 1)[0]

    rows = [  # (what, ours, the program's)
        # MOZ's event voice start (0x513731): base and voiced lines, ebx and esi, by the WO1 voice mode
        ("MOZ WO2-mode events", (WO2_EVENT_VOICES[0], MOZ_EVENT_LINES["wo2-x360"]), (mov(0x513740, 0xBB),
                                                                                    mov(0x51373B, 0xBE))),
        ("MOZ WO1-mode events", (WO1_EVENT_VOICES[0], MOZ_EVENT_LINES["wo1-x360"]), (mov(0x51374C, 0xBB),
                                                                                    mov(0x513747, 0xBE))),
        # the speak command keeps @NNNN - 1 (WO1 0x821A11F4, WO2 0x821BD5DC), as maps pairs suffix - 1
        ("WO1 story id", -1, _imm(wo1, 0x821A11F4, 14)), ("WO2 story id", -1, _imm(wo2, 0x821BD5DC, 14)),
        ("WO1 story base", WO1_RECORD_BASE, _imm(wo1, 0x821889E0, 14)),
        ("WO2 story base", WO2_CLIP_BASE, _imm(wo2, 0x8219F9C8, 14)),
        ("WO1 event lines", WO1_EVENT_VOICES[1:], (_imm(wo1, 0x82188944, 14), _imm(wo1, 0x82188920, 11))),
        ("WO2 event lines", WO2_EVENT_VOICES[1:], (_imm(wo2, 0x8219F930, 14), _imm(wo2, 0x8219F908, 11))),
        # base plus limit is the whole voice index: the story block ends the disc's records
        ("WO1 records", inputs["counts"]["wo1-x360"], _imm(wo1, 0x821889E0, 14) + _imm(wo1, 0x821889D0, 11)),
        ("WO2 records", inputs["counts"]["wo2-x360"], _imm(wo2, 0x8219F9C8, 14) + _imm(wo2, 0x8219F9B8, 11)),
    ]
    assert (_call(wo1, 0x821A13F8), _call(wo2, 0x821BD79C)) == (0x82188998, 0x8219F980)
    wrong = [(what, ours, theirs) for what, ours, theirs in rows if ours != theirs]
    assert not wrong, wrong


def _wo1_items(inputs) -> list[tuple[str, int, int, tuple, tuple]]:
    """(item, MOZ voice id, WO1 X360 record, MOZ text place, X360 text place) for the pages (4073 / ENS 6) and the
    camp talk (4105 / ENS 18)."""
    exe, wo1, stock, ens = inputs["exe"], inputs["wo1"], inputs["stock"], inputs["wo1_ens"]
    items = []
    # Pages: the voice start plays 0x8226E778(page [s+0xB4]) + line [s+0xB9] (0x8226F210 to 0x8226F238) and the draw
    # shows that block and line; MOZ 0x560600(page) + line the same way.
    assert _call(wo1, 0x8226F214) == 0x8226E778 and _call(wo1, 0x8226F238) == 0x82188800
    table, base = _stack_table(wo1, 0x8226E778)
    ours, theirs = mesc_blocks(stock[4073]), mesc_blocks(ens[DONOR_MESC[4073][1]])
    for p, voices in enumerate(page_voices(exe, ours)):
        for j, voice in enumerate(voices):
            place = (p, j) if p < len(theirs) and j < len(theirs[p]) else None
            items.append((f"4073 page {p} line {j}", voice, base + table[p] + j, (p, j), place))
    assert len(table) == PAGE_TABLE[2]
    # Camp talk: scene [s+0x30] line [s+0x35] plays 0x8228F958(scene) + line, one less for lines 20-22 of scene 0x25
    # (0x82291018 to 0x82291058), and shows MESC line 0x8228F8F8(scene) + line, the line counts of the scenes before.
    assert _call(wo1, 0x82291038) == 0x8228F958 and _call(wo1, 0x82291058) == 0x82188800
    assert (_imm(wo1, 0x82291020, 11), _imm(wo1, 0x82291028, 14), _imm(wo1, 0x8229102C, 10)) == (0x25, -20, 2)
    table, base = _stack_table(wo1, 0x8228F958)
    scenes, donor_scenes = container(stock[4105]), container(ens[DONOR_MESC[4105][1]])
    voices = iter(camp_voices(exe, stock[4105]))
    for s in range(SCENE_TABLE[2]):
        for j in range(scenes[s][0]):
            line = sum(e[0] for e in scenes[:s]) + j
            donor = sum(e[0] for e in donor_scenes[:s]) + j if j < donor_scenes[s][0] else None
            k = j - (s == STILL_LINES[0] and j in STILL_LINES[1])
            items.append((f"4105 scene {s} line {j}", next(voices), base + table[s] + k, (0, line), (0, donor)))
    return items


def _wo2_items(inputs) -> list[tuple[str, int, int, tuple, tuple]]:
    """The same for the Z pages (4071 / ENS 8), the Dream briefings (4190) and the Z stills (4103 / ENS 32)."""
    exe, wo2, stock, ens = inputs["exe"], inputs["wo2"], inputs["stock"], inputs["wo2_ens"]
    image = pe.layout(exe)

    def moz_add(va: int) -> int:  # add eax, imm32
        code = exe[pe.file_offset(image, va):pe.file_offset(image, va) + 5]
        assert code[0] == 0x05, hex(va)
        return struct.unpack_from("<I", code, 1)[0]

    items = []
    # Z pages: stage s reads its records from entry s + 1 (MOZ 0x55FB07, stage [0x801AA0]) or ENS 8 entry s + 4 (X360
    # 0x822B09C8, stage [0x82575DD8 + 0x55E0]; ENS 8 is archive id 0xEF2, 0x822B05A8), and record k plays word 0 +
    # 0x15A (0x55FB3B) or + 0x5C (0x822B09E4); the text shown is word 1, equal to word 0 in every record.
    assert moz_add(0x55FB3B) == Z_NARRATION_BASE
    first, base = _imm(wo2, 0x822B09C8, 14), _imm(wo2, 0x822B09E4, 14)
    ours, theirs = container(stock[4071]), container(ens[DONOR_MESC[4071][1]])
    stages = len(ours) - 1
    for s in range(stages):
        a = [struct.unpack_from("<4I", ours[s + 1], k) for k in range(0, len(ours[s + 1]), 16)]
        b = [struct.unpack_from(">4I", theirs[s + first], k) for k in range(0, len(theirs[s + first]), 16)]
        for k, record in enumerate(a):
            donor = b[k] if k < len(b) else (None, None)
            assert record[0] == record[1] and donor[0] == donor[1], f"stage {s} record {k}: voice is not the text"
            items.append((f"4071 stage {s} record {k}", Z_NARRATION_BASE + record[0],
                          None if donor[0] is None else base + donor[0], (0, record[1]), (0, donor[1])))
    # Briefings: stage s plays MOZ voice s + 0x200 (0x548C1D) or X360 record s + 0x102 (0x8229F6D4, stage
    # [0x82575DD8 + 0x55E0], player 0x8219F810), and shows 4190 entry s - 0x28 (0x548C4A) or ENS s + 0xF0D - 0xEEA
    # (0x822B05D0). The Dream stages 40-67 have the same number in both games (WO2 X360 stage scripts).
    assert _call(wo2, 0x8229F6D8) == 0x8219F810
    base, text = _imm(wo2, 0x8229F6D4, 14), _imm(wo2, 0x822B05D0, 14) - _imm(wo2, 0x822B05D4, 14)
    for entry, voice in briefing_voices(exe).items():
        if entry < len(BRIEFINGS[1]):
            stage = entry + 0x28
            items.append((f"4190 entry {entry}", voice, stage + base, (X360_ENS[BRIEFINGS[1][entry]],),
                          (stage + text,)))
    # Z stills: scene n is entry n + 2 (MOZ 0x599A75) or ENS 32 entry n + 5 (X360 0x822DCB24); a type 2 command plays
    # word 2 + 0x4C4 (0x59AEDE) or + 0x146 (0x822DC8F4) and shows MESC line word 3 (0x59AEC0, 0x822DC8DC).
    first, base = _imm(wo2, 0x822DCB24, 14), _imm(wo2, 0x822DC8F4, 14)
    ours, theirs = container(stock[4103]), container(ens[DONOR_MESC[4103][1]])

    def commands(entry: bytes, order: str) -> tuple[int, list[tuple[int, int]]]:
        rows = [struct.unpack_from(f"{order}HHIII", entry, k) for k in range(0, len(entry) - 15, 16)]
        return rows[0][1], [(voice, text) for kind, _, _, voice, text in rows if kind == 2]

    for n in range(len(ours) - 2):
        number, a = commands(ours[n + 2], "<")
        donor_number, b = commands(theirs[n + first], ">") if n + first < len(theirs) else (None, [])
        assert number == n == donor_number, f"4103 entry {n + 2} is scene {number}, the X360's {donor_number}"
        for i, (voice, line) in enumerate(a):
            donor = b[i] if i < len(b) else (None, None)
            items.append((f"4103 scene {n} command {i}", Z_STILL_BASE + voice,
                          None if donor[0] is None else base + donor[0], (0, line), (0, donor[1])))
    return items


@pytest.mark.parametrize("donor", ["wo1-x360", "wo2-x360"])
def test_narration_blocks_give_each_line_the_record_its_donor_plays_with_the_same_line(inputs, donor) -> None:
    # Bug: a narration run maps a stock id to another record than the donor plays for the same page, scene, record
    # or stage (a run that starts one off, a page MOZ inserted, a count that runs into the next block), or the donor
    # shows another line in that place than the one the pairings take (block by block, line by line), or the
    # subtitle contexts give a donor line another clip (mesc_records). Oracle: both games' programs, read for the
    # same item.
    program = inputs[donor[:3]]
    loads = ({4073: _imm(program, 0x8226F09C, 14) - 0xA53, 4105: _imm(program, 0x822912DC, 14) - 0xA53}
             if donor == "wo1-x360" else
             {4071: _imm(program, 0x822B05A8, 14) - 0xEEA, 4103: _imm(program, 0x822DDE28, 14) - 0xEEA})
    assert {n: e for n, (s, e) in DONOR_MESC.items() if s == donor} == loads  # the ENS entries the donor loads
    items = _wo1_items(inputs) if donor == "wo1-x360" else _wo2_items(inputs)
    runs = WO1_NARRATION_VOICES if donor == "wo1-x360" else WO2_NARRATION_VOICES
    mapped = {i: r for run in runs for i, r in block_voice_map(run).items()}
    joined = {voice: record for _, voice, record, _, _ in items}
    wrong = [f"{name}: voice {voice} maps to {mapped.get(voice)}, the donor plays {record}"
             for name, voice, record, _, _ in items if mapped.get(voice) != record]
    wrong += [f"{name}: paired with donor line {ours}, the donor shows {theirs}" for name, _, _, ours, theirs in items if ours != theirs]
    wrong += [f"voice {voice} maps to {mapped[voice]}, no stock line plays it" for voice in sorted(set(mapped) - set(joined))]
    exe, stock, ens = inputs["exe"], inputs["stock"], inputs[f"{donor[:3]}_ens"]
    plays = {(int(name.split()[0]), *theirs): record for name, _, record, _, theirs in items}
    for entry, (source, ens_entry) in DONOR_MESC.items():
        if source == donor:
            found = mesc_records(donor, ens_entry, mesc_blocks(ens[ens_entry]), line_voices(exe, entry, stock[entry]))
            wrong += [f"{entry}: the contexts give ENS {ens_entry} line {line.pointer[4:]} record {clip[1]}, the donor "
                      f"plays {plays.get((entry, *line.pointer[4:]))}" for clip, line in found
                      if plays.get((entry, *line.pointer[4:])) != clip[1]]
    print(f"{donor}: {len(items)} lines joined")
    assert not wrong, f"{len(wrong)} disagreements: " + "; ".join(wrong[:20])
