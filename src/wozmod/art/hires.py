"""Serialize runtime texture replacement banks."""

import struct

from wozmod.runtime.english_runtime import _require

HIRES_ROW = struct.Struct("<9I")
HIRES_OVERLAY = 0x80000000  # scale flag: native plate first, vector lettering over it
HIRES_RIGHT_GROW = 0x40000000  # upper band bits add width, preserving an unresolved help right edge
HIRES_SCALE_MASK = 0x3FFFFFFF
HIRES_BAND_MASK = 0xFFFF  # upper bits hold a minimum width, or additive growth with RIGHT_GROW
UV_CAP = 4095  # the quad UV field holds 12 texel bits: every far edge stays at or below it


def hires_blob(rows: list[tuple[int, int, int, int, int, int, int, bytes]]) -> bytes:








    from wozmod.formats.textures import parse_g1t

    def pow2(n: int) -> int:
        return 1 << (n - 1).bit_length()

    header, blobs = [], bytearray()
    for entry, item, scale, stock_w, stock_h, band_h, bands_per_col, g1t in rows:
        name = f"hi-res {entry}.{item}"
        stored_scale, stored_band = scale, band_h
        scale &= HIRES_SCALE_MASK
        band_h &= HIRES_BAND_MASK
        _require(scale >= 2, f"{name}: scale {scale} is not an enlargement")
        texture = parse_g1t(g1t).textures[0]
        _require(texture.mipmap_count == 1, f"{name}: a sheet is single-mip")
        if bands_per_col:
            _require(band_h and stock_h % band_h == 0, f"{name}: {stock_h} rows are not whole {band_h} bands")
            cols = -(-(stock_h // band_h) // bands_per_col)
            col_h = bands_per_col * band_h
        else:
            cols, col_h = 1, stock_h
        _require(cols * stock_w * scale <= UV_CAP and col_h * scale <= UV_CAP,
                 f"{name}: {cols * stock_w * scale}x{col_h * scale} texels pass the UV cap {UV_CAP}")
        want = (pow2(cols * stock_w * scale), pow2(col_h * scale))
        _require((texture.width, texture.height) == want,
                 f"{name}: sheet is {texture.width}x{texture.height}, not {want[0]}x{want[1]}")
        header.append((entry, item, stored_scale, stock_w, stock_h, stored_band, bands_per_col, len(blobs), len(g1t)))
        blobs += g1t + bytes(-len(g1t) % 16)
    base = -(-(4 + HIRES_ROW.size * len(header)) // 16) * 16  # sheets 16-aligned
    table = struct.pack("<I", len(header)) + b"".join(HIRES_ROW.pack(*r[:7], base + r[7], r[8]) for r in header)
    return table + bytes(base - len(table)) + bytes(blobs)

