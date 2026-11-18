"""Sections, file offsets, exports and pointer-table strings of an i386 PE image."""

from __future__ import annotations

import struct


class PEError(ValueError):
    """Not an i386 PE image, or an address with no bytes in the file."""


def _unpack(fmt: str, image, at: int) -> tuple:
    """struct.unpack_from through a slice, so the image can be the stock exe's StockBytes (game_files), which serve
    spans and no buffer."""
    return struct.unpack(fmt, image[at:at + struct.calcsize(fmt)])


def sections(header: bytes) -> list[dict]:
    """Name, vsize, rva, raw_size and raw of each section; the table follows the optional header's own size."""
    pe = _unpack("<I", header, 0x3C)[0]
    if header[pe:pe + 4] != b"PE\0\0" or _unpack("<H", header, pe + 4)[0] != 0x14C:
        raise PEError("not an i386 PE image")
    table = pe + 24 + _unpack("<H", header, pe + 20)[0]
    found = []
    for i in range(_unpack("<H", header, pe + 6)[0]):
        name, vsize, rva, raw_size, raw = _unpack("<8sIIII", header, table + 40 * i)
        found.append({"name": name.rstrip(b"\0"), "vsize": vsize, "rva": rva, "raw_size": raw_size, "raw": raw})
    return found


def build_stamp(header: bytes) -> tuple[int, int]:
    """The image's TimeDateStamp (the linker's build time, Unix seconds) and SizeOfImage, from its headers."""
    pe = _unpack("<I", header, 0x3C)[0]
    if header[pe:pe + 4] != b"PE\0\0" or _unpack("<H", header, pe + 4)[0] != 0x14C:
        raise PEError("not an i386 PE image")
    return _unpack("<I", header, pe + 8)[0], _unpack("<I", header, pe + 24 + 56)[0]


def layout(image: bytes) -> dict:
    """The sections and image base that file_offset maps a VA through."""
    found = sections(image)
    optional = _unpack("<I", image, 0x3C)[0] + 24
    return {"sections": found, "image_base": _unpack("<I", image, optional + 28)[0]}


def file_offset(layout: dict, va: int) -> int:
    """File offset of the byte at ``va``; only a section's raw bytes count, never its zero-filled virtual tail."""
    rva = va - layout["image_base"]
    for s in layout["sections"]:
        if s["rva"] <= rva < s["rva"] + s["raw_size"]:
            return rva - s["rva"] + s["raw"]
    raise PEError(f"VA {va:#x} is not file-backed")


def pointer_strings(image: bytes, table_va: int, count: int, stride: int) -> list[bytes]:
    """The NUL-ended string behind each of ``count`` pointers, ``stride`` bytes apart from ``table_va``."""
    where = layout(image)
    found = []
    for i in range(count):
        at = file_offset(where, _unpack("<I", image, file_offset(where, table_va + stride * i))[0])
        found.append(image[at:image.index(b"\0", at)])
    return found


def exports(image: bytes) -> dict[str, int]:
    """Each exported name's VA, from the export directory (the optional header's first data directory)."""
    where = layout(image)
    base = where["image_base"]
    directory = _unpack("<I", image, _unpack("<I", image, 0x3C)[0] + 24 + 96)[0]
    if not directory:
        return {}
    at = file_offset(where, base + directory)
    names, functions, name_table, ordinals = (_unpack("<I", image, at + offset)[0]
                                              for offset in (24, 28, 32, 36))
    found = {}
    for i in range(names):
        name_at = file_offset(where, base + _unpack("<I", image, file_offset(where, base + name_table + 4 * i))[0])
        ordinal = _unpack("<H", image, file_offset(where, base + ordinals + 2 * i))[0]
        rva = _unpack("<I", image, file_offset(where, base + functions + 4 * ordinal))[0]
        found[image[name_at:image.index(b"\0", name_at)].decode("ascii")] = base + rva
    return found
