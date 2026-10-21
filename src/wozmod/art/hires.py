"""Write and read hires.bin, the runtime's bank of hi-res lettering."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wozmod.formats.textures import encode_dxt5
from wozmod.runtime.layout import DEFINES

if TYPE_CHECKING:  # Pillow stays out of the import: the font tools import this module without it
    from PIL import Image

HIRES_SCALE = 3  # sheet texels per stock texel: 1:1 on a 2160-high screen

# The row flags and the limits past which the runtime refuses the whole bank, as runtime/english_runtime.c has them.
HIRES_OVERLAY = DEFINES["HIRES_OVERLAY"]  # scale flag: native plate first, vector lettering over it
HIRES_RIGHT_GROW = DEFINES["HIRES_RIGHT_GROW"]  # the minimum is growth, preserving an unresolved help right edge
HIRES_SCALE_MASK = DEFINES["HIRES_SCALE_MASK"]
HIRES_MAX, HIRES_SHEETS, HIRES_BANKS = DEFINES["HIRES_MAX"], DEFINES["HIRES_SHEETS"], DEFINES["HIRES_BANKS"]
HIRES_SCALE_MIN, HIRES_SCALE_MAX = DEFINES["HIRES_SCALE_MIN"], DEFINES["HIRES_SCALE_MAX"]
HIRES_LEVELS = DEFINES["HIRES_LEVELS"]
HIRES_SIDE_MIN_LOG2, HIRES_SIDE_MAX_LOG2 = DEFINES["HIRES_SIDE_MIN_LOG2"], DEFINES["HIRES_SIDE_MAX_LOG2"]
HIRES_BAND_MASK = 0xFFFF  # the band field's low half; its high half holds the minimum
UV_CAP = 4095  # the quad UV field holds 12 texel bits: every far edge stays at or below it


# The packed bank. All lettering is drawn once, at HIRES_SCALE; from it the bank holds packings (HIRES_PACKINGS), each
# at its own size and with its own levels, each level half the one before. In a packing every row's sheet image sits in
# a shared DXT5 sheet of at most 4096 texels a side, beside the others. The runtime creates the sheets of the one
# packing and level a display draws from, so a screen size can take whichever stays sharpest. Layout, all u32: magic,
# row count, packing count, sheet count; per row entry, item, scale (with flags), w, h, band | minimum << 16, per_col;
# per packing scale | levels << 8; per packing and row the sheet (an index into the sheet table) and the origin
# (x | y << 16, level-0 texels); per sheet log2 w | log2 h << 8 | levels << 16, then the offset of its level 0, with
# each next level right after the one before.
HIRES_MAGIC = b"HRS3"
HIRES_PACKINGS = ((3, 2), (2, 1))  # texels per stock texel and levels: 3x and 1.5x, and 2x for the sizes they miss
HIRES_ROW = struct.Struct("<7I")
HIRES_PLACE = struct.Struct("<2I")
HIRES_SHEET = struct.Struct("<2I")
GUTTER = 8  # transparent texels between images: bilinear taps at level 1 stay inside a 4-texel band of it


class HiresError(ValueError):
    """A hi-res row or bank the runtime cannot draw, or would refuse."""


@dataclass(frozen=True)
class HiresRow:
    """One stock texture's lettering in the bank: its size in stock texels (w, h; band rows of per_col to a column),
    and per packing its sheet image's DXT5 blocks, level by level (hires_blocks). overlay: the native plate is drawn
    first and the lettering over it. minimum: the narrowest the quad may be; with right_grow, width added on the
    right instead."""
    entry: int
    item: int
    w: int
    h: int
    band: int
    per_col: int
    blocks: list[list[bytes]]
    overlay: bool = False
    right_grow: bool = False
    minimum: int = 0


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise HiresError(reason)


def _within_limits(rows: int, packings, sheets) -> None:
    """Raise HiresError for each way the runtime refuses a bank whole: too many rows, packings or sheets, a packing's
    scale or levels out of bounds, or a sheet's side out of bounds. packings: (scale, levels); sheets: (log2 w,
    log2 h). Each sheet has its packing's levels, so the packing's bound covers the sheet's."""
    _require(rows <= HIRES_MAX and 0 < len(packings) <= HIRES_BANKS and len(sheets) <= HIRES_SHEETS,
             f"hi-res bank of {rows} rows, {len(packings)} packings and {len(sheets)} sheets: the runtime refuses the "
             f"whole bank past {HIRES_MAX} rows, {HIRES_BANKS} packings or {HIRES_SHEETS} sheets")
    for scale, levels in packings:
        _require(HIRES_SCALE_MIN <= scale <= HIRES_SCALE_MAX and 0 < levels <= HIRES_LEVELS,
                 f"hi-res packing at {scale}x with {levels} levels: the runtime refuses the whole bank outside "
                 f"{HIRES_SCALE_MIN}x to {HIRES_SCALE_MAX}x and 1 to {HIRES_LEVELS} levels")
    for w, h in sheets:
        _require(HIRES_SIDE_MIN_LOG2 <= min(w, h) and max(w, h) <= HIRES_SIDE_MAX_LOG2,
                 f"hi-res sheet of {1 << w}x{1 << h} texels: the runtime refuses the whole bank outside "
                 f"{1 << HIRES_SIDE_MIN_LOG2} to {1 << HIRES_SIDE_MAX_LOG2} texels a side")


