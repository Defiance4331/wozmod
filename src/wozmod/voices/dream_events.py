"""The Z Dream scenes (stock events 402-413) under the English voice: each spoken line's English, cut from the WO2
PSP movie of the same scene.

The WO2 PSP plays these scenes as movies, GEV_201 to GEV_212 (text.psp_alignment.PSP_EVENT_CAPTIONS pairs them), each
with two soundtracks over one music bed: English (PMF substream 0) and Japanese (1), each a full mix of voice and
music. The Japanese track holds the stock Japanese clips, mixed in at one gain per scene (measured 0.746-0.765). The
English voice is separated from the English track alone (separation.py: Mel-Band RoFormer). Subtracting the Japanese
track instead, with the stock clips added back, left Japanese that could be heard: the PSP's Japanese encode stops
following the stock clip above 3 kHz, so its sibilants stayed (donors ledger rows 12 and 13). Per scene:

1. Each line's stock clip (voice id event_voice) is found in JP - EN, where the music bed cancels, just after the
   line's subtitle, by correlation (the native xcorr_lag). Where it sits says when the line is spoken, and how loud
   the PSP mixed it is the scene's gain.
2. The English track is separated over the stretches the lines can use (wanted): from each line's subtitle to the
   last sound either voice makes before the next line.
3. Each line is cut from the separated voice, from where its stock clip starts in the movie, for as long as the scene
   gives it before the next line, ends after its last audible sound, and is lifted by the scene's gain back to the
   stock clips' level, which is the English clips' level too (EBU R128 per clip, median: the stock Japanese event
   clips -12.8 LUFS, the X360 English event clips the install plays -12.4). The game starts a line's clip with its
   subtitle, but the PSP put each stock clip 0.186 s after its subtitle: cut from the subtitle, the English would
   trail its subtitle and the line's mouths by that much (heard in game, run 2026-11-15 dream-C), while cut from
   the clip it keeps the timing the dub has against the Japanese take. English heard before its clip keeps its
   start (_take_start). Each take ends where the next line's take starts, so English that runs past the next
   subtitle is heard once, in the next line's take, which the game starts there.

A clip the PSP edited (a pause shortened, a stretch sped up, to end before the next line) does not match whole: it
is placed pause by pause. A clip it sped up does not match pause by pause either: the PSP sped it up by splicing,
copying the stock waveform in runs of 20-100 ms and dropping 70-360 samples between runs (event 403 line 306: runs
at correlation 0.95-0.99, 4874 samples dropped over one pause and 2750 more over the next 1.1 s). Such a clip is
placed block by block along the drops that match best (native splice_drops).
"""

from __future__ import annotations

import math
import os
import tempfile
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from wozmod import native
from wozmod.formats.audio import OnDemand, _ffmpeg_each, event_voice, kovs_ogg, vorbis_records
from wozmod.formats.events import EVENT_TICKS, event_scenes
from wozmod.formats.pmf import START_PADDING, audio_tracks
from wozmod.progress import report
from wozmod.workers import ordered_map
from wozmod.text.movie_captions import CUE_TICKS
from wozmod.text.psp_alignment import PSP_EVENT_CAPTIONS

from . import separation
from .dream_movies import ENGLISH, JAPANESE, RATE, decoded, under_full_scale
from .events import AUDIBLE_FLOOR_DB, audible_length

if TYPE_CHECKING:
    from wozmod.game_files import StockVoices

