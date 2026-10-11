"""LINKDATA readers and LZP2 codec."""

from __future__ import annotations

import collections
import struct
from dataclasses import dataclass
from pathlib import Path

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
RECORD = struct.Struct("<III")
WORD = struct.Struct("<I")
SM4L_HEADER = struct.Struct(">4sIII")
SM4L_RECORD = struct.Struct(">IIII")
PS2_HEADER = struct.Struct("<4I")
PS2_RECORD = struct.Struct("<II")
# WO2 PS2 USA: LINKDATA.BNS starts at disc sector 501815 with a 2969-member table.
BNS_BASE = 501815 * SECTOR_SIZE
WO2_BNS_HEADER = struct.pack("<4I", 0x33B59, 2969, 2048, 0)


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

    def encode(self) -> bytes:

        return self.source


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
            output.extend(stream[position:end])
            position = end
        elif token < 0x80:
            if position + 2 > len(stream):
                raise ArchiveError("LZP2 repeated-byte run is truncated")
            run_length = ((token & 0x3F) << 8) | stream[position]
            run_length += 4
            output.extend(bytes((stream[position + 1],)) * run_length)
            position += 2
        else:
            if position >= len(stream):
                raise ArchiveError("LZP2 back-reference is truncated")
            run_length = ((token >> 3) & 0x0F) + 3
            distance = ((token & 0x07) << 8) | stream[position]
            distance += 1
            position += 1
            if distance > len(output):
                raise ArchiveError("LZP2 back-reference exceeds output")
            for _ in range(run_length):
                output.append(output[-distance])
        if len(output) > expected_size:
            raise ArchiveError("LZP2 output exceeds its declared size")
    raise ArchiveError("LZP2 stream has no end token")


def parse_lzp2(data: bytes, signature: bytes = LZP2_SIGNATURE) -> Lzp2Member:

    source = bytes(data)
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
    decoded, token_bytes = _decode_stream(stream, expected_size)
    return Lzp2Member(
        source=source,
        signature=signature,
        decoded=decoded,
        compressed_bytes=compressed_size,
        token_bytes=token_bytes,
        stream_padding_bytes=compressed_size - token_bytes,
    )


def _repeat_length(data: bytes, position: int) -> int:
    length = 1
    limit = min(MAX_REPEAT_BYTES, len(data) - position)
    while length < limit and data[position + length] == data[position]:
        length += 1
    return length


def _encode_stream(data: bytes) -> bytes:
    positions: dict[bytes, collections.deque[int]] = collections.defaultdict(
        collections.deque
    )

    def add_position(position: int) -> None:
        expired = position - MAX_BACK_REFERENCE_DISTANCE
        if expired >= 0 and expired + 3 <= len(data):
            expired_key = data[expired : expired + 3]
            expired_positions = positions.get(expired_key)
            if expired_positions and expired_positions[0] == expired:
                expired_positions.popleft()
                if not expired_positions:
                    del positions[expired_key]
        if position + 3 <= len(data):
            positions[data[position : position + 3]].append(position)

    def match_at(position: int) -> tuple[int, int]:
        if position + 3 > len(data):
            return 0, 0
        candidates = positions.get(data[position : position + 3], ())
        best_length = 0
        best_distance = 0
        limit = min(MAX_BACK_REFERENCE_BYTES, len(data) - position)
        for previous in reversed(candidates):
            distance = position - previous
            length = 3
            while (
                length < limit
                and data[position + length] == data[position + length - distance]
            ):
                length += 1
            if length > best_length:
                best_length = length
                best_distance = distance
            if best_length == limit:
                break
        return best_length, best_distance

    def advance(start: int, count: int) -> int:
        end = start + count
        for visited in range(start, end):
            add_position(visited)
        return end

    stream = bytearray()
    position = 0
    while position < len(data):
        repeat_length = _repeat_length(data, position)
        match_length, distance = match_at(position)
        if repeat_length >= 4 and (
            repeat_length > match_length
            or repeat_length > MAX_BACK_REFERENCE_BYTES
        ):
            encoded_length = repeat_length - 4
            stream.extend(
                (
                    0x40 | (encoded_length >> 8),
                    encoded_length & 0xFF,
                    data[position],
                )
            )
            position = advance(position, repeat_length)
            continue
        if match_length >= 3:
            encoded_distance = distance - 1
            stream.extend(
                (
                    0x80
                    | ((match_length - 3) << 3)
                    | (encoded_distance >> 8),
                    encoded_distance & 0xFF,
                )
            )
            position = advance(position, match_length)
            continue

        literal_start = position
        position = advance(position, 1)
        while position < len(data) and position - literal_start < MAX_LITERAL_BYTES:
            next_repeat = _repeat_length(data, position)
            next_match, _distance = match_at(position)
            if next_repeat >= 4 or next_match >= 3:
                break
            position = advance(position, 1)
        stream.append(position - literal_start)
        stream.extend(data[literal_start:position])
    stream.append(0)
    stream.extend(bytes(-len(stream) % LZP2_STREAM_ALIGNMENT))
    return bytes(stream)


def encode_lzp2(data: bytes) -> bytes:

    decoded = bytes(data)
    if len(decoded) > 0xFFFFFFFF:
        raise ArchiveError("LZP2 input exceeds its 32-bit size field")
    stream = _encode_stream(decoded)
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

    data = bytes(data)
    records, trailer_offset = _preliminary_entries(data, data_size)
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
    if data_offset != data_size:
        raise ArchiveError("LINKDATA record allocations do not consume the BIN")
    return LinkdataIndex(
        records=records,
        trailer=trailer,
        entries=tuple(entries),
        data_size=data_size,
    )


def encode_records(
    records: tuple[LinkdataIdxRecord, ...] | list[LinkdataIdxRecord],
    trailer: bytes = b"",
) -> bytes:

    return b"".join(record.encode() for record in records) + bytes(trailer)


def entry_bytes(stream, entry: LinkdataEntryGeometry) -> bytes:
    stream.seek(entry.offset)
    blob = stream.read(entry.allocated_bytes)
    parts = []
    for member in entry.members:
        data = blob[member.relative_offset:member.relative_offset + member.stored_bytes]
        parts.append(parse_lzp2(data).decoded if data[:4] == b"LZP2" else data)
    return b"".join(parts)


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


def wo2_members(iso_path: Path, members) -> dict[int, bytes]:
    entries = {}
    with iso_path.open("rb") as stream:
        stream.seek(BNS_BASE)
        if stream.read(16) != WO2_BNS_HEADER:
            raise ValueError("unexpected WO2 LINKDATA.BNS header")
        table = stream.read(2969 * 8)
        for member in members:
            block, size = struct.unpack_from("<II", table, member * 8)
            stream.seek(BNS_BASE + block * SECTOR_SIZE)
            entries[member] = stream.read(size)
    return entries


def ps2_members(archive_path: Path, members) -> dict[int, bytes]:
    """Read stored members from a sector-indexed PS2-layout archive."""
    entries = {}
    with archive_path.open("rb") as stream:
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
        table = stream.read(count * PS2_RECORD.size)
        for member in members:
            if not 0 <= member < count:
                raise ArchiveError(f"PS2 archive member is out of range: {member}")
            sector, size = PS2_RECORD.unpack_from(table, member * PS2_RECORD.size)
            offset = sector * SECTOR_SIZE
            if offset + size > data_size or (size and offset < table_end):
                raise ArchiveError(f"PS2 archive member extent is invalid: {member}")
            stream.seek(offset)
            data = stream.read(size)
            entries[member] = data
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

