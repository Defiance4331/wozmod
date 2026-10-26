"""Voice and sound-bank formats."""

from __future__ import annotations

import hashlib
import os
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from wozmod import process, resources
from wozmod.formats.linkdata import ps2_extents
from wozmod.progress import report

if TYPE_CHECKING:
    from wozmod.formats.scripts import Script


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


def _vorbis_block_flags(setup: bytes) -> list[int]:
    """Each Vorbis mode's block flag (0 short, 1 long), read backwards from the end of the setup header.

    The mode list closes the header: a 6-bit count less one, then per mode a block flag, two 16-bit zeros and an
    8-bit mapping, then the framing bit. The codebooks before it are not parsed, so the scan keeps the longest run
    of modes whose count field agrees, as ffmpeg's vorbis_parser does.
    """
    bits, position = int.from_bytes(setup, "little"), len(setup) * 8 - 1
    while position >= 0 and not bits >> position & 1:
        position -= 1
    flags: list[int] = []
    found: list[int] | None = None
    while position >= 41 and len(flags) < 64:
        mode = bits >> (position - 41) & ((1 << 41) - 1)
        if mode >> 1 & 0xFFFFFFFF or mode >> 33 > 63:
            break
        flags.insert(0, mode & 1)
        position -= 41
        if position >= 6 and (bits >> (position - 6) & 63) + 1 == len(flags):
            found = list(flags)
    if not found:
        raise VoiceBankError("Vorbis setup header has no mode list")
    return found


def _vorbis_decoded_samples(payload: bytes) -> tuple[int, int]:
    """(samples a validated KOVS record's Vorbis packets decode to, samples in its last packet), from block sizes.

    A packet decodes to a quarter of its block plus a quarter of the one before it, and the first audio packet
    to none. The final granule position is what the game plays: at most the last packet's samples below this.
    """
    stream = bytearray(payload[KOVS_HEADER_BYTES:])
    for position in range(min(256, len(stream))):
        stream[position] ^= position
    headers: list[bytes] = []  # the three header packets whole, then only each audio packet's first byte
    audio: list[int] = []
    packet, started, offset = bytearray(), False, 0
    while offset < len(stream):
        table_end = offset + 27 + stream[offset + 26]
        body = table_end
        for size in stream[offset + 27:table_end]:
            if len(headers) < 3:
                packet += stream[body:body + size]
            elif size and not started:
                audio.append(stream[body])
                started = True
            body += size
            if size < 255:
                if len(headers) < 3:
                    headers.append(bytes(packet))
                    packet.clear()
                started = False
        offset = body
    if not audio:
        return 0, 0
    blocks = (1 << (headers[0][28] & 15), 1 << (headers[0][28] >> 4))
    flags = _vorbis_block_flags(headers[2])
    mode_bits = (len(flags) - 1).bit_length()
    total, last, previous = 0, 0, None
    for head in audio:
        mode = head >> 1 & ((1 << mode_bits) - 1)
        if head & 1 or mode >= len(flags):
            raise VoiceBankError("Vorbis audio packet is malformed")
        current = blocks[flags[mode]]
        last = 0 if previous is None else (previous + current) // 4
        total, previous = total + last, current
    return total, last


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
# A story line's voice on the X360: the speak command parses @NNNN and stores NNNN - 1 (WO1 0x821A11F4, WO2
# 0x821BD5DC), and the player adds the record base below the id limit (WO1 0x82188998: 0x5C1 under 0x1E5A; WO2
# 0x8219F980: 0x66C under 0x229C). Base plus limit is each disc's voice index count (9,243 and 10,504).
WO1_RECORD_BASE = 0x5C1
WO2_STAGES = range(0x28)
WO2_SUFFIX_BASE = 0x3078
WO2_CLIP_BASE = 0x66C
WO2_VORBIS_QUALITY = 7
# WO2 PSP 0x1BE014 plays sound/voice_na.bns member 0x58B + a line's voice index (0x1BE03C), and the index is @NNNN - 1
# (0x1568C4 stores it at 0x1569A4..0x1569C0): with
# that base the Japanese bank's take of every Z Dream line has the stock take's length less Z's 0.5 s of padding.
PSP_CLIP_BASE = 1419


