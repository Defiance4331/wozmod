from __future__ import annotations

import struct

import pytest
from PIL import Image

from wozmod.art.hires import HIRES_BANKS, HIRES_OVERLAY


def parse_bank(blob: bytes) -> tuple[list[tuple], list[tuple[int, int]], list[list[tuple[int, int]]], list[list]]:
    """The packed bank as the runtime reads it: rows; each packing's scale and level count; each packing's sheet and
    origin per row; and per sheet each level's width, height and blocks."""
    assert blob[:4] == b"HRS3"
    rows, banks, sheets = struct.unpack_from("<3I", blob, 4)
    at = 16
    records = [struct.unpack_from("<7I", blob, at + 28 * i) for i in range(rows)]
    at += 28 * rows
    packings = [(v & 0xFF, v >> 8) for v in struct.unpack_from(f"<{banks}I", blob, at)]
    at += 4 * banks
    places = [[struct.unpack_from("<2I", blob, at + 8 * (b * rows + i)) for i in range(rows)] for b in range(banks)]
    at += 8 * banks * rows
    levels_of = []
    for s in range(sheets):
        shape, offset = struct.unpack_from("<2I", blob, at + 8 * s)
        w, h, count = 1 << (shape & 0xFF), 1 << (shape >> 8 & 0xFF), shape >> 16
        levels = []
        for k in range(count):
            size = (w >> k) * (h >> k)
            levels.append((w >> k, h >> k, blob[offset:offset + size]))
            offset += size
        levels_of.append(levels)
    return records, packings, places, levels_of


def bank_rows(blob: bytes, bank: int = 0) -> dict[tuple[int, int], tuple[tuple, bytes]]:
    """Each row's record with its placement in one packing, and the level-0 blocks of its image there, read at its
    origin (the image size from the row's fields and the packing's scale)."""
    records, packings, places, sheets = parse_bank(blob)
    scale, levels = packings[bank]
    step = 4 << (levels - 1)
    out = {}
    for record, (sheet, origin) in zip(records, places[bank], strict=True):
        band, per_col = record[5] & 0xFFFF, record[6]
        cols = -(-(record[4] // band) // per_col) if per_col else 1
        w = -(-cols * record[3] * scale // step) * step
        h = -(-(per_col * band if per_col else record[4]) * scale // step) * step
        sw, _, data = sheets[sheet][0]
        x, y = origin & 0xFFFF, origin >> 16
        out[record[0], record[1]] = (*record, sheet, origin), b"".join(
            data[(y // 4 + r) * sw * 4 + x * 4:(y // 4 + r) * sw * 4 + (x + w) * 4] for r in range(h // 4))
    return out


def test_hires_sheet_does_not_swap_band_rows_and_columns():
    from wozmod.art.hires import hires_sheet

    art = Image.new("RGBA", (8, 128))
    for b in range(8):
        art.paste((b + 1, 0, 0, 255), (0, b * 16, 8, b * 16 + 16))
    sheet = hires_sheet(art, 4, 8, 2, 2, 2)
    assert sheet.size == (32, 32)
    assert [sheet.getpixel(((b // 2) * 8, (b % 2) * 16))[0] for b in range(8)] == list(range(1, 9))
    assert sheet.getpixel((16, 31)) == (6, 0, 0, 255)


def test_packed_bank_returns_every_rows_blocks_at_its_origin_in_every_packing_and_level():
    import random

    from wozmod.art.hires import hires_bank, hires_extent

    rng = random.Random(7)
    # wide rows force a second shelf, tall ones a second sheet; a banded row checks the column layout
    shapes = [(1000, 32, 32, 0), (1000, 32, 32, 0), (300, 64, 64, 0), (1000, 1000, 56, 0), (1000, 1000, 56, 0),
              (128, 32, 32, 0), (256, 672, 42, 0), (512, 1596, 42, 19)]
    rows, wanted = [], []
    for n, (w, h, band, per_col) in enumerate(shapes):
        blocks, sizes = [], []
        for scale, levels in HIRES_BANKS:
            ew, eh = hires_extent(scale, w, h, band, per_col, levels)
            blocks.append([rng.randbytes((ew >> k) * (eh >> k)) for k in range(levels)])
            sizes.append((ew, eh))
        rows.append((4000 + n, n, 3 | HIRES_OVERLAY * (n == 2), w, h, band | (n << 16), per_col, blocks))
        wanted.append(sizes)
    records, packings, places, sheets = parse_bank(hires_bank(rows))
    assert packings == list(HIRES_BANKS)
    assert len({places[0][i][0] for i in range(len(rows))}) > 1
    failures = []
    for i, (row, record, sizes) in enumerate(zip(rows, records, wanted, strict=True)):
        if record != row[:7]:
            failures.append((row[:2], "fields", record))
        for bank, (ew, eh) in enumerate(sizes):
            sheet, origin = places[bank][i]
            x, y = origin & 0xFFFF, origin >> 16
            if x + ew > 4095 or y + eh > 4095:
                failures.append((row[:2], bank, "past the UV cap", x, y))
            if {places[b][j][0] for b in range(len(sizes)) if b != bank for j in range(len(rows))} & {sheet}:
                failures.append((row[:2], bank, "shares a sheet with another packing"))
            for k, blocks in enumerate(row[7][bank]):
                sw, _, data = sheets[sheet][k]
                pitch, width = sw * 4, (ew >> k) * 4  # bytes per row of 4x4 blocks
                at = (y >> k) // 4 * pitch + (x >> k) * 4
                got = b"".join(data[at + r * pitch:at + r * pitch + width] for r in range((eh >> k) // 4))
                if got != blocks:
                    failures.append((row[:2], bank, "level", k))
    assert not failures, failures


def test_bank_takes_the_blocks_of_a_row_whose_height_is_not_whole_bands():
    """Bug: the sheet image sized from the whole bands (245 of 256 rows) while the bank expects all 256, so the build
    stops on a stock texture like 4079.43."""
    from wozmod.art.hires import HIRES_SCALE, hires_bank, hires_blocks

    art = Image.new("RGBA", (16 * HIRES_SCALE, 256 * HIRES_SCALE), (200, 200, 200, 255))
    _, packings, places, _ = parse_bank(hires_bank([(1, 2, HIRES_SCALE, 16, 256, 35, 0, hires_blocks(art, 16, 35, 0))]))
    assert len(places) == len(packings) == len(HIRES_BANKS)


def test_packed_bank_refuses_levels_of_the_wrong_size_and_rows_past_the_uv_cap():
    """Blocks of another size would be spliced over a neighbour; a row past 4095 texels cannot be addressed."""
    from wozmod.art.hires import hires_bank
    from wozmod.runtime.english_runtime import RuntimeBuildError

    # 8x8 stock texels: 24x24 at 3x, then 12x12; 16x16 at 2x
    with pytest.raises(RuntimeBuildError, match="levels of"):
        hires_bank([(13, 5, 3, 8, 8, 8, 0, [[bytes(24 * 24), bytes(16 * 16)], [bytes(16 * 16)]])])
    with pytest.raises(RuntimeBuildError, match="levels of"):
        hires_bank([(13, 5, 3, 8, 8, 8, 0, [[bytes(24 * 24), bytes(12 * 12)], [bytes(24 * 24)]])])
    with pytest.raises(RuntimeBuildError, match="UV cap"):
        hires_bank([(13, 5, 3, 1366, 8, 8, 0, [[bytes(4104 * 24), bytes(2052 * 12)], [bytes(2736 * 16)]])])