def hires_extent(scale: int, w: int, h: int, band: int, per_col: int, levels: int) -> tuple[int, int]:
    """A row's sheet image size in level-0 texels: band columns side by side, rounded so that every level is whole
    DXT blocks and every image starts on a block of every level."""
    if per_col:
        _require(band and h % band == 0, f"{h} rows are not whole {band} bands")
        cols, col_h = -(-(h // band) // per_col), per_col * band
    else:
        cols, col_h = 1, h
    step = 4 << (levels - 1)
    return -(-cols * w * scale // step) * step, -(-col_h * scale // step) * step


def hires_sheet(art: Image.Image, stock_w: int, band_h: int, per_col: int, scale: int, levels: int) -> Image.Image:
    """A row's art laid out as its sheet image: bands in columns of per_col, at the size hires_extent gives."""
    from PIL import Image as pillow

    bands = art.height // (band_h * scale)  # rows past the last whole band stay transparent
    canvas = pillow.new("RGBA", hires_extent(scale, stock_w, art.height // scale, band_h, per_col, levels))
    per_col = per_col or bands
    for b in range(bands):
        row = art.crop((0, b * band_h * scale, art.width, (b + 1) * band_h * scale))
        canvas.paste(row, ((b // per_col) * stock_w * scale, (b % per_col) * band_h * scale))
    return canvas


def hires_levels(image: Image.Image, levels: int) -> list[bytes]:
    """The DXT5 blocks of each level of a sheet image, level 0 first. Each level is level 0 resized with Lanczos
    (Pillow weights colour by alpha, so no dark fringe): in the offline filtering model a box-halved level lost 3 to 5
    times more against the ideal at 1080 high than a Lanczos one."""
    from PIL import Image as pillow

    out = []
    for k in range(levels):
        level = image.resize((image.width >> k, image.height >> k), pillow.Resampling.LANCZOS) if k else image
        out.append(encode_dxt5(level.tobytes(), *level.size))
    return out


def hires_blocks(art: Image.Image, stock_w: int, band_h: int, per_col: int) -> list[list[bytes]]:
    """A row's blocks in every packing, level by level, from its art drawn at HIRES_SCALE (stock size times it)."""
    from PIL import Image as pillow

    out = []
    for scale, levels in HIRES_PACKINGS:
        size = (art.width * scale // HIRES_SCALE, art.height * scale // HIRES_SCALE)
        scaled = art if scale == HIRES_SCALE else art.resize(size, pillow.Resampling.LANCZOS)
        out.append(hires_levels(hires_sheet(scaled, stock_w, band_h, per_col, scale, levels), levels))
    return out


def _fit(sky: list[list[int]], w: int, h: int) -> tuple[int, int, int] | None:
    """The lowest, then leftmost, place on a skyline (segments x, top y, width) for a w x h image and its gutter."""
    best = None
    for i, (x, _, _) in enumerate(sky):
        if x + w > UV_CAP:
            break
        y, j, reach = 0, i, x
        while reach < x + w + GUTTER and j < len(sky):
            y, reach, j = max(y, sky[j][1]), sky[j][0] + sky[j][2], j + 1
        if reach >= x + w + GUTTER and y + h <= UV_CAP and (best is None or (y, x) < best[:2]):
            best = (y, x, i)
    return best


def _pack(sizes: list[tuple[int, int]]) -> tuple[list[tuple[int, int, int]], list[tuple[int, int]]]:
    """Skyline packing, tallest first, into the first sheet with room: each image's sheet and origin, and each
    sheet's used width and height. Far edges stay within the UV cap, so the last texel row and column of a 4096 sheet
    stay empty."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0], i))
    places: list = [None] * len(sizes)
    skies: list[list[list[int]]] = []
    used: list[list[int]] = []
    for i in order:
        w, h = sizes[i]
        for sheet, sky in enumerate(skies):
            spot = _fit(sky, w, h)
            if spot:
                break
        else:
            skies.append([[0, 0, UV_CAP + 1 + GUTTER]])
            used.append([0, 0])
            sheet, sky = len(skies) - 1, skies[-1]
            spot = _fit(sky, w, h)
        y, x, at = spot
        places[i] = (sheet, x, y)
        used[sheet] = [max(used[sheet][0], x + w), max(used[sheet][1], y + h)]
        right = x + w + GUTTER  # the image and its gutter become one segment; what they cover is cut away
        rest = [[max(sx, right), sy, sx + sw - max(sx, right)] for sx, sy, sw in sky[at:] if sx + sw > right]
        sky[at:] = [[x, y + h + GUTTER, w + GUTTER]] + rest
    return places, [(u[0], u[1]) for u in used]


def hires_bank(rows: list[HiresRow]) -> bytes:
    """The bank file of rows, in their order. Raises HiresError for a row whose blocks are not its image at every
    level of every packing or whose image passes the UV cap, and for a bank past the runtime's limits."""
    _within_limits(len(rows), HIRES_PACKINGS, [])  # first: the image sizes below need levels in bounds
    fields = []
    for row in rows:
        scale = HIRES_SCALE | HIRES_OVERLAY * row.overlay | HIRES_RIGHT_GROW * row.right_grow
        fields.append(HIRES_ROW.pack(row.entry, row.item, scale, row.w, row.h, row.band | row.minimum << 16,
                                     row.per_col))
    head = HIRES_MAGIC + struct.pack("<3I", len(rows), len(HIRES_PACKINGS), 0) + b"".join(fields)
    head += b"".join(struct.pack("<I", scale | levels << 8) for scale, levels in HIRES_PACKINGS)
    places, dims, sheets = [], [], []
    for bank, (scale, levels) in enumerate(HIRES_PACKINGS):
        sizes = []
        for row in rows:
            name = f"hi-res {row.entry}.{row.item} at {scale}x"
            size = hires_extent(scale, row.w, row.h, row.band, row.per_col, levels)
            _require(size[0] <= UV_CAP and size[1] <= UV_CAP,
                     f"{name}: {size[0]}x{size[1]} texels pass the UV cap {UV_CAP}")
            want = [(size[0] >> k) * (size[1] >> k) for k in range(levels)]  # DXT5: a byte per texel
            got = [len(b) for b in row.blocks[bank]]
            _require(got == want, f"{name}: levels of {got} bytes, not {want}")
            sizes.append(size)
        spots, used = _pack(sizes)
        first = len(dims)
        bank_dims = [tuple(1 << max(n - 1, 0).bit_length() for n in u) for u in used]
        bank_sheets = [[bytearray((w >> k) * (h >> k)) for k in range(levels)] for w, h in bank_dims]  # transparent
        for (sheet, x, y), (w, h), row in zip(spots, sizes, rows, strict=True):
            for k, blocks in enumerate(row.blocks[bank]):
                pitch, row_pitch = (bank_dims[sheet][0] >> k) * 4, (w >> k) * 4  # bytes per row of 4x4 blocks
                at = (y >> k) // 4 * pitch + (x >> k) * 4
                for by in range((h >> k) // 4):
                    bank_sheets[sheet][k][at + by * pitch:at + by * pitch + row_pitch] = \
                        blocks[by * row_pitch:(by + 1) * row_pitch]
            places.append(HIRES_PLACE.pack(first + sheet, x | y << 16))
        dims += [(w, h, levels) for w, h in bank_dims]
        sheets += bank_sheets
    _within_limits(len(rows), HIRES_PACKINGS, [(w.bit_length() - 1, h.bit_length() - 1) for w, h, _ in dims])
    head = head[:12] + struct.pack("<I", len(dims)) + head[16:] + b"".join(places)
    base = -(-(len(head) + HIRES_SHEET.size * len(dims)) // 16) * 16
    table, data = [], bytearray()
    for (w, h, levels), parts in zip(dims, sheets, strict=True):
        table.append(HIRES_SHEET.pack((w.bit_length() - 1) | (h.bit_length() - 1) << 8 | levels << 16,
                                      base + len(data)))
        data += b"".join(parts)
    head += b"".join(table)
    return head + bytes(base - len(head)) + bytes(data)


def read_hires_bank(blob: bytes) -> list[HiresRow]:
    """The rows of a bank file, each with its blocks read back from its sheets: the rows hires_bank wrote it from.
    Raises HiresError where the runtime refuses the bank or a row: past its limits, a row drawn at another scale, an
    image outside its sheet or past the UV cap, or a sheet that two packings share."""
    _require(blob[:4] == HIRES_MAGIC, f"not a hi-res bank: magic {blob[:4]!r}")
    count, banks, sheets = struct.unpack_from("<3I", blob, 4)
    _require(count <= HIRES_MAX and banks <= HIRES_BANKS and sheets <= HIRES_SHEETS,
             f"hi-res bank of {count} rows, {banks} packings and {sheets} sheets is past the runtime's limits")
    at = 16 + HIRES_ROW.size * count
    packings = [(v & 0xFF, v >> 8) for v in struct.unpack_from(f"<{banks}I", blob, at)]
    at += 4 * banks
    places = [[HIRES_PLACE.unpack_from(blob, at + HIRES_PLACE.size * (b * count + i)) for i in range(count)]
              for b in range(banks)]
    at += HIRES_PLACE.size * banks * count
    table = [HIRES_SHEET.unpack_from(blob, at + HIRES_SHEET.size * s) for s in range(sheets)]
    _within_limits(count, packings, [(shape & 0xFF, shape >> 8 & 0xFF) for shape, _ in table])
    owner: dict[int, int] = {}
    rows = []
    for i in range(count):
        entry, item, scale, w, h, band, per_col = HIRES_ROW.unpack_from(blob, 16 + HIRES_ROW.size * i)
        name = f"hi-res {entry}.{item}"
        _require(scale & HIRES_SCALE_MASK == HIRES_SCALE,
                 f"{name}: drawn at {scale & HIRES_SCALE_MASK}x, not {HIRES_SCALE}x")
        blocks = []
        for bank, (bank_scale, levels) in enumerate(packings):
            sheet, origin = places[bank][i]
            _require(sheet < sheets and owner.setdefault(sheet, bank) == bank,
                     f"{name} at {bank_scale}x: sheet {sheet} is not one of this packing's")
            shape, offset = table[sheet]
            sheet_w, sheet_h, sheet_levels = 1 << (shape & 0xFF), 1 << (shape >> 8 & 0xFF), shape >> 16
            x, y = origin & 0xFFFF, origin >> 16
            w_k, h_k = hires_extent(bank_scale, w, h, band & HIRES_BAND_MASK, per_col, levels)
            _require(x + w_k <= min(sheet_w, UV_CAP) and y + h_k <= min(sheet_h, UV_CAP) and sheet_levels >= levels,
                     f"{name} at {bank_scale}x: {w_k}x{h_k} texels at {x},{y} leave sheet {sheet}")
            row_levels = []
            for k in range(levels):
                pitch, width = (sheet_w >> k) * 4, (w_k >> k) * 4  # bytes per row of 4x4 blocks
                start = offset + (y >> k) // 4 * pitch + (x >> k) * 4
                row_levels.append(b"".join(blob[start + r * pitch:start + r * pitch + width]
                                           for r in range((h_k >> k) // 4)))
                offset += (sheet_w >> k) * (sheet_h >> k)
            blocks.append(row_levels)
        rows.append(HiresRow(entry, item, w, h, band & HIRES_BAND_MASK, per_col, blocks,
                             overlay=bool(scale & HIRES_OVERLAY), right_grow=bool(scale & HIRES_RIGHT_GROW),
                             minimum=band >> 16))
    return rows
