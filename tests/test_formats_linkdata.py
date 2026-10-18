from __future__ import annotations

import io
import struct
from dataclasses import asdict
from pathlib import Path

import pytest

from wozmod.formats import linkdata
from synthetic import write_ens, write_stock_archive


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


def test_lzp2_encoder_preserves_baseline_token_choice_and_alignment() -> None:
    expected = struct.pack("<4s4sII", b"LZP2", b"\x5c\x8f\x82\x3f", 11, 16)
    expected += b"\x03abc\x80\x02\x40\x01Z\0" + bytes(6)
    assert linkdata.encode_lzp2(b"abcabcZZZZZ") == expected


def test_lzp2_encoder_takes_the_longest_reference_then_the_nearest() -> None:
    # Bug: the nearest 3-byte source taken although an older one matches longer, or the oldest of two
    # equally long sources taken (both decode, but the parse is no longer the baseline's).
    data = b"abcdeQ" + b"abcdeR" + b"abcS" + b"abcde"
    stream = (b"\x06abcdeQ" + b"\x90\x05"  # literals, then 5 bytes from 6 back
              + b"\x01R" + b"\x80\x05"      # "abc" from 6 back
              + b"\x01S" + b"\x90\x09"      # "abcde": at 10 back (also at 16), not the 3-byte "abc" at 4 back
              + b"\0")
    stream += bytes(-len(stream) % 16)
    assert linkdata.encode_lzp2(data) == struct.pack("<4s4sII", b"LZP2", b"\x5c\x8f\x82\x3f", len(data), len(stream)) + stream