# Each Z Dream scene's PSP movie: GEV n loads the caption schedule FNS member n - 0x2B.
DREAM_EVENTS = {o: f"PSP_GAME/USRDIR/movie/event/GEV_{member + 0x2B}.pmf" for o, member in PSP_EVENT_CAPTIONS.items()}
# The PSP movie plays the scene's clock at 59.94 a second, one caption tick per two: every PSP cue starts at half its
# stock line's speak tick, rounded down, and the stock clips sit 0.186 s (sd 0.020) after their subtitles in all 12
# movies with no drift, while on a 60-a-second clock they drift 1 ms a second (83 ms by the end of event 411).
MOVIE_TICKS = 2 * CUE_TICKS
# Where a stock clip may start in the PSP movie, from its subtitle's start: measured 0.12-0.22 s.
BEFORE, AFTER = RATE // 4, RATE // 2
# The PSP's gain on a stock clip it holds: 0.70-0.79 measured; the other voice mode's clip gives 0.01-0.21.
GAINS = (0.6, 0.9)
# The least normalised correlation a placed clip reaches: 0.34-0.97 measured (the English over it lowers it);
# another line's clip reaches 0.02-0.13.
MATCH = 0.25
# A pause inside a clip: 10 ms frames the retimer would not hear (under AUDIBLE_FLOOR_DB from the clip's peak) for
# PAUSE samples or more; quiet runs fewer than PAUSE samples apart are one pause.
PAUSE = RATE // 10
TAIL = RATE // 5  # kept after a line's last audible sound (events.audible_length): its decay and breath
LEAD = RATE // 20  # kept before a line's first sound when its English starts ahead of its stock clip
# English speaks ahead of its clip when sound comes within ONSET_DB of the line's peak before the clip (event 408:
# -4 dB, a line starting early or the one before running on). Quieter sound there is not a line starting: a breath,
# or a trace of the music the separator let through.
ONSET_DB = -20
HOP = RATE // 100
# What is left of the music bed in JP - EN between lines (the two tracks correlate at 0.97-0.99 where no one speaks)
# peaks at -40 to -26 dBFS in 10 ms frames, by scene: the floor is the scene's 10th percentile of them, and a voice is
# heard in that difference BACKGROUND_DB above it (the retimer's rule alone, 30 dB under the peak, hears the floor).
FLOOR_PERCENT, BACKGROUND_DB = 10, 6.0
# Most takes peak over full scale, which the game's voice path cuts (dream_movies.LIMIT): takes played at +3 to +8 dBFS
# played back flat at the chain gain times 1.0 (run 2026-11-15_2215-fixes-voiceclip, 6 lines, 25,274 samples over).
# The cause is the PSP's movie audio: lifted by the scene's gain, its Japanese track peaks 1.6 dB over the stock clips
# it holds at the same loudness (median over 90 lines), and its English track as much. finished_takes turns each down
# only as far as that needs.


class DreamEventError(ValueError):
    """A Z Dream scene's PSP movie does not fit its stock event."""


@dataclass(frozen=True)
class Line:
    """A speak record of a Z Dream event that plays a voice."""
    index: int  # the line index
    voice: int  # its voice id
    start: int  # the movie sample at which the PSP shows its subtitle
    room: int  # samples until the next line speaks or the sub-scene ends, on the game's clock


@dataclass
class SceneVoices:
    """What one scene gives: an English take per voice id, at the stock clips' level, and how it was made."""
    takes: dict[int, array]  # voice id -> mono 32-bit float samples at RATE
    gain: float  # the PSP's gain on the stock clips, which the takes are divided by
    split: list[Line] = field(default_factory=list)  # lines placed pause by pause
    spliced: list[Line] = field(default_factory=list)  # lines placed block by block along the PSP's splices
    gains: dict[int, float] = field(default_factory=dict)  # voice id -> the gain finished_takes gave its take


def scene_lines(ordinal: int, data: bytes) -> list[Line]:
    """The voiced speaks of a stock Z Dream event file, in the order the scene runs them."""
    scene = event_scenes(data)
    if len({w.sub for w in scene.windows}) > 1:
        raise DreamEventError(f"event {ordinal}: lines spoken in more than one sub-scene, but the PSP has one movie")
    lines = []
    for w in scene.windows:
        if (voice := event_voice(ordinal, w.index)) is None:
            continue
        following = min((v.start for v in scene.windows if v.start > w.start), default=scene.lengths[w.sub])
        lines.append(Line(w.index, voice, round(w.start * RATE / MOVIE_TICKS),
                          math.floor((following - w.start) * RATE / EVENT_TICKS)))
    return lines


