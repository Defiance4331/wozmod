from __future__ import annotations

import struct
from pathlib import Path

import pytest

from wozmod.discs.common import DiscError, DiscFile
from wozmod.discs.iso9660 import Iso9660


SECTOR = 2048


def _record(name: bytes, sector: int, size: int, flags: int = 0) -> bytes:
    record = bytearray(33 + len(name) + (len(name) % 2 == 0))
    record[0] = len(record)
    record[2:10] = struct.pack("<I", sector) + struct.pack(">I", sector)
    record[10:18] = struct.pack("<I", size) + struct.pack(">I", size)
    record[25] = flags
    record[28:32] = struct.pack("<H", 1) + struct.pack(">H", 1)
    record[32] = len(name)
    record[33 : 33 + len(name)] = name
    return bytes(record)


def _descriptor(kind: int, root: int, root_size: int) -> bytes:
    descriptor = bytearray(SECTOR)
    descriptor[0:7] = bytes([kind]) + b"CD001\x01"
    descriptor[80:88] = struct.pack("<I", 48) + struct.pack(">I", 48)
    descriptor[120:124] = struct.pack("<H", 1) + struct.pack(">H", 1)
    descriptor[124:128] = struct.pack("<H", 1) + struct.pack(">H", 1)
    descriptor[128:132] = struct.pack("<H", SECTOR) + struct.pack(">H", SECTOR)
    descriptor[156:190] = _record(b"\x00", root, root_size, 2)
    if kind == 2:
        descriptor[88:91] = b"%/E"
    return bytes(descriptor)


def _image(tmp_path: Path, blocks: dict[int, bytes], *, joliet: bool = False,
           root_size: int = SECTOR) -> Path:
    image = bytearray(48 * SECTOR)
    descriptors = [(16, _descriptor(1, 20, root_size))]
    if joliet:
        descriptors.append((17, _descriptor(2, 22, SECTOR)))
    descriptors.append((18 if joliet else 17, _descriptor(255, 0, 0)))
    for sector, content in descriptors + list(blocks.items()):
        start = sector * SECTOR
        image[start : start + len(content)] = content
    path = tmp_path / "input.iso"
    path.write_bytes(image)
    return path


def test_joliet_names_are_not_replaced_by_primary_aliases(tmp_path: Path) -> None:
    path = _image(tmp_path, {
        20: _record(b"ALIAS.BIN;1", 30, 5),
        22: _record("Assets".encode("utf-16-be"), 23, SECTOR, 2),
        23: _record("Long Name.bin".encode("utf-16-be"), 31, 7),
        30: b"wrong",
        31: b"payload",
    }, joliet=True)
    with Iso9660(path) as disc:
        assert disc.files() == (DiscFile("Assets/Long Name.bin", 7),)
        with disc.open("Assets/Long Name.bin") as stream:
            assert stream.read() == b"payload"


def test_plain_iso_version_suffix_does_not_leak_into_paths(tmp_path: Path) -> None:
    path = _image(tmp_path, {20: _record(b"SAMPLE.BIN;1", 30, 4), 30: b"data"})
    with Iso9660(path) as disc:
        assert disc.files() == (DiscFile("SAMPLE.BIN", 4),)
        with disc.open("SAMPLE.BIN") as stream:
            assert stream.read() == b"data"


def test_directory_padding_does_not_drop_the_next_block(tmp_path: Path) -> None:
    path = _image(tmp_path, {
        20: _record(b"FIRST.BIN", 30, 1),
        21: _record(b"LAST.BIN", 31, 1),
        30: b"a", 31: b"z",
    }, root_size=2 * SECTOR)
    with Iso9660(path) as disc:
        assert disc.files() == (DiscFile("FIRST.BIN", 1), DiscFile("LAST.BIN", 1))


def test_multi_extent_file_does_not_drop_or_reorder_later_extents(tmp_path: Path) -> None:
    records = b"".join([
        _record(b"SPLIT.BIN", 33, SECTOR, 128),
        _record(b"SPLIT.BIN", 30, SECTOR, 128),
        _record(b"SPLIT.BIN", 36, 5),
    ])
    expected = b"a" * SECTOR + b"b" * SECTOR + b"final"
    path = _image(tmp_path, {20: records, 33: b"a" * SECTOR, 30: b"b" * SECTOR,
                             36: b"final"})
    with Iso9660(path) as disc:
        assert disc.files() == (DiscFile("SPLIT.BIN", len(expected)),)
        with disc.open("SPLIT.BIN") as stream:
            assert stream.read() == expected
            stream.seek(SECTOR - 2)
            assert stream.read(5) == b"aabbb"
            stream.seek(-5, 2)
            assert stream.read(100) == b"final"


def test_interrupted_multi_extent_file_is_not_published_as_complete(tmp_path: Path) -> None:
    records = _record(b"SPLIT.BIN", 30, SECTOR, 128) + _record(b"OTHER.BIN", 31, 1)
    path = _image(tmp_path, {20: records})
    with Iso9660(path) as disc:
        with pytest.raises(DiscError, match="interrupted"):
            disc.files()


def test_extent_past_volume_end_is_not_returned_as_a_short_file(tmp_path: Path) -> None:
    path = _image(tmp_path, {20: _record(b"OUTSIDE.BIN", 47, SECTOR + 1)})
    with Iso9660(path) as disc:
        with pytest.raises(DiscError, match="beyond the volume"):
            disc.files()


def test_path_separator_in_identifier_cannot_create_a_false_directory(tmp_path: Path) -> None:
    path = _image(tmp_path, {20: _record(b"BAD/FILE.BIN", 30, 1)})
    with Iso9660(path) as disc:
        with pytest.raises(DiscError, match="Unsafe"):
            disc.files()


def test_interleaved_extent_is_not_streamed_as_contiguous_bytes(tmp_path: Path) -> None:
    record = bytearray(_record(b"INTERLEAVED.BIN", 30, 1))
    record[26] = 1
    record[27] = 1
    path = _image(tmp_path, {20: bytes(record)})
    with Iso9660(path) as disc:
        with pytest.raises(DiscError, match="Interleaved"):
            disc.files()
