"""The stock game folder's files, as the install's build steps read them: the one way step code reads the game.

- exe(folder): OROCHI_Z.exe as StockBytes, which serve spans (slices, single bytes, find and index) and the strings the
  text table is keyed by (StockBytes.strings), but no buffer: struct, re and bytes() cannot read around them;
- archive(stem): a LINKDATA archive's entries, unpacked (read) or as stored (read with unpack False, members);
- voices(folder): Voice.bin clips by voice id, found through the voice index (LINKDATA1 entry VOICE_INDEX);
- whole(path): any other file, read whole (the Dream movies).

Each piece is announced before it is read, with sys.audit(READ, path, at, end, form): form "span" for bytes at..end of a
file, "decoded" or "stored" for entry `at` of the archive at path (its stem), "clip" for clip `at`. Each file is
opened right after sys.audit(OPEN, path). The read gate (install/gate.py), an audit hook, checks each piece against
the step's manifest and lets through only the game-file opens announced here, so a step that reads the game around
this module is caught. The indexes (the .IDX files, the voice index) only locate pieces and are not pieces.
"""

from __future__ import annotations

import os
import re
import struct
import sys
from pathlib import Path

from wozmod.formats.linkdata import Archive, decoded_member, parse_index

READ, OPEN = "wozmod.game_files.read", "wozmod.game_files.open"
EXE, VOICE = "OROCHI_Z.exe", "data/Sound/Voice/Voice.bin"
ARCHIVES = ("LINKDATA1", "LINKDATA2")
VOICE_INDEX = 4058  # LINKDATA1: (u32 offset, u32 size) big-endian at 8 + 8 * clip, for each Voice.bin clip
# A NUL-ended exe string: 1 to 400 bytes between NULs (a longer run is not text a row is keyed by).
STRING = re.compile(rb"(?<=\0)[^\0]{1,400}(?=\0)")


def _read(path, at: int, end: int | None, form: str) -> None:
    sys.audit(READ, os.fspath(path), at, end, form)


def _open(path: Path):
    sys.audit(OPEN, os.fspath(path))
    return open(path, "rb")


def _data(path: Path) -> bytes:
    with _open(path) as stream:
        return stream.read()


class StockBytes:
    """A stock file's bytes. Each read announces its span: a slice its start to stop, a byte itself, a find or index
    from where it starts to the end of what it found (or the whole range searched, when nothing was)."""

    def __init__(self, path: Path, data: bytes):
        self.path, self._data = os.fspath(path), data

    def _span(self, start: int, end: int) -> None:
        if end > start:
            _read(self.path, start, end, "span")

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        raise TypeError("stock bytes are read by span")

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self._data))
            self._span(*((start, stop) if step == 1 else (0, len(self._data))))
        else:
            at = key + len(self._data) if key < 0 else key
            self._span(at, at + 1)
        return self._data[key]

    def _searched(self, found: int, sub, start, end) -> int:
        start = 0 if start is None else start + len(self._data) if start < 0 else start
        end = len(self._data) if end is None else end + len(self._data) if end < 0 else end
        self._span(start, end if found < 0 else found + (len(sub) if isinstance(sub, (bytes, bytearray)) else 1))
        return found

    def find(self, sub, start=None, end=None) -> int:
        return self._searched(self._data.find(sub, start, end), sub, start, end)

    def index(self, sub, start=None, end=None) -> int:
        found = self._searched(self._data.find(sub, start, end), sub, start, end)
        if found < 0:
            raise ValueError("subsection not found")
        return found

    def strings(self, key) -> Strings:
        """Every NUL-ended string (STRING), by key(string) (None: none), the first string of each key."""
        found: dict = {}
        for match in STRING.finditer(self._data):
            if (keyed := key(match.group())) and keyed[0] not in found:
                found[keyed[0]] = (keyed[1], match.start(), match.end())
        return Strings(self, found)