def _stretch(samples: array, start: int, length: int) -> array:
    """samples[start:start + length], silence where that runs past either end."""
    before = max(-start, 0)
    found = samples[max(start, 0):max(start + length, 0)]
    return array("f", bytes(4 * before)) + found + array("f", bytes(4 * (length - before - len(found))))


def _f32(data: bytes) -> array:
    samples = array("f")
    samples.frombytes(data)
    return samples


def _peaks(samples: array, size: int = HOP, count: int | None = None) -> list[float]:
    """The peak of each frame of size samples every HOP: those that fit whole, or count of them, silence past the end."""
    return native.module.frame_peaks(samples.tobytes(), size, HOP, count)


def _peak(samples: array) -> float:
    return _peaks(samples, len(samples))[0] if samples else 0.0


def _find(search: array, piece: array, first: int, last: int) -> tuple[int, bool]:
    """Where piece best matches search when it starts between first and last, and whether that is the PSP's copy."""
    lag, peak, gain = native.module.xcorr_lag(piece.tobytes(), _stretch(search, first, last - first + len(piece)).tobytes())
    return first + lag, peak >= MATCH and GAINS[0] <= gain <= GAINS[1]


def _pauses(clip: array) -> list[tuple[int, int]]:
    """The (start, end) samples of each pause inside the clip, between its first and last sounds."""
    peaks = _peaks(clip)
    floor = max(peaks, default=0.0) * 10 ** (AUDIBLE_FLOOR_DB / 20)
    sounds = [k for k, peak in enumerate(peaks) if peak > floor]
    gaps = [(a + 1, b) for a, b in zip(sounds, sounds[1:], strict=False) if b > a + 1]
    merged: list[tuple[int, int]] = []
    for a, b in gaps:
        if merged and (a - merged[-1][1]) * HOP < PAUSE:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(a * HOP, b * HOP) for a, b in merged if (b - a) * HOP >= PAUSE]


def _spliced(search: array, line: Line, clip: array) -> Placement:
    """The clip as the PSP's sped-up copy holds it: 2 * HOP blocks every HOP, each where the path native
    splice_drops finds puts it, added up Hann-weighted (where blocks land on the same samples, their weighted mean).
    The copy starts where a stock clip may (BEFORE to AFTER from the subtitle) and plays at most twice as fast, so it
    has dropped at most half the clip."""
    block, free = 2 * HOP, BEFORE + AFTER
    most = free + len(clip) // 2
    padded = array("f", bytes(4 * HOP)) + clip  # block 0 straddles the clip's start
    count = -(-len(padded) // HOP)
    floor = _peak(clip) * 10 ** (AUDIBLE_FLOOR_DB / 20)
    latest = line.start + AFTER - HOP  # block 0 at drop 0
    drops = native.module.splice_drops(padded.tobytes(), _stretch(search, latest - most, most + (count - 1) * HOP + block).tobytes(),
                                       block, HOP, most, free, [p > floor for p in _peaks(padded, block, count)])
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / block) for i in range(block)]  # here, so its cos is Python's
    places = [latest + k * HOP - drop for k, (drop, _) in enumerate(drops)]
    first = min(places)
    copy = _f32(native.module.overlap_add(padded.tobytes(), places, window, HOP))
    # The clip starts where its first block that matches (MATCH) puts it: a block before that says nothing.
    sure = next((k for k, (_, peak) in enumerate(drops) if peak >= MATCH), 0)
    return Placement(places[sure] - (sure - 1) * HOP, [(first, copy)], "splices")


@dataclass(frozen=True)
class Placement:
    """Where a stock clip sits in a PSP movie."""
    at: int  # the movie sample of the clip's first sample
    pieces: list[tuple[int, array]]  # (movie sample, samples): added up, the clip as the PSP's copy holds it
    how: str  # "whole", "pauses" or "splices"


