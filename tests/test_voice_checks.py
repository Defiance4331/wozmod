"""tools/voice_checks.py: putting a check's recording and its screen polls on one clock, and finding a take in it."""

import struct

import numpy

import voice_checks


def test_the_recording_starts_at_its_tightest_bound_past_a_header_with_extra_chunks():
    """Bugs: the data is assumed to start at byte 44 (parecord's float WAV carries a fact chunk, so every caption
    would be judged 0.1 ms or more off), or the loosest bound is kept, putting the start late by the write delay."""
    head = (b"RIFF" + struct.pack("<I", 0) + b"WAVE" + b"fmt " + struct.pack("<I", 18) + bytes(18)
            + b"fact" + struct.pack("<I", 4) + bytes(4) + b"data" + struct.pack("<I", 0))
    offset = voice_checks.wav_data_offset(head)
    assert offset == len(head) == 12 + 26 + 12 + 8
    began, rate, frame = 1000.0, 48_000, 8
    # blocks of 960 samples (20 ms), each written 3 to 30 ms after its last sample was captured; one row before data
    delays = [0.030, 0.012, 0.003, 0.020]
    growth = [[began - 0.5, offset]] + [[began + (n + 1) * 0.02 + d, offset + (n + 1) * 960 * frame]
                                        for n, d in enumerate(delays)]
    assert abs(voice_checks.recording_start(growth, offset, frame, rate) - (began + 0.003)) < 1e-9


def test_each_caption_starts_between_the_polls_around_it_and_ends_at_the_next():
    """Bugs: a caption's start is the first poll that shows it (late by up to a poll gap), a two-line caption is
    taken as two, or a caption replaced by the next without a blank frame merges with it."""
    polls = [[10.0, []], [10.1, [[600, 100, "first"]]], [10.2, [[640, 100, "two"], [600, 100, "line"]]],
             [10.3, [[600, 100, "line"], [640, 100, "two"]]], [10.4, []], [10.6, [[600, 100, "last"]]]]
    spans = voice_checks.caption_spans(polls, end=12.0)
    assert [(s["text"], round(s["start"], 3), s["end"]) for s in spans] == [
        ("first", 10.05, 10.2), ("line two", 10.15, 10.4), ("last", 10.5, 12.0)]
    assert [s["gap"] for s in spans] == [0.1, 0.1, 0.2]


def test_a_take_is_found_where_it_plays_under_music_and_another_take_is_not():
    """Bugs: the correlation's lag is read from the wrong end (off by the take's length), or a take that never
    played scores as found."""
    rng = numpy.random.default_rng(7)
    rate = voice_checks.SCAN_RATE
    take, other = rng.standard_normal(rate), rng.standard_normal(rate)
    recording = 0.5 * rng.standard_normal(6 * rate)  # the music bed
    at = int(2.345 * rate)
    recording[at:at + rate] += 0.3 * take  # played at the chain's gain
    where, corr = voice_checks.find(recording, take)
    assert where == at / rate and corr > 0.4
    assert voice_checks.find(recording, other)[1] < voice_checks.FOUND


def test_the_movie_check_reads_the_voice_language_not_the_text_language():
    """Bug: the [language] section's text= value is read as the voice setting, so a run with English text and
    Japanese voices expects the English movie file."""
    settings = "[display]\nmode=windowed\n[language]\ntext=english\nvoice=japanese\n"
    assert voice_checks._voice_mode(settings) == "japanese"
    assert voice_checks._voice_mode("[language]\ntext=japanese\n") == "english"


