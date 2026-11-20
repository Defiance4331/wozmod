from __future__ import annotations

import dataclasses
import io
import os
import shutil
import struct
from pathlib import Path

import pytest

from wozmod.game_files import StockVoices
from wozmod.formats.audio import (
    BankClips,
    MOZ_VOICE_IDS,
    VoiceBankError,
    block_voice_map,
    english_voice_bank,
    english_voice_clips,
    kovs_record,
    moz_se_banks,
    parse_kovs_record,
    quote_slots,
    se_bank,
    se_entries,
    psp_se_entries,
    se_plan,
    slot_voice_map,
    wo2_se_entries,
    wo2_voice_records,
)
from synthetic import kovs, ogg_crc


def voice_bank(clips: dict[int, bytes]) -> bytes:
    """The Voice.bin english_voice_bank writes for clips."""
    out = io.BytesIO()
    english_voice_bank(clips, out)
    return out.getvalue()


def _se_header(entries: list[bytes]) -> bytes:
    offsets = [8 + 4 * len(entries) + 40 * k for k in range(len(entries))]
    body = struct.pack(f"<HHI{len(entries)}I", 0x3200, len(entries), 0, *offsets) + b"".join(entries)
    return b"_HBW0000" + struct.pack("<I", 12 + len(body)) + body


def test_kovs_rejects_corruption_after_the_identification_packet() -> None:
    changed = bytearray(kovs(101))
    changed[-1] ^= 1

    with pytest.raises(VoiceBankError, match="checksum"):
        parse_kovs_record(bytes(changed))
    assert parse_kovs_record(bytes(changed), verify_checksums=False)[2].sample_rate == 48_000


@pytest.mark.parametrize("damage", ["length", "reserved"])
def test_kovs_rejects_a_wrong_stream_length_or_nonzero_reserved_header(damage: str) -> None:
    changed = bytearray(kovs(90))
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
    looped = bytearray(kovs(101, serial=18, samples=4321, rate=22_050))
    struct.pack_into("<i", looped, 8, -123)
    size, loop, metadata = parse_kovs_record(bytes(looped))
    assert (size, loop, dataclasses.astuple(metadata)) == (69, -123, (1, 18, 1, 22_050, 4321))


def test_wo2_voice_index_requires_contiguous_sector_starts() -> None:
    index = struct.pack(">II", 2, 0) + struct.pack(">10I", 0, 0, 2, 100, 48_000, 1, 2, 3, 201, 48_000)

    assert wo2_voice_records(index) == [(0, 100), (4096, 201)]
    changed = bytearray(index)
    struct.pack_into(">I", changed, 32, 3)
    with pytest.raises(VoiceBankError, match="contiguous"):
        wo2_voice_records(bytes(changed))


def test_voice_bank_table_serves_sparse_ids_with_their_own_aligned_records() -> None:
    # Bugs: a record served under another id or misaligned; a voice slot past the stock ids (voices.maps.voice_slots)
    # left out of the count, so the runtime (english_voice: id < count) never plays it.
    clips = {MOZ_VOICE_IDS - 1: kovs(90, serial=1), 5: kovs(101, serial=2), MOZ_VOICE_IDS + 1: kovs(80, serial=3)}
    bank = voice_bank(clips)
    count = struct.unpack_from("<I", bank)[0]
    table = [struct.unpack_from("<II", bank, 4 + 8 * i) for i in range(count)]

    assert count == MOZ_VOICE_IDS + 2
    assert {i: (off % 16, bank[off:off + size]) for i, (off, size) in enumerate(table) if size} == {i: (0, clip) for i, clip in clips.items()}


def test_voice_bank_is_written_clip_by_clip_not_built_whole_beside_the_clips() -> None:
    # Bug: Voice.bin (about 1 GB) is assembled in memory beside the clips it is made of before it is written, which
    # set the voices step's peak (2026-11: 2.87 GiB, a third of it the bank's copy).
    import tracemalloc

    class Sink(io.RawIOBase):  # keeps nothing it is given
        def write(self, data) -> int:
            return len(data)

    clips = {voice: kovs(345, serial=voice) for voice in range(4000)}  # 1.4 MB: four times the table
    given = sum(map(len, clips.values()))
    tracemalloc.start()
    try:
        english_voice_bank(clips, Sink())
        held = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert held < given / 2, (held, given)


