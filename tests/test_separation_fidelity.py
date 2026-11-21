"""How faithfully the Dream voice separator gives back a voice it is known to hold: the installer's own engines, on
real music (slow: WOZMOD_AUDIO=1 with WOZMOD_GAME, WOZMOD_DONORS and WOZMOD_SEPARATOR; about 3 minutes).

No disc holds the Dream scenes' English voices alone, so there is no truth to compare a separated scene with. The
truth here is made: a clean voice (a stock clip of another Dream scene, at the gain the PSP mixes voices at) laid
over a stretch of a PSP movie's English track where no one speaks. The separator must give that voice back.

REFERENCE is what the model gives on these mixes with its weights as trained (32-bit floats, none of the 4-bit
storage the release ships), through this same code: the error the model itself makes. The shipped model may add at
most GATE_DB to it in any band, which is the bar the owner's listening test was run under.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("wozmod_native")

from dream_inputs import scene_tracks, stock_clips  # noqa: E402
from wozmod.voices import separation  # noqa: E402
from wozmod.voices.dream_events import DREAM_EVENTS, RATE, placed  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("WOZMOD_AUDIO") != "1" or not shutil.which("ffmpeg")
    or not all(os.environ.get(name) for name in ("WOZMOD_GAME", "WOZMOD_DONORS", "WOZMOD_SEPARATOR")),
    reason="set WOZMOD_AUDIO=1, WOZMOD_GAME, WOZMOD_DONORS and WOZMOD_SEPARATOR, with ffmpeg")

BANDS = (0, 1000, 3000, 6000, 10000, separation.RATE // 2)  # Hz: the five bands the error is measured in
# The error of the model as trained on these mixes, per band, in dB of the voice (the network with 32-bit float
# weights, run by this module's measured(); 2026-11-21, nine mixes). One mix's music carries a tune between 1 and
# 3 kHz that the model takes for voice, which is why that band reads so much worse than the others.
REFERENCE = (-12.90, -4.57, -13.93, -12.60, -12.74)  # the shipped model on the CPU: -12.86 -4.56 -13.84 -12.51 -12.39
GATE_DB = 0.5
MIX = 0.75  # the PSP's gain on a voice
MARGIN = separation.RATE // 2  # music kept before and after a laid voice


@pytest.fixture(scope="module")
def mixes() -> dict[str, tuple[bytes, object, int]]:
    return made_mixes()


def made_mixes() -> dict[str, tuple[bytes, object, int]]:
    """Per scene: (the mix as the separator takes it, the voice laid in it (mono, at the separator's rate), the
    sample it starts at). The voice is the longest stock clip of the next scene that fits the scene's longest
    stretch without speech."""
    game = Path(os.environ["WOZMOD_GAME"])
    scenes = {o: scene_tracks(o) for o in sorted(DREAM_EVENTS)}
    voices = {o: stock_clips(game, sorted({line.voice for line in lines}), separation.RATE)
              for o, (lines, *_) in scenes.items()}
    made = {}
    order = sorted(scenes)
    for k, o in enumerate(order):
        lines, clips, english, japanese, sound = scenes[o]
        spoken = sorted(placed(english, japanese, lines, clips).wanted)
        edges = [0, *(edge for span in spoken for edge in span), len(english)]
        gaps = [(b - a, a) for a, b in zip(edges[::2], edges[1::2], strict=True)]
        length, start = (value * separation.RATE // RATE for value in max(gaps))
        fitting = [clip for clip in voices[order[(k + 1) % len(order)]].values() if len(clip) + 2 * MARGIN <= length]
        if not fitting:
            continue
        voice = MIX * np.asarray(max(fitting, key=len), dtype=np.float64)
        mix = np.frombuffer(sound, "<f4").reshape(-1, 2).astype(np.float64)
        at = start + MARGIN
        mix[at:at + len(voice)] += voice[:, None]
        made[str(o)] = (mix.astype("<f4").tobytes(), voice, at)
    assert len(made) >= 8, "too few scenes have a stretch without speech that a clip fits"
    return made


def _spectrum(x) -> object:
    window = np.hanning(2048)
    frames = np.lib.stride_tricks.sliding_window_view(x, 2048)[::512]
    return np.fft.rfft(frames * window, axis=1)


def measured(mixes, model: Path, gpu: bool) -> tuple[list[float], dict[str, object]]:
    """(the separator's error per band, in dB of the laid voices, summed over the mixes and the frames where the
    voice sounds; each mix's separated voice over its laid voice's span, mono)."""
    spans = {name: [(at, at + len(voice))] for name, (_, voice, at) in mixes.items()}
    separated = separation.separate({name: mix for name, (mix, _, _) in mixes.items()}, spans, model,
                                    os.process_cpu_count(), gpu=gpu)
    edges = np.fft.rfftfreq(2048, 1 / separation.RATE)
    bands = [(edges >= low) & (edges < high) for low, high in zip(BANDS, BANDS[1:], strict=False)]
    error, energy, found = np.zeros(len(bands)), np.zeros(len(bands)), {}
    for name, (_, voice, at) in mixes.items():
        found[name] = np.frombuffer(separated[name], "<f4").reshape(-1, 2).mean(1)[at:at + len(voice)].astype(np.float64)
        truth, given = _spectrum(voice), _spectrum(found[name])
        power = (abs(truth) ** 2).sum(1)
        sounding = power > power.max() * 1e-3
        for b, band in enumerate(bands):
            error[b] += (abs(given[sounding][:, band] - truth[sounding][:, band]) ** 2).sum()
            energy[b] += (abs(truth[sounding][:, band]) ** 2).sum()
    return [float(10 * np.log10(e / v)) for e, v in zip(error, energy, strict=True)], found


def test_the_shipped_model_gives_a_known_voice_back_as_well_as_the_model_as_trained(mixes, capfd):
    # Bug: the 4-bit weights are unpacked wrongly (a nibble order, a block's scale, a transposed matrix), the graph
    # differs from the model (a norm, the rotary turn, a band's bins), the STFT or the join is off, or a chunk is
    # separated without its context: the voice comes back with more error than the model as trained makes, which is
    # what the players would hear in every Dream line.
    errors, cpu = measured(mixes, Path(os.environ["WOZMOD_SEPARATOR"]), gpu=False)

    names = [f"{low}-{high} Hz" for low, high in zip(BANDS, BANDS[1:], strict=False)]
    worse = {name: (round(error, 2), reference) for name, error, reference in zip(names, errors, REFERENCE, strict=True)
             if error > reference + GATE_DB}
    assert not worse, f"error against the model as trained, dB: {worse} (all bands: {[round(e, 2) for e in errors]})"

    # Bug: another engine (a GPU) passes its first-chunk check and then gives a voice that is not the CPU's: a
    # kernel that is wrong only for some input, a copy that races its kernel. Measured -57 to -72 dB.
    capfd.readouterr()
    _, other = measured(mixes, Path(os.environ["WOZMOD_SEPARATOR"]), gpu=True)
    log = capfd.readouterr().err
    if "used from here" not in log:
        pytest.skip("no engine but the CPU ran here: " + "; ".join(
            line.removeprefix(separation.LOG) for line in log.splitlines() if "not tried" in line or "not used" in line))
    with np.errstate(divide="ignore"):  # a scene whose chunks the CPU ran both times differs by nothing
        apart = {name: round(float(10 * np.log10(((other[name] - cpu[name]) ** 2).sum() / (cpu[name] ** 2).sum())), 1)
                 for name in cpu}
    assert all(db < -40 for db in apart.values()), f"the other engine's voice against the CPU's, dB by scene: {apart}"
