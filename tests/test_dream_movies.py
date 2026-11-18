"""The Dream movies' English soundtrack: how far the PSP track is moved, and that the built movie keeps the stock
video's lip sync.

The fast tests use synthetic noise. The slow one (WOZMOD_AUDIO=1 with WOZMOD_GAME and WOZMOD_DONORS) builds
movie200 from the stock movie and WO2 PSP's MEV_201.pmf.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
native = pytest.importorskip("wozmod_native")

from wozmod.voices.dream_movies import (  # noqa: E402
    LIMIT, RATE, SEARCH, DreamMovieError, encode_under_full_scale, offset, peak, placed,
)


def _f32(values) -> bytes:
    return np.asarray(values, dtype="<f4").tobytes()


@pytest.mark.parametrize("delay", [2632, -1500])
def test_the_offset_is_how_far_the_psp_track_trails_the_stock_audio(delay):
    # Bug: the sign is flipped (the English track moves the wrong way, 110 ms off the lips), or the lag is counted
    # from the start of the searched stretch instead of the stretch the stock audio is compared with (off by
    # SEARCH samples).
    rng = np.random.default_rng(200)
    stock = rng.standard_normal(15 * RATE) * 0.2
    psp = np.concatenate([np.zeros(delay), stock])[:len(stock)] if delay > 0 else stock[-delay:]
    psp = 0.9 * psp + 0.01 * rng.standard_normal(len(psp))  # another encode of the same mix
    assert offset(_f32(stock), _f32(psp), "movie200") == delay


@pytest.mark.parametrize("damage", ["weak", "moved"])
def test_one_stretch_that_matches_weakly_or_elsewhere_stops_the_build(damage):
    # Bug: only the best or the average stretch is held to MATCH, or the stretches' offsets are not compared, so a
    # PSP track that is another scene in part, or cut differently, builds a movie whose English leaves the lips.
    rng = np.random.default_rng(201)
    stock, delay = rng.standard_normal(60 * RATE), 2632
    psp = np.concatenate([np.zeros(delay), stock])[:len(stock)]
    last = slice(len(stock) - 12 * RATE, len(stock))  # holds the last stretch, and only it
    if damage == "weak":
        psp[last] = 0.3 * psp[last] + rng.standard_normal(12 * RATE)  # still clearly at the same offset
    else:
        psp[last] = np.concatenate([np.zeros(delay + 100), stock])[last]  # 100 samples later from here
    with pytest.raises(DreamMovieError, match="movie201"):
        offset(_f32(stock), _f32(psp), "movie201")


def test_the_track_moves_by_whole_stereo_samples_and_is_cut_to_the_stock_length():
    # Bug: the lag is applied in bytes or in mono samples, so the channels swap or the track moves half as far; or the
    # padding is left off a short track.
    left = np.arange(1, 11) / 10
    track = _f32(np.stack([left, -left], axis=1).ravel())

    def frames(moved: bytes) -> np.ndarray:
        return np.frombuffer(moved, dtype="<f4").reshape(-1, 2)

    assert np.allclose(frames(placed(track, 3, 5)), np.stack([left[3:8], -left[3:8]], axis=1))
    moved = frames(placed(track, -2, 14))
    assert np.allclose(moved[:, 0], [0, 0, *left, 0, 0]) and np.allclose(moved[:, 1], [0, 0, *-left, 0, 0])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
@pytest.mark.parametrize("over", [1.12, 0.8])
def test_the_encoded_track_peaks_at_the_largest_16_bit_sample_and_no_lower(tmp_path, over):
    # Bug: a fixed margin comes back (the English plays up to 1 dB quieter than it needs, as the -1 dBFS trim did),
    # the gain comes from the track before the WMA encode (whose overshoot then cuts samples at 16 bits), or a track
    # under full scale is turned up or down.
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x36:rate=25:duration=2",
                    "-c:v", "wmv2", "-f", "asf", str(tmp_path / "stock.wmv")], check=True)
    rng = np.random.default_rng(202)
    noise = rng.standard_normal((2 * RATE, 2)) * np.hanning(2 * RATE)[:, None]
    (tmp_path / "english.f32").write_bytes(_f32(noise * over / np.abs(noise).max()))

    gain = encode_under_full_scale(tmp_path / "stock.wmv", tmp_path / "english.f32", tmp_path / "movie.wmv")

    found = peak(tmp_path / "movie.wmv")
    if over > 1:
        # Within the encoder's scatter on loud noise (about 1 %), well above the old fixed -1 dBFS.
        assert LIMIT * 10 ** (-0.3 / 20) < found <= LIMIT and gain < 1, (found, gain)
    else:
        assert gain == 1 and found < 0.9, (found, gain)


# Slow: the real movie.

SLOW = pytest.mark.skipif(
    os.environ.get("WOZMOD_AUDIO") != "1" or not os.environ.get("WOZMOD_GAME") or not os.environ.get("WOZMOD_DONORS")
    or not shutil.which("ffmpeg"),
    reason="set WOZMOD_AUDIO=1, WOZMOD_GAME and WOZMOD_DONORS, with ffmpeg")


def _inputs() -> tuple[Path, Path]:
    return Path(os.environ["WOZMOD_GAME"]) / "movie", Path(os.environ["WOZMOD_DONORS"]) / "wo2-psp"


def _mono(path: Path) -> np.ndarray:
    """A movie's audio as a player times it: sample n plays n / RATE seconds after the file starts. ffmpeg's WMA
    decoders give no output for the first packets of an ffmpeg-encoded track, but stamp the rest with their time."""
    return _decode(path, (), f"aresample={RATE}:resampler=soxr:async=1:first_pts=0")


def _riff(path: Path) -> np.ndarray:
    """A PSP RIFF's audio, numbered by decoded samples as the build numbers it."""
    return _decode(path, ("-f", "wav"), f"asetpts=round(N/SR/TB),aresample={RATE}:resampler=soxr")


