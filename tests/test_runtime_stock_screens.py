"""Runtime layout values checked against what the stock game draws on the same screen."""

from __future__ import annotations

import json
import os
import re
import struct
from pathlib import Path

import capstone
import disasm
import pytest
from PIL import Image

from wozmod.formats import pe
from wozmod.runtime import design, layout, runtime_edits, text_boxes


@pytest.fixture(scope="module")
def game() -> Path:
    folder = os.environ.get("WOZMOD_GAME")
    if not folder or not Path(folder, "OROCHI_Z.exe").is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    return Path(folder)


@pytest.fixture(scope="module")
def code(game: Path):
    """read(va, n, patched): bytes of the stock exe, or of the exe with every English code edit applied."""
    stock = (game / "OROCHI_Z.exe").read_bytes()
    where = pe.layout(stock)
    patched = bytearray(stock)
    for va, before, after in runtime_edits.CODE_EDITS:
        at = pe.file_offset(where, va)
        assert patched[at:at + len(before)] == before, hex(va)
        patched[at:at + len(after)] = after

    def read(va: int, n: int, edited: bool = False) -> bytes:
        at = pe.file_offset(where, va)
        return bytes((patched if edited else stock)[at:at + n])
    return read


def test_weapon_name_starts_after_the_moz_thumbnail_and_ends_before_the_skill_squares(code) -> None:
    """The X360 name x (row+0x2A) put every weapon name under the thumbnail MOZ draws in the row; a lane
    wider than the room before the (moved) skill squares runs names into them."""
    assert code(0x5950D8, 2) == bytes.fromhex("8d47") and code(0x595135, 3) == bytes.fromhex("8dbc39")
    name_x = struct.unpack("<b", code(0x5950D8 + 2, 1, True))[0]
    squares = struct.unpack("<i", code(0x595135 + 3, 4, True))[0]  # the MOZ row moves them right
    # Thumbnail x and row x in each list that draws one (camp weapons, gallery weapons, fusion), and its width.
    for thumb, row, width, opcodes in [(0x529745, 0x595240, 0x52977C, ("8d6f", "83c6")),
                                       (0x5543C0, 0x5543CB, 0x55443C, ("8d56", "8d5e")),
                                       (0x5844A3, 0x584442, 0x58449F, ("83c2", "83c5"))]:
        assert (code(thumb, 2).hex(), code(row, 2).hex(), code(width, 1).hex()) == (*opcodes, "6a")
        end = code(thumb + 2, 1)[0] - code(row + 2, 1)[0] + code(width + 1, 1)[0]
        assert name_x >= end, f"name at row+{name_x} under the thumbnail ending at row+{end}"
    assert name_x + layout.single_line_sites()[0x5950EC] <= squares


@pytest.fixture(scope="module")
def calls(game: Path) -> dict[int, set[int]]:
    """Target -> VAs of every `call rel32` (e8) to it in the stock .text."""
    image = (game / "OROCHI_Z.exe").read_bytes()
    where = pe.layout(image)
    text = next(s for s in where["sections"] if s["name"] == b".text")
    base, data = where["image_base"] + text["rva"], image[text["raw"]:text["raw"] + text["raw_size"]]
    found: dict[int, set[int]] = {}
    for m in re.finditer(b"\xe8", data):
        at = m.start()
        if at + 5 <= len(data):
            found.setdefault((base + at + 5 + struct.unpack_from("<i", data, at + 1)[0]) & 0xFFFFFFFF,
                             set()).add(base + at)
    return found


