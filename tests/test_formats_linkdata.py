from __future__ import annotations

import io
import os
import struct
import subprocess
import sys
import types
from dataclasses import asdict
from pathlib import Path

import pytest

from wozmod.formats import linkdata


def _index_fixture() -> tuple[bytes, bytes]:
    records = ((100, 2048, 0), (0, 0, 0), (3000, 0, 0), (5000, 6144, 8))
    index = b"".join(struct.pack("<III", *row) for row in records)
    index += struct.pack("<IIIII", 100, 0, 10, 2050, 0)
    data = (
        b"A" * 100 + bytes(1948)
        + b"B" * 3000 + bytes(1096)
        + b"C" * 10 + bytes(2038)
        + b"D" * 2050 + bytes(2046)
    )
    return index, data


def test_zero_allocation_row_does_not_shift_later_linkdata_members() -> None:
    fixture, data = _index_fixture()
    index = linkdata.parse_index(fixture, len(data))

    assert index.encode() == fixture
    assert linkdata.encode_records(index.records, index.trailer) == fixture
    assert [
        (e.offset, e.allocated_bytes, e.stored_bytes, e.padding_bytes,
         [(m.relative_offset, m.stored_bytes, m.allocated_bytes) for m in e.members])
        for e in index.entries
    ] == [
        (0, 2048, 100, 1948, [(0, 100, 2048)]),
        (2048, 0, 0, 0, []),
        (2048, 4096, 3000, 1096, [(0, 3000, 4096)]),
        (6144, 6144, 2060, 4084, [(0, 10, 2048), (2048, 2050, 4096)]),
    ]


@pytest.mark.parametrize(
    ("mutate", "size_change", "message"),
    (
        (lambda d: d[:4] + struct.pack("<I", 2047) + d[8:], 0, "allocation is not"),
        (lambda d: d[:44] + struct.pack("<I", 4) + d[48:], 0, "member table starts"),
        (lambda d: d[:-4], 0, "vector is not terminated"),
        (lambda d: d, -1, "exceeds the BIN boundary"),
    ),
    ids=["unaligned-span", "overlapping-trailer", "missing-terminator", "bin-overrun"],
)
def test_wrong_linkdata_span_cannot_move_reads_to_another_entry(mutate, size_change, message) -> None:
    index, data = _index_fixture()
    with pytest.raises(linkdata.ArchiveError, match=message):
        linkdata.parse_index(mutate(index), len(data) + size_change)


@pytest.mark.parametrize("signature", (b"\x5c\x8f\x82\x3f", b"\xae\x47\x81\x3f"))
def test_lzp2_token_lengths_and_distance_do_not_drop_a_byte(signature: bytes) -> None:
    tokens = b"\x03abc\x80\x02\x40\x01Z\0" + bytes(6)
    fixture = struct.pack("<4s4sII", b"LZP2", signature, 11, 16) + tokens
    member = linkdata.parse_lzp2(fixture, signature)

    assert (member.decoded, member.token_bytes, member.stream_padding_bytes) == (b"abcabcZZZZZ", 10, 6)
    assert member.encode() == fixture


def test_lzp2_encoder_preserves_baseline_token_choice_and_alignment() -> None:
    expected = struct.pack("<4s4sII", b"LZP2", b"\x5c\x8f\x82\x3f", 11, 16)
    expected += b"\x03abc\x80\x02\x40\x01Z\0" + bytes(6)
    assert linkdata.encode_lzp2(b"abcabcZZZZZ") == expected


@pytest.mark.parametrize(
    "data", (b"", b"a", bytes(range(256)), b"Z" * 20000, b"0123456789" * 1000),
    ids=["empty-end-token", "single-final-literal", "literal-token-boundary", "repeat-length-boundary", "reference-length-boundary"],
)
def test_lzp2_encoder_does_not_overflow_repeat_or_reference_limits(data: bytes) -> None:
    encoded = linkdata.encode_lzp2(data)
    assert linkdata.parse_lzp2(encoded).decoded == data


