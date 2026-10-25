"""Runtime layout values checked against what the stock game draws on the same screen."""

from __future__ import annotations

import json
import os
import re
import struct
from pathlib import Path

import pytest
from PIL import Image

from wozmod.formats import pe
from wozmod.runtime import layout, runtime_edits, text_boxes


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


LENGTHS = {0x6A: 2, 0x68: 5, 0x6B: 3, 0x69: 6, 0x99: 1, 0x83: 3, 0x81: 6, 0xB9: 5, **dict.fromkeys(range(0x50, 0x58), 1)}


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
    grid_right = 0x13 + 3 * column + cell  # the card's attribute grid, from the card's left edge
    plates = {e["item"]: e["plate"]["width"] for e in json.loads(Path("data/art/menu_textures.json").read_text())[
        "textures"] if e["entry"] == 4079 and e.get("item") in (198, 213)}
    wrong = [f"{row} steps {s}, not past the {tag} px tag" for row, s in step.items() if s <= tag]
    if len(set(step.values())) != 1:
        wrong.append(f"the rows step differently: {step}")
    if ends != {0x343 + 3 * step["card"]}:
        wrong.append(f"the upgrade rows end at {sorted(ends)}, not after three steps of {step['card']}")
    if 0xD0 + 2 * step["card"] + tag > grid_right:
        wrong.append(f"three tags end at card x {0xD0 + 2 * step['card'] + tag}, past the grid at {grid_right}")
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