def _decode(path: Path, before: tuple[str, ...], timing: str) -> np.ndarray:
    run = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", *before, "-i", str(path), "-map", "0:a:0",
                          "-af", f"{timing},pan=mono|c0=0.5*c0+0.5*c1", "-f", "f32le", "-"],
                         capture_output=True, check=True)
    return np.frombuffer(run.stdout, dtype="<f4")


def _video_start(path: Path) -> float:
    """Seconds from the start of the file to its first video frame."""
    run = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time:format=start_time",
                          "-of", "json", str(path)], capture_output=True, check=True, text=True, encoding="utf-8")
    found = json.loads(run.stdout)
    video = [float(s["start_time"]) for s in found["streams"] if s["codec_type"] == "video"]
    return video[0] - float(found["format"]["start_time"])


@SLOW
def test_the_built_movie200_keeps_the_stock_lip_sync(tmp_path, built):
    # Bug: the English track is moved the wrong way, by the lag in 44.1 kHz samples (4.5 ms short), or not at all,
    # or the video is shifted against the audio when the two are joined; the substreams are swapped, so the movie
    # plays the Japanese track again.
    from wozmod.formats.pmf import audio_tracks
    from wozmod.voices.dream_movies import DREAM_MOVIES, ENGLISH, JAPANESE

    movies, psp = _inputs()
    pmf = (psp / DREAM_MOVIES["movie200"]).read_bytes()
    movie = built("voices", tmp_path / "voices") / "out/wozmod/movie/voice/movie200.wmv"

    # Where the stock audio's shared music bed sits in the built audio, against where each file's video starts.
    stock, english = _mono(movies / "movie200.wmv"), _mono(movie)
    reference = stock[5 * RATE:len(stock) - 5 * RATE]
    lag, peak, _ = native.xcorr_lag(reference.tobytes(), english[5 * RATE - SEARCH:len(stock) - 5 * RATE + SEARCH].tobytes())
    moved = (lag - SEARCH) / RATE
    # The muxer moves both streams later by the WMA encoder's lead-in, so the audio stays with the frames.
    expected = _video_start(movie) - _video_start(movies / "movie200.wmv")
    assert abs(moved - expected) <= 0.001, (moved, expected)
    assert peak > 0.3

    # The English, not the Japanese, substream: the built audio is the PSP English decode, which shares only the
    # music bed with the Japanese (measured 0.9996 against 0.84 here).
    tracks = audio_tracks(pmf)
    for number in (ENGLISH, JAPANESE):
        (tmp_path / f"{number}.wav").write_bytes(tracks[number])
    window = english[20 * RATE:40 * RATE].tobytes()
    peaks = {n: native.xcorr_lag(window, _riff(tmp_path / f"{n}.wav")[15 * RATE:45 * RATE].tobytes())[1]
             for n in (ENGLISH, JAPANESE)}
    assert peaks[ENGLISH] > 0.99 and peaks[ENGLISH] - peaks[JAPANESE] > 0.1, peaks