def _pass(tmp_path, name, plays, captions, last_caption_showing=False):
    """A listen's files: a recording (its samples stand in for ffmpeg's decode) where each take plays at its time,
    its growth journal (the recording starts at wall clock 1000), and polls showing each caption from its time."""
    rate, seconds = voice_checks.SCAN_RATE, 8
    recording = 0.2 * numpy.random.default_rng(len(name)).standard_normal(seconds * rate)
    for take, at in plays:
        recording[int(at * rate):int(at * rate) + len(take)] += take
    head = b"RIFF" + struct.pack("<I", 0) + b"WAVE" + b"data" + struct.pack("<I", 0)
    wav = tmp_path / f"{name}.wav"
    wav.write_bytes(head + name.encode())
    growth = tmp_path / f"{name}.growth.json"
    growth.write_text(f"[[{1000 + seconds}, {len(head) + seconds * 48_000 * 8}]]")
    polls = [[1000 + t / 100, [[600, 100, f"caption {k}"] for k, at in enumerate(captions)
                               if at <= t / 100 < (captions + [seconds])[k + 1]]] for t in range(seconds * 100)]
    if not last_caption_showing:
        polls = [p for p in polls if p[0] < 1000 + seconds - 0.5] + [[1000 + seconds - 0.4, []]]
    return {"wav": str(wav), "growth": str(growth), "polls": polls, "end": 1000.0 + seconds}, recording


def _english_and_stock(tmp_path, monkeypatch, english_lags, stock_lags, last_caption_showing=False):
    rng = numpy.random.default_rng(403)
    rate = voice_checks.SCAN_RATE
    takes = {vid: rng.standard_normal(rate) for vid in (0xC20, 0xC21)}
    stock = {vid: rng.standard_normal(rate) for vid in takes}
    captions = [1.0, 4.0]
    heard, english = _pass(tmp_path, "english", [(takes[v], c + english_lags[v]) for v, c in zip(takes, captions, strict=True)],
                           captions, last_caption_showing)
    # The stock pass speaks the lines in the other order, so a pairing by caption order is caught.
    heard["stock"], japanese = _pass(tmp_path, "stock", [(stock[v], c + stock_lags[v])
                                                         for v, c in zip(reversed(stock), captions, strict=True)], captions)
    heard.update(bank=str(tmp_path / "english.wav"), game=str(tmp_path))
    sounds = {b"english": english, b"stock": japanese} | {b"take %d" % v: t for v, t in takes.items()} | {
        b"stock %d" % v: t for v, t in stock.items()}
    monkeypatch.setattr(voice_checks, "decode", lambda data, suffix: sounds[data.removeprefix(
        b"RIFF\0\0\0\0WAVEdata\0\0\0\0")])
    monkeypatch.setattr(voice_checks, "bank_takes", lambda bank, ids: {v: b"take %d" % v for v in takes})
    monkeypatch.setattr(voice_checks, "stock_takes", lambda game, ids: {v: b"stock %d" % v for v in stock})
    english_asr = lambda wav, clips: [{"language": "en", "language_probability": 0.9, "match": 1.0}] * len(clips)  # noqa: E731
    return voice_checks.lines_in_english(heard, "", listen=english_asr)


def test_a_take_is_judged_by_where_the_game_starts_the_same_lines_stock_clip(tmp_path, monkeypatch):
    """Bugs: the take's lag behind its caption is held to an absolute bound, so the harness's own audio delay (every
    stock clip lands 0.08-0.22 s after its caption) fails every line; a line is compared with another line's stock
    clip (paired by caption order, not voice id); or a take late against its stock clip passes."""
    in_step = _english_and_stock(tmp_path, monkeypatch, {0xC20: 0.10, 0xC21: 0.25}, {0xC20: 0.12, 0xC21: 0.22})
    assert in_step.ok, in_step.numbers
    late = _english_and_stock(tmp_path, monkeypatch, {0xC20: 0.10, 0xC21: 0.35}, {0xC20: 0.12, 0xC21: 0.22})
    assert [line["ok"] for line in late.numbers["per_line"]] == [True, False], late.numbers


def test_a_recording_that_ends_during_a_caption_fails_instead_of_missing_its_line(tmp_path, monkeypatch):
    """Bug: a listen shorter than the scene drops its last lines (their takes run past the recording's end, so
    none is found) and the row reads as a take that did not play, as in run 2026-11-16_0007-dc-dream."""
    cut = _english_and_stock(tmp_path, monkeypatch, {0xC20: 0.15, 0xC21: 0.15}, {0xC20: 0.15, 0xC21: 0.15},
                             last_caption_showing=True)
    assert not cut.ok and "error" in cut.numbers, cut.numbers
