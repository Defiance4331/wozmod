from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .common import DiscError, DiscFile, ExtentStream


_SECTOR = 2048
_JOLIET_LEVELS = {b"%/@": 1, b"%/C": 2, b"%/E": 3}


def _both_endian(data: bytes, offset: int, width: int) -> int:
    little = int.from_bytes(data[offset : offset + width], "little")
    big = int.from_bytes(data[offset + width : offset + 2 * width], "big")
    if little != big:
        raise DiscError("ISO number has conflicting byte orders")
    return little


@dataclass(frozen=True)
class _Record:
    identifier: bytes
    offset: int
    size: int
    flags: int


class Iso9660:
    """Read a single-session ISO 9660 or Joliet file tree."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._source = self.path.open("rb")
        self._size = self.path.stat().st_size
        self._entries: dict[str, tuple[tuple[int, int], ...]] | None = None
        try:
            primary: bytes | None = None
            supplementary: bytes | None = None
            level = 0
            sector = 16
            while True:
                descriptor = self._read_at(sector * _SECTOR, _SECTOR)
                if descriptor[1:6] != b"CD001" or descriptor[6] != 1:
                    raise DiscError("Invalid ISO volume descriptor")
                kind = descriptor[0]
                if kind == 255:
                    break
                if kind == 1:
                    primary = descriptor
                elif kind == 2:
                    candidate_level = _JOLIET_LEVELS.get(descriptor[88:91], 0)
                    if candidate_level > level:
                        supplementary = descriptor
                        level = candidate_level
                sector += 1
            if primary is None:
                raise DiscError("ISO primary volume descriptor is missing")
            descriptor = supplementary if supplementary is not None else primary
            self._joliet = supplementary is not None
            self._block_size = _both_endian(descriptor, 128, 2)
            if self._block_size < 512 or self._block_size & (self._block_size - 1):
                raise DiscError("Invalid ISO logical block size")
            self._volume_size = _both_endian(descriptor, 80, 4) * self._block_size
            if self._volume_size > self._size:
                raise DiscError("ISO volume extends beyond the image")
            if _both_endian(descriptor, 120, 2) != 1:
                raise DiscError("ISO volume sets are unsupported")
            root_size = descriptor[156]
            self._root = self._record(descriptor[156 : 156 + root_size])
            if not self._root.flags & 2:
                raise DiscError("ISO root record is not a directory")
        except BaseException:
            self._source.close()
            raise

    def __enter__(self) -> Iso9660:
        self._check_open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._source.close()

    def _check_open(self) -> None:
        if self._source.closed:
            raise ValueError("ISO reader is closed")

    def _read_at(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > self._size:
            raise DiscError("ISO read extends beyond the image")
        self._source.seek(offset)
        result = self._source.read(size)
        if len(result) != size:
            raise DiscError("Truncated ISO image")
        return result

    def _record(self, data: bytes) -> _Record:
        if len(data) < 34 or data[0] != len(data):
            raise DiscError("Invalid ISO directory record length")
        name_size = data[32]
        if name_size == 0 or 33 + name_size + (name_size % 2 == 0) > len(data):
            raise DiscError("Truncated ISO file identifier")
        if data[26] or data[27]:
            raise DiscError("Interleaved ISO files are unsupported")
        if _both_endian(data, 28, 2) != 1:
            raise DiscError("ISO file belongs to another volume")
        offset = (_both_endian(data, 2, 4) + data[1]) * self._block_size
        size = _both_endian(data, 10, 4)
        if offset + size > self._volume_size:
            raise DiscError("ISO file extent extends beyond the volume")
        return _Record(data[33 : 33 + name_size], offset, size, data[25])

    def _name(self, identifier: bytes) -> str:
        try:
            name = identifier.decode("utf-16-be" if self._joliet else "ascii")
        except UnicodeDecodeError as error:
            raise DiscError("Invalid ISO file identifier encoding") from error
        if ";" in name:
            stem, version = name.rsplit(";", 1)
            if version.isascii() and version.isdecimal():
                name = stem
        if name.endswith("."):
            name = name[:-1]
        if not name or name in (".", "..") or any(c in name for c in "/\\\x00"):
            raise DiscError("Unsafe ISO file identifier")
        return name

    def _directory(self, directory: _Record):
        position = 0
        while position < directory.size:
            block_size = min(self._block_size, directory.size - position)
            block = self._read_at(directory.offset + position, block_size)
            cursor = 0
            while cursor < len(block):
                size = block[cursor]
                if size == 0:
                    break
                if cursor + size > len(block):
                    raise DiscError("ISO directory record crosses a logical block")
                yield self._record(block[cursor : cursor + size])
                cursor += size
            position += block_size

    def _index(self) -> dict[str, tuple[tuple[int, int], ...]]:
        self._check_open()
        if self._entries is not None:
            return self._entries
        entries: dict[str, tuple[tuple[int, int], ...]] = {}
        directories = [("", self._root)]
        seen_directories: set[tuple[int, int]] = set()
        seen_paths: set[str] = set()
        while directories:
            parent, directory = directories.pop()
            location = (directory.offset, directory.size)
            if location in seen_directories:
                raise DiscError("ISO directory cycle or alias")
            seen_directories.add(location)
            pending_name: bytes | None = None
            pending_flags = 0
            extents: list[tuple[int, int]] = []
            for record in self._directory(directory):
                if record.identifier in (b"\x00", b"\x01"):
                    if pending_name is not None:
                        raise DiscError("ISO multi-extent file is interrupted")
                    continue
                path = parent + self._name(record.identifier)
                if pending_name is not None:
                    if record.identifier != pending_name or record.flags & ~128 != pending_flags:
                        raise DiscError("ISO multi-extent file is interrupted")
                else:
                    if path in seen_paths:
                        raise DiscError("Duplicate ISO path")
                    seen_paths.add(path)
                if record.flags & 2:
                    if record.flags & 128:
                        raise DiscError("ISO multi-extent directories are unsupported")
                    directories.append((path + "/", record))
                    continue
                extents.append((record.offset, record.size))
                if record.flags & 128:
                    pending_name = record.identifier
                    pending_flags = record.flags & ~128
                else:
                    entries[path] = tuple(extents)
                    pending_name = None
                    extents = []
            if pending_name is not None:
                raise DiscError("ISO multi-extent file has no final record")
        self._entries = entries
        return entries

    def files(self) -> tuple[DiscFile, ...]:
        return tuple(
            DiscFile(path, sum(size for _, size in extents))
            for path, extents in sorted(self._index().items())
        )

    def open(self, path: str) -> BinaryIO:
        extents = self._index().get(path)
        if extents is None:
            raise FileNotFoundError(path)
        return ExtentStream(self.path, extents)