def test_clips_read_back_from_a_written_bank_are_the_clips_it_was_written_from(tmp_path) -> None:
    # Bug: the voices step reads its clips back from Voice.bin once it is written (BankClips), so a span read from
    # the wrong table row, or a voice slot past the stock ids left out, hands the lip sync and the event timing
    # another line's clip, or none.
    clips = {MOZ_VOICE_IDS + 1: kovs(80, serial=3), 5: kovs(101, serial=2), 0: kovs(90, serial=1)}
    with open(tmp_path / "Voice.bin", "wb") as out:
        english_voice_bank(clips, out)
    with BankClips(tmp_path / "Voice.bin") as back:
        assert dict(back) == clips and list(back) == sorted(clips)


def test_se_replacement_keeps_each_clip_offset_takes_the_donor_volume_and_preserves_other_fields() -> None:
    # Bug: an English shout keeps the stock entry's volume (byte +34), set for the Japanese recording, instead of the
    # one its donor game plays it at, which made Dodomeki's shouts 3 dB louder than on the Xbox 360.
    original = [struct.pack("<IHHHH4xIII6shh2s", 0x01000000, 48_000, 0x8800, 512, 1017, 9, 0, 512, bytes((k,)) * 6, -100, 7, b"zz")
                for k in range(3)]
    clips = [(256, bytes((1,)) * 512, -401), (512, bytes((2,)) * 1024, 0), (0, b"", -10000)]
    header, data = se_bank(_se_header(original), clips)

    assert data == b"_DBW0000" + struct.pack("<I", 1548) + bytes((1,)) * 512 + bytes((2,)) * 1024
    assert struct.unpack_from("<I", header, 16)[0] == 1536
    for offset, old, (samples, pcm, volume) in zip(se_entries(header), original, clips, strict=True):
        entry = header[offset:offset + 40]
        count, start, size = struct.unpack_from("<III", entry, 16)
        assert (count, data[12 + start:12 + start + size], struct.unpack_from("<h", entry, 34)[0]) == (samples, pcm, volume)
        assert entry[:16] + entry[28:34] + entry[36:] == old[:6] + struct.pack("<HHH", 0x4800, 2, 1) + old[12:16] + old[28:34] + old[36:]


def test_se_index_reads_header_and_data_as_pairs_without_swapping_offsets() -> None:
    body = b"head!clip"
    spans = [(0, 5), (5, 4)] + [(0, 0)] * 242
    index = struct.pack(">II", 244, 0) + b"".join(struct.pack(">II", *span) for span in spans)

    assert moz_se_banks(index, body) == [(b"head!", b"clip")] + [(b"", b"")] * 121


def _shout_fixture(wo2_entries: int = 16):
    """MOZ banks like the stock ones (9-104 officers, 105-121 generic units), and donor banks each laid out as its
    disc's: a bank's clips are (officer, entry) offsets, and the other-unit banks repeat the officers' (WO2 X360
    102-195 = 8-101, WO1 X360 87-165 = 8-86)."""
    stock = [(b"", b"")] * 9 + [(_se_header([bytes(40)] * (16 if b < 105 else 11)), b"") for b in range(9, 122)]
    clips = lambda officer, n: [(1000 * officer + k, 1, 48_000) for k in range(n)]
    wo2 = [[]] * 8 + [clips(u, wo2_entries if u == 5 else 16) for u in range(94)] * 2
    wo2 += [clips(u, 11) for u in range(94, 111)] + [[]] * 4
    wo1 = [[]] * 8 + [clips(50_000 + u, 13) for u in range(79)] * 2 + [clips(50_000 + u, 24) for u in range(79, 94)]
    psp = [[]] * 8 + [clips(90_000 + u, 16) for u in range(96)] + [[]] * 113
    return stock, {"wo1-x360": wo1, "wo2-x360": wo2, "wo2-psp": psp}


def test_se_banks_pair_by_officer_where_a_donor_repeats_its_officers_banks() -> None:
    # Bug: pairing MOZ bank n with WO2 X360 bank n - 1 puts San Zang's bank 103 on WO2 102, a repeat of Zhao Yun's
    # bank 8, so switching to San Zang plays Zhao Yun's shouts. No two MOZ officers may play the same clips.
    stock, donors = _shout_fixture()
    plan = se_plan(stock, donors)
    played = {}
    for moz, sources in plan.items():
        played.setdefault(tuple(donors[disc][bank][j] for disc, bank, j in sources), []).append(moz)

    assert [banks for banks in played.values() if len(banks) > 1] == []


