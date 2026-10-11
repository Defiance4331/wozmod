"""Voice and sound-bank formats."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import struct
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

from wozmod import resources

KOVS_HEADER_BYTES = 32
VOICE_RECORD_ALIGNMENT = 16
OGG_CAPTURE_PATTERN = b"OggS"


class VoiceBankError(ValueError):
    """Invalid voice-bank structure."""


@dataclass(frozen=True)
class OggStreamMetadata:
    """Ogg stream metadata."""

    page_count: int
    stream_serial: int
    channels: int
    sample_rate: int
    sample_count: int


@dataclass(frozen=True)
class VoiceBankRecord:
    """KOVS record geometry and identity."""

    ordinal: int
    offset: int
    stream_bytes: int
    logical_bytes: int
    allocated_bytes: int
    padding_bytes: int
    loop_start_sample: int | None
    sha256: str
    ogg: OggStreamMetadata


@dataclass(frozen=True)
class VoiceBankLayout:
    """Voice-bank geometry and identity."""

    source_bytes: int
    source_sha256: str
    records: tuple[VoiceBankRecord, ...]


@contextmanager
def _open_read_only_regular(path: Path, label: str) -> Iterator[BinaryIO]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VoiceBankError(f"cannot open {label} read-only: {path}") from exc
    with os.fdopen(descriptor, "rb") as stream:
        status = os.fstat(stream.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise VoiceBankError(f"{label} is not a regular file: {path}")
        yield stream


def _read_exact(stream: BinaryIO, size: int, label: str) -> bytes:
    payload = stream.read(size)
    if len(payload) != size:
        raise VoiceBankError(f"{label} is truncated")
    return payload


def _aligned_size(logical_bytes: int) -> int:
    return (
        (logical_bytes + VOICE_RECORD_ALIGNMENT - 1)
        // VOICE_RECORD_ALIGNMENT
        * VOICE_RECORD_ALIGNMENT
    )


_BIT_REVERSED = bytes(int(f"{value:08b}"[::-1], 2) for value in range(256))


def _ogg_checksum(payload: bytes | bytearray) -> int:
    """Ogg's MSB-first CRC-32 (polynomial 0x04C11DB7, zero start), computed by zlib on bit-reversed bytes."""
    reflected = zlib.crc32(bytes(payload).translate(_BIT_REVERSED), 0xFFFFFFFF) ^ 0xFFFFFFFF
    return int(f"{reflected:032b}"[::-1], 2)


