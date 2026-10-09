from __future__ import annotations

import struct

import pytest

from wozmod.art.hires import HIRES_OVERLAY, HIRES_RIGHT_GROW, hires_blob
from wozmod.runtime.english_runtime import RuntimeBuildError


def sheet(width: int, height: int) -> bytes:
    payload = bytes(max(width // 4, 1) * max(height // 4, 1) * 8)
    dimensions = (width.bit_length() - 1) | (height.bit_length() - 1) << 4
    size = 32 + 4 + 8 + len(payload)
    return (struct.pack("<4s4s6I", b"GT1G", b"0000", size, 32, 1, 0, 0, 0)
            + struct.pack("<I", 4)
            + struct.pack("<4BI", 0x10, 6, dimensions, 0, 0x11111) + payload)


def test_hires_bank_does_not_shift_second_sheet_into_first_padding():
    first, second = sheet(4, 4), sheet(8, 4)
    blob = hires_blob([(10, 2, 2, 2, 2, 0, 0, first), (11, 3, 2, 4, 2, 0, 0, second)])
    expected = (struct.pack("<I", 2)
                + struct.pack("<9I", 10, 2, 2, 2, 2, 0, 0, 80, 52)
                + struct.pack("<9I", 11, 3, 2, 4, 2, 0, 0, 144, 60)
                + bytes(4) + first + bytes(12) + second + bytes(4))
    assert blob == expected


def test_hires_flags_cannot_be_counted_as_scale_or_band_height():
    failures = []
    for flag, width, sheet_width in ((HIRES_OVERLAY, 128, 256), (HIRES_RIGHT_GROW, 1204, 4096)):
        texture = sheet(sheet_width, 64)
        blob = hires_blob([(12, 4, flag | 2, width, 32, 32 | (120 << 16), 0, texture)])
        expected = (12, 4, flag | 2, width, 32, 32 | (120 << 16), 0)
        if struct.unpack_from("<7I", blob, 4) != expected:
            failures.append(flag)
    assert not failures, failures


def test_hires_repacking_cannot_accept_an_overflowing_or_wrong_sized_sheet():
    with pytest.raises(RuntimeBuildError, match="UV cap"):
        hires_blob([(13, 5, 2, 2, 2048, 32, 0, sheet(4, 4096))])
    with pytest.raises(RuntimeBuildError, match="sheet is 4x4096"):
        hires_blob([(13, 5, 2, 2, 2048, 32, 32, sheet(4, 4096))])
    texture = sheet(8, 2048)
    blob = hires_blob([(13, 5, 2, 2, 2048, 32, 32, texture)])
    assert struct.unpack_from("<7I", blob, 4) == (13, 5, 2, 2, 2048, 32, 32)
