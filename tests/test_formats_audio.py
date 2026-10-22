from __future__ import annotations

import dataclasses
import os
import shutil
import struct
from pathlib import Path

import pytest

from wozmod.formats.audio import (
    MOZ_VOICE_IDS,
    VoiceBankError,
    block_voice_map,
    build_synthetic_kovs_record,
    english_voice_bank,
    english_voice_clips,
    kovs_record,
    moz_se_banks,
    parse_kovs_record,
    se_bank,
    se_entries,
    slot_voice_map,
    wo2_se_entries,
    wo2_voice_records,
)


def _checksum(page: bytes) -> int:
    crc = 0
    for value in page:
        crc ^= value << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04C11DB7 if crc & 0x80000000 else 0)) & 0xFFFFFFFF
    return crc


def _record(size: int, *, serial: int = 0x53594E54, samples: int = 0, rate: int = 48_000) -> bytes:
    identification = b"\x01vorbis" + struct.pack("<I", 0) + b"\x01" + struct.pack("<Iiii", rate, 0, 0, 0) + b"\xb8\x01"
    filler = size - 91
    lacing = bytes((30, filler)) if filler >= 0 else bytes((30,))
    page = bytearray(b"OggS\x00\x06" + struct.pack("<QII", samples, serial, 0) + bytes(4) + bytes((len(lacing),)) + lacing + identification + bytes(max(0, filler)))
    struct.pack_into("<I", page, 22, _checksum(page))
    for at in range(min(256, len(page))):
        page[at] ^= at
    return b"KOVS" + struct.pack("<Ii", len(page), 0) + bytes(20) + page


def _se_header(entries: list[bytes]) -> bytes:
    offsets = [8 + 4 * len(entries) + 40 * k for k in range(len(entries))]
    body = struct.pack(f"<HHI{len(entries)}I", 0x3200, len(entries), 0, *offsets) + b"".join(entries)
    return b"_HBW0000" + struct.pack("<I", 12 + len(body)) + body


def test_kovs_rejects_corruption_after_the_identification_packet() -> None:
    changed = bytearray(_record(101))
    changed[-1] ^= 1

    with pytest.raises(VoiceBankError, match="checksum"):
        parse_kovs_record(bytes(changed))
    assert parse_kovs_record(bytes(changed), verify_checksums=False)[2].sample_rate == 48_000


@pytest.mark.parametrize("damage", ["length", "reserved"])
def test_kovs_rejects_a_wrong_stream_length_or_nonzero_reserved_header(damage: str) -> None:
    changed = bytearray(_record(90))
    if damage == "length":
        struct.pack_into("<I", changed, 4, 59)
    else:
        changed[31] = 1

    with pytest.raises(VoiceBankError):
        parse_kovs_record(bytes(changed))


def test_kovs_position_transform_stops_at_byte_256() -> None:
    stream = bytes(i % 251 for i in range(300))
    encoded = kovs_record(stream)
    expected = bytes(value ^ i if i < 256 else value for i, value in enumerate(stream))

    assert encoded == b"KOVS" + struct.pack("<Ii", 300, 0) + bytes(20) + expected


def test_kovs_metadata_does_not_drop_signed_loop_start_or_use_a_fixed_sample_rate() -> None:
    looped = bytearray(_record(101, serial=18, samples=4321, rate=22_050))
    struct.pack_into("<i", looped, 8, -123)
    size, loop, metadata = parse_kovs_record(bytes(looped))
    assert (size, loop, dataclasses.astuple(metadata)) == (69, -123, (1, 18, 1, 22_050, 4321))


def test_synthetic_record_does_not_add_a_zero_length_packet_at_minimum_size() -> None:
    for size in (90, 101, 150):
        assert build_synthetic_kovs_record(size, stream_serial=71, sample_rate=22_050, sample_count=901) == _record(size, serial=71, rate=22_050, samples=901)