def test_every_list_that_draws_the_weapon_row_grows_with_it(code, calls) -> None:
    """Bug: a list drawn around the wider MOZ weapon row keeps its stock frame (the end-of-battle discard list did),
    so names and skill pips run past its box. Every call of the row, and every call of the functions that draw its
    frames, must be in the shared rule's tables, and each frame must grow by what the row grows."""
    def push(va: int, edited: bool) -> int:
        assert code(va, 1) == b"\x68", hex(va)
        return struct.unpack("<I", code(va + 1, 4, edited))[0]
    grow = push(0x59506C, True) - push(0x59506C, False)
    pips = [struct.unpack("<i", code(0x595135 + 3, 4, edited))[0] for edited in (False, True)]
    assert grow > 0 and pips[1] - pips[0] == grow
    assert calls[runtime_edits.WEAPON_ROW] == set(runtime_edits.WEAPON_ROW_FRAMES)
    for call, (function, frame, _) in runtime_edits.WEAPON_ROW_FRAMES.items():
        assert function < frame < call, hex(call)
        assert push(frame, True) - push(frame, False) == grow, f"frame {frame:#x} around the row call {call:#x}"
    drawn_from = set().union(*(calls[f] for f, _, _ in runtime_edits.WEAPON_ROW_FRAMES.values()))
    assert drawn_from == set(runtime_edits.WEAPON_LIST_ORIGINS)


# Pages that set a list beside a weapon card: (list x, list width, column x: the card and any info box stacked with it,
# column width), each a site and the offset of its 32-bit value in the instruction; and the (rest, travel) sites of the
# column's slides from x 0x500. INFO_WIDTH: the info boxes' width, pushed in their detours' caves (runtime_edits).
CARD_PAGE_SITES = {
    "discard": ((0x5846B9, 2), (0x584368, 1), [(0x5846EF, 2)], (0x59858F, 1), []),
    "weapon pick": ((0x5298EC, 4), (0x5951E6, 1), [(0x529A90, 1)], (0x53375B, 1), [(0x529A90, 0x529A71)]),
    "camp weapon panel": ((0x531983, 4), (0x5951E6, 1), [(0x531B5E, 1)], (0x59858F, 1), [(0x531B5E, 0x531B3F)]),
    "pre-battle weapon panel": ((0x53C92D, 4), (0x5951E6, 1), [(0x53CAF5, 1)], (0x59858F, 1), [(0x53CAF5, 0x53CAD6)]),
    "upgrade skills weapons": ((0x527A84, 4), (0x526DE4, 1), [(0x527D47, 1), (0x527BE8, 1)], (0x5955BB, 1),
                               [(0x527D47, 0x527D28), (0x527BE8, 0x527BF2)]),
    "attributes": ((0x528F27, 1), (0x5332F8, 1), [(0x529012, 1), (0x52921D, 1)], (0x53375B, 1), [(0x52921D, 0x529227)]),
    "fusion weapons": ((0x52D1F2, 4), (0x5951E6, 1), [(0x52D4E8, 1)], (0x53375B, 1), [(0x52D4E8, 0x52D3A4)]),
    "fusion transfer": ((0x52F9A2, 1), (0x52E5CD, 1), [(0x52FC5A, 1), (0x52FA59, 1)], (0x52D077, 1),
                        [(0x52FA59, 0x52FA3A), (0x52FB5B, 0x52FB6A)]),
    "fusion confirm": ((0x52ED9C, 4), (0x5951E6, 1), [(0x52EEFC, 1)], (0x52D077, 1), []),
    # the pre-battle officer page (0x598B70): the card column (rows over the card), then the portrait column
    "pre-battle officer page": ((0x598DF0, 1), (0x598E01, 1), [(0x5988C8, 1)], (0x5988DA, 1), [(0x5988C8, 0x5988A7)]),
}
INFO_WIDTH = [runtime_edits.CAVE + 0x540 + 11, runtime_edits.CAVE + 0x560 + 11]
# The attributes page draws its card over the weapon pick's at one absolute x, so its group is not centred; the
# fusion card keeps one x on every fusion step (docs/design-language.md, Panels on a page, step 5).
SHARED_CARD = {"attributes": "weapon pick", "fusion transfer": "fusion weapons", "fusion confirm": "fusion weapons"}