@pytest.mark.parametrize(
    ("fixture", "message"),
    (
        (struct.pack("<4s4sII", b"LZP2", b"\x5c\x8f\x82\x3f", 3, 16) + b"\x80\0" + bytes(14), "back-reference exceeds"),
        (struct.pack("<4s4sII", b"LZP2", b"\x5c\x8f\x82\x3f", 1, 16) + b"\x01A\0\x01" + bytes(12), "padding is not zero"),
        (struct.pack("<4s4sII", b"LZP2", b"\x5c\x8f\x82\x3f", 1, 15) + b"\x01A\0" + bytes(12), "not 16-byte aligned"),
        (struct.pack("<4s4sII", b"LZP2", b"\xae\x47\x81\x3f", 1, 16) + b"\x01A\0" + bytes(13), "unsupported LZP2 signature"),
    ),
    ids=["reference-before-output", "nonzero-tail", "unaligned-stream", "wrong-donor-signature"],
)
def test_invalid_lzp2_stream_cannot_ship_silent_corruption(fixture: bytes, message: str) -> None:
    with pytest.raises(linkdata.ArchiveError, match=message):
        linkdata.parse_lzp2(fixture)


def test_entry_read_keeps_raw_members_and_decodes_only_lzp2(tmp_path: Path) -> None:
    packed = struct.pack("<4s4sII", b"LZP2", b"\x5c\x8f\x82\x3f", 3, 16) + b"\x03abc\0" + bytes(11)
    raw = b"raw"
    index = struct.pack("<III3I", 6, 4096, 0, len(packed), len(raw), 0)
    body = packed + bytes(2048 - len(packed)) + raw + bytes(2048 - len(raw))
    parsed = linkdata.parse_index(index, len(body))
    assert linkdata.entry_bytes(io.BytesIO(body), parsed.entries[0]) == b"abcraw"
    bin_path, idx_path = tmp_path / "test.bin", tmp_path / "test.idx"
    bin_path.write_bytes(body)
    idx_path.write_bytes(index)
    assert linkdata.moz_entries(bin_path, idx_path, [0]) == {0: b"abcraw"}


def test_shrunken_lzp2_stored_size_must_reach_the_final_allocation_sector() -> None:
    packed = linkdata.encode_lzp2(b"A" * 50)
    index = struct.pack("<III2I", 50, 4096, 0, len(packed), 0)
    with pytest.raises(linkdata.ArchiveError, match="member allocations total"):
        linkdata.parse_index(index, 4096)
    padded = bytearray(packed + bytes(2064 - len(packed)))
    struct.pack_into("<I", padded, 12, len(padded) - 16)
    index = struct.pack("<III2I", 50, 4096, 0, len(padded), 0)
    parsed = linkdata.parse_index(index, 4096)
    assert linkdata.entry_bytes(io.BytesIO(padded + bytes(4096 - len(padded))), parsed.entries[0]) == b"A" * 50


def test_wo1_big_endian_sector_count_does_not_become_a_byte_offset(tmp_path: Path) -> None:
    index = struct.pack(">4sIII8I", b"SM4L", 2, 3, 0, 0, 2, 3000, 0, 2, 1, 3, 0)
    records = linkdata.parse_wo1_index(index, data_size=6144)
    assert [asdict(r) for r in records] == [
        dict(offset_sectors=0, allocated_sectors=2, stored_bytes=3000, field_c=0),
        dict(offset_sectors=2, allocated_sectors=1, stored_bytes=3, field_c=0),
    ]
    bin_path, idx_path = tmp_path / "donor.lnk", tmp_path / "donor.idx"
    bin_path.write_bytes(b"a" * 3000 + bytes(1096) + b"end" + bytes(2045))
    idx_path.write_bytes(index)
    assert linkdata.wo1_entries(bin_path, idx_path, [1]) == {1: b"end"}


def test_wo1_index_gap_cannot_point_at_the_wrong_donor_record() -> None:
    index = struct.pack(">4sIII8I", b"SM4L", 2, 3, 0, 0, 1, 16, 0, 2, 2, 16, 0)
    with pytest.raises(linkdata.ArchiveError, match="geometry is unsupported: 1"):
        linkdata.parse_wo1_index(index, data_size=6144)


@pytest.mark.parametrize("kind", range(4))
def test_wo2_ring_match_keeps_overlapping_copy_and_kind_width(kind: int) -> None:
    packed = struct.pack("<4I", kind, 9, 24, 26) + (0b1110 << 60).to_bytes(8, "little")
    packed += struct.pack("<H", 3 << (10 + kind) | 1) + b"ABC"
    assert linkdata.unpack_member(packed) == b"ABCABCABC"


def test_wo2_raw_member_is_not_rejected_as_a_packed_header() -> None:
    raw = b"raw authored fixture"
    assert linkdata.decoded_member(raw) == raw


