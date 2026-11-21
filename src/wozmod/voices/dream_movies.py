"""The Dream movies (movie200, movie201) under the English voice: the stock video, with the English soundtrack of
the same scene in WO2 PSP (USA).

The PSP movie (a PMF) holds the scene's footage, frame for frame as stock, with two soundtracks: English and
Japanese. Its Japanese track is the stock audio (another encode of one mix), so lining it up with the stock audio
gives the offset at which the English track lines up with the stock video's lips. ffmpeg's decode of the PSP track
starts late by its decoder's delay and the encoder's lead-in (about 55 ms); the offset is measured, every install,
by correlation in several stretches of the movie, and the build stops when they do not agree.

The runtime plays wozmod/movie/voice/<name>.wmv in place of both movie/<name>.wmv and movie/<name>_s.wmv.
"""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
from array import array
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

from wozmod import game_files, native, platform, resources
from wozmod.formats.pmf import audio_tracks
from wozmod.progress import report

DREAM_MOVIES = {"movie200": "PSP_GAME/USRDIR/movie/MEV_201.pmf", "movie201": "PSP_GAME/USRDIR/movie/MEV_202.pmf"}
ENGLISH, JAPANESE = 0, 1  # the PMF substreams
RATE = 48_000  # the stock movies' audio, and the samples the offset counts
WINDOWS, WINDOW, SEARCH = 5, 10 * RATE, RATE  # stretches compared, their length, and the largest offset looked for
# The least normalised correlation a stretch of the right track reaches: measured 0.85-0.99 for both movies; the
# other movie's track reaches 0.02-0.06.
MATCH = 0.5
# The PSP delay is a fraction of a sample over a whole number, so stretches find one of two neighbouring offsets.
SPREAD = 1
# The game plays its audio as 16-bit PCM and so cuts it at full scale. A movie's goes through its WM ASF Reader graph
# (on Wine every played sample is a whole 16-bit step): the stock movie200's 10 samples over full scale (+0.03 to
# +0.24 dB) play at exactly 1.0, the rest within -96 dB of ffmpeg's float decode (run 2026-11-15_2145-levels-stock200).
# A voice clip goes through libvorbisfile's ov_read (0x67CE00, called for 16-bit signed samples by the stream read
# 0x679680), which rounds each float times 32768 and clamps it to -32768..32767. The PSP English is over full scale in
# its own decode (movie200 +1.11 dBFS, 49 samples; movie201 +0.53, 3; and 78 of the 94 Dream cutscene takes, see
# dream_events). The WMA encode moves a movie's peak by -0.3 to +0.3 dB, the Vorbis encode a take's by -1.2 to +0.4
# (median -0.07). So a finished file is turned down until its decoded peak is the largest 16-bit sample, LIMIT: no
# sample is cut, and no level is given up past that.
LIMIT = 32767 / 32768
# How close a turned-down file's gain comes to the loudest that keeps its decoded peak at LIMIT: 0.01 dB, a tenth of
# the smallest level difference a listener can hear. A choice of what is close enough, not a measurement.
RESOLUTION_DB = 0.01
AUDIO = ["-c:a", "wmav2", "-b:a", "192k"]  # ffmpeg encodes no WMA Pro, which the stock movies use


class DreamMovieError(ValueError):
    """The PSP track does not line up with the stock movie."""


def offset(stock: bytes, japanese: bytes, movie: str) -> int:
    """The samples by which the PSP Japanese track trails the stock audio, japanese[n + offset] ~ stock[n], both
    mono 32-bit float at RATE. Raises DreamMovieError, naming the movie, unless every stretch matches strongly at
    offsets SPREAD apart at most; the offset most stretches find wins (the smaller on a tie)."""
    span = min(len(stock), len(japanese)) // 4
    if span < WINDOW + 2 * SEARCH:
        raise DreamMovieError(f"{movie}: the audio is {span / RATE:.1f} s long, too short to line up")
    found = []
    for k in range(WINDOWS):
        start = SEARCH + k * (span - 2 * SEARCH - WINDOW) // (WINDOWS - 1)
        lag, peak, _ = native.module.xcorr_lag(stock[4 * start:4 * (start + WINDOW)],
                                               japanese[4 * (start - SEARCH):4 * (start + WINDOW + SEARCH)])
        found.append((lag - SEARCH, peak))
    lags = [lag for lag, _ in found]
    if min(peak for _, peak in found) < MATCH or max(lags) - min(lags) > SPREAD:
        shown = ", ".join(f"{lag} at {peak:.2f}" for lag, peak in found)
        raise DreamMovieError(f"{movie}: the WO2 PSP movie's Japanese track does not line up with the stock audio "
                              f"(offset and correlation per stretch: {shown})")
    counts = Counter(lags)
    return min(lags, key=lambda lag: (-counts[lag], lag))