def test_card_pages_set_list_and_card_column_a_panel_gap_apart_centred_in_the_safe_area(code) -> None:
    """Bug: a page's list grows but keeps its stock x and runs into the card; a card or info box keeps its stock x and
    leaves the safe area; an info box stacked with a card keeps its own x or width; a slide keeps its stock travel and
    lands off the new rest; or the discard list moves without the portrait drawn above it at a 3 px inset.
    docs/design-language.md: side-by-side panels 28 apart, centred in x 64..1216; a column's panels share both edges."""
    value = lambda site, edited=True: struct.unpack("<i", code(site[0] + site[1], 4, edited))[0]
    info_width = {struct.unpack("<i", code(va, 4, True))[0] for va in INFO_WIDTH}
    bad, columns = [], {}
    for page, (list_x, list_w, column, column_w, slides) in CARD_PAGE_SITES.items():
        lx, lw, cw = value(list_x), value(list_w), value(column_w)
        cx = {value(site) for site in column}
        columns[page] = cx
        right = max(cx) + cw
        if len(cx) != 1 or (len(column) > 1 and info_width != {cw}):
            bad.append((page, "column edges", sorted(cx), cw, info_width))
        if min(cx) - (lx + lw) != 28 or lx < 64 or right > 1216:
            bad.append((page, "gap or safe area", lx, lw, sorted(cx), right))
        if page not in SHARED_CARD and abs((lx - 64) - (1216 - right)) > 1:
            bad.append((page, "not centred", lx - 64, 1216 - right))
        for rest, travel in slides:
            if 0x500 - value((travel, 2)) != value((rest, 1)):
                bad.append((page, "slide", hex(rest), 0x500 - value((travel, 2)), value((rest, 1))))
    bad += [(page, "card moves between steps", columns[page], columns[other])
            for page, other in SHARED_CARD.items() if columns[page] != columns[other]]
    rows = value((0x598BBD, 1))  # the officer page's rows: the name x; plate at -0x60, first row y at the lea's disp
    if rows - 0x60 != value((0x598DF0, 1)) or rows + struct.unpack("<b", code(0x598BD7, 1, True))[0] != 131:
        bad.append(("pre-battle officer page", "rows", rows - 0x60, value((0x598DF0, 1))))
    portrait = (0x5845FC, 2)
    if value(portrait) - value((0x5846B9, 2)) != value(portrait, False) - value((0x5846B9, 2), False) != 3:
        bad.append(("discard", "portrait inset", value(portrait) - value((0x5846B9, 2))))
    assert not bad


LENGTHS = {0x6A: 2, 0x68: 5, 0x6B: 3, 0x69: 6, 0x99: 1, 0x83: 3, 0x81: 6, 0xB9: 5, 0x8D: 4, **dict.fromkeys(range(0x50, 0x58), 1)}
# (0x8D only in its lea r, [r + r + disp8] form, the one the walked runs use)


def edited_run(code, va: int, n: int) -> list[bytes]:
    """The first n instructions the edited exe runs from va, through a detour's jump into the cave and back."""
    run = []
    while len(run) < n:
        op = code(va, 1, True)[0]
        if op == 0xE9:
            va += 5 + struct.unpack("<i", code(va + 1, 4, True))[0]
            continue
        run.append(code(va, LENGTHS[op], True))
        va += LENGTHS[op]
    return run


def immediate(ins: bytes) -> int:
    """The immediate of push imm8/imm32, imul r, r, imm8/imm32, or an add or cmp of a register and imm8/imm32."""
    return {0x6A: lambda: struct.unpack("<b", ins[1:2])[0], 0x68: lambda: struct.unpack("<i", ins[1:5])[0],
            0x6B: lambda: struct.unpack("<b", ins[2:3])[0], 0x69: lambda: struct.unpack("<i", ins[2:6])[0],
            0x83: lambda: struct.unpack("<b", ins[2:3])[0], 0x81: lambda: struct.unpack("<i", ins[2:6])[0]}[ins[0]]()


