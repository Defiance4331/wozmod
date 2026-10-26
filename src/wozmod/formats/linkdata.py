"""LINKDATA readers and LZP2 codec."""

from __future__ import annotations

import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from wozmod import native

LZP2_MAGIC = b"LZP2"
LZP2_SIGNATURE = bytes.fromhex("5c8f823f")
WO1_LZP2_SIGNATURE = bytes.fromhex("ae47813f")
LZP2_HEADER = struct.Struct("<4s4sII")
LZP2_STREAM_ALIGNMENT = 16
MAX_LITERAL_BYTES = 0x3F
MAX_BACK_REFERENCE_BYTES = 18
MAX_BACK_REFERENCE_DISTANCE = 0x800
MAX_REPEAT_BYTES = 0x3F03
SECTOR_SIZE = 2_048
LINKDATA2_FIRST = 4_251  # the game numbers LINKDATA1 entries from 0, then LINKDATA2 entries from here
RECORD = struct.Struct("<III")
WORD = struct.Struct("<I")
SM4L_HEADER = struct.Struct(">4sIII")
SM4L_RECORD = struct.Struct(">IIII")
PS2_HEADER = struct.Struct("<4I")
PS2_RECORD = struct.Struct("<II")
# WO2 PS2 USA: LINKDATA.BNS starts at disc sector 501815 with a 2969-member table.


class ArchiveError(ValueError):
    """Invalid archive bytes."""


@dataclass(frozen=True)
class Lzp2Member:

    source: bytes
    signature: bytes
    decoded: bytes
    compressed_bytes: int
    token_bytes: int
    stream_padding_bytes: int