# Event lines: (MOZ voice of line 0, donor record of line 0, lines the donor voices). MOZ plays 0x8F4 + index below
# 0x1FF in the WO1 voice mode, else 0xAF3 + index below 0x150 (0x513731); WO1 X360 0x821888F0 plays record 0x3BD +
# index below 0x1FF, WO2 X360 0x8219F8D8 plays 0x576 + index below 0xF1.
WO1_EVENT_VOICES = (0x8F4, 0x3BD, 0x1FF)
WO2_EVENT_VOICES = (0xAF3, 0x576, 0xF1)
MOZ_EVENT_LINES = {"wo1-x360": 0x1FF, "wo2-x360": 0x150}  # by the donor whose events the voice mode plays


WO2_SELECT_VOICES = (0, 0, 92)
# Officers 92 and 93 have select voices of their own in both programs: MOZ 0x512B20 plays 0x5875 and 0x5883 (pushed at
# 0x512B29, 0x512B3E), and WO2 X360 0x8229C89C plays clip 0x28F4 for 92 and 0x2902 for 93 (0x8229C8A0, 0x8229C8A8).
WO2_NEW_OFFICER_SELECT_VOICES = {0x5875: 0x28F4, 0x5883: 0x2902}
# MOZ plays select voice = officer id (0x512B20). The X360 lacks officers 94 and 95; the PSP bank holds their voices
# at 94 and 95 (inferred: its first 76 clips are the stock takes, and 94 and 95 are the stock ones less the padding).
PSP_SELECT_VOICES = (94, 94, 2)
# The Z Dream briefings, which only the PSP voices. MOZ stage s from 0x84 plays s + 0x1C0 (0x548C10). WO2 PSP
# 0x2113F0 plays voice_na.bns member stage + 0x102 from its stage 0x48, + 0x106 below (0x211430 to 0x211458, the
# stage at 0x126FE64), through 0x1BDF60, which reads the bank table 0x1C0F4C reads for 0x1BE014. Its stages 0x48-0x53
# are MOZ's 0x84-0x8F (script_alignment.PSP_STAGE_MEMBERS), so voices 580-591 are records 330-341.
PSP_BRIEFING_VOICES = (0x84 + 0x1C0, 0x48 + 0x102, 12)


# Narration, still events and briefings: the donor game picks the same page, scene, record or stage as MOZ
# (text.narration_voices) and plays its own base on the same offset; tests/test_voice_blocks.py joins them item by
# item. WO1 pages: 0x8226E778(page) = table + 0x50, MOZ's table less 1 from its entry 209 on (MOZ adds a slot there).
# WO1 camp talk: 0x8228F958(scene) = table + 0x149, MOZ's table. WO2 Z pages: record word 0 + 0x5C (0x822B09E4),
# and from 92 + 166 the stage briefings, stage + 0x102 (0x8229F6D4), Dream 40-67 included. WO2 Z stills: command
# word 2 + 0x146 (0x822DC8F4). The run ends at voice 579, the last of those briefings: the Z Dream briefings after it
# (580-591) are PSP_BRIEFING_VOICES.
WO1_NARRATION_VOICES = ((96, 80, 209), (306, 289, 40), (592, 329, 628))
WO2_NARRATION_VOICES = ((346, 92, 234), (1220, 326, 1072))


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


def stage_voice(ordinal: int, suffix: int, flag: int) -> int:
    """A stage line's voice id, as the speak command plays it (opcode 38, handler 0x4F17E0): the suffix less one on
    the base 0x5137F0 picks. Stages 72-131 run in the WO1 voice mode (0x4F06BB); there the base is 0xC48, or 0x3078
    when the command's flag (scripts.speak_flags) is set, and an index from 0x1166 on always takes 0x3078
    (0x4F4FBB); elsewhere the flag picks 0xC48 and its absence 0x3078."""
    index = suffix - 1
    if ordinal in WO1_STAGES:
        return index + (WO2_SUFFIX_BASE if flag or index >= 0x1166 else WO1_SUFFIX_BASE)
    return index + (WO1_SUFFIX_BASE if flag else WO2_SUFFIX_BASE)


