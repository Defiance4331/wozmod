"""Synthetic cabinet records exercise file and compression continuation."""

from __future__ import annotations

import struct
import zlib
from collections import defaultdict

import pytest

from wozmod.discs.cabinet import CabinetSet
from wozmod.discs.common import DiscError


def _zip(data: bytes, window: bytes = b"") -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15, zdict=window)
    return b"CK" + compressor.compress(data) + compressor.flush()


def _cabinet(
    path, files, folders, *, previous=None, following=None, index=0, table_gap=0,
):
    flags = (1 if previous else 0) | (2 if following else 0)
    optional = b""
    if previous:
        optional += previous.encode() + b"\0disk\0"
    if following:
        optional += following.encode() + b"\0disk\0"
    file_offset = 36 + len(optional) + 8 * len(folders) + table_gap
    table = b"".join(
        struct.pack("<IIHHHH", size, offset, folder, 0, 0, 0)
        + name.encode() + b"\0"
        for name, size, offset, folder in files
    )
    data_offset = file_offset + len(table)
    folder_table = bytearray()
    content = bytearray()
    for records in folders:
        folder_table += struct.pack("<IHH", data_offset + len(content), len(records), 1)
        for data, size in records:
            content += struct.pack("<IHH", 0, len(data), size) + data
    size = data_offset + len(content)
    header = struct.pack(
        "<4sIIIII BBHHHHH", b"MSCF", 0, size, 0, file_offset, 0,
        3, 1, len(folders), len(files), flags, 321, index,
    )
    path.write_bytes(header + optional + folder_table + bytes(table_gap) + table + content)


def test_split_file_joins_last_and_first_folder_without_dropping_compressed_prefix(tmp_path):
    prefix = b"unselected-prefix"
    wanted = bytes(range(250)) * 2
    combined = prefix + wanted
    compressed = _zip(combined)
    _cabinet(
        tmp_path / "first.cab",
        [("skip.dat", 3, 0, 0), ("prefix.dat", len(prefix), 0, 1),
         ("nested\\chosen.dat", len(wanted), len(prefix), 0xFFFE)],
        [[(_zip(b"abc"), 3)], [(compressed[:17], 0)]],
        following="second.cab", table_gap=11,
    )
    _cabinet(
        tmp_path / "second.cab",
        [("nested\\chosen.dat", len(wanted), len(prefix), 0xFFFD),
         ("later.dat", 3, 0, 1)],
        [[(compressed[17:], len(combined))], [(_zip(b"xyz"), 3)]],
        previous="first.cab", index=1,
    )
    with CabinetSet(lambda name: (tmp_path / name).open("rb"), "first.cab") as reader:
        assert [(file.path, file.size) for file in reader.files()] == [
            ("skip.dat", 3), ("prefix.dat", len(prefix)),
            ("nested/chosen.dat", len(wanted)), ("later.dat", 3),
        ]
        assert b"".join(reader.iter_file("nested/chosen.dat", 19)) == wanted


def test_mszip_next_block_uses_the_previous_blocks_window(tmp_path):
    first = bytes((index * 37 + index // 251) % 256 for index in range(32768))
    second = first[-2048:]
    wanted = first[-100:] + second
    _cabinet(
        tmp_path / "first.cab",
        [("skip.dat", len(first) - 100, 0, 0),
         ("chosen.dat", len(wanted), len(first) - 100, 0xFFFE)],
        [[(_zip(first), len(first))]], following="second.cab",
    )
    _cabinet(
        tmp_path / "second.cab",
        [("chosen.dat", len(wanted), len(first) - 100, 0xFFFD)],
        [[(_zip(second, first), len(second))]], previous="first.cab", index=1,
    )
    reader = CabinetSet(lambda name: (tmp_path / name).open("rb"), "first.cab")
    assert b"".join(reader.iter_file("chosen.dat")) == wanted


def test_batch_selection_does_not_drop_a_second_file_inside_the_same_block(tmp_path):
    _cabinet(
        tmp_path / "batch.cab",
        [("one.dat", 5, 0, 0), ("skip.dat", 4, 5, 0), ("two.dat", 6, 9, 0)],
        [[(_zip(b"firstskipsecond"), 15)]],
    )
    reader = CabinetSet(lambda name: (tmp_path / name).open("rb"), "batch.cab")
    result = defaultdict(bytes)
    for path, chunk in reader.iter_files(("two.dat", "one.dat"), 2):
        assert len(chunk) <= 2
        result[path] += chunk
    assert dict(result) == {"one.dat": b"first", "two.dat": b"second"}


def test_invalid_folder_index_cannot_read_bytes_from_another_folder(tmp_path):
    _cabinet(tmp_path / "bad.cab", [("chosen.dat", 3, 0, 1)], [[(_zip(b"abc"), 3)]])
    with pytest.raises(DiscError, match="folder index"):
        CabinetSet(lambda name: (tmp_path / name).open("rb"), "bad.cab")


def test_truncated_folder_data_cannot_silently_return_a_short_file(tmp_path):
    _cabinet(tmp_path / "short.cab", [("chosen.dat", 4, 0, 0)], [[(_zip(b"abc"), 3)]])
    reader = CabinetSet(lambda name: (tmp_path / name).open("rb"), "short.cab")
    with pytest.raises(DiscError, match="exceeds its folder"):
        b"".join(reader.iter_file("chosen.dat"))