# Parts drawn at an absolute x on a fusion step, at a fixed offset from that step's card: (site, stock card x).
FUSION_CARD_PARTS = {
    0x52D604: 0x273, 0x52D5CD: 0x273,  # 0x52D0D0's second card at rest, and where mode 5 moves it from
    0x52EEFC: 0x273, 0x52EF14: 0x273, 0x52EF88: 0x273,  # 0x52ECC0: card, fusion overlay, heading tab
    0x52ED42: 0x273, 0x52ED32: 0x273,  # and the dialog under the card, at rest and its slide's end
    0x52FC5A: 0x274, 0x52FC69: 0x274, 0x52FCD9: 0x274,  # 0x52F940: card, fusion overlay, heading tab
    0x52FA59: 0x274, 0x52FB5B: 0x274,  # and the info box under it and the count box on its left edge
}


def test_fusion_steps_draw_every_part_of_the_card_where_the_one_fusion_card_is(code) -> None:
    """The fusion draws its weapon card on three steps (0x52D0D0, 0x52F940, 0x52ECC0), each at an absolute x; the
    overlay, heading tab, thumbnail, info, count and dialog boxes, page arrow and the cursor over the card at their own.
    Bug: a part left at its stock x when the card moves, so it floats beside the card on a step no route reaches; or
    the cursor (0x52CBE0) framing the stock grid or tags instead of the card's."""
    value = lambda va, at=1, edited=True: struct.unpack("<i", code(va + at, 4, edited))[0]
    card = value(0x52D4E8)
    wrong = [f"{va:#x} at card {value(va) - card:+}, stock {value(va, edited=False) - stock:+}"
             for va, stock in FUSION_CARD_PARTS.items() if value(va) - card != value(va, edited=False) - stock]
    thumbnail = value(0x529052) - value(0x529012)  # the weapon pick's thumbnail and card
    wrong += [f"thumbnail {va:#x} at card {value(va) - card:+}" for va in (0x52EF51, 0x52FCA2) if value(va) - card != thumbnail]
    width = value(0x53375B)
    if value(0x52D555) + 21 != card + width // 2:  # the 42 px page arrow between the two cards, on their centre
        wrong.append(f"page arrow centre {value(0x52D555) + 21}, card centre {card + width // 2}")
    step, grid = edited_run(code, 0x52CCA5, 2)
    card_step = immediate(edited_run(code, 0x595528, 1)[0])
    grid_x = struct.unpack("<b", edited_run(code, 0x595528, 2)[1][3:4])[0]
    if (immediate(step), immediate(grid) - card) != (card_step, grid_x):
        wrong.append(f"grid cursor steps {immediate(step)} from card {immediate(grid) - card:+}")
    tag_step = immediate(edited_run(code, 0x5954B9, 3)[2])
    _, tag_w, _, tag_x, tag_y = edited_run(code, 0x52CD02, 5)
    card_y = 0xDD  # 0x52F940's card at rest (stock lerp 0x95 -> 0xDD), which the cursor frames
    expected = (tag_step, layout.DEFINES["TAG_WIDTH"], immediate(edited_run(code, 0x59549D, 1)[0]),
                struct.unpack("<b", code(0x5954AC, 1, True))[0])
    got = (immediate(edited_run(code, 0x52CCF2, 1)[0]), immediate(tag_w), immediate(tag_x) - card,
           immediate(tag_y) - card_y)
    if got != expected:
        wrong.append(f"tag cursor (step, width, x, y) {got}, the card's tags {expected}")
    assert not wrong