def stage_line_voices(ordinal: int, script: Script, unspoken: int | None = None) -> dict[int, tuple[int, ...]]:
    """The voice ids each suffixed line of a stock stage script plays (script.speaks): one per bank flag of the speak
    commands that speak it, in flag order. A line no command speaks plays none, or with unspoken the id that flag
    gives its suffix."""
    return {i: tuple(stage_voice(ordinal, suffix, flag)
                     for flag in sorted(script.speaks.get(i) or ({unspoken} - {None})))
            for i, line in enumerate(script.dialog.records) if (suffix := _suffix(line.value))}


def quote_voice(suffix: int) -> int:
    """A quote line's voice id: its suffix less one on 0x3078."""
    return WO2_SUFFIX_BASE + suffix - 1


def quote_slots(tables: list[tuple[list[bytes], list[bytes]]]) -> list[tuple[int, bytes]]:
    """Each suffixed stock quote's voice id beside the donor line in its slot, tables indexed as the stock's, for the
    slots the quote code reads (script_alignment.quote_slot_read): a voice no game plays needs no clip."""
    from wozmod.text.script_alignment import quote_slot_read

    return [(quote_voice(s), b) for t, (ours, theirs) in enumerate(tables)
            for k, (a, b) in enumerate(zip(ours, theirs, strict=False)) if quote_slot_read(t, k) and (s := _suffix(a))]


def slot_voice_map(slots: list[tuple[int, bytes]], record_base: int) -> dict[int, int]:
    """Map each stock voice id to the record of the suffixed donor line in its slot, unless its slots disagree."""
    found: dict[int, set[int]] = {}
    for moz_id, theirs in slots:
        if t := _suffix(theirs):
            found.setdefault(moz_id, set()).add(t - 1 + record_base)
    return {moz_id: clips.pop() for moz_id, clips in found.items() if len(clips) == 1}


def kovs_record(ogg: bytes) -> bytes:
    """Wrap an Ogg stream in a KOVS header and position transform."""
    stream = bytearray(ogg)
    for position in range(min(256, len(stream))):
        stream[position] ^= position
    return b"KOVS" + struct.pack("<Ii", len(stream), 0) + bytes(20) + stream


# Clips per ffmpeg process: starting ffmpeg costs more than encoding one short clip, but each clip in a process
# keeps its own encoder alive, and 32 of them cost 12 % more CPU than 8 (measured, same output).
FFMPEG_BATCH = 8


def _ffmpeg_each(jobs: list[tuple[list[str], bytes, list[str]]], workers: int | None = None,
                 label: str = "converting audio", span: tuple[float, float] = (0.0, 1.0)) -> list[bytes]:
    """Run ffmpeg once per (input options, input bytes, output options) job, FFMPEG_BATCH jobs per process.

    label and span say what the progress view shows: the part of the voices stage this call covers.
    """
    import subprocess
    import tempfile
    import threading
    from concurrent.futures import ThreadPoolExecutor

    finished, lock = [0], threading.Lock()

    def batch(start: int) -> list[bytes]:
        part = jobs[start:start + FFMPEG_BATCH]
        # On Windows a virus scanner can hold a new file, so the folder delete fails; a leftover folder is harmless.
        with tempfile.TemporaryDirectory(prefix="wozmod-ffmpeg-", ignore_cleanup_errors=True) as folder:
            command = [resources.ffmpeg() or "ffmpeg", "-hide_banner", "-loglevel", "error", "-filter_threads", "1"]
            for k, (before, data, _) in enumerate(part):
                Path(folder, f"{k}.in").write_bytes(data)
                command += [*before, "-i", str(Path(folder, f"{k}.in"))]
            for k, (_, _, after) in enumerate(part):
                command += ["-map", f"{k}:a", *after, str(Path(folder, f"{k}.out"))]
            process.run(command, capture_output=True, check=True, stdin=subprocess.DEVNULL)  # never read the terminal
            outputs = [Path(folder, f"{k}.out").read_bytes() for k in range(len(part))]
        with lock:  # report() raises Cancelled when the player cancelled, which stops the other batches too
            finished[0] += len(part)
            report("voices", label, span[0] + (span[1] - span[0]) * finished[0] / len(jobs), (finished[0], len(jobs)))
        return outputs

    with ThreadPoolExecutor(workers or os.process_cpu_count()) as pool:
        return [out for part in pool.map(batch, range(0, len(jobs), FFMPEG_BATCH)) for out in part]