def _parse_ogg_stream(
    encoded_stream: bytes,
    *,
    ordinal: int,
    verify_checksums: bool,
) -> OggStreamMetadata:
    stream = bytearray(encoded_stream)
    for position in range(min(256, len(stream))):
        stream[position] ^= position

    offset = 0
    page_count = 0
    stream_serial: int | None = None
    sample_count: int | None = None
    first_packet: bytes | None = None
    packet = bytearray()
    packet_continues = False
    saw_eos = False

    while offset < len(stream):
        if len(stream) - offset < 27 or stream[offset : offset + 4] != OGG_CAPTURE_PATTERN:
            raise VoiceBankError(
                f"record {ordinal} has an invalid Ogg page at stream byte {offset}"
            )
        if stream[offset + 4] != 0:
            raise VoiceBankError(f"record {ordinal} uses an unsupported Ogg version")
        header_type = stream[offset + 5]
        if header_type & ~0x07:
            raise VoiceBankError(f"record {ordinal} has invalid Ogg header flags")
        if page_count == 0:
            if not header_type & 0x02 or header_type & 0x01:
                raise VoiceBankError(f"record {ordinal} lacks a valid Ogg BOS page")
        elif header_type & 0x02:
            raise VoiceBankError(f"record {ordinal} has a second Ogg BOS page")
        if bool(header_type & 0x01) != packet_continues:
            raise VoiceBankError(
                f"record {ordinal} has inconsistent Ogg packet continuation"
            )

        granule, page_serial, sequence = struct.unpack_from("<QII", stream, offset + 6)
        if stream_serial is None:
            stream_serial = page_serial
        elif page_serial != stream_serial:
            raise VoiceBankError(f"record {ordinal} contains multiple Ogg streams")
        if sequence != page_count:
            raise VoiceBankError(f"record {ordinal} Ogg page sequence is not contiguous")

        segment_count = stream[offset + 26]
        table_end = offset + 27 + segment_count
        if table_end > len(stream):
            raise VoiceBankError(f"record {ordinal} Ogg segment table is truncated")
        lacing = stream[offset + 27 : table_end]
        body_bytes = sum(lacing)
        page_end = table_end + body_bytes
        if page_end > len(stream):
            raise VoiceBankError(f"record {ordinal} Ogg page body is truncated")
        if verify_checksums:
            page = bytearray(stream[offset:page_end])
            expected_checksum = struct.unpack_from("<I", page, 22)[0]
            page[22:26] = bytes(4)
            if _ogg_checksum(page) != expected_checksum:
                raise VoiceBankError(f"record {ordinal} Ogg page checksum is invalid")

        body_position = table_end
        for segment_bytes in lacing:
            segment_end = body_position + segment_bytes
            if first_packet is None:
                packet.extend(stream[body_position:segment_end])
            body_position = segment_end
            packet_continues = segment_bytes == 255
            if not packet_continues and first_packet is None:
                first_packet = bytes(packet)
                packet.clear()

        is_final_page = page_end == len(stream)
        if header_type & 0x04:
            if not is_final_page:
                raise VoiceBankError(f"record {ordinal} has an early Ogg EOS page")
            saw_eos = True
        if granule != 0xFFFFFFFFFFFFFFFF:
            sample_count = granule
        offset = page_end
        page_count += 1

    if page_count == 0 or stream_serial is None:
        raise VoiceBankError(f"record {ordinal} has an empty Ogg stream")
    if not saw_eos:
        raise VoiceBankError(f"record {ordinal} lacks a final Ogg EOS page")
    if packet_continues:
        raise VoiceBankError(f"record {ordinal} ends inside an Ogg packet")
    if first_packet is None or len(first_packet) < 30:
        raise VoiceBankError(f"record {ordinal} lacks a Vorbis identification packet")
    if first_packet[:7] != b"\x01vorbis":
        raise VoiceBankError(f"record {ordinal} first Ogg packet is not Vorbis identification")
    version = struct.unpack_from("<I", first_packet, 7)[0]
    channels = first_packet[11]
    sample_rate = struct.unpack_from("<I", first_packet, 12)[0]
    if version != 0 or channels == 0 or sample_rate == 0:
        raise VoiceBankError(f"record {ordinal} has invalid Vorbis identification")
    if sample_count is None:
        raise VoiceBankError(f"record {ordinal} has no timed Ogg page")

    return OggStreamMetadata(
        page_count=page_count,
        stream_serial=stream_serial,
        channels=channels,
        sample_rate=sample_rate,
        sample_count=sample_count,
    )


def parse_kovs_record(
    payload: bytes,
    *,
    ordinal: int = 0,
    verify_checksums: bool = True,
) -> tuple[int, int | None, OggStreamMetadata]:
    """Validate a KOVS record and return stream metadata."""

    if len(payload) < KOVS_HEADER_BYTES or payload[:4] != b"KOVS":
        raise VoiceBankError(f"record {ordinal} does not start with KOVS")
    stream_bytes, loop_start = struct.unpack_from("<Ii", payload, 4)
    if stream_bytes != len(payload) - KOVS_HEADER_BYTES:
        raise VoiceBankError(f"record {ordinal} KOVS stream length is inconsistent")
    if any(payload[12:KOVS_HEADER_BYTES]):
        raise VoiceBankError(f"record {ordinal} KOVS reserved bytes are non-zero")
    ogg = _parse_ogg_stream(
        payload[KOVS_HEADER_BYTES:],
        ordinal=ordinal,
        verify_checksums=verify_checksums,
    )
    return stream_bytes, loop_start if loop_start != 0 else None, ogg