def test_wo2_voice_index_requires_contiguous_sector_starts() -> None:
    index = struct.pack(">II", 2, 0) + struct.pack(">10I", 0, 0, 2, 100, 48_000, 1, 2, 3, 201, 48_000)

    assert wo2_voice_records(index) == [(0, 100), (4096, 201)]
    changed = bytearray(index)
    struct.pack_into(">I", changed, 32, 3)
    with pytest.raises(VoiceBankError, match="contiguous"):
        wo2_voice_records(bytes(changed))


def test_voice_bank_table_serves_sparse_ids_with_their_own_aligned_records() -> None:
    clips = {MOZ_VOICE_IDS - 1: _record(90, serial=1), 5: _record(101, serial=2)}
    bank = english_voice_bank(clips)
    table = [struct.unpack_from("<II", bank, 4 + 8 * i) for i in range(MOZ_VOICE_IDS)]

    assert struct.unpack_from("<I", bank)[0] == MOZ_VOICE_IDS
    assert {i: (off % 16, bank[off:off + size]) for i, (off, size) in enumerate(table) if size} == {i: (0, clip) for i, clip in clips.items()}
    assert len(bank) == 181_536


def test_se_replacement_keeps_each_clip_offset_and_preserves_non_audio_fields() -> None:
    original = [struct.pack("<IHHHH4xIII12s", 0x01000000, 48_000, 0x8800, 512, 1017, 9, 0, 512, bytes((k,)) * 12) for k in range(3)]
    clips = [(256, bytes((1,)) * 512), (512, bytes((2,)) * 1024), (0, b"")]
    header, data = se_bank(_se_header(original), clips)

    assert data == b"_DBW0000" + struct.pack("<I", 1548) + bytes((1,)) * 512 + bytes((2,)) * 1024
    assert struct.unpack_from("<I", header, 16)[0] == 1536
    for offset, old, (samples, ima) in zip(se_entries(header), original, clips, strict=True):
        entry = header[offset:offset + 40]
        count, start, size = struct.unpack_from("<III", entry, 16)
        assert (count, data[12 + start:12 + start + size], entry[:16] + entry[28:]) == (samples, ima, old[:6] + struct.pack('<HHH', 0x4800, 2, 1) + old[12:16] + old[28:])


def test_se_index_reads_header_and_data_as_pairs_without_swapping_offsets() -> None:
    body = b"head!clip"
    spans = [(0, 5), (5, 4)] + [(0, 0)] * 242
    index = struct.pack(">II", 244, 0) + b"".join(struct.pack(">II", *span) for span in spans)

    assert moz_se_banks(index, body) == [(b"head!", b"clip")] + [(b"", b"")] * 121


def test_wo2_se_entries_use_record_words_and_accumulate_unaligned_bank_sizes() -> None:
    def bank(records: list[tuple[int, int]]) -> bytes:
        sample = b"SDsdSmpl" + struct.pack("<II", 0, len(records)) + b"".join(struct.pack("<18I", offset, *([0] * 12), 48_000, 0, 0, 0, size) for offset, size in records)
        chunk = b"SDsdVers" + bytes(8) + b"SDsdHead" + bytes(40) + sample
        return chunk + bytes(2048 - len(chunk))

    assert wo2_se_entries(bank([(0, 80), (80, 100)]) + bank([(0, 70)])) == [[(0, 80, 48_000), (80, 100, 48_000)], [(180, 70, 48_000)]]


def test_slot_voice_map_drops_ambiguous_ids_and_unsuffixed_slots_without_shifting() -> None:
    tables = [([b"a@100", b"b", b"c@102"], [b"d@7", b"e@8", b"f@9"]), ([b"g@100", b"h@103"], [b"i@11", b"j@12"])]

    assert slot_voice_map(tables, 1644) == {0x3078 + 101: 1652, 0x3078 + 102: 1655}


def test_block_voice_map_does_not_drop_the_last_id_in_a_run() -> None:
    assert block_voice_map((100, 17, 3)) == {100: 17, 101: 18, 102: 19}