def donor_voice_choice(banks: list[tuple[str, dict[int, int], list[tuple[int, int]], Path]],
                       ) -> dict[int, tuple[str, int]]:
    """Each stock voice id's (source, donor record) from (source, mapping, clip extents, bank) banks: the earliest
    bank that maps it to a clip other than a placeholder, which is a clip repeated more than twice in its bank."""
    from collections import Counter
    chosen: dict[int, tuple[str, int]] = {}
    for source, mapping, records, bank in banks:
        with bank.open("rb") as stream:
            digests = [hashlib.sha256(_read_at(stream, *record)).digest() for record in records]
        copies = Counter(digests)
        for moz_id, clip in mapping.items():
            if moz_id not in chosen and copies[digests[clip]] <= 2:
                chosen[moz_id] = (source, clip)
    return chosen


def donor_voice_clips(banks: list[tuple[str, dict[int, int], list[tuple[int, int]], Path]],
                      jobs: int | None = None) -> tuple[dict[int, bytes], dict[int, str]]:
    """Convert the donor RIFF clips donor_voice_choice picks, with each clip's source. ffmpeg reads any RIFF codec
    the donors use (Xbox 360 XMA, PSP ATRAC3plus) and every clip leaves as 48 kHz mono Vorbis."""
    chosen = donor_voice_choice(banks)
    riffs, sources = {}, {}
    for source, _, records, bank in banks:
        with bank.open("rb") as stream:
            for moz_id, (picked, clip) in chosen.items():
                if picked == source:
                    riffs[moz_id], sources[moz_id] = _read_at(stream, *records[clip]), source
    def encode(moz_ids: list[int], quality: int, span: tuple[float, float]) -> dict[int, bytes]:
        # The Ogg granule positions, which the game reads as the clip's length, come from the frame timestamps.
        # ffmpeg's RIFF reader packs two ATRAC3plus frames per packet but stamps one frame's duration, so the PSP
        # timestamps repeat and advance at half speed: asetpts numbers them by the decoded samples instead, rounded
        # because it truncates the quotient and N/SR/TB can land a hair under the whole number (one sample short).
        vorbis = ["-af", "asetpts=round(N/SR/TB)", "-ac", "1", "-ar", "48000", "-c:a", "libvorbis", "-threads", "1",
                  "-q:a", str(quality), "-fflags", "+bitexact", "-flags:a", "+bitexact", "-f", "ogg"]
        oggs = _ffmpeg_each([(["-threads", "1", "-f", "wav"], riffs[i], vorbis) for i in moz_ids], jobs,
                            "converting voice clips", span)
        return dict(zip(moz_ids, map(kovs_record, oggs), strict=True))

    clips = encode(list(riffs), WO2_VORBIS_QUALITY, (0.1, 0.85))
    # The game reads a clip whole into a SLOT_BYTES buffer: a clip that does not fit is encoded again, one step lower.
    for quality in range(WO2_VORBIS_QUALITY - 1, -1, -1):
        if not (long := [i for i, clip in clips.items() if len(clip) > SLOT_BYTES]):
            break
        clips |= encode(long, quality, (0.85, 0.85))  # progress stays put
    return clips, sources


