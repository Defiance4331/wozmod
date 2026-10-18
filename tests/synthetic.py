"""Game-format bytes built from the format description, for tests: string tables, scripts, MESC blocks, archives."""

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
    """A stage script: four section offsets, two filler sections, the notice table padded to 4, the dialog table."""
    sections = [b"HEAD", b"TAIL", notices + bytes(-len(notices) % 4), dialog]
    offsets = [20 + sum(map(len, sections[:i])) for i in range(4)]
    return struct.pack(f"{order}5I", 4, *offsets) + b"".join(sections)


def mesc(*lines: bytes) -> bytes:
    """A MESC block: count, total size, an offset per line, then each line NUL-terminated."""
    offsets, body = [], b""
    for line in lines:
        offsets.append(12 + 4 * len(lines) + len(body))
        body += line + b"\0"
    return b"MESC" + struct.pack(f"<II{len(lines)}I", len(lines), 12 + 4 * len(lines) + len(body), *offsets) + body


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