def place(search: array, line: Line, clip: array) -> Placement:
    """Where the stock clip sits in search (the scene's JP - EN). The clip is placed whole;
    failing that, it is split in the middle of each pause, and each piece after the first is looked for from where
    the clip has it to as far earlier as the pause before it is long (the PSP shortens a pause, never lengthens it);
    failing that, it is placed along the PSP's splices from where it matches best whole."""
    at, found = _find(search, clip, line.start - BEFORE, line.start + AFTER)
    if found:
        return Placement(at, [(at, clip)], "whole")
    if (pauses := _pauses(clip)) and (pieces := _by_pauses(search, line, clip, pauses)):
        return Placement(pieces[0][0], pieces, "pauses")
    return _spliced(search, line, clip)


def _by_pauses(search: array, line: Line, clip: array, pauses: list[tuple[int, int]]) -> list[tuple[int, array]] | None:
    cuts = [0, *((a + b) // 2 for a, b in pauses), len(clip)]
    pieces, at = [], line.start - BEFORE
    for k, (a, b) in enumerate(zip(cuts, cuts[1:], strict=False)):
        first, last = (line.start - BEFORE, line.start + AFTER) if k == 0 else (at - (pauses[k - 1][1] - pauses[k - 1][0]), at + HOP)
        at, found = _find(search, clip[a:b], first, last)
        if not found:
            return None
        pieces.append((at, clip[a:b]))
        at += b - a
    return pieces


def _take_start(voice: array, line: Line, clip: int) -> int:
    """Where a line's take starts in the movie: at its stock clip, where the game's own voice starts with the
    subtitle, unless English is heard before the clip (the dub can start ahead of the Japanese take, or carry on from
    the line before): then LEAD before that first sound, and never before the subtitle."""
    heard = _stretch(voice, line.start, max(clip - line.start, 0))  # strictly before the clip
    peaks = _peaks(heard)
    floor = _peak(_stretch(voice, line.start, line.room)) * 10 ** (ONSET_DB / 20)
    first = next((i * HOP for i, peak in enumerate(peaks) if peak > floor), None)
    return clip if first is None else max(line.start, min(clip, line.start + first - LEAD))


@dataclass(frozen=True)
class Placed:
    """Where a scene's lines sit in its PSP movie."""
    lines: list[Line]
    placements: list[Placement]  # per line
    gain: float  # the PSP's gain on the stock clips
    wanted: list[tuple[int, int]]  # per line, the movie samples (start, end) its take is cut from at most


def placed(english: array, japanese: array, lines: list[Line], clips: dict[int, array]) -> Placed:
    """Where each line's stock clip sits in the scene's two PSP tracks (mono 32-bit float at RATE, the movie's first
    sample first), the scene's gain, and the stretch each line's take can come from: from its subtitle, since a take
    starts there at the earliest, to TAIL past the last sound either voice makes (heard in JP - EN over the scene's
    floor) before the line's time is up, which a take does not outlast."""
    if len(english) != len(japanese):
        raise DreamEventError(f"the English track has {len(english)} samples and the Japanese {len(japanese)}")
    search = _f32(native.module.difference(japanese.tobytes(), english.tobytes()))
    placements = [place(search, line, clips[line.voice]) for line in lines]
    pieces = [piece for found in placements for piece in found.pieces]
    if not pieces:
        raise DreamEventError("no stock clip lines up with the PSP movie's Japanese track")
    gain = native.module.fit_gain(b"".join(p.tobytes() for _, p in pieces),
                                  b"".join(_stretch(search, at, len(p)).tobytes() for at, p in pieces))
    frames = sorted(_peaks(search))
    background = frames[len(frames) * FLOOR_PERCENT // 100] * 10 ** (BACKGROUND_DB / 20) if frames else 0.0
    wanted = []
    for line, found in zip(lines, placements, strict=True):
        most = found.at + line.room - line.start
        heard = round(audible_length(_stretch(search, line.start, most), RATE, background) * RATE) + TAIL
        wanted.append((line.start, line.start + min(most, heard)))
    return Placed(lines, placements, gain, wanted)


def scene_voices(voice: array, found: Placed) -> SceneVoices:
    """Each line's English take from the scene's separated English voice (mono 32-bit float at RATE, the movie's
    first sample first; silence where it was not separated). A line spoken twice gets its shorter take, which fits
    both."""
    lines = found.lines
    out = SceneVoices({}, found.gain, [line for line, each in zip(lines, found.placements, strict=True) if each.how == "pauses"],
                      [line for line, each in zip(lines, found.placements, strict=True) if each.how == "splices"])
    starts = [_take_start(voice, line, each.at) for line, each in zip(lines, found.placements, strict=True)]
    for k, line in enumerate(lines):
        # A take ends where the next line's starts, so English cut into both is not heard twice. The next line is
        # the one the scene speaks next.
        after = min((j for j, other in enumerate(lines) if other.start > line.start), key=lambda j: lines[j].start,
                    default=None)
        length = min(line.room, found.wanted[k][1] - starts[k], *(() if after is None else (starts[after] - starts[k],)))
        take = _f32(native.module.scaled(_stretch(voice, starts[k], length).tobytes(), 1 / found.gain))
        take = take[:min(len(take), round(audible_length(take, RATE) * RATE) + TAIL)]
        if line.voice in out.takes and len(out.takes[line.voice]) <= len(take):
            continue
        out.takes[line.voice] = take
    # The stock timing stays (these events are not in script_alignment.EVENTS): each take ends before the next line.
    assert all(len(out.takes[line.voice]) <= line.room for line in lines)
    return out


def _decoded_track(riff: bytes, channels: int, rate: int) -> array:
    with tempfile.TemporaryDirectory(prefix="wozmod-scene-", ignore_cleanup_errors=True) as folder:
        (Path(folder) / "track.wav").write_bytes(riff)
        samples = array("f")
        samples.frombytes(decoded(Path(folder) / "track.wav", channels, ("-f", "wav"), START_PADDING, rate))
    return samples


def _track(riff: bytes) -> array:
    """A PMF soundtrack as the takes count it: mono at RATE."""
    return _decoded_track(riff, 1, RATE)


def _voice_at_rate(stereo: bytes) -> array:
    """A separated voice (interleaved stereo at separation.RATE) as the takes count it: mono at RATE, resampled as
    _track resamples the soundtrack it was separated from, so a sample of one is the same moment in the other."""
    with tempfile.TemporaryDirectory(prefix="wozmod-scene-", ignore_cleanup_errors=True) as folder:
        (Path(folder) / "voice.f32").write_bytes(stereo)
        samples = array("f")
        samples.frombytes(decoded(Path(folder) / "voice.f32", 1,
                                  ("-f", "f32le", "-ar", str(separation.RATE), "-ch_layout", "stereo")))
    return samples


def build_dream_events(stock: StockVoices, psp: Path, events: dict[int, bytes], separator: Path,
                       jobs: int | None = None) -> tuple[dict[int, bytes], dict[int, SceneVoices]]:
    """The English take of each Z Dream line as a voice bank record (KOVS) by voice id, and what each scene gave,
    from the stock game's clips, the extracted WO2 PSP disc, the stock event files 402-413 and the separator's model
    folder (separation.MODEL); jobs caps the ffmpeg processes and the separator's threads."""
    if not hasattr(native.module, "separator_spectrum"):
        raise DreamEventError("the Dream scenes need the wozmod-native package (uv sync --extra native)")
    lines = {o: scene_lines(o, events[o]) for o in DREAM_EVENTS}
    voices = sorted({line.voice for found in lines.values() for line in found})
    takes = stock.clips(voices)
    clips = {}
    for v, pcm in zip(voices, _ffmpeg_each(((["-f", "ogg"], kovs_ogg(takes[v]), ["-ac", "1", "-ar", str(RATE), "-f", "f32le"])
                                            for v in voices), len(voices), jobs, "cutting the Dream scenes", (0.8, 0.805)),
                      strict=True):
        clips[v] = array("f")
        clips[v].frombytes(pcm)
    # The scenes are independent: at most jobs at once (each runs one ffmpeg at a time, and the sample loops in the
    # native module let go of the GIL).
    jobs = jobs or os.process_cpu_count()
    order = sorted(DREAM_EVENTS)

    def progress(span: tuple[float, float], label: str = "cutting the Dream scenes"):
        finished = [0]

        def said(_) -> None:  # report() raises Cancelled when the player cancelled, which stops the other scenes too
            finished[0] += 1
            report("voices", label, span[0] + (span[1] - span[0]) * min(finished[0], len(order)) / len(order),
                   (min(finished[0], len(order)), len(order)))
        return said

    def place_scene(o: int) -> tuple[Placed, bytes]:
        tracks = audio_tracks((psp / DREAM_EVENTS[o]).read_bytes())
        found = placed(_track(tracks[ENGLISH]), _track(tracks[JAPANESE]), lines[o], clips)
        return found, _decoded_track(tracks[ENGLISH], 2, separation.RATE).tobytes()

    found, sounds = {}, {}
    for o, (each, sound) in zip(order, ordered_map(place_scene, order, jobs, progress((0.805, 0.81))), strict=True):
        found[o], sounds[str(o)] = each, sound
    # A wanted stretch in the separator's samples: no sample of it left out by the rounding.
    spans = {str(o): [(a * separation.RATE // RATE, -(-b * separation.RATE // RATE)) for a, b in found[o].wanted]
             for o in order}
    separated = separation.separate(
        sounds, spans, separator, jobs,
        lambda done, total: report("voices", "separating the Dream voices", 0.81 + 0.035 * done / total, (done, total)))
    del sounds

    def cut(o: int) -> SceneVoices:
        return scene_voices(_voice_at_rate(separated[str(o)]), found[o])

    scenes = dict(zip(order, ordered_map(cut, order, jobs, progress((0.845, 0.848))), strict=True))
    takes = {}
    for scene in scenes.values():
        for v, take in scene.takes.items():
            if v in takes:
                raise DreamEventError(f"voice id {v} is spoken in two Z Dream scenes")
            takes[v] = take
    records, gains = finished_takes(takes, jobs)
    for scene in scenes.values():
        scene.gains = {v: gains[v] for v in scene.takes}
    return records, scenes


def finished_takes(takes: dict[int, array], jobs: int | None = None) -> tuple[dict[int, bytes], dict[int, float]]:
    """Each take (mono 32-bit float at RATE) as a voice bank record (KOVS) by voice id, and the gain it was given:
    turned down only as far as the game's 16-bit decode of the record needs (dream_movies.under_full_scale)."""
    spans = iter([(0.848, 0.85)])  # a pass after the first leaves the progress where it is

    def encode(gains: dict[int, float]) -> dict[int, tuple[bytes, float]]:
        def source(v: int) -> tuple[list[str], bytes, str]:  # each take's bytes made only as its batch starts
            g = gains[v]
            return (["-f", "f32le", "-ar", str(RATE), "-ch_layout", "mono"], takes[v].tobytes(),
                    (f"volume={g!r}:precision=float," if g != 1 else "") + "asetpts=round(N/SR/TB)")

        records = vorbis_records(OnDemand(list(gains), source), jobs, "cutting the Dream scenes",
                                 next(spans, (0.85, 0.85)))
        pcm = _ffmpeg_each(((["-f", "ogg"], kovs_ogg(records[v]), ["-f", "f32le"]) for v in gains), len(gains), jobs,
                           "cutting the Dream scenes", (0.85, 0.85))
        peaks = [max(map(abs, array("f", samples)), default=0.0) for samples in pcm]
        return dict(zip(gains, zip((records[v] for v in gains), peaks, strict=True), strict=True))

    finished = under_full_scale(encode, takes)
    return {v: record for v, (record, _) in finished.items()}, {v: gain for v, (_, gain) in finished.items()}