def placed(english: bytes, lag: int, frames: int) -> bytes:
    """The English track (interleaved stereo 32-bit float at RATE) moved earlier by lag samples and cut or padded
    with silence to frames samples."""
    frame = 8  # bytes per stereo sample
    moved = english[lag * frame:] if lag >= 0 else bytes(-lag * frame) + english
    return moved[:frames * frame] + bytes(max(frames * frame - len(moved), 0))


def _ffmpeg(*arguments: str) -> bytes:
    command = [resources.ffmpeg() or "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *arguments]
    return platform.run_hidden(command, capture_output=True, check=True, stdin=subprocess.DEVNULL).stdout


def decoded(source: Path, channels: int, before: tuple[str, ...] = (), skip: int = 0, rate: int = RATE) -> bytes:
    """The first audio stream as 32-bit float at rate, mono as the mean of the channels, numbered by decoded samples
    (the RIFF reader stamps two ATRAC3plus frames with one frame's duration: see audio.donor_voice_clips), without
    its first skip samples at the stream's own rate."""
    mix = ",pan=mono|c0=0.5*c0+0.5*c1" if channels == 1 else ""
    trim = f"atrim=start_sample={skip}," if skip else ""
    return _ffmpeg(*before, "-i", str(source), "-map", "0:a:0", "-af",
                   f"{trim}asetpts=round(N/SR/TB),aresample={rate}:resampler=soxr{mix}", "-f", "f32le", "-")


def peak(source: Path) -> float:
    """The largest sample magnitude of the first audio stream, decoded as 32-bit float."""
    samples = array("f")
    samples.frombytes(_ffmpeg("-i", str(source), "-map", "0:a:0", "-f", "f32le", "-"))
    return max(max(samples, default=0.0), -min(samples, default=0.0))


def under_full_scale[K, T](encode: Callable[[dict[K, float]], dict[K, tuple[T, float]]],
                           keys: Iterable[K]) -> dict[K, tuple[T, float]]:
    """Each key's finished file and the gain it was given, from encode, which takes a gain per key and returns each
    key's file at that gain with its decoded peak. A file whose peak at gain 1 is LIMIT or under keeps gain 1.

    The others are searched for the loudest gain whose decoded peak is LIMIT or under, without assuming the peak
    follows the gain: a lossy encoder moves it by its own rounding (up to about 1 % from one gain to the next). The
    first try is the gain that would bring the peak to LIMIT if it did follow; while a try is over, the next goes
    further down by twice as many dB as the step before (from RESOLUTION_DB), or to that estimate if lower. Once one
    passes, the search halves the dB between the loudest passing gain and the quietest failing one until they are
    RESOLUTION_DB apart, and keeps the loudest passing file. Each file ends checked at LIMIT or under, in a number
    of encodes the gains bound, all files encoded together each round."""
    finished: dict[K, tuple[T, float]] = {}
    trying = dict.fromkeys(keys, 1.0)
    passed: dict[K, tuple[T, float]] = {}  # the loudest passing file and its gain
    failed: dict[K, float] = {}  # the quietest failing gain
    down: dict[K, float] = {}  # the dB of the next step below a failing gain, while none has passed
    close = 10 ** (RESOLUTION_DB / 20)
    while trying:
        following = {}
        for key, (output, found) in encode(trying).items():
            gain = trying[key]
            if found <= LIMIT:
                if key not in failed:  # passed at gain 1
                    finished[key] = output, gain
                    continue
                passed[key] = output, gain
            else:
                failed[key] = gain
            if key in passed:
                low, high = passed[key][1], failed[key]
                if high / low <= close:
                    finished[key] = passed[key]
                else:
                    following[key] = math.sqrt(low * high)
            elif key in down:
                following[key] = min(gain * LIMIT / found, gain * 10 ** (-down[key] / 20))
                down[key] *= 2
            else:  # over at gain 1: try where the peak would be LIMIT if it followed the gain
                following[key] = gain * LIMIT / found
                down[key] = RESOLUTION_DB
        trying = following
    return finished


def encode_under_full_scale(video: Path, english: Path, target: Path) -> float:
    """Write target, an ASF file of video's video stream and english (interleaved stereo 32-bit float at RATE) as
    WMA, turned down as far as its decoded peak needs to stay at LIMIT or under (under_full_scale); return the gain
    it was given. Each try is its own file beside target: the search can end on a failing try, and the passing one it
    keeps is then put in target's place."""
    tries: list[Path] = []

    def encode(gains: dict[Path, float]) -> dict[Path, tuple[Path, float]]:
        gain = gains[target]
        attempt = target.with_name(f".{target.name}.{len(tries)}.try")
        tries.append(attempt)
        # The WMA encoder's first packet (its 2048-sample lead-in) is stamped before zero, and ASF holds no negative
        # time, so the muxer moves both streams 43 ms later: the frames keep their place against the English.
        _ffmpeg("-y", "-i", str(video), "-f", "f32le", "-ar", str(RATE), "-ch_layout", "stereo", "-i", str(english),
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                *(["-filter:a", f"volume={gain!r}:precision=float"] if gain != 1 else []), *AUDIO,
                "-threads", "1", "-fflags", "+bitexact", "-flags:a", "+bitexact", "-f", "asf", str(attempt))
        return {target: (attempt, peak(attempt))}

    try:
        kept, gain = under_full_scale(encode, [target])[target]
        os.replace(kept, target)
    finally:
        for attempt in tries:
            attempt.unlink(missing_ok=True)
    return gain


def build_dream_movie(name: str, stock: bytes, pmf: bytes, target: Path) -> float:
    """Write the English Dream movie `name` to target from the stock movie's bytes and its PSP movie's; return the
    gain the English track was given."""
    tracks = audio_tracks(pmf)
    with tempfile.TemporaryDirectory(prefix="wozmod-movie-", ignore_cleanup_errors=True) as folder:  # see _ffmpeg_each
        folder = Path(folder)
        (folder / "stock.wmv").write_bytes(stock)
        (folder / "english.wav").write_bytes(tracks[ENGLISH])
        (folder / "japanese.wav").write_bytes(tracks[JAPANESE])
        reference = decoded(folder / "stock.wmv", 1)
        lag = offset(reference, decoded(folder / "japanese.wav", 1, ("-f", "wav")), name)
        (folder / "english.f32").write_bytes(placed(decoded(folder / "english.wav", 2, ("-f", "wav")), lag,
                                                    len(reference) // 4))
        report("voices", "converting audio", 0.9)
        target.parent.mkdir(parents=True, exist_ok=True)
        return encode_under_full_scale(folder / "stock.wmv", folder / "english.f32", target)


def build_dream_movies(stock: Path, psp: Path, output_dir: Path) -> dict[str, float]:
    """Write movie/voice/movie200.wmv and movie201.wmv below output_dir (the wozmod folder) from the stock game
    folder and the extracted WO2 PSP disc; return each movie's English gain."""
    if not hasattr(native.module, "xcorr_lag"):
        raise DreamMovieError("the Dream movies need the wozmod-native package (uv sync --extra native)")
    return {name: build_dream_movie(name, game_files.whole(stock / "movie" / f"{name}.wmv"), (psp / source).read_bytes(),
                                    output_dir / "movie/voice" / f"{name}.wmv")
            for name, source in DREAM_MOVIES.items()}