def inspect_voice_bank(
    path: Path,
    *,
    expected_sha256: str | None = None,
    verify_checksums: bool = True,
) -> VoiceBankLayout:
    """Parse sequential KOVS records and zero padding."""

    path = Path(path)
    records: list[VoiceBankRecord] = []
    source_hash = hashlib.sha256()
    with _open_read_only_regular(path, "voice bank") as stream:
        before = os.fstat(stream.fileno())
        source_bytes = before.st_size
        if source_bytes == 0 or source_bytes % VOICE_RECORD_ALIGNMENT:
            raise VoiceBankError("voice bank size is not a non-zero multiple of 16")

        offset = 0
        while offset < source_bytes:
            ordinal = len(records)
            header = _read_exact(stream, KOVS_HEADER_BYTES, f"record {ordinal} header")
            if header[:4] != b"KOVS":
                raise VoiceBankError(
                    f"record {ordinal} does not start with KOVS at byte {offset}"
                )
            stream_bytes = struct.unpack_from("<I", header, 4)[0]
            logical_bytes = KOVS_HEADER_BYTES + stream_bytes
            allocated_bytes = _aligned_size(logical_bytes)
            if offset + allocated_bytes > source_bytes:
                raise VoiceBankError(f"record {ordinal} allocation exceeds the bank")
            encoded_stream = _read_exact(
                stream, stream_bytes, f"record {ordinal} KOVS stream"
            )
            payload = header + encoded_stream
            parsed_stream_bytes, loop_start, ogg = parse_kovs_record(
                payload,
                ordinal=ordinal,
                verify_checksums=verify_checksums,
            )
            padding_bytes = allocated_bytes - logical_bytes
            padding = _read_exact(stream, padding_bytes, f"record {ordinal} padding")
            if any(padding):
                raise VoiceBankError(f"record {ordinal} padding is non-zero")
            source_hash.update(payload)
            source_hash.update(padding)
            records.append(
                VoiceBankRecord(
                    ordinal=ordinal,
                    offset=offset,
                    stream_bytes=parsed_stream_bytes,
                    logical_bytes=logical_bytes,
                    allocated_bytes=allocated_bytes,
                    padding_bytes=padding_bytes,
                    loop_start_sample=loop_start,
                    sha256=hashlib.sha256(payload).hexdigest(),
                    ogg=ogg,
                )
            )
            offset += allocated_bytes

        after = os.fstat(stream.fileno())
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise VoiceBankError("voice bank changed during read-only inspection")

    digest = source_hash.hexdigest()
    if expected_sha256 is not None and digest != expected_sha256.lower():
        raise VoiceBankError("voice bank SHA-256 does not match the expected target")
    return VoiceBankLayout(
        source_bytes=source_bytes,
        source_sha256=digest,
        records=tuple(records),
    )


def build_synthetic_kovs_record(
    logical_bytes: int,
    *,
    stream_serial: int = 0x53594E54,
    sample_rate: int = 48_000,
    sample_count: int = 0,
) -> bytes:
    """Build a synthetic KOVS record of an exact size."""

    if logical_bytes < KOVS_HEADER_BYTES:
        raise VoiceBankError("synthetic record size is too small")
    stream_bytes = logical_bytes - KOVS_HEADER_BYTES
    identification = (
        b"\x01vorbis"
        + struct.pack("<I", 0)
        + b"\x01"
        + struct.pack("<I", sample_rate)
        + struct.pack("<iii", 0, 0, 0)
        + b"\xb8\x01"
    )
    filler_bytes: int | None = None
    filler_lacing: list[int] = []
    for candidate in range(max(0, stream_bytes - 320), stream_bytes + 1):
        if candidate == 0:
            lacing: list[int] = []
        else:
            quotient, remainder = divmod(candidate, 255)
            lacing = [255] * quotient + [remainder]
        if len(lacing) + 1 > 255:
            continue
        if 27 + 1 + len(lacing) + len(identification) + candidate == stream_bytes:
            filler_bytes = candidate
            filler_lacing = lacing
            break
    if filler_bytes is None:
        raise VoiceBankError(
            "synthetic record size cannot be represented by one bounded Ogg page"
        )

    lacing = bytes([len(identification), *filler_lacing])
    filler = bytes(filler_bytes)
    page = bytearray(
        OGG_CAPTURE_PATTERN
        + b"\x00\x06"
        + struct.pack("<QII", sample_count, stream_serial, 0)
        + bytes(4)
        + bytes((len(lacing),))
        + lacing
        + identification
        + filler
    )
    page[22:26] = struct.pack("<I", _ogg_checksum(page))
    if len(page) != stream_bytes:
        raise AssertionError("synthetic Ogg sizing invariant failed")
    payload = kovs_record(bytes(page))
    parse_kovs_record(payload)
    return payload