@SLOW
def test_the_other_dream_movies_psp_track_does_not_line_up_with_movie200(tmp_path):
    # Bug: the pairing check passes a wrong track (MEV_202 is movie201's scene), so movie200 would get another
    # scene's English.
    from wozmod.formats.pmf import audio_tracks
    from wozmod.voices.dream_movies import DREAM_MOVIES, JAPANESE

    movies, psp = _inputs()
    (tmp_path / "jp.wav").write_bytes(audio_tracks((psp / DREAM_MOVIES["movie201"]).read_bytes())[JAPANESE])
    with pytest.raises(DreamMovieError, match="movie200"):
        offset(_mono(movies / "movie200.wmv").tobytes(), _riff(tmp_path / "jp.wav").tobytes(), "movie200")


def test_turning_down_ends_at_the_loudest_passing_gain_in_few_encodes_however_the_encoder_rounds_the_peak():
    # Bug: the turn-down steps by the overshoot alone, assuming the decoded peak follows the gain; an encoder that
    # moves the peak in coarser steps leaves a file barely over the limit for dozens of encodes (one Dream take took
    # 347), or the search keeps a failing encode, or turns a file that passed at gain 1 down.
    from wozmod.voices.dream_movies import RESOLUTION_DB, under_full_scale

    def decoded_peak(level: float, gain: float) -> float:
        """Not in proportion to the gain, as a lossy encoder's is not: wobbling by 0.4 % and in 0.2 % steps."""
        return math.ceil(gain * level * (1 + 0.004 * math.sin(9e3 * gain)) / 0.002) * 0.002

    levels = {"quiet": 0.9, **{f"over {level}": level for level in (1.0007, 1.0015, 1.0031, 1.017, 1.09, 1.3, 1.77, 2.3, 3.7)}}
    encodes = {key: [] for key in levels}

    def encode(gains):
        for key, gain in gains.items():
            encodes[key].append(gain)
        return {key: ((key, gain), decoded_peak(levels[key], gain)) for key, gain in gains.items()}

    finished = under_full_scale(encode, levels)
    close = 10 ** (RESOLUTION_DB / 20)
    wrong = []
    for key, ((which, kept), gain) in finished.items():
        if which != key or kept != gain or decoded_peak(levels[key], gain) > LIMIT:
            wrong.append(f"{key}: kept {which} at {kept}, a peak of {decoded_peak(levels[key], gain)}")
        if key != "quiet" and not any(gain < tried <= gain * close and decoded_peak(levels[key], tried) > LIMIT
                                      for tried in encodes[key]):
            wrong.append(f"{key}: {gain} is not within {RESOLUTION_DB} dB of a gain found to fail")
        if len(encodes[key]) > 12:
            wrong.append(f"{key}: {len(encodes[key])} encodes")
    assert finished["quiet"][1] == 1.0 and encodes["quiet"] == [1.0]
    assert wrong == []