def english_voice_clips(wo1_mapping: dict[int, int], wo1_index: bytes, wo1_bank: Path,
                        wo2_mapping: dict[int, int], wo2_index: bytes, wo2_bank: Path,
                        stock_index: bytes, stock_bank: Path,
                        jobs: int | None = None, *, psp: tuple[dict[int, int], Path] | None = None,
                        stock_ids: dict[int, int] | None = None) -> tuple[dict[int, bytes], dict[int, str]]:
    """Select usable WO2 X360 clips, then WO1 X360 ones, then WO2 PSP ones (lower quality: only for a line neither
    Xbox 360 disc has), with each clip's source; jobs is the ffmpeg process count. A WO1 clip as long as the stock
    clip is the stock take, not an English one, and is left out; stock_ids names the stock id whose take a voice slot
    stands in for."""
    stock_ids = stock_ids or {}
    banks = [("wo2-x360", wo2_mapping, wo2_voice_records(wo2_index), wo2_bank),
             ("wo1-x360", wo1_mapping, wo2_voice_records(wo1_index), wo1_bank)]
    if psp:
        banks.append(("wo2-psp", psp[0], ps2_extents(psp[1]), psp[1]))
    clips, sources = donor_voice_clips(banks, jobs)
    samples = lambda record: parse_kovs_record(record, verify_checksums=False)[2].sample_count  # noqa: E731
    with stock_bank.open("rb") as stock:
        stock_takes = {moz_id for moz_id, source in sources.items() if source == "wo1-x360" and samples(clips[moz_id])
                       == samples(_read_at(stock, *struct.unpack_from(">II", stock_index,
                                                                      8 + 8 * stock_ids.get(moz_id, moz_id))))}
    return ({i: clip for i, clip in clips.items() if i not in stock_takes},
            {i: source for i, source in sources.items() if i not in stock_takes})


def _read_at(stream: BinaryIO, offset: int, size: int) -> bytes:
    stream.seek(offset)
    return _read_exact(stream, size, "donor voice record")


def english_voice_bank(clips: dict[int, bytes]) -> bytes:
    """Assemble an indexed bank of validated mono 48 kHz clips: every stock voice id, then any voice slot past them
    (wozmod.voices.maps.voice_slots)."""
    ids = max(MOZ_VOICE_IDS, max(clips, default=0) + 1)
    table, body = bytearray(_aligned_size(4 + 8 * ids)), bytearray()
    struct.pack_into("<I", table, 0, ids)
    for moz_id, data in sorted(clips.items()):
        ogg = parse_kovs_record(data, ordinal=moz_id)[2]
        if (ogg.sample_rate, ogg.channels) != (48_000, 1) or len(data) > SLOT_BYTES:
            raise VoiceBankError(f"voice id {moz_id} is not a 48 kHz mono clip under {SLOT_BYTES} bytes")
        decoded, last = _vorbis_decoded_samples(data)
        if not 0 <= decoded - ogg.sample_count <= last:  # the game stops a clip at its declared length
            raise VoiceBankError(f"voice id {moz_id} declares {ogg.sample_count} samples but holds {decoded}")
        struct.pack_into("<II", table, 4 + 8 * moz_id, len(table) + len(body), len(data))
        body += data + bytes(_aligned_size(len(data)) - len(data))
    return bytes(table + body)


SE_BANKS = 122
SE_ENTRY_BYTES = 40


