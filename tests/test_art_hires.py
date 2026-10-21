from __future__ import annotations

import struct

import pytest
from PIL import Image

from wozmod.art import hires
from wozmod.art.hires import HIRES_PACKINGS, HiresError, HiresRow, hires_bank, read_hires_bank


def blank_row(entry: int, w: int = 8, h: int = 8, band: int = 8) -> HiresRow:
    """A row of transparent blocks, the size its image takes in every packing hires has now."""
    blocks = []
    for scale, levels in hires.HIRES_PACKINGS:
        ew, eh = hires.hires_extent(scale, w, h, band, 0, levels) if levels else (0, 0)
        blocks.append([bytes((ew >> k) * (eh >> k)) for k in range(levels)])
    return HiresRow(entry, 0, w, h, band, 0, blocks)


def test_hires_sheet_does_not_swap_band_rows_and_columns():
    from wozmod.art.hires import hires_sheet

    art = Image.new("RGBA", (8, 128))
    for b in range(8):
        art.paste((b + 1, 0, 0, 255), (0, b * 16, 8, b * 16 + 16))
    sheet = hires_sheet(art, 4, 8, 2, 2, 2)
    assert sheet.size == (32, 32)
    assert [sheet.getpixel(((b // 2) * 8, (b % 2) * 16))[0] for b in range(8)] == list(range(1, 9))
    assert sheet.getpixel((16, 31)) == (6, 0, 0, 255)


def test_packed_bank_reads_back_every_rows_fields_flags_and_blocks():
    """Bug: a flag or the minimum lands on the wrong bit, or a row's blocks are spliced at the wrong origin, level or
    packing's sheet, so the runtime draws another row's lettering or none."""
    import random

    rng = random.Random(7)
    # wide rows force a second shelf, tall ones a second sheet; a banded row checks the column layout
    shapes = [(1000, 32, 32, 0), (1000, 32, 32, 0), (300, 64, 64, 0), (1000, 1000, 56, 0), (1000, 1000, 56, 0),
              (128, 32, 32, 0), (256, 672, 42, 0), (512, 1596, 42, 19)]
    rows = []
    for n, (w, h, band, per_col) in enumerate(shapes):
        blocks = []
        for scale, levels in HIRES_PACKINGS:
            ew, eh = hires.hires_extent(scale, w, h, band, per_col, levels)
            blocks.append([rng.randbytes((ew >> k) * (eh >> k)) for k in range(levels)])
        rows.append(HiresRow(4000 + n, n, w, h, band, per_col, blocks, overlay=n == 2, right_grow=n == 5,
                             minimum=n * 1000))
    blob = hires_bank(rows)
    assert struct.unpack_from("<I", blob, 12)[0] > len(HIRES_PACKINGS)  # the shapes need a second sheet
    back = read_hires_bank(blob)
    assert len(back) == len(rows)
    failures = [(row.entry, field) for row, got in zip(rows, back, strict=True) for field in row.__dataclass_fields__
                if getattr(row, field) != getattr(got, field)]
    assert not failures, failures


def test_bank_takes_the_blocks_of_a_row_whose_height_is_not_whole_bands():
    """Bug: the sheet image sized from the whole bands (245 of 256 rows) while the bank expects all 256, so the build
    stops on a stock texture like 4079.43."""
    from wozmod.art.hires import HIRES_SCALE, hires_blocks

    art = Image.new("RGBA", (16 * HIRES_SCALE, 256 * HIRES_SCALE), (200, 200, 200, 255))
    row = HiresRow(1, 2, 16, 256, 35, 0, hires_blocks(art, 16, 35, 0))
    assert read_hires_bank(hires_bank([row])) == [row]


def test_packed_bank_refuses_levels_of_the_wrong_size_and_rows_past_the_uv_cap():
    """Blocks of another size would be spliced over a neighbour; a row past 4095 texels cannot be addressed."""
    # 8x8 stock texels: 24x24 at 3x, then 12x12; 16x16 at 2x
    with pytest.raises(HiresError, match="levels of"):
        hires_bank([HiresRow(13, 5, 8, 8, 8, 0, [[bytes(24 * 24), bytes(16 * 16)], [bytes(16 * 16)]])])
    with pytest.raises(HiresError, match="levels of"):
        hires_bank([HiresRow(13, 5, 8, 8, 8, 0, [[bytes(24 * 24), bytes(12 * 12)], [bytes(24 * 24)]])])
    with pytest.raises(HiresError, match="UV cap"):
        hires_bank([HiresRow(13, 5, 1366, 8, 8, 0, [[bytes(4104 * 24), bytes(2052 * 12)], [bytes(2736 * 16)]])])


def test_bank_writer_refuses_each_bank_the_runtime_refuses_whole_and_takes_one_at_each_bound(monkeypatch):
    """Bug: the build writes a bank the runtime refuses whole (too many rows, packings or sheets; a packing's scale or
    levels, or a sheet's side, out of bounds), so players silently get no hi-res lettering at all; or a check is off
    by one and stops a build the runtime would take."""
    two = lambda: [blank_row(1), blank_row(2)]  # 2 rows, one sheet per packing
    cases = [  # condition, module values, rows, refused
        ("rows at HIRES_MAX", {"HIRES_MAX": 2}, two, False),
        ("rows past HIRES_MAX", {"HIRES_MAX": 1}, two, True),
        ("packings at HIRES_BANKS", {"HIRES_BANKS": 2}, two, False),
        ("packings past HIRES_BANKS", {"HIRES_BANKS": 1}, two, True),
        ("no packing", {"HIRES_PACKINGS": ()}, two, True),
        ("sheets at HIRES_SHEETS", {"HIRES_SHEETS": 2}, two, False),
        ("sheets past HIRES_SHEETS", {"HIRES_SHEETS": 1}, two, True),
        ("scales at their bounds", {"HIRES_PACKINGS": ((hires.HIRES_SCALE_MIN, 1), (hires.HIRES_SCALE_MAX, 1))},
         two, False),
        ("scale under HIRES_SCALE_MIN", {"HIRES_PACKINGS": ((hires.HIRES_SCALE_MIN - 1, 1),)}, two, True),
        ("scale over HIRES_SCALE_MAX", {"HIRES_PACKINGS": ((hires.HIRES_SCALE_MAX + 1, 1),)}, two, True),
        ("levels at HIRES_LEVELS", {"HIRES_PACKINGS": ((3, hires.HIRES_LEVELS),)}, two, False),
        ("levels over HIRES_LEVELS", {"HIRES_PACKINGS": ((3, hires.HIRES_LEVELS + 1),)}, two, True),
        ("no levels", {"HIRES_PACKINGS": ((3, 0),)}, two, True),
        ("sheet side at its minimum", {}, lambda: [blank_row(1, 3, 3, 3)], False),  # 4 or 6 texels: 8-texel sheets
        ("sheet side under its minimum", {}, lambda: [blank_row(1, 1, 1, 1)], True),  # 2x: 4 texels, a 4-texel sheet
        ("sheet side at its maximum", {}, lambda: [blank_row(1, 1360)], False),  # 3x: 4080 texels, a 4096 sheet
        ("sheet side over its maximum", {"UV_CAP": 8191}, lambda: [blank_row(1, 1366)], True),  # 3x: 4098 texels
    ]
    failures = []
    for condition, values, rows, refused in cases:
        with monkeypatch.context() as patch:
            for name, value in values.items():
                patch.setattr(hires, name, value)
            try:
                hires_bank(rows())
                got = "written"
            except HiresError:
                got = "refused"
            except Exception as error:  # a crash is a failing row too, not the end of the table
                got = repr(error)
        if got != ("refused" if refused else "written"):
            failures.append((condition, got))
    assert not failures, failures
