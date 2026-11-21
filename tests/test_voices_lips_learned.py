import itertools
import os
import statistics
from array import array
from pathlib import Path

import pytest

from wozmod.voices.lips import SPEECH_RATE, Speech, frames, learned_shapes, mouthed, own_blocks, shapes
from wozmod.voices.phones import PHONE_RATE

pytest.importorskip("wozmod_native")

FRAME = SPEECH_RATE // 30


def _speaking(pcm, length):
    """Per lip frame, whether it is within 18 dB of the line's loudest: the frames where Koei's mouth is judged."""
    samples = array("h")
    samples.frombytes(pcm)
    power = [sum(x * x for x in samples[t * FRAME:(t + 1) * FRAME]) / FRAME + 1e-3 for t in range(length)]
    return [p > max(power) * 10 ** -1.8 for p in power]


def _changes_per_second(line, speaking):
    return sum(a != b for a, b, s in zip(line, line[1:], speaking[1:], strict=False) if s) / (sum(speaking) / 30)


def _short(lines):
    """The share of the open runs (of one shape, not shut) over all lines that last 1 or 2 frames."""
    runs = [len(list(g)) for line in lines for k, g in itertools.groupby(line) if k not in (0, 255)]
    return sum(n <= 2 for n in runs) / len(runs)


def _change_f1(ours, theirs, speaking):
    """F1 of our changes of shape against Koei's, each matched once within a frame either way, over speaking frames
    whose frame and the one before are labelled; None when neither side changes."""
    judged = [t for t in range(1, len(theirs)) if speaking[t] and 255 not in (theirs[t], theirs[t - 1])]
    mine, koei = ([t for t in judged if line[t] != line[t - 1]] for line in (ours, theirs))
    free, hits = set(koei), 0
    for t in mine:
        if (match := next((u for u in (t - 1, t, t + 1) if u in free), None)) is not None:
            free.discard(match)
            hits += 1
    return 2 * hits / (len(mine) + len(koei)) if mine or koei else None


@pytest.mark.skipif(os.environ.get("WOZMOD_LIPS") != "1" or not (os.environ.get("WOZMOD_GAME")
                                                                  and os.environ.get("WOZMOD_DONORS")
                                                                  and os.environ.get("WOZMOD_SPEECH")
                                                                  and os.environ.get("WOZMOD_SEPARATOR")),
                    reason="set WOZMOD_LIPS=1, WOZMOD_GAME, WOZMOD_DONORS (the installer's donors folder), "
                           "WOZMOD_SPEECH (the speech model folder) and WOZMOD_SEPARATOR (the Dream voice model "
                           "folder), with ffmpeg")
def test_learned_mouths_give_koeis_shapes_on_lines_they_did_not_learn_from(tmp_path, monkeypatch):
    """Learned from the lines with Koei's own English block at even voice ids, the mouths of the odd ones against
    Koei's, per speaking frame. Bugs: features or labels that no longer tell the shapes apart (shapes picked by
    loudness, as before, scored 0.13; Koei's most common shape 0.06), labels read from the wrong set, a path that
    flaps the mouth (the loudness generator changed shape 11.9 times a speaking second) or freezes it, one that
    twitches between shapes within a syllable (a path with no notion of how long a shape lasts held 0.120 of its open
    runs at 1-2 frames, Koei 0.063), or changes of shape a frame late (F1 of our changes against Koei's, matched
    within a frame: 0.651 mean on time, about 0.61 a frame late), or phones left out of the scores, read for the wrong
    frames or from the wrong clip's words (the sound alone gives agreement median 0.395, phones a frame late about
    0.006 less a frame). Measured 2026-11-16 with phones: agreement median 0.500 over 232 lines (0.395 without), F1 mean
    0.649. The floors leave room for another ffmpeg's decoding: agreement 0.45 is still 0.055 over the sound alone."""
    import wozmod.voices.build as build
    from wozmod.formats.audio import decoded_clips

    class Captured(Exception):
        pass

    def capture(clips, *inputs):  # the clips are read from Voice.bin while the build holds it open: keep a copy
        raise Captured((dict(clips), *inputs))

    monkeypatch.setattr(build, "english_lips", capture)
    with pytest.raises(Captured) as caught:
        build.build_voice_banks(Path(os.environ["WOZMOD_GAME"]), Path(os.environ["WOZMOD_DONORS"]),
                                Path(os.environ["WOZMOD_SPEECH"]), Path(os.environ["WOZMOD_SEPARATOR"]), tmp_path)
    clips, sources, stock, donors, _, texts, listener = caught.value.args[0]
    own, faces = own_blocks(clips, sources, stock, donors)
    some = {voice: clips[voice] for voice in own}
    sound, heard = decoded_clips(some, SPEECH_RATE), decoded_clips(some, PHONE_RATE)
    said = {voice: Speech(sound[voice], heard[voice], texts.get(voice)) for voice in own}
    learn, held = sorted(v for v in own if v % 2 == 0), sorted(v for v in own if v % 2)
    found = learned_shapes({v: (said[v], own[v]) for v in learn}, {v: (said[v], frames(own[v])) for v in held}, listener)
    agree, rates, koei, f1, found_keyed, koei_shapes = [], [], [], [], [], []
    for voice in held:
        theirs, speaking = shapes(own[voice]), _speaking(sound[voice], frames(own[voice]))
        if sum(speaking) <= 15:
            continue
        ours = shapes(mouthed(own[voice], found[voice]))
        found_keyed.append(ours)
        koei_shapes.append(theirs)
        judged = [(a, b) for a, b, s in zip(ours, theirs, speaking, strict=True) if s and b != 255]
        agree.append(sum(a == b for a, b in judged) / len(judged))
        rates.append(_changes_per_second(ours, speaking))
        koei.append(_changes_per_second(theirs, speaking))
        if (score := _change_f1(ours, theirs, speaking)) is not None:
            f1.append(score)
    deciles = statistics.quantiles(agree, n=10)
    print(f"{len(own)} lines with their own block, {len(faces)} without; {len(learn)} learned from, {len(agree)} scored: agreement median {statistics.median(agree):.3f} "
          f"(p10 {deciles[0]:.3f}, p90 {deciles[-1]:.3f}); changes a speaking second {statistics.median(rates):.1f}, "
          f"Koei {statistics.median(koei):.1f}; change F1 median {statistics.median(f1):.3f} mean {statistics.mean(f1):.4f}; "
          f"open runs of 1-2 frames {_short(found_keyed):.3f}, Koei {_short(koei_shapes):.3f}")
    assert len(agree) > 150
    assert statistics.median(agree) > 0.45
    assert 0.75 < statistics.median(rates) / statistics.median(koei) < 1.25
    assert statistics.mean(f1) > 0.63
    assert _short(found_keyed) < _short(koei_shapes)
