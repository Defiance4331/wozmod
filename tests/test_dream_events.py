"""The Z Dream scenes' English takes, cut from the WO2 PSP movies of events 402-413.

The fast tests build a scene from synthetic noise: a music bed in both tracks, English bursts in one, the stock clips
(other bursts) at the PSP's mix gain in the other; the separated voice the takes are cut from is the English bursts
alone, as a separator that leaves nothing else would give them (the separator itself: tests/test_separation.py and
tests/test_separation_fidelity.py). The slow ones (WOZMOD_AUDIO=1 with WOZMOD_GAME, WOZMOD_DONORS and
WOZMOD_SEPARATOR) build event 408 from the stock game and WO2 PSP's GEV_201.pmf, and place the three sped-up lines.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from array import array
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
native = pytest.importorskip("wozmod_native")

from dream_inputs import scene_tracks  # noqa: E402
from synthetic import timed_event  # noqa: E402
from wozmod.formats.audio import kovs_ogg, vorbis_records  # noqa: E402
from wozmod.voices.dream_events import (  # noqa: E402
    HOP, LEAD, RATE, TAIL, Line, finished_takes, placed, scene_lines, scene_voices,
)
from wozmod.voices.dream_movies import LIMIT  # noqa: E402

MIX = 0.75  # the PSP's gain on the stock clips, and on the English
CLIP_DELAY = int(0.19 * RATE)  # where the PSP puts a stock clip after its subtitle


def _f32(values) -> array:
    return array("f", np.asarray(values, dtype="<f4").tobytes())


def _burst(rng, seconds: float, peak: float) -> np.ndarray:
    noise = rng.standard_normal(int(seconds * RATE)) * np.hanning(int(seconds * RATE))
    return noise * peak / np.abs(noise).max()


class _Scene:
    """A synthetic PSP movie: the music bed in both tracks, what is said in English in one and the stock clips in
    the other, both at the PSP's gain."""

    def __init__(self, length: float, seed: int):
        self.rng = np.random.default_rng(seed)
        bed = 0.03 * self.rng.standard_normal(int(length * RATE))  # about 25 dB under the voices
        self.english, self.japanese, self.voice = bed.copy(), bed.copy(), np.zeros(len(bed))

    def say(self, at: int, sound: np.ndarray) -> None:
        self.english[at:at + len(sound)] += MIX * sound
        self.voice[at:at + len(sound)] += MIX * sound

    def clip(self, at: int, sound: np.ndarray) -> None:
        self.japanese[at:at + len(sound)] += MIX * sound

    def built(self, lines: list[Line], clips: dict[int, np.ndarray]):
        """(where the lines sit, the takes cut from the English voice alone)."""
        found = placed(_f32(self.english), _f32(self.japanese), lines, {v: _f32(c) for v, c in clips.items()})
        return found, scene_voices(_f32(self.voice), found)


def _laid_out(sound: np.ndarray, offset: int, length: int) -> np.ndarray:
    out = np.zeros(length)
    out[offset:offset + len(sound)] = sound[:max(length - offset, 0)]
    return out