def test_se_bank_whose_officer_bank_has_another_entry_count_stops_the_build() -> None:
    # Bug: a bank whose officer's donor bank has another entry count is skipped and silently stays Japanese (how
    # Benkei escaped the wrong pairing), or is filled entry by entry from a bank laid out for other shouts.
    stock, donors = _shout_fixture(wo2_entries=17)

    with pytest.raises(VoiceBankError, match="SE bank 14 has 16 entries"):
        se_plan(stock, donors)


def test_wo2_se_entries_use_record_words_and_accumulate_unaligned_bank_sizes() -> None:
    # Bugs: a shifted record word, a dropped volume factor (words 7 to 9 multiply, each in hundredths), or a bank
    # offset that restarts at each bank.
    def bank(records: list[tuple[int, int, tuple[int, int, int]]]) -> bytes:
        sample = b"SDsdSmpl" + struct.pack("<II", 0, len(records)) + b"".join(
            struct.pack("<18I", offset, *([0] * 6), *factors, *([0] * 3), 48_000, 0, 0, 0, size) for offset, size, factors in records)
        chunk = b"SDsdVers" + bytes(8) + b"SDsdHead" + bytes(40) + sample
        return chunk + bytes(2048 - len(chunk))

    banks = wo2_se_entries(bank([(0, 80, (100, 100, 63)), (80, 100, (100, 90, 63))]) + bank([(0, 70, (100, 100, 100))]))
    assert banks == [[(0, 80, 48_000, -401), (80, 100, 48_000, -493)], [(180, 70, 48_000, 0)]]


def test_psp_se_entries_give_each_clip_the_volume_of_the_tone_that_plays_it() -> None:
    # Bugs: the padding at the end of the tone chunk read as one more tone (which gave San Zang's unplayed clip 15 a
    # volume), or a tone's clip counted from 0 instead of the PPVA chunk's first index.
    first = 2
    tones = [(first, 0x1000), (first + 1, 3200)]
    pptn = b"".join(struct.pack("<i28xI60x", clip, volume) for clip, volume in tones) + struct.pack("<I", first + 2) + bytes(0x2C)
    pptn = b"PPTN" + struct.pack("<I24x", 0x20 + len(pptn)) + pptn
    records = [(0, 16_000, 32), (32, 16_000, 32), (64, 11_025, 32), (0xFFFFFFFF,) * 3]
    ppva = b"".join(struct.pack("<IIIi", *r, -1) for r in records)
    ppva = b"PPVA" + struct.pack("<I8xI12x", 0x20 + len(ppva), first) + ppva
    chunks = b"PPHD" + bytes(28) + pptn + ppva
    bank = struct.pack("<4I", 2, 16, 16 + len(chunks), 96) + chunks + bytes(96)
    pack = struct.pack("<4I", 0, 24 + len(bank), 1, 0x800) + struct.pack("<II", 24, len(bank)) + bank
    body = 24 + 16 + len(chunks)

    assert psp_se_entries(pack) == [[(body, 32, 16_000, 0), (body + 32, 32, 16_000, -214), (body + 64, 32, 11_025, None), None]]


def test_slot_voice_map_drops_ambiguous_ids_and_unsuffixed_slots_without_shifting() -> None:
    tables = [([b"a@100", b"b", b"c@102"], [b"d@7", b"e@8", b"f@9"]), ([b"g@100", b"h@103"], [b"i@11", b"j@12"])]

    assert slot_voice_map(quote_slots(tables), 1644) == {0x3078 + 101: 1652, 0x3078 + 102: 1655}


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
    stock.write_bytes(kovs(96, samples=1234))
    stock_index = bytes(8) + struct.pack(">II", 0, 96) * 14

    selected, sources = english_voice_clips({10: 0, 11: 1, 12: 2}, wo1_index, wo1_bank, {10: 3, 11: 0, 12: 2, 13: 1}, wo2_index, wo2_bank, StockVoices(stock_index, stock))

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
    stock.write_bytes(kovs(96, samples=1234))
    stock_index = bytes(8) + struct.pack(">II", 0, 96) * 15

    clips, sources = english_voice_clips(
        {11: 0}, wo1_index, wo1_bank, {10: 0}, wo2_index, wo2_bank, StockVoices(stock_index, stock),
        psp=({10: 0, 11: 1, 12: 2, 14: 3}, psp_bank))

    assert sources == {10: "wo2-x360", 11: "wo1-x360", 12: "wo2-psp"}
    seconds = {i: round(parse_kovs_record(clip)[2].sample_count / 48_000, 2) for i, clip in clips.items()}
    assert seconds == {10: 0.1, 11: 0.2, 12: 0.5}
    voice_bank(clips)  # raises unless every clip is 48 kHz mono