MOZ_VOICE_IDS = 22_665
SLOT_BYTES = 0x4EF84
WO1_STAGES = range(0x48, 0x84)
WO1_SUFFIX_BASE = 0xC48
WO1_RECORD_BASE = 1473
WO2_STAGES = range(0x28)
WO2_SUFFIX_BASE = 0x3078
WO2_CLIP_BASE = 1644
WO2_VORBIS_QUALITY = 4


WO1_EVENT_VOICES = (0x8F4, 957, 511)
WO2_EVENT_VOICES = (0xAF3, 1398, 246)


WO2_SELECT_VOICES = (0, 0, 92)


WO1_NARRATION_VOICES = ((96, 80, 209), (306, 289, 40), (592, 329, 628))
WO2_NARRATION_VOICES = ((346, 92, 234), (1220, 326, 1072))


def wo1_voice_records(index: bytes) -> list[tuple[int, int]]:
    """Read byte offsets and logical sizes from an SM4L voice index."""
    magic, count = struct.unpack_from("<4sI", index)
    if magic != b"SM4L" or len(index) < 16 + 16 * count:
        raise VoiceBankError("not a WO1 SM4L voice index")
    return [(start * 2048, size) for start, _, size, _ in struct.iter_unpack("<IIII", index[16:16 + 16 * count])]


def wo2_voice_records(index: bytes) -> list[tuple[int, int]]:
    """Read byte offsets and logical sizes from an X360 voice index."""
    count = struct.unpack_from(">I", index)[0]
    records = list(struct.iter_unpack(">5I", index[8:]))
    sector = 0
    for i, (clip, start, sectors, size, rate) in enumerate(records):
        if (clip, start, rate) != (i, sector, 48_000) or size > sectors * 2048:
            raise VoiceBankError(f"WO2 voice index record {i} is not a contiguous 48 kHz clip")
        sector += sectors
    if len(records) != count or len(index) != 8 + 20 * count:
        raise VoiceBankError("not a WO2 Xbox 360 voice index")
    return [(start * 2048, size) for _, start, _, size, _ in records]


def block_voice_map(block: tuple[int, int, int]) -> dict[int, int]:
    """Expand an id, donor record and count run."""
    base, record, count = block
    return {base + i: record + i for i in range(count)}


def _suffix(line: bytes) -> int | None:
    return int(line[line.rindex(b"@") + 1:]) if re.search(rb"@[0-9]+$", line) else None


def slot_voice_map(tables: list[tuple[list[bytes], list[bytes]]], record_base: int) -> dict[int, int]:
    """Map matching table slots with unambiguous suffixes."""
    found: dict[int, set[int]] = {}
    for ours, theirs in tables:
        for a, b in zip(ours, theirs):
            if (s := _suffix(a)) and (t := _suffix(b)):
                found.setdefault(WO2_SUFFIX_BASE + s - 1, set()).add(t - 1 + record_base)
    return {moz_id: clips.pop() for moz_id, clips in found.items() if len(clips) == 1}


def wo1_voice_clips(mapping: dict[int, int], index: bytes, bank: Path, stock_index: bytes,
                    stock_bank: Path) -> dict[int, bytes]:
    """Read donor KOVS records whose sample counts differ from stock."""
    records = wo1_voice_records(index)
    samples = lambda record: parse_kovs_record(record, verify_checksums=False)[2].sample_count
    with bank.open("rb") as stream, stock_bank.open("rb") as stock:
        clips = {moz_id: _read_at(stream, *records[record]) for moz_id, record in mapping.items()}
        return {moz_id: clip for moz_id, clip in clips.items() if samples(clip) != samples(
            _read_at(stock, *struct.unpack_from(">II", stock_index, 8 + 8 * moz_id)))}


def kovs_record(ogg: bytes) -> bytes:
    """Wrap an Ogg stream in a KOVS header and position transform."""
    stream = bytearray(ogg)
    for position in range(min(256, len(stream))):
        stream[position] ^= position
    return b"KOVS" + struct.pack("<Ii", len(stream), 0) + bytes(20) + stream


# Clips per ffmpeg process: starting ffmpeg costs more than encoding one short clip.
FFMPEG_BATCH = 32