def test_each_line_gets_its_english_at_the_stock_clips_level_from_where_its_clip_starts():
    # Bug: a take cut from its subtitle keeps the PSP's lag of each clip behind its subtitle, so in game the English
    # trails the subtitle and the mouths by 0.19 s; the PSP's gain is not divided out (the English plays 2.5 dB
    # low); a take is cut from the line before or after; its trailing silence is kept or its last sound is cut; or
    # the stretch handed to the separator leaves out part of a take, which then plays silence there.
    scene = _Scene(4.0, 408)
    lines = [Line(241, 0xAF3 + 241, RATE // 2, int(1.6 * RATE)), Line(242, 0xAF3 + 242, int(2.2 * RATE), int(1.8 * RATE))]
    clips = {line.voice: _burst(scene.rng, 0.6, 0.5) for line in lines}
    voices = {lines[0].voice: _burst(scene.rng, 0.8, 0.6), lines[1].voice: _burst(scene.rng, 0.5, 0.9)}
    lead = RATE // 10  # the English starts 0.1 s after the line's Japanese clip
    for line in lines:
        scene.say(line.start + CLIP_DELAY + lead, voices[line.voice])
        scene.clip(line.start + CLIP_DELAY, clips[line.voice])

    found, built = scene.built(lines, clips)

    # The English over each clip moves the fitted gain by chance (0.738 here); a gain left in would be 1/0.75 off.
    assert abs(built.gain - MIX) < 0.02 and [each.at for each in found.placements] == [line.start + CLIP_DELAY for line in lines]
    for line, (first, last) in zip(lines, found.wanted, strict=True):
        take = np.asarray(built.takes[line.voice])
        expected = _laid_out(voices[line.voice], lead, line.room)
        assert np.abs(take - expected[:len(take)]).max() < 0.02, line.index
        assert np.abs(expected[len(take):]).max(initial=0) < 0.02, line.index  # nothing audible left out
        assert len(take) <= lead + len(voices[line.voice]) + TAIL + HOP, line.index
        assert first <= line.start + CLIP_DELAY and line.start + CLIP_DELAY + len(take) <= last, line.index


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_finished_take_peaks_at_the_largest_16_bit_sample_and_a_take_under_full_scale_is_untouched():
    # Bug: a fixed margin comes back (a take over full scale plays up to 1 dB quieter than it needs, as the -1 dBFS
    # trim did), the gain comes from the take before the Vorbis encode (whose overshoot then cuts samples in the
    # game's 16-bit decode), or a take under full scale is turned up or down.
    rng = np.random.default_rng(408)
    takes = {0xB00: _burst(rng, 1.0, 1.4), 0xB01: _burst(rng, 1.0, 1.15), 0xB02: _burst(rng, 1.0, 0.7)}

    records, gains = finished_takes({v: _f32(take) for v, take in takes.items()})

    def decoded(record: bytes) -> np.ndarray:
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-f", "ogg", "-i", "-", "-f", "f32le", "-"],
                             input=kovs_ogg(record), capture_output=True, check=True).stdout
        return np.frombuffer(pcm, "<f4")

    for v in (0xB00, 0xB01):
        # Within the encoder's scatter on loud noise (about 1 %), well above the old fixed -1 dBFS.
        found = np.abs(decoded(records[v])).max()
        assert LIMIT * 10 ** (-0.3 / 20) < found <= LIMIT and gains[v] < 1, (v, found, gains[v])
    # Untouched: the record vorbis_records makes of the take itself.
    plain = vorbis_records({0: (["-f", "f32le", "-ar", str(RATE), "-ch_layout", "mono"], _f32(takes[0xB02]).tobytes(),
                                "anull")})[0]
    assert gains[0xB02] == 1 and records[0xB02] == plain


def test_english_heard_before_its_clip_keeps_its_start():
    # Bug: a take always cut at its stock clip loses the start of a dub line that begins ahead of the Japanese take
    # (heard in event 408: two lines start 0.0-0.06 s after their subtitles, the clip 0.19 s after).
    scene = _Scene(3.0, 408)
    line = Line(241, 0xAF3 + 241, RATE // 2, 2 * RATE)
    clip = _burst(scene.rng, 0.6, 0.5)
    said = scene.rng.standard_normal(int(0.9 * RATE))
    said *= 0.5 / np.abs(said).max()  # no fade-in: its first sound is its first sample
    early = RATE // 10  # the English starts 0.1 s after the subtitle, 0.09 s before the clip
    scene.say(line.start + early, said)
    scene.clip(line.start + CLIP_DELAY, clip)

    _, built = scene.built([line], {line.voice: clip})

    take = np.asarray(built.takes[line.voice])
    assert np.abs(take - _laid_out(said, LEAD, len(take))).max() < 0.02  # from LEAD before its first sound


def test_english_cut_into_the_next_lines_take_is_not_in_this_ones():
    # Bug: a take runs for its whole room from its clip, 0.19 s past the next subtitle, so when the next line's
    # English starts before its own clip (its take starts there) the first 0.16 s of it ends this take too, and the
    # game plays that word twice: at the end of this line, then with the next subtitle.
    scene = _Scene(4.0, 403)
    lines = [Line(305, 0xAF3 + 305, RATE // 2, int(1.5 * RATE)), Line(306, 0xAF3 + 306, 2 * RATE, int(1.5 * RATE))]
    clips = {line.voice: _burst(scene.rng, 0.6, 0.5) for line in lines}
    first, second = _burst(scene.rng, 1.0, 0.6), scene.rng.standard_normal(int(0.9 * RATE))
    second *= 0.6 / np.abs(second).max()  # no fade-in: its first sound is its first sample
    early = int(0.03 * RATE)  # the second line's English starts 0.03 s after its subtitle, 0.16 s before its clip
    scene.say(lines[0].start + CLIP_DELAY, first)
    scene.say(lines[1].start + early, second)
    for line in lines:
        scene.clip(line.start + CLIP_DELAY, clips[line.voice])

    _, built = scene.built(lines, clips)

    take = np.asarray(built.takes[lines[0].voice])  # its first sample is the movie's at the first clip
    assert np.abs(take - _laid_out(first, 0, len(take))).max() < 0.02
    assert np.abs(_laid_out(first, 0, lines[0].room)[len(take):]).max(initial=0) < 0.02  # its own English all kept
    second_take = np.asarray(built.takes[lines[1].voice])  # from its subtitle: its English starts under LEAD after
    assert np.abs(second_take - _laid_out(second, early, len(second_take))).max() < 0.02  # heard once, in its own


def test_what_the_separator_leaves_of_the_music_before_a_clip_does_not_start_its_take_early():
    # Bug: a trace of the music the separator lets through (25 dB and more under the line) counts as English heard
    # before the clip, so every take starts at its subtitle and speaks 0.19 s late in game.
    scene = _Scene(3.0, 408)
    line = Line(241, 0xAF3 + 241, RATE // 2, 2 * RATE)
    clip = _burst(scene.rng, 0.6, 0.5)
    said = scene.rng.standard_normal(int(0.9 * RATE))
    said *= 0.5 / np.abs(said).max()
    scene.voice += 0.5 * 10 ** (-28 / 20) * scene.rng.standard_normal(len(scene.voice)) / 4  # peaking about 28 dB under
    scene.say(line.start + CLIP_DELAY, said)  # the English starts with its clip, as most lines do
    scene.clip(line.start + CLIP_DELAY, clip)

    _, built = scene.built([line], {line.voice: clip})

    take = np.asarray(built.takes[line.voice])
    assert np.abs(take[:RATE // 2] - said[:RATE // 2]).max() < 0.1  # the take starts with the English


def test_a_clip_the_psp_shortened_a_pause_in_is_still_found_where_it_starts():
    # Bug: an edited clip is looked for whole only (it matches nowhere, and its line is timed from a wrong place or
    # refused), or the pieces after a pause are searched only at the whole clip's place.
    scene = _Scene(3.0, 410)
    line = Line(261, 0xAF3 + 261, RATE // 2, 2 * RATE)
    pause = np.zeros(int(0.3 * RATE))
    first, second = _burst(scene.rng, 0.5, 0.5), _burst(scene.rng, 0.5, 0.5)
    voice = _burst(scene.rng, 1.2, 0.6)
    scene.say(line.start + CLIP_DELAY + RATE // 10, voice)
    scene.clip(line.start + CLIP_DELAY, np.concatenate([first, pause[:int(0.2 * RATE)], second]))

    found, built = scene.built([line], {line.voice: np.concatenate([first, pause, second])})

    assert built.split == [line] and found.placements[0].at == line.start + CLIP_DELAY
    take = np.asarray(built.takes[line.voice])
    assert np.abs(take - _laid_out(voice, RATE // 10, len(take))).max() < 0.02


def _voiced(rng, seconds: float) -> np.ndarray:
    """A voice-like sound: harmonics of a pitch gliding from 140 to 260 Hz and breath noise, under a syllable
    envelope."""
    t = np.arange(int(seconds * RATE)) / RATE
    phase = 2 * np.pi * np.cumsum(140 + 120 * t / seconds) / RATE
    tone = sum(np.sin(k * phase + rng.uniform(0, 6)) / k for k in range(1, 12)) + 0.5 * rng.standard_normal(len(t))
    onset = np.minimum(1, np.minimum(t / 0.01, (seconds - t) / 0.05))  # a syllable starts within 10 ms
    return tone * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)) * onset * 0.2


def _sped_up(clip: np.ndarray, rng) -> np.ndarray:
    """The clip as the PSP speeds it up: runs of 30-70 ms copied as they are, with one pitch period (at the splice)
    dropped between runs and a 2 ms crossfade, about 6 % shorter in all."""
    out, at, fade = [], 0, np.linspace(0, 1, 96)
    while at < len(clip):
        run = clip[at:at + int(rng.uniform(0.03, 0.07) * RATE)]
        if out:
            out[-1][-96:] = out[-1][-96:] * (1 - fade) + run[:96] * fade
            run = run[96:]
        out.append(run.copy())
        at += len(run) + (96 if len(out) > 1 else 0) + RATE // int(140 + 120 * at / len(clip))
    return np.concatenate(out)


def test_a_clip_the_psp_sped_up_by_splicing_is_still_found_where_it_starts():
    # Bug: a sped-up clip is looked for whole, or by pauses, and found nowhere (as 403/306, 410/255 and 411/273
    # were), so its line is timed from wherever the search ended; or the drops are taken the wrong way (blocks
    # placed later), which moves its start.
    scene = _Scene(5.0, 403)
    lines = [Line(305, 0xAF3 + 305, RATE // 2, 2 * RATE), Line(306, 0xAF3 + 306, int(2.5 * RATE), int(2.5 * RATE))]
    clips = {lines[0].voice: _burst(scene.rng, 1.0, 0.5), lines[1].voice: _voiced(scene.rng, 2.0)}
    copy = _sped_up(clips[lines[1].voice], scene.rng)
    assert len(clips[lines[1].voice]) - len(copy) > 0.05 * RATE  # the drops add up to more than a pause shortens
    voice = _burst(scene.rng, 1.6, 0.6)
    scene.clip(lines[0].start + CLIP_DELAY, clips[lines[0].voice])
    scene.clip(lines[1].start + CLIP_DELAY, copy)
    scene.say(lines[1].start + CLIP_DELAY + RATE // 10, voice)

    found, built = scene.built(lines, clips)

    assert built.spliced == [lines[1]]
    assert abs(found.placements[1].at - (lines[1].start + CLIP_DELAY)) <= HOP  # within the 10 ms its blocks step by
    take = np.asarray(built.takes[lines[1].voice])
    offset = lines[1].start + CLIP_DELAY + RATE // 10 - found.placements[1].at
    assert np.abs(take - _laid_out(voice, offset, len(take))).max() < 0.02


def test_a_line_spoken_twice_gets_the_take_that_fits_both_places():
    # Bug: the first place's take is kept, which runs past the next line where the line is spoken again.
    scene = _Scene(4.0, 404)
    voice, clip = 0xAF3 + 315, _burst(scene.rng, 0.4, 0.5)
    lines = [Line(315, voice, RATE // 4, int(2.5 * RATE)), Line(315, voice, int(3.0 * RATE), int(0.8 * RATE))]
    long, short = _burst(scene.rng, 1.5, 0.6), _burst(scene.rng, 0.5, 0.6)
    for line, said in zip(lines, (long, short), strict=True):
        scene.say(line.start + CLIP_DELAY + RATE // 20, said)
        scene.clip(line.start + CLIP_DELAY, clip)

    take = np.asarray(scene.built(lines, {voice: clip})[1].takes[voice])

    assert len(take) <= lines[1].room
    assert np.abs(take - _laid_out(short, RATE // 20, len(take))).max() < 0.02


def test_a_line_starts_on_the_movies_clock_and_has_until_the_next_speak_on_the_games():
    # Bug: the start is counted at 60 ticks a second (the PSP movie runs the scene at 59.94: 1 ms late a second), the
    # room on the movie's clock (a take runs past the next line in the game), or an unvoiced speak (index past the
    # voice table) does not end the line before it.
    data = timed_event([(60.0, 100.0, 241, b"Fixture"), (180.0, 200.0, 0xE200, b"Fixture"),
                        (3000.0, 3100.0, 242, b"Fixture")], 3600.0)

    assert scene_lines(408, data) == [
        Line(241, 0xAF3 + 241, round(60 * RATE / 59.94), 120 * RATE // 60),
        Line(242, 0xAF3 + 242, round(3000 * RATE / 59.94), 600 * RATE // 60),
    ]


SLOW = pytest.mark.skipif(
    os.environ.get("WOZMOD_AUDIO") != "1" or not shutil.which("ffmpeg")
    or not all(os.environ.get(name) for name in ("WOZMOD_GAME", "WOZMOD_DONORS", "WOZMOD_SEPARATOR")),
    reason="set WOZMOD_AUDIO=1, WOZMOD_GAME, WOZMOD_DONORS and WOZMOD_SEPARATOR, with ffmpeg")


@SLOW
def test_event_408s_takes_hold_the_psp_english_without_the_japanese():
    # Bug: the PSP track keeps ffmpeg's start padding or the scene runs on a 60-a-second clock (the stock clips land
    # 55 ms later after their subtitles, or drift), the substreams are swapped (the takes hold Japanese and no
    # English), the separated voice comes back a different length or rate than the track the lines were placed in
    # (the takes are cut from the wrong moments), or the voice ids are the other voice mode's (no clip matches).
    from wozmod.voices import separation
    from wozmod.voices.dream_events import _voice_at_rate

    lines, clips, english, japanese, sound = scene_tracks(408)
    found = placed(english, japanese, lines, clips)
    spans = [(a * separation.RATE // RATE, -(-b * separation.RATE // RATE)) for a, b in found.wanted]
    separated = separation.separate({"408": sound}, {"408": spans}, Path(os.environ["WOZMOD_SEPARATOR"]),
                                    os.process_cpu_count())["408"]

    built = scene_voices(_voice_at_rate(separated), found)

    assert sorted(built.takes) == sorted(line.voice for line in lines) and 0.7 < built.gain < 0.8
    delays = []
    for line, each in zip(lines, found.placements, strict=True):
        (at, clip), = each.pieces
        delays.append((at - line.start) / RATE)
        take = np.asarray(built.takes[line.voice], dtype=float)
        # Where the take sits in the movie: between its subtitle and its clip (the clip, unless English comes first).
        a, b = line.start, min(at, line.start + RATE) + len(take)
        region = np.asarray(english, dtype="<f4")[a:b]
        start = line.start + native.xcorr_lag(take.astype("<f4").tobytes(), region.tobytes())[0]
        assert line.start <= start <= at, (line.index, start, at)
        stock = _laid_out(np.asarray(clip, dtype=float), at - start, len(take))
        the_english = np.asarray(english, dtype=float)[start:start + len(take)]
        the_japanese = np.asarray(japanese, dtype=float)[start:start + len(take)]

        def similarity(a, b):
            return np.dot(a, b) / np.sqrt(np.dot(a, a) * np.dot(b, b))
        # Measured: the takes against their stock clip -0.01 to 0.02, the Japanese track against it 0.74 to 0.95; the
        # takes against the English track 0.86 to 0.95.
        assert abs(similarity(take, stock)) < 0.1 and similarity(the_japanese, stock) > 0.5, line.index
        assert similarity(take, the_english) > 0.6, line.index
    # Measured 0.13-0.20 s, mean 0.18, in this scene (0.186 over all 12 scenes).
    assert 0.15 < float(np.median(delays)) < 0.22, delays


@SLOW
def test_the_three_lines_the_psp_sped_up_are_placed_along_its_splices():
    # Bug: the sped-up clips of 403/306, 410/255 and 411/273 are looked for whole or by pauses only, so each line is
    # timed from wherever the search ended, up to half a second off. Measured: their clips start 0.207, 0.205 and
    # 0.226 s after their subtitles (the other lines: 0.12 to 0.22 s).
    for o, index in {403: 306, 410: 255, 411: 273}.items():
        lines, clips, english, japanese, _ = scene_tracks(o)
        found = placed(english, japanese, lines, clips)
        assert [line.index for line, each in zip(lines, found.placements, strict=True) if each.how == "splices"] == [index], o
        line, each = next(pair for pair in zip(lines, found.placements, strict=True) if pair[0].index == index)
        assert 0.18 < (each.at - line.start) / RATE < 0.25, (o, index, each.at - line.start)


# The Python loops the native module replaced (as they were at ba41b41), kept as the oracle for the native ones.
def _old_peaks(samples, size, step, count=None):
    if count is None:
        return [max(map(abs, samples[i:i + size])) for i in range(0, len(samples) - size + 1, step)]
    return [max(map(abs, _old_stretch(samples, k * step, size))) for k in range(count)]


def _old_stretch(samples, start, length):
    before = max(-start, 0)
    found = samples[max(start, 0):max(start + length, 0)]
    return array("f", bytes(4 * before)) + found + array("f", bytes(4 * (length - before - len(found))))


def _old_overlap_add(padded, places, window, hop):
    block = len(window)
    blocks = [_old_stretch(padded, k * hop, block) for k in range(len(places))]
    first = min(places)
    total, weight = [0.0] * (max(places) + block - first), [0.0] * (max(places) + block - first)
    for at, b in zip(places, blocks, strict=True):
        for i, (w, v) in enumerate(zip(window, b, strict=True), at - first):
            total[i] += w * v
            weight[i] += w
    return array("f", (t / w if w else 0.0 for t, w in zip(total, weight, strict=True)))


def test_the_native_scene_loops_give_exactly_what_the_python_loops_gave():
    # Bug: a native loop rounds where Python did not (a sum in 32 bits), adds blocks in another order, or misplaces a
    # block that runs past the track: the Dream lines are placed or cut differently, or differently on another
    # thread count.
    import math
    rng = np.random.default_rng(7)
    f32 = lambda n, scale=0.5: array("f", (rng.standard_normal(n) * scale).astype("<f4").tobytes())  # noqa: E731
    english, japanese, hop = f32(5000), f32(5000), 48
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / (2 * hop)) for i in range(2 * hop)]
    places = [int(p) for p in rng.integers(0, 600, 40)]
    gain = 0.8123456789
    m = native
    got = {
        "frame peaks": (m.frame_peaks(english.tobytes(), hop, hop), _old_peaks(english, hop, hop)),
        "padded block peaks": (m.frame_peaks(english.tobytes(), 2 * hop, hop, 110), _old_peaks(english, 2 * hop, hop, 110)),
        "difference": (m.difference(japanese.tobytes(), english.tobytes()),
                       array("f", (j - e for j, e in zip(japanese, english, strict=True))).tobytes()),
        "scaled": (m.scaled(english.tobytes(), 1 / gain), array("f", map((1 / gain).__mul__, english)).tobytes()),
        "overlap-add": (m.overlap_add(english.tobytes(), places, window, hop),
                        _old_overlap_add(english, places, window, hop).tobytes()),
    }
    assert [name for name, (new, old) in got.items() if new != old] == []