@pytest.mark.skipif(os.environ.get("WOZMOD_AUDIO") != "1" or not shutil.which("ffmpeg"), reason="set WOZMOD_AUDIO=1 with ffmpeg")
def test_x360_placeholder_falls_back_without_blocking_a_usable_shared_clip(tmp_path: Path) -> None:
    # Bug: a repeated WO2 placeholder is installed or blocks the WO1 clip for its id, a WO1 clip overrides a
    # usable WO2 one, or a WO1 clip that is the stock take (as long as the stock clip) replaces it.
    def wav(samples: int, tone: int) -> bytes:
        pcm = b"".join(struct.pack("<h", (i * tone) % 4000 - 2000) for i in range(samples))
        fmt = struct.pack("<IHHIIHH", 16, 1, 1, 48_000, 96_000, 2, 16)
        return b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm

    def x360_bank(name: str, riffs: list[bytes]) -> tuple[bytes, Path]:
        bank = tmp_path / name
        bank.write_bytes(b"".join(c + bytes(-len(c) % 2048) for c in riffs))
        return struct.pack(">II", len(riffs), 0) + b"".join(struct.pack(">5I", i, sum(-(-len(c) // 2048) for c in riffs[:i]), -(-len(clip) // 2048), len(clip), 48_000) for i, clip in enumerate(riffs)), bank

    wo2_index, wo2_bank = x360_bank("wo2.bdx", [wav(4800, 7)] * 3 + [wav(9600, 13)])
    wo1_index, wo1_bank = x360_bank("wo1.bdx", [wav(6000, 5), wav(7000, 11), wav(1234, 3)])
    stock = tmp_path / "stock.bin"
    stock.write_bytes(_record(96, samples=1234))
    stock_index = bytes(8) + struct.pack(">II", 0, 96) * 14

    selected, sources = english_voice_clips({10: 0, 11: 1, 12: 2}, wo1_index, wo1_bank, {10: 3, 11: 0, 12: 2, 13: 1}, wo2_index, wo2_bank, stock_index, stock)

    assert {i: parse_kovs_record(clip)[2].sample_count for i, clip in selected.items()} == {10: 9600, 11: 7000}
    assert sources == {10: "wo2-x360", 11: "wo1-x360"}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_psp_voice_fills_only_a_line_neither_xbox_360_disc_has_and_leaves_as_48_khz(tmp_path: Path) -> None:
    # Bug: the lower-quality PSP take replaces an Xbox 360 line, a line only the PSP has stays Japanese, its 44.1 kHz
    # clip reaches the bank unconverted (which the build rejects), or a repeated PSP placeholder ships.
    from synthetic import write_ps2_archive

    def wav(samples: int, rate: int) -> bytes:
        pcm = b"".join(struct.pack("<h", (i * 7) % 4000 - 2000) for i in range(samples))
        fmt = struct.pack("<IHHIIHH", 16, 1, 1, rate, 2 * rate, 2, 16)
        return b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm

    def x360_bank(name: str, riffs: list[bytes]) -> tuple[bytes, Path]:
        bank = tmp_path / name
        bank.write_bytes(b"".join(c + bytes(-len(c) % 2048) for c in riffs))
        starts = [sum(-(-len(c) // 2048) for c in riffs[:i]) for i in range(len(riffs))]
        return struct.pack(">II", len(riffs), 0) + b"".join(
            struct.pack(">5I", i, starts[i], -(-len(c) // 2048), len(c), 48_000) for i, c in enumerate(riffs)), bank

    wo2_index, wo2_bank = x360_bank("wo2.bdx", [wav(4800, 48_000)])  # 0.1 s
    wo1_index, wo1_bank = x360_bank("wo1.bdx", [wav(9600, 48_000)])  # 0.2 s
    placeholder = wav(441, 44_100)
    psp_bank = write_ps2_archive(tmp_path / "voice_na.bns", {
        0: wav(13_230, 44_100), 1: wav(17_640, 44_100), 2: wav(22_050, 44_100),  # 0.3, 0.4 and 0.5 s
        3: placeholder, 4: placeholder, 5: placeholder,
    })
    stock = tmp_path / "stock.bin"
    stock.write_bytes(_record(96, samples=1234))
    stock_index = bytes(8) + struct.pack(">II", 0, 96) * 15

    clips, sources = english_voice_clips(
        {11: 0}, wo1_index, wo1_bank, {10: 0}, wo2_index, wo2_bank, stock_index, stock,
        psp=({10: 0, 11: 1, 12: 2, 14: 3}, psp_bank))

    assert sources == {10: "wo2-x360", 11: "wo1-x360", 12: "wo2-psp"}
    seconds = {i: round(parse_kovs_record(clip)[2].sample_count / 48_000, 2) for i, clip in clips.items()}
    assert seconds == {10: 0.1, 11: 0.2, 12: 0.5}
    english_voice_bank(clips)  # raises unless every clip is 48 kHz mono


def _atrac3plus_riff(frames: int) -> bytes:
    """A mono 44.1 kHz ATRAC3plus RIFF laid out as the PSP bank's are (192-byte frames of 2048 samples), silent."""
    import uuid
    block = 192
    frame = bytes((0b0110_0000,)) + bytes(block - 1)  # a zero start bit, then the channel-unit terminator
    subtype = uuid.UUID("e923aabf-cb58-4471-a119-fffa01e4ce62").bytes_le  # KSDATAFORMAT_SUBTYPE_ATRAC3PLUS
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, 1, 44_100, block * 44_100 // 2048, block, 0, 22, 2048, 4) + subtype
    data = frame * frames
    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_psp_atrac3plus_clip_declares_its_whole_length(tmp_path: Path) -> None:
    # Bug: ffmpeg stamps each two-frame ATRAC3plus packet with one frame's duration, the Ogg granule positions follow
    # those timestamps, and the clip declares about half its length: the game stops every PSP line halfway. With 16
    # frames the last starts at sample 30720, where a timestamp rebuilt as N/SR/TB truncates to one sample short.
    import subprocess

    from synthetic import write_ps2_archive

    from wozmod.formats.audio import donor_voice_clips
    from wozmod.formats.linkdata import ps2_extents

    riff = _atrac3plus_riff(16)
    bank = write_ps2_archive(tmp_path / "voice_na.bns", {0: riff})
    clips, _ = donor_voice_clips([("wo2-psp", {12: 0}, ps2_extents(bank), bank)])

    (tmp_path / "clip.wav").write_bytes(riff)
    pcm = subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(tmp_path / "clip.wav"), "-ac", "1", "-ar", "48000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout  # ffmpeg's own decode
    assert parse_kovs_record(clips[12])[2].sample_count == len(pcm) // 2
    english_voice_bank(clips)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_voice_bank_refuses_a_clip_that_declares_less_than_it_holds(tmp_path: Path) -> None:
    # Bug: a clip whose final granule position is short of its audio ships, and the game cuts the line there.
    import subprocess

    wav = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5:sample_rate=48000",
                    "-ac", "1", str(wav)], check=True)
    ogg = bytearray(subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-c:a", "libvorbis", "-f", "ogg", "-"],
                                   capture_output=True, check=True).stdout)
    last = ogg.rindex(b"OggS")
    end = last + 27 + ogg[last + 26] + sum(ogg[last + 27:last + 27 + ogg[last + 26]])
    assert end == len(ogg) and struct.unpack_from("<Q", ogg, last + 6)[0] == 24_000
    english_voice_bank({1: kovs_record(bytes(ogg))})

    struct.pack_into("<Q", ogg, last + 6, 12_000)  # the final page now declares half the clip
    ogg[last + 22:last + 26] = bytes(4)
    struct.pack_into("<I", ogg, last + 22, _checksum(ogg[last:end]))
    with pytest.raises(VoiceBankError, match="declares 12000 samples but holds"):
        english_voice_bank({1: kovs_record(bytes(ogg))})


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_batched_x360_clips_keep_their_own_audio_across_batch_boundaries(tmp_path: Path, monkeypatch) -> None:
    # Bug: one ffmpeg process converts several clips; a clip gets a neighbour's audio, or the last
    # partial batch is dropped.
    import wozmod.formats.audio as audio

    def wav(samples: int) -> bytes:
        pcm = b"".join(struct.pack("<h", (i * 7) % 4000 - 2000) for i in range(samples))
        fmt = struct.pack("<IHHIIHH", 16, 1, 1, 48_000, 96_000, 2, 16)
        return b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm

    lengths = [4800, 6400, 8000, 9600, 11200]  # three batches of two, the last one partial
    clips = [wav(n) for n in lengths]
    bank = tmp_path / "wo2-x360.bdx"
    bank.write_bytes(b"".join(c + bytes(-len(c) % 2048) for c in clips))
    starts = [sum(-(-len(c) // 2048) for c in clips[:i]) for i in range(len(clips))]
    index = struct.pack(">II", len(clips), 0) + b"".join(
        struct.pack(">5I", i, starts[i], -(-len(c) // 2048), len(c), 48_000) for i, c in enumerate(clips))
    monkeypatch.setattr(audio, "FFMPEG_BATCH", 2)

    converted, _ = audio.donor_voice_clips([("wo2-x360", {100 + i: i for i in range(len(clips))}, audio.wo2_voice_records(index), bank)], jobs=2)

    assert {moz_id: parse_kovs_record(clip)[2].sample_count for moz_id, clip in converted.items()} == {
        100 + i: n for i, n in enumerate(lengths)}


def test_pcm_se_entries_say_pcm_not_adpcm_and_point_at_whole_samples() -> None:
    # Bug: PCM data under the stock entry's IMA ADPCM format fields, which the game would decode as
    # noise, or sizes counted in ADPCM blocks so that every later clip starts in the wrong place.
    original = [struct.pack("<IHHHH4xIII12s", 0x01000000, 48_000, 0x8800, 512, 1017, 9, 0, 512, bytes(12)) for _ in range(2)]
    clips = [(3, struct.pack("<3h", 1, -2, 3)), (2, struct.pack("<2h", 4, 5))]

    header, data = se_bank(_se_header(original), clips)

    assert data == b"_DBW0000" + struct.pack("<I", 12 + 10) + clips[0][1] + clips[1][1]
    entries = [header[at:at + 40] for at in se_entries(header)]
    assert [struct.unpack_from("<HHHH", e, 4) for e in entries] == [(48_000, 0x4800, 2, 1)] * 2
    assert [struct.unpack_from("<III", e, 16) for e in entries] == [(3, 0, 6), (2, 6, 4)]
    with pytest.raises(VoiceBankError):  # ADPCM-sized data does not pass for PCM
        se_bank(_se_header(original[:1]), [(3, bytes(512))])


def test_voice_clip_too_long_for_the_game_slot_is_encoded_again_one_step_lower(tmp_path: Path, monkeypatch) -> None:
    # Bug: a clip above the game's slot buffer stays in the bank (the build then fails or the game cuts it),
    # or the whole bank drops in quality because one clip is long.
    import wozmod.formats.audio as audio

    riffs = [b"A" * 2048, b"B" * 2048]
    bank = tmp_path / "wo2-x360.bdx"
    bank.write_bytes(b"".join(riffs))
    index = struct.pack(">II", 2, 0) + b"".join(struct.pack(">5I", i, i, 1, 2048, 48_000) for i in range(2))
    calls = []

    def fake(jobs, workers=None, *progress):
        quality = int(jobs[0][2][jobs[0][2].index("-q:a") + 1])
        calls.append((quality, [job[1] for job in jobs]))
        # clip B is too long at the top quality and fits one step lower
        return [b"x" * (audio.SLOT_BYTES if job[1][:1] == b"B" and quality == audio.WO2_VORBIS_QUALITY else 100) for job in jobs]

    monkeypatch.setattr(audio, "_ffmpeg_each", fake)
    clips, _ = audio.donor_voice_clips([("wo2-x360", {7: 0, 8: 1}, audio.wo2_voice_records(index), bank)])

    assert calls == [(audio.WO2_VORBIS_QUALITY, riffs), (audio.WO2_VORBIS_QUALITY - 1, [riffs[1]])]
    assert {i: len(c) for i, c in clips.items()} == {7: 132, 8: 132}