def _ffmpeg_each(jobs: list[tuple[list[str], bytes, list[str]]], workers: int | None = None) -> list[bytes]:
    """Run ffmpeg once per (input options, input bytes, output options) job, FFMPEG_BATCH jobs per process."""
    import subprocess
    import tempfile
    from concurrent.futures import ThreadPoolExecutor

    def batch(start: int) -> list[bytes]:
        part = jobs[start:start + FFMPEG_BATCH]
        with tempfile.TemporaryDirectory(prefix="wozmod-ffmpeg-") as folder:
            command = [resources.ffmpeg() or "ffmpeg", "-hide_banner", "-loglevel", "error", "-filter_threads", "1"]
            for k, (before, data, _) in enumerate(part):
                Path(folder, f"{k}.in").write_bytes(data)
                command += [*before, "-i", str(Path(folder, f"{k}.in"))]
            for k, (_, _, after) in enumerate(part):
                command += ["-map", f"{k}:a", *after, str(Path(folder, f"{k}.out"))]
            subprocess.run(command, capture_output=True, check=True)
            return [Path(folder, f"{k}.out").read_bytes() for k in range(len(part))]

    with ThreadPoolExecutor(workers or os.process_cpu_count()) as pool:
        return [out for part in pool.map(batch, range(0, len(jobs), FFMPEG_BATCH)) for out in part]


def wo2_voice_clips(mapping: dict[int, int], index: bytes, bank: Path, jobs: int | None = None) -> dict[int, bytes]:
    """Convert donor RIFF clips, excluding repeated placeholders."""
    from collections import Counter
    records = wo2_voice_records(index)
    with bank.open("rb") as stream:
        riffs = {moz_id: _read_at(stream, *records[clip]) for moz_id, clip in mapping.items()}
        copies = Counter(hashlib.sha256(_read_at(stream, *record)).digest() for record in records)
    riffs = {moz_id: riff for moz_id, riff in riffs.items() if copies[hashlib.sha256(riff).digest()] <= 2}
    vorbis = ["-ac", "1", "-ar", "48000", "-c:a", "libvorbis", "-threads", "1",
              "-q:a", str(WO2_VORBIS_QUALITY), "-fflags", "+bitexact", "-flags:a", "+bitexact", "-f", "ogg"]
    oggs = _ffmpeg_each([(["-threads", "1", "-f", "wav"], riff, vorbis) for riff in riffs.values()], jobs)
    return dict(zip(riffs, map(kovs_record, oggs)))


def english_voice_clips(wo1_mapping: dict[int, int], wo1_index: bytes, wo1_bank: Path,
                        wo2_mapping: dict[int, int], wo2_index: bytes, wo2_bank: Path,
                        stock_index: bytes, stock_bank: Path,
                        jobs: int | None = None) -> tuple[dict[int, bytes], dict[str, int]]:
    """Select usable X360 clips before WO1 fallbacks; jobs is the ffmpeg process count (default: CPU count)."""
    wo2 = wo2_voice_clips(wo2_mapping, wo2_index, wo2_bank, jobs)
    wo1 = wo1_voice_clips({i: record for i, record in wo1_mapping.items() if i not in wo2},
                          wo1_index, wo1_bank, stock_index, stock_bank)
    return wo1 | wo2, {"wo1-pc": len(wo1), "wo2-x360": len(wo2)}


def _read_at(stream: BinaryIO, offset: int, size: int) -> bytes:
    stream.seek(offset)
    return _read_exact(stream, size, "donor voice record")


def english_voice_bank(clips: dict[int, bytes]) -> bytes:
    """Assemble an indexed bank of validated mono 48 kHz clips."""
    table, body = bytearray(_aligned_size(4 + 8 * MOZ_VOICE_IDS)), bytearray()
    struct.pack_into("<I", table, 0, MOZ_VOICE_IDS)
    for moz_id, data in sorted(clips.items()):
        ogg = parse_kovs_record(data, ordinal=moz_id)[2]
        if (ogg.sample_rate, ogg.channels) != (48_000, 1) or len(data) > SLOT_BYTES:
            raise VoiceBankError(f"voice id {moz_id} is not a 48 kHz mono clip under {SLOT_BYTES} bytes")
        struct.pack_into("<II", table, 4 + 8 * moz_id, len(table) + len(body), len(data))
        body += data + bytes(_aligned_size(len(data)) - len(data))
    return bytes(table + body)