def test_attribute_grids_step_past_the_filled_cell_and_draw_empty_slots_as_wide(code) -> None:
    """The filled attribute cell (0x591810) is one width wherever it is drawn. A grid left at the stock column step
    or empty slot width (98) shows filled cells wider than the grey empty slots (weapon card), or lets the next slot
    cover a cell's name and level (upgrade skill effects)."""
    assert code(0x591844, 10) == bytes.fromhex("6a04 6880000000 50 6a62")
    cell = immediate(edited_run(code, 0x591844, 4)[3])
    wrong = []
    # grid, its column step's imul, and the push ... 0x1C, width that starts its empty slot's call to 0x5916B0
    for grid, step_at, slot_at in [("weapon card", 0x595528, 0x59556B), ("upgrade skills", 0x526C51, 0x526C98)]:
        assert code(step_at, 3) == bytes.fromhex("6bc969") and code(slot_at, 4) == bytes.fromhex("6a1c6a62")
        step, slot = immediate(edited_run(code, step_at, 1)[0]), immediate(edited_run(code, slot_at, 2)[1])
        if step < cell or slot != cell:
            wrong.append(f"{grid}: cell {cell}, column step {step}, empty slot {slot}")
    assert not wrong


FETCH_PLATE, FETCH_REMAPPED, REMAP = 0x58C940, 0x58E180, 0x718B60  # a texture by item, or by index into REMAP
CARD_CELL, FUSION_CELL, WASH = 0x591810, 0x52C900, 0x52C3E0  # the painters that fetch a cell's plate


def test_every_filled_attribute_cell_is_painted_once_at_the_cell_width(game, code) -> None:
    """Bugs: a painter that draws a filled cell on its own keeps the stock 98 px plate and the stock label and digit
    places (fusion's assigned slot was drawn by 0x52C900, a second copy of 0x591810: a short bar and its own level
    digit over the English label); a caller of the new-effect wash still passes 98 (fusion's confirm step); the
    green plate is drawn wide from 98 px of art; fusion's slot is drawn on the grey plate, not its own green one."""
    stock = (game / "OROCHI_Z.exe").read_bytes()
    where = pe.layout(stock)
    remap = lambda index: struct.unpack_from("<i", stock, pe.file_offset(where, REMAP + 4 * index))[0]  # noqa: E731
    plates, calls = {}, {}  # every fetch of a cell plate (4079.98-100) in the stock code, and every call
    for va, raw in disasm.code_sections(stock, where):
        for m in re.finditer(rb"\x6a(.)\xe8(.{4})", raw, re.S):
            target = va + m.end() + struct.unpack("<i", m.group(2))[0]
            item = {FETCH_PLATE: m.group(1)[0], FETCH_REMAPPED: remap(m.group(1)[0])}.get(target)
            if item in (98, 99, 100):
                plates[va + m.start()] = item
        for m in re.finditer(rb"\xe8(.{4})", raw, re.S):
            calls.setdefault(va + m.end() + struct.unpack("<i", m.group(1))[0], []).append(va + m.start())
    # each in its painter (0x591810, 0x52C900, 0x52C3E0); a fetch outside them is a painter this test does not hold
    assert sorted(plates) == [0x52C4C5, 0x52CAB9, 0x5919DB], {hex(k): v for k, v in plates.items()}
    green = plates[0x52CAB9]  # the plate stock fusion draws an assigned slot on
    cell, wrong = immediate(edited_run(code, 0x591844, 4)[3]), []

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    run = []
    for i in md.disasm(code(FUSION_CELL, 64, True), FUSION_CELL):
        run.append((i.mnemonic, i.op_str))
        if i.mnemonic == "ret":
            break
    plate = f"byte ptr [{runtime_edits.CELL_PLATE:#x}]"
    sets = [op.removeprefix(plate + ", ") for m, op in run if m == "mov" and op.startswith(plate)]
    called = run.index(("call", f"{CARD_CELL:#x}")) if ("call", f"{CARD_CELL:#x}") in run else -1
    if called < 0 or sets != [hex(green), "0"] or run[-1] != ("ret", ""):
        wrong.append(f"fusion's cell (0x52C900) is not 0x591810 on 4079.{green}: {run}")
    fetch = [(i.mnemonic, i.op_str) for i in md.disasm(code(0x5919DB, 5, True), 0x5919DB)]
    cave = int(fetch[0][1], 16) if fetch[0][0] == "jmp" else 0x5919DB
    fetch = [(i.mnemonic, i.op_str) for i in md.disasm(code(cave, 24, True), cave)][:6]
    if fetch[0] != ("movzx", f"eax, byte ptr [{runtime_edits.CELL_PLATE:#x}]") or fetch[3] != ("mov", "al, 0x62"):
        wrong.append(f"0x591810 does not fetch CELL_PLATE, else 98: {fetch}")
    for call in calls[WASH]:  # the 9-slice wash: its width pushed after its height, 0x1C, a few bytes before
        height = call - 13
        assert code(height, 4) == bytes.fromhex("6a1c6a62"), hex(call)
        if (width := immediate(edited_run(code, height, 2)[1])) != cell:
            wrong.append(f"the wash called at {call:#x} is {width} wide, not the {cell} px cell")
    widened = {e["item"]: d["splice"]["width"] for e in json.loads(Path("data/art/menu_textures.json").read_text())[
        "textures"] if e["entry"] == 4079 for d in e.get("draw", []) if "splice" in d}
    for item in (plates[0x5919DB], green):  # the plates drawn as one stretched quad (the wash is a 9-slice)
        width = widened.get(item)
        if (getattr(design, width) if isinstance(width, str) else width) != cell:
            wrong.append(f"the cell plate 4079.{item} is widened to {width}, not the {cell} px cell")
    assert not wrong


