from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from wozmod.discs.common import DiscError, DiscFile
from wozmod.discs.xdvdfs import Xdvdfs


def _record(
    name: bytes, sector: int, size: int, *, left: int = 0, right: int = 0,
    directory: bool = False,
) -> bytes:
    return struct.pack("<HHIIBB", left, right, sector, size, 0x10 if directory else 0x20, len(name)) + name


def _image(
    tmp_path: Path, records: dict[int, bytes], *, partition: int = 0,
    root_size: int = 2048, directories: dict[int, bytes] | None = None,
    payloads: dict[int, bytes] | None = None,
) -> Path:
    path = tmp_path / "disc.iso"
    descriptor = bytearray(2048)
    descriptor[:20] = b"MICROSOFT*XBOX*MEDIA"
    struct.pack_into("<II", descriptor, 20, 33, root_size)
    descriptor[-20:] = b"MICROSOFT*XBOX*MEDIA"
    with path.open("wb") as output:
        output.seek(partition + 32 * 2048)
        output.write(descriptor)
        output.seek(partition + 33 * 2048)
        output.write(bytes(root_size))
        for offset, data in records.items():
            output.seek(partition + 33 * 2048 + offset)
            output.write(data)
        for sector, data in (directories or {}).items():
            output.seek(partition + sector * 2048)
            output.write(data)
        for sector, data in (payloads or {}).items():
            output.seek(partition + sector * 2048)
            output.write(data)
    return path


@pytest.mark.parametrize("partition", [0, 0x0FD90000, 0x02080000])
def test_partition_base_is_added_to_file_sector(tmp_path: Path, partition: int) -> None:
    path = _image(
        tmp_path, {0: _record(b"item", 36, 5)}, partition=partition,
        payloads={36: b"owned"},
    )
    with Xdvdfs(path) as disc:
        assert disc.partition_offset == partition
        with disc.open("item") as source:
            assert source.read() == b"owned"


def test_dword_tree_pointers_do_not_drop_nested_files(tmp_path: Path) -> None:
    path = _image(
        tmp_path,
        {
            0: _record(b"middle", 36, 3, left=8, right=16),
            32: _record(b"first", 35, 2),
            64: _record(b"nested", 34, 2048, directory=True),
        },
        directories={34: _record(b"last", 37, 4).ljust(2048, b"\0")},
        payloads={35: b"ab", 36: b"cde", 37: b"fghi"},
    )
    expected = {"first": b"ab", "middle": b"cde", "nested/last": b"fghi"}
    with Xdvdfs(path) as disc:
        assert disc.files() == tuple(DiscFile(name, len(data)) for name, data in expected.items())
        actual = {}
        for name in expected:
            with disc.open(name) as source:
                actual[name] = source.read()
        assert actual == expected


def test_tree_cycle_is_rejected_instead_of_silently_dropping_an_entry(tmp_path: Path) -> None:
    path = _image(
        tmp_path,
        {0: _record(b"first", 0, 0, left=8), 32: _record(b"second", 0, 0, left=8)},
    )
    with Xdvdfs(path) as disc, pytest.raises(DiscError):
        disc.files()


def test_file_extent_past_image_is_rejected_before_a_short_read(tmp_path: Path) -> None:
    path = _image(tmp_path, {0: _record(b"item", 35, 4)}, payloads={35: b"abc"})
    with Xdvdfs(path) as disc, pytest.raises(DiscError):
        disc.files()


def test_record_name_cannot_escape_the_slash_relative_file_tree(tmp_path: Path) -> None:
    path = _image(tmp_path, {0: _record(b"../item", 0, 0)})
    with Xdvdfs(path) as disc, pytest.raises(DiscError):
        disc.files()


def test_case_insensitive_lookup_preserves_the_listed_filename(tmp_path: Path) -> None:
    path = _image(tmp_path, {0: _record(b"ITEM", 35, 2)}, payloads={35: b"ok"})
    with Xdvdfs(path) as disc:
        assert disc.files() == (DiscFile("ITEM", 2),)
        with disc.open("item") as source:
            assert source.read() == b"ok"


def test_parent_component_is_rejected_before_disc_path_lookup(tmp_path: Path) -> None:
    path = _image(tmp_path, {0: _record(b"item", 0, 0)})
    with Xdvdfs(path) as disc, pytest.raises(DiscError):
        disc.open("../item")


def test_empty_file_is_not_dropped_with_empty_directories(tmp_path: Path) -> None:
    path = _image(
        tmp_path,
        {0: _record(b"empty-dir", 0, 0, left=8, directory=True), 32: _record(b"empty-file", 0, 0)},
    )
    with Xdvdfs(path) as disc:
        assert disc.files() == (DiscFile("empty-file", 0),)
        with disc.open("empty-file") as source:
            assert source.read() == b""


def test_directory_extent_cycle_is_rejected_before_repeating_paths(tmp_path: Path) -> None:
    path = _image(tmp_path, {0: _record(b"again", 33, 2048, directory=True)})
    with Xdvdfs(path) as disc, pytest.raises(DiscError):
        disc.files()


@pytest.mark.skipif(not os.environ.get("WOZMOD_WO2_X360_ISO"), reason="Donor disc not configured")
def test_real_disc_large_file_size_is_not_truncated_at_signed_boundary() -> None:
    path = Path(os.environ["WOZMOD_WO2_X360_ISO"])
    if not path.is_file():
        pytest.skip("Donor disc is absent")
    expected = {
        "default.xex": 5_451_776,
        "data/linkdata_ans.idx": 210_088,
        "data/linkdata_bns.idx": 61_104,
        "data/linkdata_bns.lnk": 3_198_955_520,
        "data/linkdata_ens.idx": 4_448,
        "data/linkdata_ens.lnk": 355_137_536,
        "data/link_sebank.bdx": 50_376_704,
        "data/link_sebank.hdx": 485_376,
        "data/link_vodat.bdx": 441_327_616,
    }
    with Xdvdfs(path) as disc:
        actual = {entry.path.casefold(): entry.size for entry in disc.files()}
        assert {name: actual.get(name) for name in expected} == expected