def _filler(size: int) -> bytes:
    return bytes(0x80 + (i * 37 + i // 97) % 0x80 for i in range(size))


@pytest.mark.parametrize(
    ("data", "tail"),
    (
        (b"abcd" + _filler(2040) + b"abc\1\2" + b"abcd", [b"\x80\x04", b"\x01d"]),
        (b"abc" + _filler(2040) + b"\1\2\3\4\5" + b"abc", [b"\x05\1\2\3\4\5", b"\x87\xff"]),
        (b"abc" + _filler(2041) + b"\1\2\3\4\5" + b"abc", [b"\x08\1\2\3\4\5abc"]),
    ),
    ids=["longer-source-2049-back", "only-source-2048-back", "only-source-2049-back"],
)
def test_lzp2_back_reference_reaches_exactly_the_2048_byte_window(data: bytes, tail: list[bytes]) -> None:
    # Bug: an off-by-one window drops the farthest legal source, takes one 2049 bytes back (its distance
    # overflows the 11-bit field), or cuts a literal run where the only source is out of the window.
    encoded = linkdata.encode_lzp2(data)
    assert linkdata.parse_lzp2(encoded).decoded == data
    stream, at, tokens = encoded[16:], 0, []
    while stream[at]:
        size = 1 + stream[at] if stream[at] < 0x40 else 3 if stream[at] < 0x80 else 2
        tokens.append(stream[at:at + size])
        at += size
    assert tokens[-len(tail):] == tail


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


def test_sector_exact_sizes_take_one_sector_and_do_not_shift_later_entries() -> None:
    # Bug: a size that is an exact multiple of 2048 (or 1 byte) rounds to the wrong sector count, so the
    # entry or member after it reads from the wrong offset.
    records = ((2048, 0, 0), (1, 0, 0), (2047, 0, 0), (7, 10240, 0))
    index = b"".join(struct.pack("<III", *row) for row in records) + struct.pack("<5I", 2048, 1, 2047, 2049, 0)
    parsed = linkdata.parse_index(index, 16384)

    assert [(e.offset, e.allocated_bytes, e.record.encode(),
             [(m.relative_offset, m.stored_bytes, m.allocated_bytes) for m in e.members])
            for e in parsed.entries] == [
        (0, 2048, struct.pack("<III", *records[0]), [(0, 2048, 2048)]),
        (2048, 2048, struct.pack("<III", *records[1]), [(0, 1, 2048)]),
        (4096, 2048, struct.pack("<III", *records[2]), [(0, 2047, 2048)]),
        (6144, 10240, struct.pack("<III", *records[3]),
         [(0, 2048, 2048), (2048, 1, 2048), (4096, 2047, 2048), (6144, 2049, 4096)]),
    ]


def test_wo1_compressed_entry_is_decoded_with_the_wo1_signature(tmp_path: Path) -> None:
    # Bug: a compressed donor entry comes back as raw LZP2 bytes, or is checked against the MoZ signature.
    packed = struct.pack("<4s4sII", b"LZP2", linkdata.WO1_LZP2_SIGNATURE, 3, 16) + b"\x03abc\0" + bytes(11)
    index = struct.pack(">4sIII8I", b"SM4L", 2, 2, 0, 0, 1, 3, 0, 1, 1, len(packed), 0)
    lnk, idx = tmp_path / "donor.lnk", tmp_path / "donor.idx"
    lnk.write_bytes(b"raw" + bytes(2045) + packed + bytes(2048 - len(packed)))
    idx.write_bytes(index)

    assert linkdata.wo1_entries(lnk, idx, [1, 0]) == {1: b"abc", 0: b"raw"}


@pytest.mark.parametrize("kind", range(4))
def test_wo2_member_with_two_control_words_and_two_long_matches_decodes_every_byte(kind: int) -> None:
    # Bug: the second control word, a match's length bits (above bit 10 + kind, in the token's high byte) or
    # its offset is read wrongly, or a member that ends on the last control bit is called truncated.
    literals = bytes(range(126))
    bits = 10 + kind
    first = ((1 << 60) - 1) << 4 | 0b0111            # 60 literals, match A, 3 literals
    second = (1 << 64) - 1 - (1 << 62)               # 1 literal, match B, 62 literals
    match_a = 11 | (5 - 3) << bits                   # out[10:15] (ring slot n + 1 holds out[n])
    match_b = 67 | (6 - 3) << bits                   # out[66:69], overlapping into itself
    packed = (struct.pack("<4I", kind, 137, 32, 36) + first.to_bytes(8, "little") + second.to_bytes(8, "little")
              + struct.pack("<2H", match_a, match_b) + literals)
    expected = (literals[:60] + literals[10:15] + literals[60:64] + literals[61:64] * 2 + literals[64:])

    assert linkdata.unpack_member(packed) == expected


def test_archive_unpacks_either_signature_joins_stock_parts_and_keeps_the_order_asked(tmp_path: Path) -> None:
    # Bug: an ENS entry packed under the other LZP2 signature (as wo2-x360 entries 35-74 are) raises, a stock
    # entry's second part is read from the first part's sector, or entries come back in index order.
    x360, wo1 = b"x360 packed " * 40, b"wo1 packed " * 40
    packed = linkdata.encode_lzp2(wo1)
    ens = write_ens(tmp_path, {0: b"raw", 1: packed[:4] + linkdata.WO1_LZP2_SIGNATURE + packed[8:],
                               2: linkdata.encode_lzp2(x360)})
    write_stock_archive(tmp_path, "LINKDATA2", {0: b"one", 1: (b"head", linkdata.encode_lzp2(x360))})

    assert list(linkdata.archive(ens).read([2, 0, 1]).items()) == [(2, x360), (0, b"raw"), (1, wo1)]
    assert linkdata.archive(tmp_path / "LINKDATA2")[1] == b"head" + x360


def test_ps2_table_longer_than_a_sector_cannot_be_read_as_a_member(tmp_path: Path) -> None:
    # Bug: a member pointing inside a member table that runs past sector 0 returns the table's own bytes, or
    # the member one past the last is read from beyond the table.
    path = tmp_path / "archive.fns"
    blob = bytearray(4099)
    struct.pack_into("<4I", blob, 0, 0x33B59, 300, 2048, 0)
    struct.pack_into("<4I", blob, 16, 2, 3, 1, 3)    # member 0 at sector 2, member 1 at sector 1 (the table)
    blob[4096:4099] = b"ok!"
    path.write_bytes(blob)

    assert linkdata.ps2_members(path, [0]) == {0: b"ok!"}
    with pytest.raises(linkdata.ArchiveError, match="extent is invalid: 1"):
        linkdata.ps2_members(path, [1])
    with pytest.raises(linkdata.ArchiveError, match="out of range: 300"):
        linkdata.ps2_members(path, [300])