def test_wo2_disc_member_offsets_are_relative_to_bns_base(tmp_path: Path) -> None:
    iso = tmp_path / "donor.iso"
    with iso.open("wb") as stream:
        stream.seek(501815 * 2048)
        stream.write(struct.pack("<4I", 0x33B59, 2969, 2048, 0))
        stream.write(struct.pack("<II", 12, 3) + bytes(2968 * 8))
        stream.seek((501815 + 12) * 2048)
        stream.write(b"abc")
    assert linkdata.wo2_members(iso, [0]) == {0: b"abc"}


def test_ps2_member_sector_offsets_do_not_read_table_bytes(tmp_path: Path) -> None:
    path = tmp_path / "archive.fns"
    blob = bytearray(4099)
    struct.pack_into("<4I4I", blob, 0, 0x33B59, 2, 2048, 0, 2, 3, 1, 5)
    blob[2048:2053] = b"later"
    blob[4096:4099] = b"one"
    path.write_bytes(blob)
    assert linkdata.ps2_members(path, [1, 0]) == {1: b"later", 0: b"one"}


def test_ps2_raw_member_that_looks_packed_is_returned_without_decoding(tmp_path: Path) -> None:
    path = tmp_path / "archive.fns"
    raw = struct.pack("<4I", 0, 0, 16, 16)
    blob = struct.pack("<4I2I", 0x33B59, 1, 2048, 0, 1, len(raw))
    path.write_bytes(blob + bytes(2048 - len(blob)) + raw)
    assert linkdata.ps2_members(path, [0]) == {0: raw}


def test_ps2_negative_member_cannot_resolve_to_a_different_slot(tmp_path: Path) -> None:
    path = tmp_path / "archive.fns"
    blob = struct.pack("<4I4I", 0x33B59, 2, 2048, 0, 1, 3, 1, 3)
    path.write_bytes(blob + bytes(2048 - len(blob)) + b"one")
    with pytest.raises(linkdata.ArchiveError, match="out of range"):
        linkdata.ps2_members(path, [-1])


def test_ps2_extent_past_archive_cannot_return_truncated_donor_bytes(tmp_path: Path) -> None:
    path = tmp_path / "archive.fns"
    blob = struct.pack("<4I2I", 0x33B59, 1, 2048, 0, 1, 4)
    path.write_bytes(blob + bytes(2048 - len(blob)) + b"one")
    with pytest.raises(linkdata.ArchiveError, match="extent is invalid"):
        linkdata.ps2_members(path, [0])


@pytest.mark.skipif(os.environ.get("WOZMOD_FORMATS_ORACLE") != "1", reason="opt-in real archive oracle")
def test_real_linkdata_high_ordinal_rows_are_not_cut_off_by_a_small_table_limit() -> None:
    game_root, old_root = os.environ.get("WOZMOD_GAME"), os.environ.get("WOZMOD_OLD_REPO")
    if not game_root or not old_root:
        pytest.skip("WOZMOD_GAME and WOZMOD_OLD_REPO are required")
    old_source = Path(old_root) / "src/musou_patch/targets/musou_orochi_z/archives.py"
    bin_path, idx_path = Path(game_root) / "LINKDATA2.BIN", Path(game_root) / "LINKDATA2.IDX"
    if not all(p.is_file() for p in (old_source, bin_path, idx_path)):
        pytest.skip("archive oracle inputs are absent")
    name = "_wozmod_archive_oracle"
    old = types.ModuleType(name)
    sys.modules[name] = old
    try:
        source = subprocess.check_output([
            "git", "-C", old_root, "show", "2fa29d5:src/musou_patch/targets/musou_orochi_z/archives.py",
        ])
        exec(compile(source, str(old_source), "exec"), old.__dict__)
        blob = idx_path.read_bytes()
        before, after = old.parse_index(blob, bin_path.stat().st_size), linkdata.parse_index(blob, bin_path.stat().st_size)
        assert asdict(after) == asdict(before)
        assert after.encode() == before.encode() == blob
        with bin_path.open("rb") as stream:
            before_data = old.entry_bytes(stream, before.entries[0])
            after_data = linkdata.entry_bytes(stream, after.entries[0])
        assert after_data == before_data
        assert linkdata.encode_lzp2(after_data) == old.encode_lzp2(before_data)
    finally:
        del sys.modules[name]