# Every game plays a unit's attack shouts from the bank its unit type (the officer id, unit byte +5) gives. MOZ:
# type + 9 for every unit, types up to 0x70 (0x5132D2 to 0x5132DE as a battle starts, 0x513363). WO2 X360: type
# + 0x66, types up to 0x6E (0x821AA02C to 0x821AA044 as a battle starts, 0x821A8F08); its player plays type + 8
# (0x821AACB4), a byte-identical copy for types 0-93. WO1 X360: type + 0x57, types up to 0x5D (0x82195FC0 to
# 0x82195FCC); its player plays type + 8 (0x8219784C), a copy too. WO2 PSP: type + 8 for the player's team
# (EBOOT 0x1BFBD0 to 0x1BFC2C), banks of LINKDATA.ENS member 1 with the English voice (0x1BEE48 to 0x1BEED0).
SE_TYPE_BANKS = {"moz": (9, 0x70), "wo1-x360": (0x57, 0x5D), "wo2-x360": (0x66, 0x6E), "wo2-psp": (8, 95)}
# The MOZ unit types a donor lacks, as (first type, count): past a gap, the donor's type is MOZ's less the gaps.
# WO2 X360 lacks San Zang and Benkei (94, 95). Its per-type records (0x82007A10) equal MOZ's (0x6D4B10) for types
# 0-91 and, past the gap, for MOZ 96-187; 92 and 93 differ in one byte but are the two officers both programs give
# their own select voices (0x512B20, 0x8229C89C), and their name parts are the same table slots. WO1 X360 lacks
# the officers WO2 added (MOZ 79-95) and two generic types (MOZ 105, 106): its banks hold WO2's clip bytes at types
# 0-78, 79-87 (WO2 94-102) and 88-93 (WO2 105-110). WO2 PSP numbers its officers as MOZ does: its Japanese
# bank (member 0) of each of the 96 holds that MOZ bank's recordings.
SE_TYPE_GAPS = {"wo1-x360": ((79, 17), (105, 2)), "wo2-x360": ((94, 2),), "wo2-psp": ()}
SE_DONORS = ("wo1-x360", "wo2-x360", "wo2-psp")  # the donor order for attack shouts


def se_donor_bank(moz_bank: int, donor: str) -> int | None:
    """The donor's bank for the unit type of MOZ bank moz_bank, or None when the donor lacks that type."""
    unit = moz_bank - SE_TYPE_BANKS["moz"][0]
    if not 0 <= unit <= SE_TYPE_BANKS["moz"][1]:
        return None
    theirs = unit
    for first, count in SE_TYPE_GAPS[donor]:
        if first <= unit < first + count:
            return None
        theirs -= count if unit >= first + count else 0
    base, last = SE_TYPE_BANKS[donor]
    return base + theirs if theirs <= last else None