class Strings:
    """StockBytes.strings: looking a key up announces the span of its string with the NUL on either side, which the
    scan needed to find it. Only a lookup reads: the scan's other strings cannot change what a step builds, so they
    are not pieces. It cannot be listed, which would read every string."""

    def __init__(self, owner: StockBytes, found: dict):
        self._owner, self._found = owner, found

    def __contains__(self, key) -> bool:
        if (hit := self._found.get(key)) is None:
            return False
        self._owner._span(hit[1] - 1, hit[2] + 1)
        return True

    def __getitem__(self, key):
        if key not in self:
            raise KeyError(key)
        return self._found[key][0]

    def __iter__(self):
        raise TypeError("the exe's strings are looked up by key")


def exe(folder: Path) -> StockBytes:
    """The game folder's OROCHI_Z.exe."""
    path = Path(folder) / EXE
    return StockBytes(path, _data(path))


def whole(path: Path) -> bytes:
    """A game file read whole."""
    data = _data(Path(path))
    if data:
        _read(path, 0, len(data), "span")
    return data


class StockArchive(Archive):
    """A stock LINKDATA archive (stem.BIN, located through stem.IDX); index is the player's index of it."""

    def __init__(self, stem: Path):
        self.stem = Path(stem)
        data = self.stem.parent / f"{self.stem.name}.BIN"
        self.index = parse_index(_data(self.stem.parent / f"{self.stem.name}.IDX"), data.stat().st_size)
        super().__init__(data, tuple((e.offset, e.stored_sizes) for e in self.index.entries))

    def _stream(self):
        return _open(self.path)

    def read(self, ordinals, unpack: bool = True) -> dict[int, bytes]:
        ordinals = list(ordinals)
        for ordinal in ordinals:
            if ordinal in self:
                _read(self.stem, ordinal, None, "decoded" if unpack else "stored")
        return super().read(ordinals, unpack)

    def members(self, ordinal: int) -> list[bytes]:
        """Entry ordinal's parts, as stored."""
        if ordinal in self:
            _read(self.stem, ordinal, None, "stored")
        with self._stream() as stream:
            return self._parts(stream, ordinal, False)


def archive(stem: Path) -> StockArchive:
    """The stock archive at stem (LINKDATA1 or LINKDATA2 in the game folder)."""
    return StockArchive(stem)


def clip_span(table: bytes, clip: int) -> tuple[int, int] | None:
    """Where Voice.bin holds a clip, (offset, size), from the voice index (VOICE_INDEX unpacked); None past its end."""
    if clip < 0 or 8 + 8 * clip + 8 > len(table):
        return None
    return struct.unpack_from(">II", table, 8 + 8 * clip)


class StockVoices:
    """Voice.bin's clips, through the voice index table (LINKDATA1 VOICE_INDEX unpacked)."""

    def __init__(self, table: bytes, bank: Path):
        self.table, self.bank = table, Path(bank)

    def clips(self, voices) -> dict[int, bytes]:
        """Each voice id's KOVS record, in the order asked."""
        voices = list(voices)
        spans = {}
        for voice in voices:
            if (span := clip_span(self.table, voice)) is None:
                raise KeyError(voice)
            spans[voice] = span
            _read(self.bank, voice, None, "clip")
        found = {}
        with _open(self.bank) as stream:
            for voice, (offset, size) in spans.items():
                stream.seek(offset)
                if len(data := stream.read(size)) != size:
                    raise ValueError(f"Voice.bin ends inside clip {voice}")
                found[voice] = data
        return found


def voices(folder: Path) -> StockVoices:
    """The game folder's Voice.bin clips, found through its own voice index."""
    folder = Path(folder)
    linkdata1 = StockArchive(folder / "LINKDATA1")
    with linkdata1._stream() as stream:  # the index only locates the clips: it is not a piece
        table = decoded_member(b"".join(linkdata1._parts(stream, VOICE_INDEX, True)))
    return StockVoices(table, folder / VOICE)
