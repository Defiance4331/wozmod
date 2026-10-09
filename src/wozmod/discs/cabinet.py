"""Read selected files from uncompressed or MSZIP cabinet sets."""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import BinaryIO

from .common import DiscError, DiscFile


_FROM_PREV = 0xFFFD
_TO_NEXT = 0xFFFE
_BOTH = 0xFFFF


def _path(name: str) -> str:
    name = name.replace("\\", "/")
    if not name or name.startswith("/") or ":" in name:
        raise DiscError("Invalid cabinet file path")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise DiscError("Invalid cabinet file path")
    return "/".join(parts)


def _read(stream: BinaryIO, count: int, limit: int) -> bytes:
    if count < 0 or stream.tell() + count > limit:
        raise DiscError("Cabinet record is outside the cabinet")
    data = stream.read(count)
    if len(data) != count:
        raise DiscError("Truncated cabinet record")
    return data


def _name(stream: BinaryIO, limit: int, utf: bool = False) -> str:
    data = bytearray()
    for _ in range(257):
        byte = _read(stream, 1, limit)
        if byte == b"\0":
            try:
                return data.decode("utf-8" if utf else "cp1252")
            except UnicodeDecodeError as exc:
                raise DiscError("Invalid cabinet file name encoding") from exc
        data += byte
    raise DiscError("Cabinet file name is too long")


def _checksum(data: bytes) -> int:
    aligned = len(data) & ~3
    result = 0
    for (word,) in struct.iter_unpack("<I", data[:aligned]):
        result ^= word
    return result ^ int.from_bytes(data[aligned:], "big")


@dataclass(frozen=True)
class _FileRecord:
    path: str
    size: int
    offset: int
    folder: int


@dataclass(frozen=True)
class _Fragment:
    cabinet: str
    limit: int
    offset: int
    blocks: int
    compression: int
    reserve: int


@dataclass(frozen=True)
class _Cabinet:
    path: str
    next: str | None
    set_id: int
    index: int
    folders: tuple[_Fragment, ...]
    files: tuple[_FileRecord, ...]


@dataclass(eq=False)
class _Folder:
    fragments: list[_Fragment] = field(default_factory=list)


@dataclass(frozen=True)
class _Member:
    path: str
    size: int
    offset: int
    folder: _Folder