def test_skill_tag_rows_step_one_tag_and_stay_on_the_weapon_card(code) -> None:
    """Every row of three skill tags on a weapon card (the card, both fusion overlay paths and the upgrade's two tag
    rows drawn over it) steps past one tag, by the same amount, and ends inside the card's attribute grid. A site left
    at the stock 77 px step overlaps the wider tags or misses the card's own tags under it; a tag too wide for three
    runs off the 650-wide card; a plate, outline or drawer at another width leaves a gap or a sliver."""
    tag = layout.DEFINES["TAG_WIDTH"]
    # each row's step: the n-th instruction run from a stock instruction boundary before it
    steps = {"card": (0x5954B9, 2), "fusion overlay": (0x52EA97, 0), "fusion overlay, weapon B": (0x52EBC1, 1),
             "upgrade tag row": (0x529150, 0), "upgrade chosen tag": (0x5291EB, 0)}
    step = {row: immediate(edited_run(code, va, n + 1)[n]) for row, (va, n) in steps.items()}
    ends = {struct.unpack("<i", code(va + 2, 4, True))[0] for va in (0x529156, 0x5291F4)}  # cmp edi, end of row
    outline = immediate(edited_run(code, 0x52913C, 2)[1])  # the frame on the chosen slot
    cell, column = immediate(edited_run(code, 0x591844, 4)[3]), immediate(edited_run(code, 0x595528, 1)[0])
    grid_x = struct.unpack("<b", edited_run(code, 0x595528, 2)[1][3:4])[0]  # lea ecx, [ecx + edx + x]
    grid_right = grid_x + 3 * column + cell  # the card's attribute grid, from the card's left edge
    tag_x = immediate(edited_run(code, 0x59549D, 1)[0])  # add ebp, x: the card's first tag
    plates = {e["item"]: e["plate"]["width"] for e in json.loads(Path("data/art/menu_textures.json").read_text())[
        "textures"] if e["entry"] == 4079 and e.get("item") in (198, 213)}
    wrong = [f"{row} steps {s}, not past the {tag} px tag" for row, s in step.items() if s <= tag]
    if len(set(step.values())) != 1:
        wrong.append(f"the rows step differently: {step}")
    starts = {struct.unpack("<i", code(va + 1, 4, True))[0] for va in (0x5290EA, 0x5291CC)}  # mov edi, first tag
    if len(starts) != 1 or ends != {next(iter(starts)) + 3 * step["card"]}:
        wrong.append(f"the upgrade rows end at {sorted(ends)}, not after three steps of {step['card']}")
    if tag_x + 2 * step["card"] + tag > grid_right:
        wrong.append(f"three tags end at card x {tag_x + 2 * step['card'] + tag}, past the grid at {grid_right}")
    if {outline, *plates.values()} != {tag}:
        wrong.append(f"outline {outline} and blank plates {plates} are not the {tag} px tag")
    assert not wrong