SE_BANKS = 122
SE_BLOCK = 512
SE_BLOCK_SAMPLES = 1017
SE_ENTRY_BYTES = 40


SE_BANK_MAP = {**{b: b - 1 for b in range(9, 104)}, **{b: b + 91 for b in range(105, 122)}}


_SE_GENERIC = (0, 0, 2, 2, 4, 4, 2, 7, 7, 9, 10)
SE_WO1 = {
    **{moz: (moz - 1, tuple(range(13))) for moz in range(9, 88) if moz != 86},
    103: (8, tuple(range(13))),
    105: (87, (1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10)),
    **{moz: (moz - 18, tuple(range(11))) for moz in range(106, 114)},
    119: (99, _SE_GENERIC), 120: (100, _SE_GENERIC),
    **{moz: (wo1, _SE_GENERIC + (None,) * 9 + (20, 21, 22, 23)) for moz, wo1 in ((116, 96), (118, 98), (121, 101))},
    117: (97, _SE_GENERIC + (None,) * 9 + (20, 20, 22, 23)),
}


def moz_se_banks(seidx: bytes, se: bytes) -> list[tuple[bytes, bytes]]:
    """Read sound-bank header and data pairs from a big-endian index."""
    count = struct.unpack_from(">I", seidx)[0]
    if count != 2 * SE_BANKS or len(seidx) < 8 + 8 * count:
        raise VoiceBankError("not the MOZ seidx.bin")
    spans = [se[offset:offset + size] for offset, size in struct.iter_unpack(">II", seidx[8:8 + 8 * count])]
    return list(zip(spans[::2], spans[1::2]))


def se_entries(header: bytes) -> list[int]:
    """Read sound-bank entry offsets relative to byte 12."""
    count = struct.unpack_from("<H", header, 14)[0]
    return [12 + o for o in struct.unpack_from(f"<{count}I", header, 20)]


def wo2_se_entries(hdx: bytes) -> list[list[tuple[int, int, int]]]:
    """Read X360 sound-bank BDX offsets, sizes and rates."""
    banks, base = [], 0
    for start in range(0, len(hdx), 2048):
        if hdx[start:start + 8] != b"SDsdVers":
            continue
        smpl = hdx.index(b"SDsdSmpl", start)
        count = struct.unpack_from("<I", hdx, smpl + 12)[0]
        words = [struct.unpack_from("<18I", hdx, smpl + 16 + 0x48 * k) for k in range(count)]
        banks.append([(base + w[0], w[17], w[13]) for w in words])
        base += max(w[0] + w[17] for w in words)
    return banks


