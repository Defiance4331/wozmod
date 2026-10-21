"""i386 PE sections, file offsets and pointer-table strings."""

from __future__ import annotations

import struct

import pytest

from synthetic import pe_image
from wozmod.formats import pe


def test_optional_header_size_does_not_shift_section_table() -> None:
    """Bug: the section table is placed after a fixed-size optional header, so every section reads shifted."""
    image = pe_image(0x800, (b".first", 0x280, 0x1000, 0x200, 0x400), (b".second", 0x40, 0x2000, 0x100, 0x600),
                     pe_at=0x88, optional_size=0x68)
    assert pe.layout(bytes(image)) == {
        "image_base": 0x400000,
        "sections": [
            {"name": b".first", "vsize": 0x280, "rva": 0x1000, "raw_size": 0x200, "raw": 0x400},
            {"name": b".second", "vsize": 0x40, "rva": 0x2000, "raw_size": 0x100, "raw": 0x600},
        ],
    }


def test_file_offsets_use_raw_size_and_refuse_the_virtual_tail() -> None:
    """Bug: the lookup bounds a section by vsize, so a VA in its zero-filled tail reads the next section's bytes."""
    layout = {
        "image_base": 0x400000,
        "sections": [
            {"rva": 0x1000, "raw_size": 0x200, "vsize": 0x280, "raw": 0x400},
            {"rva": 0x2000, "raw_size": 0x100, "vsize": 0x40, "raw": 0x600},
        ],
    }
    assert [pe.file_offset(layout, va) for va in [0x401000, 0x4011FF, 0x4020FF]] == [0x400, 0x5FF, 0x6FF]
    for va in (0x400FFF, 0x401200, 0x402100):
        with pytest.raises(pe.PEError, match="not file-backed"):
            pe.file_offset(layout, va)


@pytest.mark.parametrize("machine, signature", [(0x8664, b"PE\0\0"), (0x14C, b"PX\0\0")])
def test_non_i386_or_bad_signature_cannot_be_used_as_stock_pe(machine: int, signature: bytes) -> None:
    """Bug: a 64-bit or damaged image is read with the i386 header layout, so every address lands on wrong bytes."""
    image = pe_image(0x800, (b".first", 0x280, 0x1000, 0x200, 0x400))
    struct.pack_into("<4sH", image, 0x80, signature, machine)
    with pytest.raises(pe.PEError, match="not an i386 PE image"):
        pe.layout(bytes(image))


def test_pointer_tables_resolve_each_entry_through_the_strings_own_section() -> None:
    """Bug: a wrong stride, a dropped last entry, or a pointer mapped through the table's section returns the wrong
    string; the stock name table (stride 8) and message table (stride 4) are walked by this one function."""
    image = pe_image(0x800, (b".data", 0x200, 0x1000, 0x200, 0x200), (b".rdata", 0x300, 0x3000, 0x200, 0x500))
    strings = {}
    at = 0x500
    for value in (b"zero", b"one", b"two", b"decoy"):
        strings[value] = 0x403000 + at - 0x500
        image[at:at + len(value) + 1] = value + b"\0"
        at += len(value) + 1
    struct.pack_into("<6I", image, 0x200, *(strings[v] for v in (b"zero", b"decoy", b"one", b"decoy", b"two", b"decoy")))
    struct.pack_into("<3I", image, 0x300, *(strings[v] for v in (b"two", b"zero", b"one")))
    assert pe.pointer_strings(bytes(image), 0x401000, 3, 8) == [b"zero", b"one", b"two"]
    assert pe.pointer_strings(bytes(image), 0x401100, 3, 4) == [b"two", b"zero", b"one"]