class CabinetSet:
    """List a complete set and stream files with one decode pass per folder.

    The opener returns a new seekable binary stream for each cabinet name.
    This reader closes every stream it opens. Iteration order follows folder
    offsets; callers can join the chunks emitted for each selected path.
    """

    def __init__(self, opener: Callable[[str], BinaryIO], first: str):
        self._opener = opener
        self._folders: list[_Folder] = []
        self._members: dict[str, _Member] = {}
        self._load(_path(first))

    def __enter__(self) -> CabinetSet:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """No streams are retained between operations."""

    def files(self) -> tuple[DiscFile, ...]:
        return tuple(DiscFile(file.path, file.size) for file in self._members.values())

    def iter_file(self, path: str, chunk_size: int = 1048576) -> Iterator[bytes]:
        for _, chunk in self.iter_files((path,), chunk_size):
            yield chunk

    def iter_files(
        self, paths: Iterable[str], chunk_size: int = 1048576,
    ) -> Iterator[tuple[str, bytes]]:
        if chunk_size <= 0:
            raise ValueError("Chunk size must be positive")
        selected: dict[_Folder, list[_Member]] = {}
        for path in dict.fromkeys(_path(path) for path in paths):
            try:
                member = self._members[path]
            except KeyError:
                raise FileNotFoundError(path) from None
            selected.setdefault(member.folder, []).append(member)
        for folder in self._folders:
            members = sorted(selected.get(folder, ()), key=lambda member: member.offset)
            if not members:
                continue
            empty = [member for member in members if member.size == 0]
            for member in empty:
                yield member.path, b""
            members = [member for member in members if member.size]
            if not members:
                continue
            position = 0
            index = 0
            blocks = self._iter_folder(folder)
            try:
                for block in blocks:
                    end = position + len(block)
                    while index < len(members) and members[index].offset < end:
                        member = members[index]
                        start = max(member.offset, position) - position
                        stop = min(member.offset + member.size, end) - position
                        for offset in range(start, stop, chunk_size):
                            yield member.path, block[offset:min(offset + chunk_size, stop)]
                        if member.offset + member.size > end:
                            break
                        index += 1
                    position = end
                    if index == len(members):
                        break
                if index != len(members):
                    raise DiscError("Cabinet file exceeds its folder data")
            finally:
                blocks.close()

    def _cabinet(self, path: str) -> _Cabinet:
        with self._opener(path) as stream:
            stream.seek(0, 2)
            physical_size = stream.tell()
            stream.seek(0)
            header = _read(stream, 36, physical_size)
            fields = struct.unpack("<4sIIIII BBHHHHH", header)
            (signature, reserved1, size, reserved2, files_offset, reserved3,
             minor, major, folder_count, file_count, flags, set_id, index) = fields
            if signature != b"MSCF" or (minor, major) != (3, 1):
                raise DiscError("Unsupported cabinet header")
            if reserved1 or reserved2 or reserved3 or size > physical_size or size < 36:
                raise DiscError("Invalid cabinet size or reserved fields")
            folder_reserve = data_reserve = 0
            if flags & 4:
                reserve, folder_reserve, data_reserve = struct.unpack(
                    "<HBB", _read(stream, 4, size),
                )
                _read(stream, reserve, size)
            if flags & 1:
                _name(stream, size)
                _name(stream, size)
            next_name = None
            if flags & 2:
                next_name = _path(_name(stream, size))
                _name(stream, size)
            folders = []
            for _ in range(folder_count):
                offset, blocks, compression = struct.unpack("<IHH", _read(stream, 8, size))
                _read(stream, folder_reserve, size)
                if compression not in (0, 1):
                    raise DiscError("Unsupported cabinet folder compression")
                if offset > size:
                    raise DiscError("Cabinet folder data is outside the cabinet")
                folders.append(_Fragment(path, size, offset, blocks, compression, data_reserve))
            if files_offset < stream.tell() or files_offset > size:
                raise DiscError("Invalid cabinet file table offset")
            stream.seek(files_offset)
            files = []
            for _ in range(file_count):
                length, offset, folder, _, _, attributes = struct.unpack(
                    "<IIHHHH", _read(stream, 16, size),
                )
                name = _path(_name(stream, size, bool(attributes & 0x80)))
                files.append(_FileRecord(name, length, offset, folder))
            table_end = stream.tell()
            if any(folder.blocks and folder.offset < table_end for folder in folders):
                raise DiscError("Cabinet folder overlaps the file table")
            return _Cabinet(path, next_name, set_id, index, tuple(folders), tuple(files))

    def _load(self, first: str) -> None:
        path: str | None = first
        seen = set()
        previous: _Cabinet | None = None
        continuing: _Folder | None = None
        while path is not None:
            if path in seen:
                raise DiscError("Cabinet chain contains a cycle")
            seen.add(path)
            cabinet = self._cabinet(path)
            if previous is not None:
                if cabinet.set_id != previous.set_id or cabinet.index != previous.index + 1:
                    raise DiscError("Cabinet chain identity or index mismatch")
            incoming = any(file.folder in (_FROM_PREV, _BOTH) for file in cabinet.files)
            outgoing = any(file.folder in (_TO_NEXT, _BOTH) for file in cabinet.files)
            if incoming != (continuing is not None):
                raise DiscError("Cabinet folder continuation is missing")
            local = []
            for index, fragment in enumerate(cabinet.folders):
                if index == 0 and incoming:
                    folder = continuing
                    if folder.fragments[-1].compression != fragment.compression:
                        raise DiscError("Continued cabinet folder compression changed")
                else:
                    folder = _Folder()
                    self._folders.append(folder)
                folder.fragments.append(fragment)
                local.append(folder)
            for file in cabinet.files:
                if file.folder in (_FROM_PREV, _BOTH):
                    folder_index = 0
                elif file.folder == _TO_NEXT:
                    folder_index = len(local) - 1
                else:
                    folder_index = file.folder
                if not 0 <= folder_index < len(local):
                    raise DiscError("Invalid cabinet file folder index")
                if file.folder == _BOTH and len(local) != 1:
                    raise DiscError("A twice-continued file must occupy the only folder")
                member = _Member(file.path, file.size, file.offset, local[folder_index])
                prior = self._members.get(file.path)
                if prior is not None:
                    if prior != member or file.folder not in (_FROM_PREV, _BOTH):
                        raise DiscError("Conflicting cabinet file entries")
                elif file.folder in (_FROM_PREV, _BOTH):
                    raise DiscError("Continued cabinet file has no initial entry")
                else:
                    self._members[file.path] = member
            continuing = local[-1] if outgoing and local else None
            if outgoing and cabinet.next is None:
                raise DiscError("Continued cabinet file has no next cabinet")
            previous = cabinet
            path = str(PurePosixPath(path).parent / cabinet.next) if cabinet.next else None

    def _iter_folder(self, folder: _Folder) -> Iterator[bytes]:
        window = b""
        pending = bytearray()
        for fragment in folder.fragments:
            with self._opener(fragment.cabinet) as stream:
                stream.seek(fragment.offset)
                for index in range(fragment.blocks):
                    checksum, compressed_size, size = struct.unpack(
                        "<IHH", _read(stream, 8, fragment.limit),
                    )
                    reserve = _read(stream, fragment.reserve, fragment.limit)
                    data = _read(stream, compressed_size, fragment.limit)
                    if checksum and checksum != _checksum(
                        struct.pack("<HH", compressed_size, size) + reserve + data,
                    ):
                        raise DiscError("Cabinet data checksum mismatch")
                    pending += data
                    if len(pending) > 65536:
                        raise DiscError("Cabinet compressed block is too large")
                    if size == 0:
                        if index != fragment.blocks - 1:
                            raise DiscError("Split cabinet block is not the last record")
                        continue
                    if size > 32768:
                        raise DiscError("Cabinet uncompressed block is too large")
                    if fragment.compression == 0:
                        block = bytes(pending)
                    else:
                        if pending[:2] != b"CK":
                            raise DiscError("Invalid MSZIP block signature")
                        decoder = zlib.decompressobj(-15, zdict=window)
                        try:
                            block = decoder.decompress(pending[2:], size + 1)
                        except zlib.error as exc:
                            raise DiscError("Invalid MSZIP compressed block") from exc
                        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                            raise DiscError("Incomplete or oversized MSZIP compressed block")
                    if len(block) != size:
                        raise DiscError("Cabinet uncompressed block size mismatch")
                    pending.clear()
                    window = (window + block)[-32768:]
                    yield block
        if pending:
            raise DiscError("Incomplete split cabinet block")