def test_page_origins_do_not_leave_a_glyph_callback_counting_from_the_stock_line_start(code) -> None:
    """The Z narration callback adds 4 px per glyph and resets only at the stock line x 0x168: at the X360
    origin it never reset, so the glyphs ran off screen while the screen data said they were drawn."""
    checked = 0
    for site, row in text_boxes.page_rows().items():
        before = code(site - 0x80, 0x80)
        found = re.search(rb"\xc7\x05\xe8\xad\xc2\x00(.{4})", before, re.S)
        if not found:
            continue
        callback = struct.unpack("<I", found[1])[0]
        body = code(callback, 0x600)
        body = body[:body.index(b"\xcc" * 4)]
        for reset in re.finditer(rb"\x81\x7f\x0c(.{4})", body, re.S):
            line_x = struct.unpack("<i", reset[1])[0]
            checked += 1
            if line_x != row[3]:
                patched = code(callback, len(body), True)
                assert b"\x8d\x4c\x8a\x02" not in patched, (
                    f"page {site:#x} at x {row[3]} keeps the per-glyph offset its callback resets at x {line_x}")
    assert checked >= 1


def test_z_still_name_lane_fits_the_plate_dark_box_the_stock_centres_on(game: Path, code) -> None:
    """A lane wider than the stock 406x70 plate's dark box let long names run past it (the X360 616 plate's 400)."""
    from wozmod.formats.linkdata import decoded_member, moz_entries
    from wozmod.formats.textures import parse_texture_package
    assert code(0x59A2CC, 5) == bytes.fromhex("6896010000") and code(0x59A2CA, 2) == bytes.fromhex("6a46")
    plate_y = struct.unpack("<I", code(0x59A2DE + 1, 4))[0]
    name_y = struct.unpack("<I", code(0x59A3D5 + 1, 4))[0]
    cell = code(0x59A38E + 6, 1)[0]
    # (plate x, name centre, item) for the right plate drawn first and the left one.
    sides = [(struct.unpack("<I", code(0x59A164 + 4, 4))[0], struct.unpack("<I", code(0x59A1C6 + 1, 4))[0], 1),
             (struct.unpack("<I", code(0x59A1F1 + 4, 4))[0], struct.unpack("<I", code(0x59A253 + 1, 4))[0], 0)]
    assert (plate_y, name_y, cell, sides[0][:2], sides[1][:2]) == (440, 454, 28, (762, 994), (112, 285))
    package = parse_texture_package(decoded_member(
        moz_entries(game / "LINKDATA1.BIN", game / "LINKDATA1.IDX", [4102])[4102]))
    lane = layout.DEFINES["STILL_Z_LANE"]
    for plate_x, centre, item in sides:
        texture = package.items[item].g1t.textures[0]
        image = Image.frombytes("RGBA", (texture.width, texture.height), texture.decode_rgba()).crop((0, 0, 406, 70))
        px, left, right = image.load(), 0, 405
        for y in range(name_y - plate_y, name_y - plate_y + cell):
            dark = [x for x in range(406) if px[x, y][3] > 200 and sum(px[x, y][:3]) < 150]
            runs = [run for run in re.finditer(r"1+", "".join("1" if x in set(dark) else "0" for x in range(406)))]
            widest = max(runs, key=lambda run: run.end() - run.start())
            left, right = max(left, widest.start()), min(right, widest.end() - 1)
        assert lane <= right - left + 1
        assert abs(centre - plate_x - (left + right + 1) / 2) <= 1


