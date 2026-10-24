"""Game-format bytes built from the format description, for tests: string tables, scripts, MESC blocks, archives, PE images."""

from __future__ import annotations

import struct
from pathlib import Path

SECTOR = 2048


def strings(*lines: bytes, order: str = "<") -> bytes:
    """A counted table of offsets, then each line NUL-terminated."""
    offsets, body = [], b""
    for line in lines:
        offsets.append(4 + 4 * len(lines) + len(body))
        body += line + b"\0"
    return struct.pack(f"{order}{len(lines) + 1}I", len(lines), *offsets) + body


def spans(parts, head: bytes = b"", order: str = "<") -> bytes:
    """`head`, an (offset, size) pair per part, then the parts."""
    at, table = len(head) + 8 * len(parts), b""
    for part in parts:
        table += struct.pack(f"{order}II", at, len(part))
        at += len(part)
    return head + table + b"".join(parts)


def script(notices: bytes, dialog: bytes, order: str = "<") -> bytes:
    """A stage script: four section offsets, a filler section, an empty speak-command section (u32 count 0 and two
    u32), the notice table padded to 4, the dialog table."""
    sections = [b"HEAD", struct.pack(f"{order}3I", 0, 0, 0), notices + bytes(-len(notices) % 4), dialog]
    offsets = [20 + sum(map(len, sections[:i])) for i in range(4)]
    return struct.pack(f"{order}5I", 4, *offsets) + b"".join(sections)


def mesc(*lines: bytes) -> bytes:
    """A MESC block: count, total size, an offset per line, then each line NUL-terminated."""
    offsets, body = [], b""
    for line in lines:
        offsets.append(12 + 4 * len(lines) + len(body))
        body += line + b"\0"
    return b"MESC" + struct.pack(f"<II{len(lines)}I", len(lines), 12 + 4 * len(lines) + len(body), *offsets) + body


def event_file(*scenes: list[tuple[int, int, bytes]], order: str = "<", gap: int = 0) -> tuple[bytes, list[int]]:
    """An event file of sub-scenes, each a chain of (type, kind, text) records with one u32 field, the offset of a
    0xFFFFFFFF mask and the text, from the sub-scene start; gap zero bytes sit before each sub-scene. Returns the
    file and the file offset of each text, in order."""
    head = 4 + 4 * len(scenes)
    body, bases, texts = b"", [], []
    for records in scenes:
        body += bytes(gap)
        base, chain, tail = head + len(body), b"", b""
        start = 16 * len(records) + 8  # the records, then the 0xFF record that ends the chain
        for i, (kind, sub, text) in enumerate(records):
            chain += struct.pack(f"{order}I4B2BxxI", 16 * (i + 1), kind, sub, 1, 7, 5, 4, start + len(tail))
            texts.append(base + start + len(tail) + 4)
            tail += b"\xff" * 4 + text + b"\0"
        bases.append(base)
        body += chain + struct.pack(f"{order}I4B", 0, 0xFF, 0, 0, 0) + tail
    return struct.pack(f"{order}{len(scenes) + 1}I", len(scenes), *bases) + body, texts


def write_stock_archive(folder: Path, stem: str, entries: dict, count: int | None = None) -> None:
    """Write `stem`.BIN and .IDX: an entry is bytes, or a tuple of parts that share one record."""
    records, trailer, body = [], b"", b""
    for ordinal in range(max(entries) + 1 if count is None else count):
        parts = entries.get(ordinal, b"")
        parts = parts if isinstance(parts, tuple) else (parts,)
        if len(parts) == 1:
            records.append((len(parts[0]), 0, 0))
        else:
            records.append((1, sum(len(p) + -len(p) % SECTOR for p in parts), len(trailer)))
            trailer += struct.pack(f"<{len(parts) + 1}I", *map(len, parts), 0)
        body += b"".join(p + bytes(-len(p) % SECTOR) for p in parts)
    (folder / f"{stem}.BIN").write_bytes(body)
    (folder / f"{stem}.IDX").write_bytes(b"".join(struct.pack("<3I", *r) for r in records) + trailer)


def write_ens(folder: Path, entries: dict[int, bytes], count: int | None = None) -> Path:
    """Write a donor linkdata_ens.idx and .lnk (big-endian SM4L index) and return their shared stem."""
    records, body, sector = b"", b"", 0
    for ordinal in range(max(entries) + 1 if count is None else count):
        value = entries.get(ordinal, b"")
        allocation = -(-len(value) // SECTOR)
        records += struct.pack(">4I", sector, allocation, len(value), 0)
        body += value + bytes(-len(value) % SECTOR)
        sector += allocation
    stem = folder / "linkdata_ens"
    stem.with_suffix(".idx").write_bytes(struct.pack(">4sIII", b"SM4L", len(records) // 16, sector, 0) + records)
    stem.with_suffix(".lnk").write_bytes(body)
    return stem


def write_ps2_archive(path: Path, entries: dict[int, bytes], count: int | None = None) -> Path:
    """Write a PSP/PS2-layout archive: header, a (sector, size) pair per member, each member at its own sector."""
    count = max(entries) + 1 if count is None else count
    table = 16 + 8 * count
    sector, index, body = -(-table // SECTOR), b"", b""
    for ordinal in range(count):
        data = entries.get(ordinal, b"")
        index += struct.pack("<II", sector, len(data))
        body += data + bytes(-len(data) % SECTOR)
        sector += -(-len(data) // SECTOR)
    head = struct.pack("<4I", 0x33B59, count, SECTOR, 0) + index
    path.write_bytes(head + bytes(-len(head) % SECTOR) + body)
    return path


def pe_image(size: int, *sections: tuple[bytes, int, int, int, int], image_base: int = 0x400000,
             pe_at: int = 0x80, optional_size: int = 0xE0) -> bytearray:
    """An i386 PE: its header at pe_at, an optional header of optional_size, then (name, vsize, rva, raw_size, raw)."""
    image = bytearray(size)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, pe_at)
    struct.pack_into("<4sHH12xH", image, pe_at, b"PE\0\0", 0x14C, len(sections), optional_size)
    struct.pack_into("<I", image, pe_at + 24 + 28, image_base)
    for i, section in enumerate(sections):
        struct.pack_into("<8sIIII", image, pe_at + 24 + optional_size + 40 * i, *section)
    return image
