from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import BinaryIO

from .common import DiscError, DiscFile, ExtentStream


_SECTOR = 2048
_MAGIC = b"MICROSOFT*XBOX*MEDIA"
_PARTITIONS = (0, 0x0FD90000, 0x02080000, 0x18300000)
_ENTRY = struct.Struct("<HHIIBB")


class Xdvdfs:
    """List and stream files in a game partition without extracting it."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._source = self.path.open("rb")
        self._size = os.fstat(self._source.fileno()).st_size
        self._entries: dict[str, tuple[str, int, int]] | None = None
        try:
            for base in _PARTITIONS:
                offset = base + 32 * _SECTOR
                if offset + _SECTOR > self._size:
                    continue
                descriptor = self._read_at(offset, _SECTOR)
                if descriptor[:20] == _MAGIC:
                    if descriptor[-20:] != _MAGIC:
                        raise DiscError("Invalid XDVDFS trailing descriptor signature")
                    self.partition_offset = base
                    self._root = struct.unpack_from("<II", descriptor, 20)
                    self._validate_extent(*self._root)
                    break
            else:
                raise DiscError("XDVDFS game partition was not found")
        except BaseException:
            self._source.close()
            raise

    def __enter__(self) -> Xdvdfs:
        self._check_open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._source.close()

    def _check_open(self) -> None:
        if self._source.closed:
            raise ValueError("XDVDFS reader is closed")

    def _read_at(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > self._size:
            raise DiscError("XDVDFS record is outside the image")
        self._source.seek(offset)
        data = self._source.read(size)
        if len(data) != size:
            raise DiscError("Truncated XDVDFS record")
        return data

    def _validate_extent(self, sector: int, size: int) -> int:
        offset = self.partition_offset + sector * _SECTOR
        if offset + size > self._size:
            raise DiscError("XDVDFS extent is outside the image")
        return offset

    @staticmethod
    def _name(raw: bytes) -> str:
        name = raw.decode("latin-1")
        if not name or name in (".", "..") or any(c in name for c in "/\\\0"):
            raise DiscError("Invalid XDVDFS filename")
        return name

    def _index(self) -> dict[str, tuple[str, int, int]]:
        self._check_open()
        if self._entries is not None:
            return self._entries
        entries: dict[str, tuple[str, int, int]] = {}
        pending = [("", *self._root)]
        directories: set[tuple[int, int]] = set()
        paths: set[str] = set()
        while pending:
            parent, sector, size = pending.pop()
            if not size:
                continue
            extent = (sector, size)
            if extent in directories:
                raise DiscError("Repeated XDVDFS directory extent")
            directories.add(extent)
            base = self._validate_extent(sector, size)
            nodes = [0]
            visited: set[int] = set()
            while nodes:
                offset = nodes.pop()
                if offset in visited:
                    raise DiscError("Cycle in XDVDFS directory tree")
                visited.add(offset)
                if offset + _ENTRY.size > size:
                    raise DiscError("XDVDFS tree pointer is outside its directory")
                left, right, start, length, attributes, name_size = _ENTRY.unpack(
                    self._read_at(base + offset, _ENTRY.size)
                )
                if offset + _ENTRY.size + name_size > size:
                    raise DiscError("XDVDFS filename is outside its directory")
                name = self._name(self._read_at(base + offset + _ENTRY.size, name_size))
                path = f"{parent}/{name}" if parent else name
                key = path.casefold()
                if key in paths:
                    raise DiscError("Duplicate XDVDFS path")
                paths.add(key)
                file_offset = self._validate_extent(start, length)
                if attributes & 0x10:
                    pending.append((path, start, length))
                else:
                    entries[key] = (path, file_offset, length)
                if right:
                    nodes.append(right * 4)
                if left:
                    nodes.append(left * 4)
        self._entries = entries
        return entries

    def files(self) -> tuple[DiscFile, ...]:
        return tuple(
            DiscFile(path, size)
            for path, _, size in sorted(self._index().values())
        )

    def open(self, path: str) -> BinaryIO:
        """Open a slash-relative path, matching filename case insensitively."""
        if not path or "\\" in path or "\0" in path or any(
            part in ("", ".", "..") for part in path.split("/")
        ):
            raise DiscError("Expected a slash-relative disc path")
        try:
            _, offset, size = self._index()[path.casefold()]
        except KeyError:
            raise FileNotFoundError(path) from None
        return ExtentStream(self.path, ((offset, size),))