def _atrac3plus_riff(frames: int, fact: tuple[int, int] | None = None, tag: int = 0xFFFE) -> bytes:
    """A mono 44.1 kHz ATRAC3plus RIFF laid out as the PSP bank's are (192-byte frames of 2048 samples), silent,
    with the bank's fact chunk (sample count, samples to skip) when fact is given; tag replaces the format tag."""
    import uuid
    block = 192
    frame = bytes((0b0110_0000,)) + bytes(block - 1)  # a zero start bit, then the channel-unit terminator
    subtype = uuid.UUID("e923aabf-cb58-4471-a119-fffa01e4ce62").bytes_le  # KSDATAFORMAT_SUBTYPE_ATRAC3PLUS
    fmt = struct.pack("<HHIIHHHHI", tag, 1, 44_100, block * 44_100 // 2048, block, 0, 22, 2048, 4) + subtype
    data = frame * frames
    facts = b"" if fact is None else b"fact" + struct.pack("<III", 8, *fact)
    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + facts + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_psp_atrac3plus_clip_declares_the_length_its_fact_chunk_counts(tmp_path: Path) -> None:
    # Bug: ffmpeg stamps each two-frame ATRAC3plus packet with one frame's duration, the Ogg granule positions follow
    # those timestamps, and the clip declares about half its length: the game stops every PSP line halfway. A clip
    # that keeps the decoder's padding or the encoder's tail declares more than the fact chunk's count.
    from synthetic import write_ps2_archive

    from wozmod.formats.audio import donor_voice_clips
    from wozmod.formats.linkdata import ps2_extents

    count = 30_000  # 16 frames decode to 32768 samples, of which the fact chunk counts these after its skip
    bank = write_ps2_archive(tmp_path / "voice_na.bns", {0: _atrac3plus_riff(16, (count, 2048))})
    clips, _ = donor_voice_clips([("wo2-psp", {12: 0}, ps2_extents(bank), bank)])

    assert abs(parse_kovs_record(clips[12])[2].sample_count - count * 48_000 / 44_100) <= 1  # the resampler rounds
    voice_bank(clips)


def test_atrac3plus_decode_starts_after_the_fact_skip_and_decoder_delay_and_xma_stays_whole() -> None:
    # Bug: the trim reads the fact chunk's words the wrong way round, or cuts the start off an XMA clip too.
    from wozmod.formats.audio import ATRAC3PLUS_DECODER_DELAY, atrac3plus_samples

    first = 2048 + ATRAC3PLUS_DECODER_DELAY
    assert atrac3plus_samples(_atrac3plus_riff(4, (5000, 2048))) == (first, first + 5000)
    assert atrac3plus_samples(_atrac3plus_riff(4)) == (ATRAC3PLUS_DECODER_DELAY, None)
    assert atrac3plus_samples(_atrac3plus_riff(4, (5000, 2048), tag=0x165)) is None  # XMA's tag


@pytest.mark.skipif(os.environ.get("WOZMOD_AUDIO") != "1" or not os.environ.get("WOZMOD_DONORS")
                    or not shutil.which("ffmpeg"),
                    reason="set WOZMOD_AUDIO=1 and WOZMOD_DONORS, with ffmpeg")
def test_psp_line_starts_where_its_xbox_360_take_starts() -> None:
    # Bug: ffmpeg's ATRAC3plus decode starts 2417 samples (110 ms at 22050 Hz) late, so a PSP line starts after its
    # subtitle; a trim off by a frame misses by 93 ms, and one applied to XMA moves the Xbox 360 take instead.
    # PSP members 5375, 6305 and 6510 hold the same edit as WO2 X360 records 2116, 6959 and 10218 (voice ids 12880,
    # 18958 and 22271).
    import subprocess

    import numpy as np

    from wozmod.formats.audio import donor_voice_clips
    from wozmod.formats.linkdata import ps2_extents

    donors = Path(os.environ["WOZMOD_DONORS"])
    psp, x360 = donors / "wo2-psp/PSP_GAME/USRDIR/sound/voice_na.bns", donors / "wo2-x360"
    pairs = {1: (5375, 2116), 2: (6305, 6959), 3: (6510, 10218)}
    built, _ = donor_voice_clips([("wo2-psp", {k: p for k, (p, _) in pairs.items()}, ps2_extents(psp), psp),
                                  ("wo2-x360", {10 * k: x for k, (_, x) in pairs.items()},
                                   wo2_voice_records((x360 / "linkdata_ans.idx").read_bytes()), x360 / "link_vodat.bdx")])

    def pcm(record: bytes) -> np.ndarray:
        ogg = bytearray(record[32:])  # the Ogg stream inside the KOVS record, its first 256 bytes XORed by position
        for position in range(min(256, len(ogg))):
            ogg[position] ^= position
        out = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-f", "f32le", "-"], input=bytes(ogg),
                             capture_output=True, check=True).stdout
        return np.frombuffer(out, "<f4").astype(np.float64)

    for k in pairs:
        a, b = pcm(built[10 * k]), pcm(built[k])
        n = min(len(a), len(b))
        size = 1 << (2 * n).bit_length()
        c = np.fft.irfft(np.fft.rfft(b[:n], size) * np.conj(np.fft.rfft(a[:n], size)), size)
        lag = int(np.argmax(np.r_[c[:9600], c[-9600:]]))
        lag = lag if lag < 9600 else lag - 19_200  # samples at 48 kHz the PSP clip trails the Xbox 360 one
        assert abs(lag) <= 12, (pairs[k], lag)  # a quarter of a millisecond


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_stereo_clip_with_the_same_signal_in_both_channels_keeps_its_level(tmp_path: Path) -> None:
    # Bug: -ac 1 mixes each channel of a PSP dialogue clip (the same signal in both) at 0.707 into the encoder's float
    # samples, so the mono clip runs 3 dB over its source and 445 of the PSP lines clip. Its level must be one channel's.
    import math
    import subprocess
    from array import array

    from synthetic import write_ps2_archive

    from wozmod.formats.audio import donor_voice_clips
    from wozmod.formats.linkdata import ps2_extents

    def rms_db(command: list[str], data: bytes = b"") -> float:
        pcm = array("f", subprocess.run(["ffmpeg", "-v", "error", *command, "-f", "f32le", "-ac", "1", "-"], input=data,
                                        capture_output=True, check=True).stdout)
        return 10 * math.log10(sum(s * s for s in pcm) / len(pcm))

    tone = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=48000"]
    riff = subprocess.run(["ffmpeg", "-v", "error", *tone, "-af", "pan=stereo|c0=c0|c1=c0", "-c:a", "pcm_s16le",
                           "-f", "wav", "-"], capture_output=True, check=True).stdout
    bank = write_ps2_archive(tmp_path / "voice_na.bns", {0: riff})
    clips, _ = donor_voice_clips([("wo2-psp", {12: 0}, ps2_extents(bank), bank)])

    ogg = bytearray(clips[12][32:])  # the Ogg stream inside the KOVS record, its first 256 bytes XORed by position
    for position in range(min(256, len(ogg))):
        ogg[position] ^= position
    built = rms_db(["-i", "-"], bytes(ogg))
    assert abs(built - rms_db(tone)) < 0.2, built
    voice_bank(clips)  # and it is a mono clip the bank takes