def _decode_stream(stream: bytes, expected_size: int) -> tuple[bytes, int]:
    output = bytearray()
    position = 0
    while position < len(stream):
        token = stream[position]
        position += 1
        if token == 0:
            if any(stream[position:]):
                raise ArchiveError("LZP2 stream padding is not zero")
            if len(output) != expected_size:
                raise ArchiveError(
                    "LZP2 output size does not match its declared size"
                )
            return bytes(output), position
        if token < 0x40:
            end = position + token
            if end > len(stream):
                raise ArchiveError("LZP2 literal run is truncated")
            output += stream[position:end]
            position = end
        elif token < 0x80:
            if position + 2 > len(stream):
                raise ArchiveError("LZP2 repeated-byte run is truncated")
            run_length = ((token & 0x3F) << 8) | stream[position]
            run_length += 4
            output += stream[position + 1:position + 2] * run_length
            position += 2
        else:
            if position >= len(stream):
                raise ArchiveError("LZP2 back-reference is truncated")
            run_length = ((token >> 3) & 0x0F) + 3
            distance = ((token & 0x07) << 8) | stream[position]
            distance += 1
            position += 1
            start = len(output) - distance
            if start < 0:
                raise ArchiveError("LZP2 back-reference exceeds output")
            if distance >= run_length:
                output += output[start:start + run_length]
            else:  # the copy overlaps its own output: repeat the last distance bytes
                output += (output[start:] * (run_length // distance + 1))[:run_length]
        if len(output) > expected_size:
            raise ArchiveError("LZP2 output exceeds its declared size")
    raise ArchiveError("LZP2 stream has no end token")


def parse_lzp2(data: bytes, signature: bytes = LZP2_SIGNATURE) -> Lzp2Member:

    return _parse_lzp2(bytes(data), signature)


def _parse_lzp2(source: bytes, signature: bytes) -> Lzp2Member:
    if len(source) < LZP2_HEADER.size:
        raise ArchiveError("LZP2 header is truncated")
    magic, found, expected_size, compressed_size = LZP2_HEADER.unpack_from(source)
    if magic != LZP2_MAGIC:
        raise ArchiveError("unsupported LZP2 magic")
    if found != signature:
        raise ArchiveError(f"unsupported LZP2 signature {found.hex()}")
    if compressed_size == 0 or compressed_size % LZP2_STREAM_ALIGNMENT:
        raise ArchiveError("LZP2 stream size is not 16-byte aligned")
    if LZP2_HEADER.size + compressed_size != len(source):
        raise ArchiveError("LZP2 stored size does not match its header")
    stream = source[LZP2_HEADER.size :]
    if native.module:
        try:
            decoded, token_bytes = native.module.lzp2_decode_stream(stream, expected_size)
        except ValueError as error:
            raise ArchiveError(str(error)) from None
    else:
        decoded, token_bytes = _decode_stream(stream, expected_size)
    return Lzp2Member(
        source=source,
        signature=signature,
        decoded=decoded,
        compressed_bytes=compressed_size,
        token_bytes=token_bytes,
        stream_padding_bytes=compressed_size - token_bytes,
    )


def _encode_stream(data: bytes) -> bytes:
    """Greedy parse: a run of 4+ equal bytes, else the longest (then nearest) back-reference, else literals."""
    n = len(data)
    window = MAX_BACK_REFERENCE_DISTANCE
    # stops[q] is 1 where a run or a back-reference can start; literal runs extend to the next stop.
    stops = bytearray(n)
    last: dict[bytes, int] = {}
    get = last.get
    for q in range(n - 2):
        key = data[q:q + 3]
        if q - get(key, -window - 1) <= window:
            stops[q] = 1
        last[key] = q
    for run in re.finditer(rb"(.)\1{3,}", data, re.S):
        stops[run.start()] = 1

    stream = bytearray()
    position = 0
    while position < n:
        if stops[position]:
            repeat_length = 1
            if data[position:position + 4] == data[position:position + 1] * 4:
                run = data[position:position + MAX_REPEAT_BYTES]
                repeat_length = len(run) - len(run.lstrip(run[:1]))
            match_length = distance = 0
            low = max(0, position - window)
            found = data.rfind(data[position:position + 3], low, position + 2)
            if found >= 0:
                limit = min(MAX_BACK_REFERENCE_BYTES, n - position)
                target = int.from_bytes(data[position:position + limit])
                while True:
                    # Measure the nearest source found so far, then look for the nearest source of a longer match.
                    differ = target ^ int.from_bytes(data[found:found + limit])
                    match_length = limit - 1 - (differ.bit_length() - 1) // 8 if differ else limit
                    distance = position - found
                    if match_length == limit:
                        break
                    found = data.rfind(data[position:position + match_length + 1], low, position + match_length)
                    if found < 0:
                        break
            if repeat_length >= 4 and (
                repeat_length > match_length
                or repeat_length > MAX_BACK_REFERENCE_BYTES
            ):
                encoded_length = repeat_length - 4
                stream += bytes((0x40 | (encoded_length >> 8), encoded_length & 0xFF, data[position]))
                position += repeat_length
                continue
            if match_length >= 3:
                encoded_distance = distance - 1
                stream += bytes((0x80 | ((match_length - 3) << 3) | (encoded_distance >> 8),
                                 encoded_distance & 0xFF))
                position += match_length
                continue
        literal_start = position
        end = min(n, literal_start + MAX_LITERAL_BYTES)
        position = stops.find(1, position + 1, end)
        if position < 0:
            position = end
        stream.append(position - literal_start)
        stream += data[literal_start:position]
    stream.append(0)
    stream.extend(bytes(-len(stream) % LZP2_STREAM_ALIGNMENT))
    return bytes(stream)


def encode_lzp2(data: bytes) -> bytes:

    decoded = bytes(data)
    if len(decoded) > 0xFFFFFFFF:
        raise ArchiveError("LZP2 input exceeds its 32-bit size field")
    stream = (native.module.lzp2_encode_stream if native.module else _encode_stream)(decoded)
    return LZP2_HEADER.pack(
        LZP2_MAGIC,
        LZP2_SIGNATURE,
        len(decoded),
        len(stream),
    ) + stream


def align(value: int) -> int:
    return (value + SECTOR_SIZE - 1) // SECTOR_SIZE * SECTOR_SIZE


@dataclass(frozen=True)
class LinkdataIdxRecord:

    size_or_descriptor: int
    allocated_bytes: int
    member_table_offset: int

    def encode(self) -> bytes:

        return RECORD.pack(
            self.size_or_descriptor,
            self.allocated_bytes,
            self.member_table_offset,
        )

    def allocation_size(self) -> int:

        if self.allocated_bytes:
            return self.allocated_bytes
        if self.size_or_descriptor:
            return align(self.size_or_descriptor)
        return 0


@dataclass(frozen=True)
class LinkdataMemberGeometry:

    relative_offset: int
    stored_bytes: int
    allocated_bytes: int


@dataclass(frozen=True)
class LinkdataEntryGeometry:

    ordinal: int
    offset: int
    allocated_bytes: int
    record: LinkdataIdxRecord
    stored_sizes: tuple[int, ...]

    @property
    def stored_bytes(self) -> int:

        return sum(self.stored_sizes)

    @property
    def padding_bytes(self) -> int:

        return self.allocated_bytes - self.stored_bytes

    @property
    def members(self) -> tuple[LinkdataMemberGeometry, ...]:

        offset = 0
        members: list[LinkdataMemberGeometry] = []
        for stored_bytes in self.stored_sizes:
            allocated_bytes = align(stored_bytes)
            members.append(
                LinkdataMemberGeometry(
                    relative_offset=offset,
                    stored_bytes=stored_bytes,
                    allocated_bytes=allocated_bytes,
                )
            )
            offset += allocated_bytes
        return tuple(members)


@dataclass(frozen=True)
class LinkdataIndex:

    records: tuple[LinkdataIdxRecord, ...]
    trailer: bytes
    entries: tuple[LinkdataEntryGeometry, ...]
    data_size: int

    def encode(self) -> bytes:

        return b"".join(record.encode() for record in self.records) + self.trailer


def _preliminary_entries(
    data: bytes, data_size: int
) -> tuple[tuple[LinkdataIdxRecord, ...], int]:
    if data_size <= 0:
        raise ArchiveError("LINKDATA BIN must contain at least one byte")
    records: list[LinkdataIdxRecord] = []
    data_offset = 0
    index_offset = 0
    while data_offset < data_size:
        if index_offset + RECORD.size > len(data):
            raise ArchiveError(
                "LINKDATA index record table ends before the BIN boundary"
            )
        record = LinkdataIdxRecord(*RECORD.unpack_from(data, index_offset))
        if record.allocated_bytes % SECTOR_SIZE:
            raise ArchiveError(
                f"record {len(records)} allocation is not {SECTOR_SIZE}-byte aligned"
            )
        if record.allocated_bytes and not record.size_or_descriptor:
            raise ArchiveError(
                f"record {len(records)} has an allocation without field A"
            )
        if not record.allocated_bytes and record.member_table_offset:
            raise ArchiveError(
                f"record {len(records)} has a trailer offset without an allocation"
            )
        allocation = record.allocation_size()
        if data_offset + allocation > data_size:
            raise ArchiveError(
                f"record {len(records)} allocation exceeds the BIN boundary"
            )
        records.append(record)
        data_offset += allocation
        index_offset += RECORD.size
    return tuple(records), index_offset


def parse_index(data: bytes, data_size: int) -> LinkdataIndex:
    """The entries of a stock LINKDATA index over a BIN of `data_size` bytes.

    The record table ends where the allocations fill the BIN. A BIN longer than its index (bytes past the last
    entry, as another mod's tool may leave them) ends it instead at the record count whose member-size trailer the
    records reference exactly.
    """
    data = bytes(data)
    try:
        records, trailer_offset = _preliminary_entries(data, data_size)
        return _with_trailer(data, records, trailer_offset, data_size)
    except ArchiveError as error:
        for records in _tables_short_of(data, data_size):
            try:
                return _with_trailer(data, records, RECORD.size * len(records), data_size)
            except ArchiveError:
                continue
        raise error


def _tables_short_of(data: bytes, data_size: int):
    """Each record table whose records are well formed, whose allocations fit in the BIN without filling it, and
    whose last member-size vector ends exactly at the end of the index, shortest first."""
    if len(data) % WORD.size or len(data) < WORD.size or WORD.unpack_from(data, len(data) - WORD.size)[0]:
        return
    words = len(data) // WORD.size - 1  # the last vector runs from just past the zero word before the final one
    tail = next((k + 1 for k in range(words - 1, -1, -1) if not WORD.unpack_from(data, WORD.size * k)[0]), 0)
    tail *= WORD.size
    records: list[LinkdataIdxRecord] = []
    data_offset, last_vector = 0, None
    while RECORD.size * (len(records) + 1) <= len(data):
        record = LinkdataIdxRecord(*RECORD.unpack_from(data, RECORD.size * len(records)))
        allocation = record.allocation_size()
        if (record.allocated_bytes % SECTOR_SIZE or (record.allocated_bytes and not record.size_or_descriptor)
                or (not record.allocated_bytes and record.member_table_offset)
                or data_offset + allocation >= data_size):
            return
        records.append(record)
        data_offset += allocation
        if record.allocated_bytes:
            last_vector = record.member_table_offset
        trailer = RECORD.size * len(records)
        if (trailer == len(data) if last_vector is None
                else tail <= trailer + last_vector <= len(data) - 2 * WORD.size):
            yield tuple(records)


def _with_trailer(data: bytes, records: tuple[LinkdataIdxRecord, ...], trailer_offset: int,
                  data_size: int) -> LinkdataIndex:
    trailer = data[trailer_offset:]
    expected_member_table_offset = 0
    data_offset = 0
    entries: list[LinkdataEntryGeometry] = []
    for ordinal, record in enumerate(records):
        if record.allocated_bytes:
            if record.member_table_offset != expected_member_table_offset:
                raise ArchiveError(
                    f"record {ordinal} member table starts at "
                    f"{record.member_table_offset}, expected "
                    f"{expected_member_table_offset}"
                )
            stored_sizes: list[int] = []
            while True:
                if expected_member_table_offset + WORD.size > len(trailer):
                    raise ArchiveError(
                        f"record {ordinal} member-size vector is not terminated"
                    )
                stored_size = WORD.unpack_from(
                    trailer, expected_member_table_offset
                )[0]
                expected_member_table_offset += WORD.size
                if stored_size == 0:
                    break
                stored_sizes.append(stored_size)
            if not stored_sizes:
                raise ArchiveError(
                    f"record {ordinal} member-size vector is empty"
                )
            member_allocation = sum(align(size) for size in stored_sizes)
            if member_allocation != record.allocated_bytes:
                raise ArchiveError(
                    f"record {ordinal} member allocations total "
                    f"{member_allocation}, expected {record.allocated_bytes}"
                )
        elif record.size_or_descriptor:
            stored_sizes = [record.size_or_descriptor]
        else:
            stored_sizes = []
        allocated_bytes = record.allocation_size()
        entries.append(
            LinkdataEntryGeometry(
                ordinal=ordinal,
                offset=data_offset,
                allocated_bytes=allocated_bytes,
                record=record,
                stored_sizes=tuple(stored_sizes),
            )
        )
        data_offset += allocated_bytes
    if expected_member_table_offset != len(trailer):
        raise ArchiveError(
            "LINKDATA member-size trailer contains unreferenced bytes"
        )
    return LinkdataIndex(
        records=records,
        trailer=trailer,
        entries=tuple(entries),
        data_size=data_size,
    )


def entry_bytes(stream, entry: LinkdataEntryGeometry) -> bytes:
    stream.seek(entry.offset)
    blob = stream.read(entry.allocated_bytes)
    parts = []
    for member in entry.members:
        data = blob[member.relative_offset:member.relative_offset + member.stored_bytes]
        parts.append(parse_lzp2(data).decoded if data[:4] == b"LZP2" else data)
    return b"".join(parts)


def unpacked(data: bytes) -> bytes:
    """`data`, or what it holds when it is LZP2 packed under either known signature."""
    if data[:4] != LZP2_MAGIC:
        return data
    return parse_lzp2(data, data[4:8] if data[4:8] == WO1_LZP2_SIGNATURE else LZP2_SIGNATURE).decoded


class Archive(Mapping):
    """One archive's entries by ordinal, each with its parts joined and unpacked."""

    def __init__(self, path: Path, entries: tuple[tuple[int, tuple[int, ...]], ...]):
        self.path, self._entries = path, entries  # (offset, stored size of each part) per ordinal

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(range(len(self._entries)))

    def __contains__(self, ordinal) -> bool:
        return type(ordinal) is int and 0 <= ordinal < len(self._entries)

    def __getitem__(self, ordinal: int) -> bytes:
        return self.read([ordinal])[ordinal]

    def parts(self, ordinal: int) -> int:
        return len(self._entries[ordinal][1])

    def read(self, ordinals, unpack: bool = True) -> dict[int, bytes]:
        """Several entries with one open, in the order asked; with unpack False, as stored (LZP2 left packed)."""
        found = {}
        with self.path.open("rb") as stream:
            for ordinal in ordinals:
                if ordinal not in self:
                    raise KeyError(ordinal)
                offset, sizes = self._entries[ordinal]
                parts = []
                for size in sizes:
                    stream.seek(offset)
                    parts.append(unpacked(stream.read(size)) if unpack else stream.read(size))
                    offset += align(size)
                found[ordinal] = b"".join(parts)
        return found


def archive(stem: Path) -> Archive:
    """The archive at `stem`: a donor ENS archive when `stem`.lnk exists, else stock LINKDATA (`stem`.BIN/.IDX)."""
    stem = Path(stem)
    lnk = stem.parent / f"{stem.name}.lnk"
    if lnk.exists():
        records = parse_wo1_index((stem.parent / f"{stem.name}.idx").read_bytes(), data_size=lnk.stat().st_size)
        return Archive(lnk, tuple((r.offset_sectors * SECTOR_SIZE, (r.stored_bytes,)) for r in records))
    data = stem.parent / f"{stem.name}.BIN"
    index = parse_index((stem.parent / f"{stem.name}.IDX").read_bytes(), data.stat().st_size)
    return Archive(data, tuple((e.offset, e.stored_sizes) for e in index.entries))


def moz_entries(bin_path: Path, idx_path: Path, ordinals) -> dict[int, bytes]:
    index = parse_index(idx_path.read_bytes(), bin_path.stat().st_size)
    with bin_path.open("rb") as stream:
        return {o: entry_bytes(stream, index.entries[o]) for o in ordinals}


@dataclass(frozen=True)
class Wo1IndexRecord:

    offset_sectors: int
    allocated_sectors: int
    stored_bytes: int
    field_c: int


def parse_wo1_index(data: bytes, *, data_size: int) -> tuple[Wo1IndexRecord, ...]:

    if len(data) < SM4L_HEADER.size:
        raise ArchiveError("donor index header is truncated")
    magic, record_count, total_sectors, reserved = SM4L_HEADER.unpack_from(data)
    if magic != b"SM4L" or reserved != 0:
        raise ArchiveError("donor index header is unsupported")
    if len(data) != SM4L_HEADER.size + record_count * SM4L_RECORD.size:
        raise ArchiveError("donor index size does not match its record count")
    if total_sectors * SECTOR_SIZE != data_size:
        raise ArchiveError("donor index sector count does not match its data file")
    records = tuple(
        Wo1IndexRecord(*values)
        for values in SM4L_RECORD.iter_unpack(data[SM4L_HEADER.size:])
    )
    expected_sector = 0
    for ordinal, record in enumerate(records):
        if record.offset_sectors != expected_sector or record.field_c != 0:
            raise ArchiveError(f"donor index record geometry is unsupported: {ordinal}")
        if record.stored_bytes > record.allocated_sectors * SECTOR_SIZE:
            raise ArchiveError(f"donor index record exceeds its allocation: {ordinal}")
        expected_sector += record.allocated_sectors
    if expected_sector != total_sectors:
        raise ArchiveError("donor index records do not consume all declared sectors")
    return records


def wo1_entries(lnk_path: Path, idx_path: Path, ordinals) -> dict[int, bytes]:
    index = parse_wo1_index(idx_path.read_bytes(), data_size=lnk_path.stat().st_size)
    entries = {}
    with lnk_path.open("rb") as stream:
        for ordinal in ordinals:
            record = index[ordinal]
            stream.seek(record.offset_sectors * SECTOR_SIZE)
            data = stream.read(record.stored_bytes)
            if data[:4] == b"LZP2":
                data = parse_lzp2(data, WO1_LZP2_SIGNATURE).decoded
            entries[ordinal] = data
    return entries


def _ps2_table(stream: BinaryIO) -> tuple[list[tuple[int, int]], int, int]:
    """A PS2-layout archive's (sector, size) records, with the table end and the archive size."""
    stream.seek(0, 2)
    data_size = stream.tell()
    stream.seek(0)
    header = stream.read(PS2_HEADER.size)
    if len(header) != PS2_HEADER.size:
        raise ArchiveError("PS2 archive header is truncated")
    magic, count, sector_size, reserved = PS2_HEADER.unpack(header)
    if magic != 0x33B59 or sector_size != SECTOR_SIZE or reserved != 0:
        raise ArchiveError("PS2 archive header is unsupported")
    table_end = PS2_HEADER.size + count * PS2_RECORD.size
    if table_end > data_size:
        raise ArchiveError("PS2 archive member table is truncated")
    return list(PS2_RECORD.iter_unpack(stream.read(count * PS2_RECORD.size))), table_end, data_size


def _ps2_extent(records: list[tuple[int, int]], member: int, table_end: int, data_size: int) -> tuple[int, int]:
    if not 0 <= member < len(records):
        raise ArchiveError(f"PS2 archive member is out of range: {member}")
    sector, size = records[member]
    offset = sector * SECTOR_SIZE
    if offset + size > data_size or (size and offset < table_end):
        raise ArchiveError(f"PS2 archive member extent is invalid: {member}")
    return offset, size


def ps2_extents(archive_path: Path) -> list[tuple[int, int]]:
    """The byte offset and size of every member of a sector-indexed PS2-layout archive."""
    with archive_path.open("rb") as stream:
        records, table_end, data_size = _ps2_table(stream)
    return [_ps2_extent(records, member, table_end, data_size) for member in range(len(records))]


def ps2_members(archive_path: Path, members) -> dict[int, bytes]:
    """Read stored members from a sector-indexed PS2-layout archive."""
    entries = {}
    with archive_path.open("rb") as stream:
        records, table_end, data_size = _ps2_table(stream)
        for member in members:
            offset, size = _ps2_extent(records, member, table_end, data_size)
            stream.seek(offset)
            entries[member] = stream.read(size)
    return entries


def unpack_member(data: bytes) -> bytes:
    kind, size, match, literal = struct.unpack_from("<4I", data)
    if kind > 3 or not 16 <= match <= literal <= len(data):
        raise ValueError("not a packed LINKDATA.BNS member")
    bits = 10 + kind
    mask = (1 << bits) - 1
    ring, out = bytearray(mask + 1), bytearray()

    def put(byte: int) -> None:
        ring[(len(out) + 1) & mask] = byte
        out.append(byte)

    for word in range(16, match, 8):
        control = int.from_bytes(data[word:word + 8], "little")
        for bit in range(63, -1, -1):
            if len(out) >= size:
                return bytes(out[:size])
            if control >> bit & 1:
                put(data[literal])
                literal += 1
            else:
                token = data[match] | data[match + 1] << 8
                match += 2
                for k in range((token >> bits) + 3):
                    put(ring[(token + k) & mask])
    if len(out) < size:
        raise ValueError("packed LINKDATA.BNS member ends early")
    return bytes(out[:size])


def decoded_member(data: bytes) -> bytes:
    try:
        return unpack_member(data)
    except ValueError:
        return data