def wo1_se_entries(hdx: bytes) -> list[list[tuple[int, int, int]]]:
    """Read PC sound-bank BDX offsets, sizes and rates."""
    banks, base = [], 0
    for start in range(0, len(hdx), 2048):
        count, size = struct.unpack_from("<HI", hdx, start + 2)
        offsets = struct.unpack_from(f"<{count}I", hdx, start + 8)
        if not count or offsets[0] != 8 + 4 * count:
            continue
        banks.append([(base + struct.unpack_from("<I", hdx, start + o + 20)[0],
                       struct.unpack_from("<I", hdx, start + o + 24)[0],
                       struct.unpack_from("<H", hdx, start + o + 4)[0]) for o in offsets])
        base += -(-size // 2048) * 2048
    return banks


def _xma2_riff(packets: bytes, rate: int) -> bytes:
    """Wrap mono XMA2 packets in a RIFF container."""
    fmt = struct.pack("<HHIIHHH", 0x166, 1, rate, rate * 2, 2048, 16, 34)
    fmt += struct.pack("<HIIIIIIIBBH", 1, 0, 0, len(packets), 0, 0, 0, 0, 0, 4, 1)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(packets)) + packets
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def _ima_adpcm(clips: list[tuple[bytes, bool, int, int]], jobs: int | None) -> list[tuple[int, bytes]]:
    """Convert (clip, is WO1, donor rate, rate) donor clips to IMA ADPCM in 512-byte blocks."""
    pcms = _ffmpeg_each([
        (["-f", "s16le", "-ar", str(donor_rate), "-ac", "1"] if wo1 else ["-f", "wav"],
         clip if wo1 else _xma2_riff(clip, donor_rate), ["-ac", "1", "-ar", str(rate), "-f", "s16le"])
        for clip, wo1, donor_rate, rate in clips], jobs)
    wavs = _ffmpeg_each([
        (["-f", "s16le", "-ar", str(rate), "-ac", "1"], pcm,
         ["-c:a", "adpcm_ima_wav", "-block_size", str(SE_BLOCK), "-fflags", "+bitexact", "-f", "wav"])
        for pcm, (_, _, _, rate) in zip(pcms, clips)], jobs)
    out = []
    for pcm, wav in zip(pcms, wavs):
        at = 12
        while wav[at:at + 4] != b"data":
            at += 8 + struct.unpack_from("<I", wav, at + 4)[0]
        samples = len(pcm) // 2
        out.append((samples, wav[at + 8:at + 8 + -(-samples // SE_BLOCK_SAMPLES) * SE_BLOCK]))
    return out


def se_bank(header: bytes, clips: list[tuple[int, bytes]]) -> tuple[bytes, bytes]:
    """Replace sound-bank sample counts, offsets and data."""
    entries = se_entries(header)
    if len(clips) != len(entries) or any(len(ima) != -(-n // SE_BLOCK_SAMPLES) * SE_BLOCK for n, ima in clips):
        raise VoiceBankError("English SE clips do not fit the bank's entries")
    out, data = bytearray(header), bytearray()
    for at, (samples, ima) in zip(entries, clips):
        struct.pack_into("<III", out, at + 16, samples, len(data), len(ima))
        data += ima
    struct.pack_into("<I", out, 16, len(data))
    return bytes(out), b"_DBW0000" + struct.pack("<I", 12 + len(data)) + bytes(data)


def english_se_bank(seidx: bytes, se: bytes, wo1_hdx: bytes, wo1_bdx: Path, wo2_hdx: bytes,
                    wo2_bdx: Path, jobs: int | None = None) -> tuple[bytes, dict[str, int]]:
    """Assemble sound banks from paired donor entries; jobs is the ffmpeg process count (default: CPU count)."""
    stock, wo1, wo2 = moz_se_banks(seidx, se), wo1_se_entries(wo1_hdx), wo2_se_entries(wo2_hdx)
    for donor, bdx in ((wo1, wo1_bdx), (wo2, wo2_bdx)):
        if max(o + n for bank in donor for o, n, _ in bank) > bdx.stat().st_size:
            raise VoiceBankError(f"{bdx.name} is shorter than its HDX describes")
    work, tiers = {}, {"wo1-pc": 0, "wo2-x360": 0}
    with wo1_bdx.open("rb") as one, wo2_bdx.open("rb") as two:
        for moz, w in SE_BANK_MAP.items():
            header = stock[moz][0]
            if len(se_entries(header)) != len(wo2[w]):
                continue
            wo1_bank, pairs = SE_WO1.get(moz, (None, ()))
            work[moz] = []
            for k, at in enumerate(se_entries(header)):
                rate = struct.unpack_from("<H", header, at + 4)[0]
                j = pairs[k] if k < len(pairs) else None
                offset, size, donor_rate = wo2[w][k] if j is None else wo1[wo1_bank][j]
                clip = _read_at(two if j is None else one, offset, size)
                work[moz].append((clip, j is not None, donor_rate, rate))
                tiers["wo2-x360" if j is None else "wo1-pc"] += 1
    converted = iter(_ima_adpcm([job for entries in work.values() for job in entries], jobs))
    banks = {moz: se_bank(stock[moz][0], [next(converted) for _ in entries]) for moz, entries in work.items()}
    table, body = bytearray(_aligned_size(4 + 16 * SE_BANKS)), bytearray()
    struct.pack_into("<I", table, 0, SE_BANKS)
    for moz, (header, data) in sorted(banks.items()):
        at = len(table) + len(body)
        struct.pack_into("<4I", table, 4 + 16 * moz, at, len(header), at + _aligned_size(len(header)), len(data))
        body += header + bytes(_aligned_size(len(header)) - len(header)) + data + bytes(_aligned_size(len(data)) - len(data))
    return bytes(table + body), tiers