@pytest.mark.skipif(os.environ.get("WOZMOD_AUDIO") != "1" or not os.environ.get("WOZMOD_DONORS")
                    or not shutil.which("ffmpeg"),
                    reason="set WOZMOD_AUDIO=1 and WOZMOD_DONORS, with ffmpeg")
def test_psp_stereo_dialogue_keeps_the_samples_of_a_22050_hz_source(tmp_path: Path) -> None:
    # Bug: voice_na.bns stereo dialogue is 22050 Hz audio in a 44100 Hz header, so the built clip has half
    # the samples and plays an octave high. A mono clip from the same bank is a stock take at the labeled rate.
    from synthetic import write_ps2_archive

    from wozmod.formats.audio import PSP_CLIP_BASE, donor_voice_clips
    from wozmod.formats.linkdata import ps2_extents, ps2_members

    bank_path = Path(os.environ["WOZMOD_DONORS"]) / "wo2-psp/PSP_GAME/USRDIR/sound/voice_na.bns"
    members = ps2_members(bank_path, [0, PSP_CLIP_BASE])
    bank = write_ps2_archive(tmp_path / "voice_na.bns", members)
    clips, _ = donor_voice_clips([("wo2-psp", {1: 0, 2: PSP_CLIP_BASE}, ps2_extents(bank), bank)])

    for moz_id, member, rate in ((1, 0, 44_100), (2, PSP_CLIP_BASE, 22_050)):
        fact = members[member].find(b"fact")
        count = struct.unpack_from("<I", members[member], fact + 8)[0]  # the samples the encoder counted
        samples = parse_kovs_record(clips[moz_id])[2].sample_count
        expect = round(count * 48_000 / rate)
        assert abs(samples - expect) <= 1, (moz_id, samples, expect)


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
    voice_bank({1: kovs_record(bytes(ogg))})

    struct.pack_into("<Q", ogg, last + 6, 12_000)  # the final page now declares half the clip
    ogg[last + 22:last + 26] = bytes(4)
    struct.pack_into("<I", ogg, last + 22, ogg_crc(ogg[last:end]))
    with pytest.raises(VoiceBankError, match="declares 12000 samples but holds"):
        voice_bank({1: kovs_record(bytes(ogg))})


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
    clips = [(3, struct.pack("<3h", 1, -2, 3), 0), (2, struct.pack("<2h", 4, 5), 0)]

    header, data = se_bank(_se_header(original), clips)

    assert data == b"_DBW0000" + struct.pack("<I", 12 + 10) + clips[0][1] + clips[1][1]
    entries = [header[at:at + 40] for at in se_entries(header)]
    assert [struct.unpack_from("<HHHH", e, 4) for e in entries] == [(48_000, 0x4800, 2, 1)] * 2
    assert [struct.unpack_from("<III", e, 16) for e in entries] == [(3, 0, 6), (2, 6, 4)]
    with pytest.raises(VoiceBankError):  # ADPCM-sized data does not pass for PCM
        se_bank(_se_header(original[:1]), [(3, bytes(512), 0)])