_SE_GENERIC = (0, 0, 2, 2, 4, 4, 2, 7, 7, 9, 10)
# MOZ entry k plays WO1 X360 entry k (its action table 0x82214A28 equals MOZ's) while the WO1 bank has one, then
# entry k of the first later donor with the officer (WO2 X360, then WO2 PSP). These MOZ banks take other WO1 entries, each byte-identical to the one it replaces,
# or the WO2 entry (None).
SE_WO1_ENTRIES = {
    105: (1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
    119: _SE_GENERIC, 120: _SE_GENERIC,
    **{moz: _SE_GENERIC + (None,) * 9 + (20, 21, 22, 23) for moz in (116, 118, 121)},
    117: _SE_GENERIC + (None,) * 9 + (20, 20, 22, 23),
}


def moz_se_banks(seidx: bytes, se: bytes) -> list[tuple[bytes, bytes]]:
    """Read sound-bank header and data pairs from a big-endian index."""
    count = struct.unpack_from(">I", seidx)[0]
    if count != 2 * SE_BANKS or len(seidx) < 8 + 8 * count:
        raise VoiceBankError("not the MOZ seidx.bin")
    spans = [se[offset:offset + size] for offset, size in struct.iter_unpack(">II", seidx[8:8 + 8 * count])]
    return list(zip(spans[::2], spans[1::2], strict=True))


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


def psp_se_entries(pack: bytes) -> list[list[tuple[int, int, int] | None]]:
    """Read the WO2 PSP sound pack (LINKDATA.ENS member 0 or 1): u32 0, size, bank count, 0x800, then each bank's
    (offset, size). A bank is u32 2, its PPHD offset, its VAG body offset and size, then PPHD, PPPG, PPTN and
    PPVA chunks; each PPVA record from byte 0x20 is (VAG offset in the body, rate, size, -1), all ones for an empty
    slot. Returns each bank's VAGs as (offset in the pack, size, rate), None for an empty slot; the size leaves out
    a closing 16-byte block of flag 7, which ends the sound rather than holding samples."""
    zero, size, count, _ = struct.unpack_from("<4I", pack)
    if zero or size != len(pack) or len(pack) < 16 + 8 * count:
        raise VoiceBankError("not a WO2 PSP sound pack")
    banks = []
    for at, length in struct.iter_unpack("<II", pack[16:16 + 8 * count]):
        bank = pack[at:at + length]
        ppva = bank.find(b"PPVA")
        records = []
        if len(bank) >= 16 and ppva >= 0:
            body = struct.unpack_from("<I", bank, 8)[0]
            for r in range(ppva + 0x20, ppva + struct.unpack_from("<I", bank, ppva + 4)[0], 16):
                offset, rate, clip, _ = struct.unpack_from("<IIIi", bank, r)
                if not (offset or rate or clip):
                    break
                if offset == rate == clip == 0xFFFFFFFF:
                    records.append(None)
                    continue
                if body + offset + clip > len(bank):
                    raise VoiceBankError("a WO2 PSP sound bank's clip runs past the bank")
                if clip >= 16 and bank[body + offset + clip - 15] == 7:
                    clip -= 16
                records.append((at + body + offset, clip, rate))
        banks.append(records)
    return banks


def _vag(data: bytes, rate: int) -> bytes:
    """Put a VAGp header (big-endian: version 0x20, data size, rate, a blank name) on PSP ADPCM data."""
    return b"VAGp" + struct.pack(">III", 0x20, 0, len(data)) + struct.pack(">I", rate) + bytes(28) + data


def _xma2_riff(packets: bytes, rate: int) -> bytes:
    """Wrap mono XMA2 packets in a RIFF container."""
    fmt = struct.pack("<HHIIHHH", 0x166, 1, rate, rate * 2, 2048, 16, 34)
    fmt += struct.pack("<HIIIIIIIBBH", 1, 0, 0, len(packets), 0, 0, 0, 0, 0, 4, 1)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(packets)) + packets
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def _pcm16(clips: list[tuple[str, bytes, int, int]], jobs: int | None) -> list[tuple[int, bytes]]:
    """Decode (donor, clip, donor rate, rate) donor clips once, X360 XMA2 or PSP ADPCM, to 16-bit mono PCM at the
    entry's rate."""
    inputs = [(["-f", "vag"], _vag(clip, donor_rate)) if donor == "wo2-psp" else
              (["-f", "wav"], _xma2_riff(clip, donor_rate)) for donor, clip, donor_rate, _ in clips]
    pcms = _ffmpeg_each([(before, data, ["-ac", "1", "-ar", str(rate), "-f", "s16le"])
                         for (before, data), (*_, rate) in zip(inputs, clips, strict=True)],
                        jobs, "converting sound effects", (0.85, 1.0))
    return [(len(pcm) // 2, pcm) for pcm in pcms]


# Entry bytes 6 to 11: format, block bytes, samples per block. Stock banks use 0x8800 (IMA ADPCM, 512-byte
# blocks of 1,017 samples); the game also plays 0x4800, 16-bit PCM, which is what the donor effects become.
SE_FORMAT_PCM = (0x4800, 2, 1)


def se_bank(header: bytes, clips: list[tuple[int, bytes]]) -> tuple[bytes, bytes]:
    """Replace sound-bank formats, sample counts, offsets and data with 16-bit PCM clips."""
    entries = se_entries(header)
    if len(clips) != len(entries) or any(len(pcm) != 2 * n for n, pcm in clips):
        raise VoiceBankError("English SE clips do not fit the bank's entries")
    out, data = bytearray(header), bytearray()
    for at, (samples, pcm) in zip(entries, clips, strict=True):
        struct.pack_into("<HHH", out, at + 6, *SE_FORMAT_PCM)
        struct.pack_into("<III", out, at + 16, samples, len(data), len(pcm))
        data += pcm
    struct.pack_into("<I", out, 16, len(data))
    return bytes(out), b"_DBW0000" + struct.pack("<I", 12 + len(data)) + bytes(data)


def se_plan(stock: list[tuple[bytes, bytes]], donors: dict[str, list[list[tuple[int, int, int]]]]
            ) -> dict[int, list[tuple[str, int, int]]]:
    """The donor (disc, bank, entry) of each entry of every MOZ bank whose unit type a donor has (se_donor_bank),
    in SE_DONORS order: WO1 X360's entry as SE_WO1_ENTRIES pairs it, else entry k of the first later donor with
    the type, whose bank must have as many entries as MOZ's. Banks of types no donor has stay out, and stock."""
    plan = {}
    for moz, (header, _) in enumerate(stock):
        entries = len(se_entries(header)) if len(header) >= 20 else 0
        banks = {disc: bank for disc in SE_DONORS if (bank := se_donor_bank(moz, disc)) is not None}
        if not entries or not banks:
            continue
        one = banks.pop("wo1-x360", None)
        pairs = () if one is None else SE_WO1_ENTRIES.get(moz, tuple(range(len(donors["wo1-x360"][one]))))
        indexed = next(iter(banks.items()), None)  # the donor whose entry k plays MOZ entry k
        if indexed and (len(donors[indexed[0]][indexed[1]]) != entries or None in donors[indexed[0]][indexed[1]]):
            raise VoiceBankError(f"SE bank {moz} has {entries} entries and its officer's {indexed[0]} bank "
                                 f"{indexed[1]} has {len(donors[indexed[0]][indexed[1]])}")
        plan[moz] = []
        for k in range(entries):
            j = pairs[k] if k < len(pairs) else None
            if j is None and indexed is None:
                raise VoiceBankError(f"SE bank {moz} entry {k} has no donor entry")
            plan[moz].append(("wo1-x360", one, j) if j is not None else (*indexed, k))
    return plan


def english_se_bank(seidx: bytes, se: bytes, wo1_hdx: bytes, wo1_bdx: Path, wo2_hdx: bytes,
                    wo2_bdx: Path, psp_pack: bytes, jobs: int | None = None) -> tuple[bytes, dict[str, int]]:
    """Assemble sound banks from paired donor entries; psp_pack is WO2 PSP LINKDATA.ENS member 1 and jobs the
    ffmpeg process count (default: CPU count)."""
    stock = moz_se_banks(seidx, se)
    donors = {"wo1-x360": wo2_se_entries(wo1_hdx), "wo2-x360": wo2_se_entries(wo2_hdx), "wo2-psp": psp_se_entries(psp_pack)}
    files = {"wo1-x360": wo1_bdx, "wo2-x360": wo2_bdx}
    for disc, bdx in files.items():
        if max(o + n for bank in donors[disc] for o, n, _ in bank) > bdx.stat().st_size:
            raise VoiceBankError(f"{bdx.name} is shorter than its HDX describes")
    work, tiers = {}, dict.fromkeys(SE_DONORS, 0)
    with files["wo1-x360"].open("rb") as one, files["wo2-x360"].open("rb") as two:
        read = {"wo1-x360": lambda o, n: _read_at(one, o, n), "wo2-x360": lambda o, n: _read_at(two, o, n),
                "wo2-psp": lambda o, n: psp_pack[o:o + n]}
        for moz, sources in se_plan(stock, donors).items():
            header = stock[moz][0]
            work[moz] = []
            for at, (disc, bank, j) in zip(se_entries(header), sources, strict=True):
                offset, size, donor_rate = donors[disc][bank][j]
                work[moz].append((disc, read[disc](offset, size), donor_rate,
                                  struct.unpack_from("<H", header, at + 4)[0]))
                tiers[disc] += 1
    converted = iter(_pcm16([job for entries in work.values() for job in entries], jobs))
    banks = {moz: se_bank(stock[moz][0], [next(converted) for _ in entries]) for moz, entries in work.items()}
    table, body = bytearray(_aligned_size(4 + 16 * SE_BANKS)), bytearray()
    struct.pack_into("<I", table, 0, SE_BANKS)
    for moz, (header, data) in sorted(banks.items()):
        at = len(table) + len(body)
        struct.pack_into("<4I", table, 4 + 16 * moz, at, len(header), at + _aligned_size(len(header)), len(data))
        body += header + bytes(_aligned_size(len(header)) - len(header)) + data + bytes(_aligned_size(len(data)) - len(data))
    return bytes(table + body), tiers