def test_every_glyph_finalizer_call_the_squish_must_rescale_is_listed_with_its_glyph_register(code) -> None:
    """A screen's own glyph callback emits quads from a call the squish did not know, so text it draws stays at
    full width: the Z still plate (callback 0x599E30) drew long names past its 180 lane. A listed site with the
    wrong glyph register reads the wrong pen."""
    finalizers = (0x5DCA30, 0x5DCCF0)
    sites = layout.glyph_sites()

    def call(ret: int) -> tuple[int, int] | None:
        """(target, glyph register 0 esi / 1 edi) of the `mov r,[glyph+0x1C]; push r; push quad; call` ending at ret."""
        head = code(ret - 10, 10)
        if head[5] != 0xE8 or head[0] != 0x8B or head[2] != 0x1C:
            return None
        return (ret + struct.unpack("<i", head[6:])[0]) & 0xFFFFFFFF, {6: 0, 7: 1}.get(head[1] & 7, -1)

    wrong = [hex(va) for va, reg in sites.items() if call(va) is None or call(va)[0] not in finalizers
             or call(va)[1] != reg]
    callbacks = 0
    image = code(0x401000, 0x2C0000)
    for install in re.finditer(rb"\xc7\x05\xe8\xad\xc2\x00(.{4})", image, re.S):
        callback = struct.unpack("<I", install[1])[0]
        body = code(callback, 0x1000)
        body = body[:body.index(b"\xcc" * 3)]
        callbacks += 1
        for at in (m.start() for m in re.finditer(rb"\xe8", body)):
            ret = callback + at + 5
            if at >= 5 and at + 5 <= len(body) and (found := call(ret)) and found[0] in finalizers and sites.get(ret) != found[1]:
                wrong.append(f"callback {callback:#x} call {ret:#x}")
    assert callbacks >= 8 and wrong == []


def test_each_caption_line_keeps_its_own_centred_x(code) -> None:
    """The cue layout set both lines of a two-line caption to the smaller centred x, so the shorter line sat
    left of centre (run 3: line centres 962.5 and 882.5 cap)."""
    assert code(0x56A2B1, 1) == b"\xe8"
    layout_at = 0x56A2B6 + struct.unpack("<i", code(0x56A2B2, 4))[0]
    stock = code(layout_at, 0xC0)
    stock = stock[:stock.index(bytes.fromhex("83c410c3")) + 4]  # add esp,0x10; ret
    store = stock.index(bytes.fromhex("8946f8"))  # x[k] = (1280 - width[k]) / 2, in the line loop
    tail = stock[store + 3:]
    assert bytes.fromhex("3bc8 7c02 8bc8") in tail  # then the stock takes the smaller x ...
    patched = code(layout_at + store + 3, len(tail), True)
    # ... and must not write it back over either line's x
    assert bytes.fromhex("890e") not in patched and bytes.fromhex("890a") not in patched


def test_right_speaker_window_box_and_text_share_one_right_edge(code) -> None:
    """Window 1 with the speaker on the right: the text x (set-text, edge - width) moved to the X360 edge but the
    box (0x54E950, edge - width - 14) stayed at the stock 1141, so each line started ~110 px left of the box."""
    assert code(0x54E89B, 1) == b"\xba" and code(0x54E8A0, 2) == bytes.fromhex("2bd3")  # edx = edge - width
    assert code(0x54E98C, 1) == b"\xb8" and code(0x54E991, 4) == bytes.fromhex("2b44242c")  # eax = edge - width
    stock = (code(0x54E89C, 4), code(0x54E98D, 4))
    assert stock[0] == stock[1]
    assert code(0x54E89C, 4, True) == code(0x54E98D, 4, True)