def test_voice_clip_too_long_for_the_game_slot_is_encoded_again_one_step_lower(tmp_path: Path, monkeypatch) -> None:
    # Bug: a clip above the game's slot buffer stays in the bank (the build then fails or the game cuts it),
    # or the whole bank drops in quality because one clip is long.
    import wozmod.formats.audio as audio

    riffs = [b"A" * 2048, b"B" * 2048]
    bank = tmp_path / "wo2-x360.bdx"
    bank.write_bytes(b"".join(riffs))
    index = struct.pack(">II", 2, 0) + b"".join(struct.pack(">5I", i, i, 1, 2048, 48_000) for i in range(2))
    calls = []

    def fake(jobs, count, workers=None, *progress):
        jobs = list(jobs)
        quality = int(jobs[0][2][jobs[0][2].index("-q:a") + 1])
        calls.append((quality, [job[1] for job in jobs]))
        # clip B is too long at the top quality and fits one step lower
        return [b"x" * (audio.SLOT_BYTES if job[1][:1] == b"B" and quality == audio.WO2_VORBIS_QUALITY else 100) for job in jobs]

    monkeypatch.setattr(audio, "_ffmpeg_each", fake)
    clips, _ = audio.donor_voice_clips([("wo2-x360", {7: 0, 8: 1}, audio.wo2_voice_records(index), bank)])

    assert calls == [(audio.WO2_VORBIS_QUALITY, riffs), (audio.WO2_VORBIS_QUALITY - 1, [riffs[1]])]
    assert {i: len(c) for i, c in clips.items()} == {7: 132, 8: 132}
