"""The Z Dream scenes' English takes, cut from the WO2 PSP movies of events 402-413.

The fast tests build a scene from synthetic noise: a music bed in both tracks, English bursts in one, the stock clips
(other bursts) at the PSP's mix gain in the other. The slow one (WOZMOD_AUDIO=1 with WOZMOD_GAME and WOZMOD_DONORS)
builds event 408 from the stock game and WO2 PSP's GEV_201.pmf.
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

from synthetic import timed_event  # noqa: E402
from wozmod.formats.audio import kovs_ogg, vorbis_records  # noqa: E402
from wozmod.voices.dream_events import (  # noqa: E402
    HOP, RATE, TAIL, Line, SceneVoices, failed_splices, finished_takes, place, scene_lines, scene_voices,
)
from wozmod.voices.dream_movies import LIMIT  # noqa: E402

MIX = 0.75  # the PSP's gain on the stock clips, and on the English
CLIP_DELAY = int(0.19 * RATE)  # where the PSP puts a stock clip after its subtitle


def _f32(values) -> array:
    return array("f", np.asarray(values, dtype="<f4").tobytes())


def _burst(rng, seconds: float, peak: float) -> np.ndarray:
    noise = rng.standard_normal(int(seconds * RATE)) * np.hanning(int(seconds * RATE))
    return noise * peak / np.abs(noise).max()


def _scene(length: float, seed: int):
    rng = np.random.default_rng(seed)
    bed = 0.03 * rng.standard_normal(int(length * RATE))  # the music bed, about 25 dB under the voices
    return rng, bed.copy(), bed.copy()


def _add(track: np.ndarray, at: int, sound: np.ndarray) -> None:
    track[at:at + len(sound)] += sound


def _laid_out(sound: np.ndarray, offset: int, length: int) -> np.ndarray:
    out = np.zeros(length)
    out[offset:offset + len(sound)] = sound[:max(length - offset, 0)]
    return out


def test_each_line_gets_its_english_at_the_stock_clips_level_from_where_its_clip_starts():
    # Bug: a take cut from its subtitle keeps the PSP's lag of each clip behind its subtitle, so in game the English
    # trails the subtitle and the mouths by 0.19 s; the placed clip is added with the wrong sign or at the wrong lag
    # (the Japanese stays, or doubles), the
    # PSP's gain is not divided out (the English plays 2.5 dB low), a take is cut from the line before or after, or its
    # trailing silence is kept or its last sound is cut.
    rng, english, japanese = _scene(4.0, 408)
    lines = [Line(241, 0xAF3 + 241, RATE // 2, int(1.6 * RATE)), Line(242, 0xAF3 + 242, int(2.2 * RATE), int(1.8 * RATE))]
    clips = {line.voice: _burst(rng, 0.6, 0.5) for line in lines}
    voices = {lines[0].voice: _burst(rng, 0.8, 0.6), lines[1].voice: _burst(rng, 0.5, 0.9)}
    lead = RATE // 10  # the English starts 0.1 s after the line's Japanese clip
    for line in lines:
        _add(english, line.start + CLIP_DELAY + lead, MIX * voices[line.voice])
        _add(japanese, line.start + CLIP_DELAY, MIX * clips[line.voice])

    built = scene_voices(_f32(english), _f32(japanese), lines, {v: _f32(c) for v, c in clips.items()})

    # The English over each clip moves the fitted gain by chance (0.738 here); a gain left in would be 1/0.75 off.
    assert abs(built.gain - MIX) < 0.02 and not built.unmatched
    for line in lines:
        take = np.asarray(built.takes[line.voice])
        expected = _laid_out(voices[line.voice], lead, line.room)
        assert np.abs(take - expected[:len(take)]).max() < 0.02, line.index
        assert np.abs(expected[len(take):]).max(initial=0) < 0.02, line.index  # nothing audible left out
        assert len(take) <= lead + len(voices[line.voice]) + TAIL + HOP, line.index


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
    from wozmod.voices.dream_events import LEAD

    rng, english, japanese = _scene(3.0, 408)
    line = Line(241, 0xAF3 + 241, RATE // 2, 2 * RATE)
    clip = _burst(rng, 0.6, 0.5)
    said = rng.standard_normal(int(0.9 * RATE))
    said *= 0.5 / np.abs(said).max()  # no fade-in: its first sound is its first sample
    early = RATE // 10  # the English starts 0.1 s after the subtitle, 0.09 s before the clip
    _add(english, line.start + early, MIX * said)
    _add(japanese, line.start + CLIP_DELAY, MIX * clip)

    built = scene_voices(_f32(english), _f32(japanese), [line], {line.voice: _f32(clip)})

    take = np.asarray(built.takes[line.voice])
    assert np.abs(take - _laid_out(said, LEAD, len(take))).max() < 0.02  # from LEAD before its first sound


def test_english_cut_into_the_next_lines_take_is_not_in_this_ones():
    # Bug: a take runs for its whole room from its clip, 0.19 s past the next subtitle, so when the next line's
    # English starts before its own clip (its take starts there) the first 0.16 s of it ends this take too, and the
    # game plays that word twice: at the end of this line, then with the next subtitle.
    rng, english, japanese = _scene(4.0, 403)
    lines = [Line(305, 0xAF3 + 305, RATE // 2, int(1.5 * RATE)), Line(306, 0xAF3 + 306, 2 * RATE, int(1.5 * RATE))]
    clips = {line.voice: _burst(rng, 0.6, 0.5) for line in lines}
    first, second = _burst(rng, 1.0, 0.6), rng.standard_normal(int(0.9 * RATE))
    second *= 0.6 / np.abs(second).max()  # no fade-in: its first sound is its first sample
    early = int(0.03 * RATE)  # the second line's English starts 0.03 s after its subtitle, 0.16 s before its clip
    _add(english, lines[0].start + CLIP_DELAY, MIX * first)
    _add(english, lines[1].start + early, MIX * second)
    for line in lines:
        _add(japanese, line.start + CLIP_DELAY, MIX * clips[line.voice])

    built = scene_voices(_f32(english), _f32(japanese), lines, {v: _f32(c) for v, c in clips.items()})

    take = np.asarray(built.takes[lines[0].voice])  # its first sample is the movie's at the first clip
    assert np.abs(take - _laid_out(first, 0, len(take))).max() < 0.02
    assert np.abs(_laid_out(first, 0, lines[0].room)[len(take):]).max(initial=0) < 0.02  # its own English all kept
    second_take = np.asarray(built.takes[lines[1].voice])  # from its subtitle: its English starts under LEAD after
    assert np.abs(second_take - _laid_out(second, early, len(second_take))).max() < 0.02  # heard once, in its own


def test_what_the_encodes_leave_before_a_clip_does_not_start_its_take_early():
    # Bug: the residue the two PSP encodes leave of the bed (event 408: 27-30 dB under the line) counts as English
    # heard before the clip, so every take starts at its subtitle and speaks 0.19 s late in game.
    rng, english, japanese = _scene(3.0, 408)
    line = Line(241, 0xAF3 + 241, RATE // 2, 2 * RATE)
    clip = _burst(rng, 0.6, 0.5)
    said = rng.standard_normal(int(0.9 * RATE))
    said *= 0.5 / np.abs(said).max()
    english += 0.5 * 10 ** (-28 / 20) * rng.standard_normal(len(english)) / 4  # residue peaking about 28 dB under
    _add(english, line.start + CLIP_DELAY, MIX * said)  # the English starts with its clip, as most lines do
    _add(japanese, line.start + CLIP_DELAY, MIX * clip)

    built = scene_voices(_f32(english), _f32(japanese), [line], {line.voice: _f32(clip)})

    take = np.asarray(built.takes[line.voice])
    assert np.abs(take[:RATE // 2] - said[:RATE // 2]).max() < 0.1  # the take starts with the English


def test_a_clip_the_psp_shortened_a_pause_in_still_cancels_and_one_it_changed_otherwise_stays_japanese():
    # Bug: an edited clip is placed whole (its second half off by the cut, so Japanese is left in the take), the
    # pieces after a pause are searched only at the whole clip's place, or a line whose clip matches only in part
    # ships with its Japanese.
    rng, english, japanese = _scene(5.0, 410)
    lines = [Line(261, 0xAF3 + 261, RATE // 2, 2 * RATE), Line(264, 0xAF3 + 264, int(2.5 * RATE), int(2.5 * RATE))]
    pause = np.zeros(int(0.3 * RATE))
    parts = {line.voice: (_burst(rng, 0.5, 0.5), _burst(rng, 0.5, 0.5)) for line in lines}
    clips = {v: np.concatenate([a, pause, b]) for v, (a, b) in parts.items()}
    shortened = np.concatenate([parts[lines[0].voice][0], pause[:int(0.2 * RATE)], parts[lines[0].voice][1]])
    changed = np.concatenate([parts[lines[1].voice][0], pause, _burst(rng, 0.5, 0.5)])  # its second half another
    voice = _burst(rng, 1.2, 0.6)
    _add(english, lines[0].start + CLIP_DELAY + RATE // 10, MIX * voice)
    _add(japanese, lines[0].start + CLIP_DELAY, MIX * shortened)
    _add(japanese, lines[1].start + CLIP_DELAY, MIX * changed)

    stock = {v: _f32(c) for v, c in clips.items()}
    built = scene_voices(_f32(english), _f32(japanese), lines, stock)
    failed = failed_splices({410: built})
    built = scene_voices(_f32(english), _f32(japanese), lines, stock, failed[410])

    take = np.asarray(built.takes[lines[0].voice])
    assert np.abs(take - _laid_out(voice, RATE // 10, len(take))).max() < 0.02
    assert built.split == [lines[0]] and built.unmatched == [lines[1]] and lines[1].voice not in built.takes


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


def test_a_clip_the_psp_sped_up_by_splicing_still_cancels_and_its_line_keeps_the_english():
    # Bug: a sped-up clip is placed whole, or by pauses, so its later runs sit off by the drops and the take keeps
    # the Japanese (as 403/306, 410/255 and 411/273 did); the drops are taken the wrong way (blocks placed later), or
    # overlapping blocks are summed without their weights, so the cancel doubles the Japanese at a splice.
    rng, english, japanese = _scene(5.0, 403)
    lines = [Line(305, 0xAF3 + 305, RATE // 2, 2 * RATE), Line(306, 0xAF3 + 306, int(2.5 * RATE), int(2.5 * RATE))]
    clips = {lines[0].voice: _burst(rng, 1.0, 0.5), lines[1].voice: _voiced(rng, 2.0)}
    copy = _sped_up(clips[lines[1].voice], rng)
    assert len(clips[lines[1].voice]) - len(copy) > 0.05 * RATE  # the drops add up to more than a pause shortens
    voice = _burst(rng, 1.6, 0.6)
    _add(japanese, lines[0].start + CLIP_DELAY, MIX * clips[lines[0].voice])
    _add(japanese, lines[1].start + CLIP_DELAY, MIX * copy)
    _add(english, lines[1].start + CLIP_DELAY + RATE // 10, MIX * voice)
    stock = {v: _f32(c) for v, c in clips.items()}

    built = scene_voices(_f32(english), _f32(japanese), lines, stock)

    assert built.spliced == [lines[1]]
    take = np.asarray(built.takes[lines[1].voice])
    error = take - _laid_out(voice, RATE // 10, len(take))
    # Placed whole or by pauses, the edited part leaves as much as it held (403/306: +0.5 to +3.2 dB); each splice
    # still leaves the 2 ms the crossfade changed and the breath noise across it (about -11 dB in all here).
    assert 10 * np.log10(np.sum(error ** 2) / np.sum(copy ** 2)) < -8


def test_a_spliced_line_keeps_its_english_only_if_it_leaves_no_more_japanese_than_the_worst_other_line():
    # Bug: the bar is each scene's own worst line (so a spliced line in a scene of clean lines is refused), a spliced
    # line counts toward the bar (so it can pass by its own leftover), or a line at the bar is refused.
    def scene(leftover: dict[int, float], spliced: list[int]):
        return SceneVoices({}, MIX, spliced=[Line(i, 0xAF3 + i, 0, 1) for i in spliced], leftover=leftover)

    scenes = {403: scene({300: -12.0, 301: -5.0, 306: -5.0, 307: -3.0}, [306, 307]),
              410: scene({250: -14.0, 255: -6.0}, [255])}
    assert failed_splices(scenes) == {403: frozenset({307})}


def test_a_line_spoken_twice_gets_the_take_that_fits_both_places():
    # Bug: the first place's take is kept, which runs past the next line where the line is spoken again.
    rng, english, japanese = _scene(4.0, 404)
    voice, clip = 0xAF3 + 315, _burst(rng, 0.4, 0.5)
    lines = [Line(315, voice, RATE // 4, int(2.5 * RATE)), Line(315, voice, int(3.0 * RATE), int(0.8 * RATE))]
    long, short = _burst(rng, 1.5, 0.6), _burst(rng, 0.5, 0.6)
    for line, said in zip(lines, (long, short), strict=True):
        _add(english, line.start + CLIP_DELAY + RATE // 20, MIX * said)
        _add(japanese, line.start + CLIP_DELAY, MIX * clip)

    take = np.asarray(scene_voices(_f32(english), _f32(japanese), lines, {voice: _f32(clip)}).takes[voice])

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
    os.environ.get("WOZMOD_AUDIO") != "1" or not os.environ.get("WOZMOD_GAME") or not os.environ.get("WOZMOD_DONORS")
    or not shutil.which("ffmpeg"),
    reason="set WOZMOD_AUDIO=1, WOZMOD_GAME and WOZMOD_DONORS, with ffmpeg")


def _stock_clips(game: Path, voices: list[int]) -> dict[int, array]:
    from wozmod import game_files
    from wozmod.formats.audio import _ffmpeg_each, kovs_ogg

    takes = game_files.voices(game).clips(voices)
    oggs = [kovs_ogg(takes[v]) for v in voices]
    pcms = _ffmpeg_each([(["-f", "ogg"], ogg, ["-ac", "1", "-ar", str(RATE), "-f", "f32le"]) for ogg in oggs], len(oggs))
    return {v: array("f", pcm) for v, pcm in zip(voices, pcms, strict=True)}


@SLOW
def test_event_408s_takes_hold_the_psp_english_without_the_japanese():
    # Bug: the PSP track keeps ffmpeg's start padding or the scene runs on a 60-a-second clock (the stock clips land
    # 55 ms later after their subtitles, or drift), the substreams are swapped (the takes hold Japanese and no
    # English), a clip is placed at the wrong lag or with the wrong sign (the Japanese stays), or the voice ids are
    # the other voice mode's (no clip matches).
    from wozmod.formats.linkdata import archive
    from wozmod.formats.pmf import audio_tracks
    from wozmod import native
    from wozmod.voices.dream_events import DREAM_EVENTS, ENGLISH, JAPANESE, _track

    game, psp = Path(os.environ["WOZMOD_GAME"]), Path(os.environ["WOZMOD_DONORS"]) / "wo2-psp"
    lines = scene_lines(408, archive(game / "LINKDATA2").read([408])[408])
    clips = _stock_clips(game, [line.voice for line in lines])
    tracks = audio_tracks((psp / DREAM_EVENTS[408]).read_bytes())
    english, japanese = _track(tracks[ENGLISH]), _track(tracks[JAPANESE])

    built = scene_voices(english, japanese, lines, clips)

    assert sorted(built.takes) == sorted(line.voice for line in lines) and 0.7 < built.gain < 0.8
    search = array("f", (np.asarray(japanese) - np.asarray(english)).astype("<f4").tobytes())
    delays = []
    for line in lines:
        (at, clip), = place(search, line, clips[line.voice]).pieces
        delays.append((at - line.start) / RATE)
        take = np.asarray(built.takes[line.voice], dtype=float)
        # Where the take sits in the movie: between its subtitle and its clip (the clip, unless English comes first).
        a, b = line.start, min(at, line.start + RATE) + len(take)
        region = (np.asarray(english, dtype=float)[a:b] - np.asarray(japanese, dtype=float)[a:b]).astype("<f4")
        start = line.start + native.module.xcorr_lag(take.astype("<f4").tobytes(), region.tobytes())[0]
        assert line.start <= start <= at, (line.index, start, at)
        stock = _laid_out(np.asarray(clip, dtype=float), at - start, len(take))
        mixed = np.asarray(english, dtype=float)[start:start + len(take)] \
            - np.asarray(japanese, dtype=float)[start:start + len(take)]
        the_english = np.asarray(english, dtype=float)[start:start + len(take)]

        def similarity(a, b):
            return np.dot(a, b) / np.sqrt(np.dot(a, a) * np.dot(b, b))
        # Measured: the takes against their stock clip -0.02 to 0.01, EN - JP -0.83 to -0.60; the takes against the
        # English track 0.73 to 0.85.
        assert abs(similarity(take, stock)) < 0.1 and similarity(mixed, stock) < -0.5, line.index
        assert similarity(take, the_english) > 0.6, line.index
    # Measured 0.13-0.20 s, mean 0.18, in this scene (0.186 over all 12 scenes).
    assert 0.15 < float(np.median(delays)) < 0.22, delays



@SLOW
def test_the_three_lines_the_psp_sped_up_get_english_without_their_japanese():
    # Bug: the sped-up clips of 403/306, 410/255 and 411/273 are placed whole (the Japanese of their second halves
    # stays), the splice path drifts off the PSP's runs, or the leftover rule refuses them (they stay Japanese).
    # Measured: leftover -8.3, -6.6 and -8.6 dB against the worst other line's -4.2 (median -8.1).
    from wozmod import game_files
    from wozmod.voices.dream_events import DREAM_EVENTS, build_dream_events

    game, psp = Path(os.environ["WOZMOD_GAME"]), Path(os.environ["WOZMOD_DONORS"]) / "wo2-psp"
    _, scenes = build_dream_events(game_files.voices(game), psp,
                                   game_files.archive(game / "LINKDATA2").read(list(DREAM_EVENTS)))

    sped_up = {403: 306, 410: 255, 411: 273}
    assert {o: [line.index for line in scene.spliced] for o, scene in scenes.items() if scene.spliced} \
        == {o: [i] for o, i in sped_up.items()}
    assert not any(scene.unmatched for scene in scenes.values())
    worst = max(db for scene in scenes.values() for i, db in scene.leftover.items() if i not in sped_up.values())
    for o, i in sped_up.items():
        line = next(line for line in scenes[o].spliced if line.index == i)
        assert line.voice in scenes[o].takes and scenes[o].leftover[i] <= worst, (o, i, scenes[o].leftover[i])


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


def _old_energies(japanese, gain, pieces, end, hop, factor):
    start = min(at for at, _ in pieces)
    placed = array("f", bytes(4 * max(end - start, 0)))
    for at, piece in pieces:
        for i, v in enumerate(piece[:max(end - at, 0)], at - start):
            placed[i] += v
    floor = max(map(abs, placed), default=0.0) * factor
    held = left = 0.0
    for i in range(0, len(placed) - hop + 1, hop):
        frame = placed[i:i + hop]
        if max(map(abs, frame)) > floor:
            track = _old_stretch(japanese, start + i, hop)
            held += sum(v * v for v in track)
            left += sum((v - gain * c) ** 2 for v, c in zip(track, frame, strict=True))
    return held, left


def _old_voice(english, japanese, pieces, gain):
    import operator
    voice = array("f", map(operator.sub, english, japanese, strict=True))
    for at, piece in pieces:
        span = slice(max(at, 0), min(at + len(piece), len(voice)))
        voice[span] = array("f", map(operator.add, voice[span], map(gain.__mul__, piece[span.start - at:span.stop - at]),
                                     strict=True))
    return voice


def test_the_native_scene_loops_give_exactly_what_the_python_loops_gave():
    # Bug: a native loop rounds where Python did not (a sum in 32 bits, x * x for x ** 2, plain summation for
    # sum()'s compensated one), adds pieces or blocks in another order, or misplaces a piece that starts before the
    # track or runs past it: the takes of the Dream scenes change, or come out different on another thread count.
    import math
    rng = np.random.default_rng(7)
    f32 = lambda n, scale=0.5: array("f", (rng.standard_normal(n) * scale).astype("<f4").tobytes())  # noqa: E731
    english, japanese, hop = f32(5000), f32(5000), 48
    pieces = [(-30, f32(400)), (900, f32(700, 2.0)), (1200, f32(300)), (4800, f32(500))]  # before, overlapping, past
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / (2 * hop)) for i in range(2 * hop)]
    places = [int(p) for p in rng.integers(0, 600, 40)]
    gain, factor = 0.8123456789, 10 ** (-30 / 20)
    m = native
    got = {
        "frame peaks": (m.frame_peaks(english.tobytes(), hop, hop), _old_peaks(english, hop, hop)),
        "padded block peaks": (m.frame_peaks(english.tobytes(), 2 * hop, hop, 110), _old_peaks(english, 2 * hop, hop, 110)),
        "difference": (m.difference(japanese.tobytes(), english.tobytes()),
                       array("f", (j - e for j, e in zip(japanese, english, strict=True))).tobytes()),
        "pieces added": (m.add_pieces(m.difference(english.tobytes(), japanese.tobytes()),
                                      [(at, p.tobytes()) for at, p in pieces], gain),
                         _old_voice(english, japanese, pieces, gain).tobytes()),
        "scaled": (m.scaled(english.tobytes(), 1 / gain), array("f", map((1 / gain).__mul__, english)).tobytes()),
        "overlap-add": (m.overlap_add(english.tobytes(), places, window, hop),
                        _old_overlap_add(english, places, window, hop).tobytes()),
        "leftover energies": (m.leftover_energies(japanese.tobytes(), [(at, p.tobytes()) for at, p in pieces], 4900,
                                                  gain, hop, factor),
                              _old_energies(japanese, gain, pieces, 4900, hop, factor)),
    }
    assert [name for name, (new, old) in got.items() if new != old] == []
