import itertools
import math
import os
import random
import statistics
import struct
from array import array
from pathlib import Path

import pytest

from wozmod.formats.audio import WO1_EVENT_VOICES, WO2_EVENT_VOICES
from wozmod.voices.lips import (
    SPEECH_RATE, TAG, X360_TAG, Speech, donor_lips, english_lips, frames, learned_shapes, lip_bank, mouthed, own_blocks,
    retimed, shapes, stock_lips, wo1_fits, x360_fits,
)
from wozmod.voices.phones import PHONE_RATE
from synthetic import kovs

pytest.importorskip("wozmod_native")


REST = b"\x00\x37\x64\x00"
SHUT = 55  # track B's rest: the mouth closed
FRAME = SPEECH_RATE // 30


def _block(order, length, tracks):
    tag = TAG if order == "<" else X360_TAG
    keys = b"".join(bytes(key) for track in tracks for key in track)
    return tag + struct.pack(f"{order}I4HI", length, *map(len, tracks), 0x12, 0x7E41) + REST + keys


def _event(order, lines):
    """Line records at 12 mod 16, each followed by its block; a record's text may be empty."""
    data = bytearray(12)
    for index, text, block in lines:
        data += b"\xff" * 4 + struct.pack(f"{order}HH", index, 16) + text.ljust(12, b"\0") + block
        data += bytes(-(len(data) - 12) % 16)
    return bytes(data)


def _clip(frames):
    return kovs(96, samples=frames * 1600)


def _speech(pattern):
    """16-bit samples for one lip frame per character: '#' speech at -9 dB of full scale, '+' 10 dB quieter, '-' 25 dB
    quieter, '.' a hiss at -70 dB, ' ' silence."""
    level = {"#": 0.5, "+": 0.5 * 10 ** -0.5, "-": 0.5 * 10 ** -1.25, ".": 0.0004, " ": 0.0}
    return b"".join(struct.pack("<h", round(32767 * level[c] * math.sin(2 * math.pi * 200 * i / SPEECH_RATE)))
                    for c in pattern for i in range(FRAME))


class Deaf:
    """A listener that hears no phones: the mouths then come from the sound alone."""

    def aligned(self, lines):
        return {}

    def heard(self, lines):
        return {}


def _said(sound, text=None):
    return Speech(sound, b"", text)


def _tracks(block):
    counts, at, out = struct.unpack_from("<3H", block, 16), 0x20, []
    for count in counts:
        out.append([tuple(block[k:k + 3]) for k in range(at, at + 3 * count, 3)])
        at += 3 * count
    return out


def test_each_english_clip_gets_the_block_its_donor_plays_with_it_or_one_made_from_its_speech():
    # Catches: a block read from the wrong offset, X360 lengths, counts or the header words after them left
    # big-endian, a line with no text dropped, a donor line paired with the wrong clip, one donor's fit rule applied
    # to the other, a made block that misses the clip length or keeps the Japanese mouth, and the clips of the lines
    # with their own block left out of what the mouths are learned from.
    tracks = [[(0, 127, 10)], [(55, 127, 4), (60, 127, 6)], [(100, 127, 10)]]
    face = [[(0, 127, 20)], [(100, 127, 20)]]  # tracks A and C stretched to the clip
    x360 = _event(">", [(3, b"donor", _block(">", 20, tracks)), (4, b"", _block(">", 10, tracks))])
    wo1 = _event(">", [(3, b"donor", _block(">", 20, tracks))])
    stock = _event("<", [(5, b"stock", _block("<", 10, tracks))])
    clips = {100: _clip(20), 101: _clip(20), 200: _clip(30), 201: _clip(20)}
    sources = {100: "wo2-x360", 101: "wo2-x360", 200: "wo1-x360", 201: "wo1-x360"}
    speech = " " * 4 + "#" * 26  # speech from frame 4, where the blocks of 100 and 200 open the mouth
    decoded = []

    def decode(some, rate):
        decoded.append((sorted(some), rate))
        return {voice: _speech(speech) for voice in some}

    lips, tiers = english_lips(clips, sources, stock_lips([(196, stock)]), {
        "wo2-x360": ({100: 1401, 101: 1402}, donor_lips([x360], ">", *WO2_EVENT_VOICES[1:]), x360_fits),
        "wo1-x360": ({200: 960, 201: 961}, donor_lips([wo1], ">", *WO1_EVENT_VOICES[1:]), wo1_fits),
    }, decode, {}, Deaf())

    assert lips[100] == _block("<", 20, tracks)  # X360 line 3 plays clip 1398 + 3 and runs the whole clip
    assert lips[200] == _block("<", 20, tracks)  # WO1 line 3 plays record 957 + 3; its block ends with the speech
    # X360 line 4 shipped a block half the clip's length; no WO1 line plays record 961, so 201 has the stock block
    for voice in (101, 201):
        made = _tracks(lips[voice])
        assert (struct.unpack_from("<I", lips[voice], 12)[0], made[0], made[2]) == (20, *face)
        assert shapes(lips[voice]) == bytes([0] * 4 + [5] * 16)  # what speech was keyed with in 100 and 200
    every = [100, 101, 200, 201]
    assert (tiers, decoded) == ({"donor": 2, "generated": 2}, [(every, SPEECH_RATE), (every, PHONE_RATE)])
    bank = lip_bank(lips)
    for voice, block in lips.items():
        offset, size = struct.unpack_from("<II", bank, 4 + 8 * voice)
        assert bank[offset:offset + size] == block


TONES = {1: 300.0, 2: 700.0, 3: 1500.0, 4: 3200.0}  # a synthetic shape's sound: one tone each, silence for shut


def _runs(seed, frames, allowed):
    """A line's shapes: runs of 4 to 11 lip frames, each another shape than the one before."""
    rng, out = random.Random(seed), []
    while len(out) < frames:
        out += [rng.choice([s for s in allowed if not out or s != out[-1]])] * rng.randint(4, 11)
    return out[:frames]


def _sound(line, seed):
    rng = random.Random(seed)
    return b"".join(struct.pack("<h", round(32767 * (0.25 * math.sin(2 * math.pi * TONES[s] * i / SPEECH_RATE)
                                                     + rng.gauss(0, 0.01)) if s else 0))
                    for t, s in enumerate(line) for i in range(t * FRAME, (t + 1) * FRAME))


def _keyed(line, group):
    """Track B keyed as Koei keys it: each run's shape (in set group) one frame in from either end, so a frame's
    nearest key is its own run's."""
    points, at = [], 0
    for n in (len(run) for run in _split(line)):
        points += [(at + 1, 55 + 15 * group + line[at]), (at + n - 2, 55 + 15 * group + line[at])]
        at += n
    points = [(0, points[0][1])] + points + [(len(line) - 1, points[-1][1])]
    return [(points[0][1], 127, 1)] + [(v, 127, t - points[i][0]) for i, (t, v) in enumerate(points[1:]) if t > points[i][0]]


def _split(line):
    runs = [[line[0]]]
    for s in line[1:]:
        runs[-1].append(s) if s == runs[-1][0] else runs.append([s])
    return runs


def test_a_line_without_its_own_block_gets_the_shapes_its_sound_had_in_the_lines_with_one():
    # Bugs: features or labels a lip frame off from the speech, so every change of shape lands a frame early or late;
    # a value of the second or third set read as the wrong shape, or dropped, when learning (most known lines here use
    # those sets, and only they show shapes 3 and 4); the mouth written in the first set under a face of the third;
    # a silent frame given an open mouth.
    lines = {voice: _runs(voice, 120, [0, 1, 2] if voice < 4 else [0, 1, 2, 3, 4]) for voice in range(12)}
    unseen = _runs(99, 150, [0, 1, 2, 3, 4])
    face = _block(">", 40, [[(0, 127, 40)], [(85, 127, 1), (88, 127, 39)], [(100, 127, 40)]])  # set 2
    blocks = {voice: _block(">", 120, [[(0, 127, 120)], _keyed(line, voice % 3 if voice >= 4 else 0), [(100, 127, 120)]])
              for voice, line in lines.items()} | {99: face}
    speech = {voice: _sound(line, voice) for voice, line in lines.items()} | {99: _sound(unseen, 99)}
    x360 = _event(">", [(voice, b"", block) for voice, block in sorted(blocks.items())])
    lips, tiers = english_lips({voice: _clip(len(line)) for voice, line in (lines | {99: unseen}).items()},
                               dict.fromkeys([*lines, 99], "wo2-x360"), {}, {
        "wo2-x360": ({voice: 1398 + voice for voice in blocks}, donor_lips([x360], ">", *WO2_EVENT_VOICES[1:]),
                     x360_fits)}, lambda some, rate: {voice: speech[voice] for voice in some}, {}, Deaf())
    assert tiers == {"donor": 12, "generated": 1}
    assert list(shapes(lips[99])) == unseen
    assert {value for value, _, _ in _tracks(lips[99])[1]} == {85 + s for s in set(unseen)}


def test_a_sound_shorter_than_any_run_of_the_known_mouths_does_not_change_the_mouth():
    # Bug: the path changes shape wherever a frame or two sound like another shape, so the mouth twitches inside a
    # held shape, though every mouth it learned from (Koei's, here runs of 4 frames or more) holds a shape longer.
    lines = {voice: _runs(voice, 120, [0, 1, 2, 3, 4]) for voice in range(8)}
    known = {voice: (_said(_sound(line, voice)), _block("<", 120, [[(0, 127, 120)], _keyed(line, 0), [(100, 127, 120)]]))
             for voice, line in lines.items()}
    held, heard = _runs(99, 150, [0, 1, 2, 3, 4]), []
    for run in _split(held):
        if run[0] and len(run) >= 8:  # 1 or 2 frames of another open shape, 3 frames into the run
            blip = [1 + run[0] % 4] * (1 + len(run) % 2)
            run = run[:3] + blip + run[3 + len(blip):]
        heard += run
    assert heard != held
    assert list(learned_shapes(known, {99: (_said(_sound(heard, 99)), len(heard))}, Deaf())[99]) == held


def _phoned(line, phone_of):
    """PocketSphinx segments for a line of lip-frame runs 6 frames long (20 of its 10 ms frames): each run's phone."""
    return [(phone_of[run[0]], 20 * k, 20) for k, run in enumerate(_split(line))]


def test_the_phones_choose_between_shapes_that_sound_alike():
    # Bugs: the phone table left out of the scores, or added with the wrong sign; the table of phones heard with no
    # words used for a line aligned to its words, or the other way round; a line's phones read for another line.
    # Here shapes 3 and 5 sound the same, so only the phones tell them apart, and the two kinds of phone disagree.
    def line(seed):
        rng = random.Random(seed)
        return [s for _ in range(20) for s in [0] * 6 + [rng.choice((3, 5))] * 6]

    aligned, heard = {0: "SIL", 3: "D", 5: "K"}, {0: "SIL", 3: "K", 5: "D"}
    lines = {voice: line(voice) for voice in range(10)} | {98: line(98), 99: line(99)}
    sound = {voice: _sound([1 if s else 0 for s in shapes], voice) for voice, shapes in lines.items()}

    class Listener:
        def aligned(self, lines_):
            return {voice: _phoned(lines[voice], aligned) for voice, (_, text) in lines_.items() if text}

        def heard(self, lines_):
            return {voice: _phoned(lines[voice], heard) for voice in lines_}

    known = {voice: (_said(sound[voice], "words"), _block("<", 240, [[(0, 127, 240)], _keyed(lines[voice], 0),
                                                                       [(100, 127, 240)]])) for voice in range(10)}
    found = learned_shapes(known, {98: (_said(sound[98], "words"), 240), 99: (_said(sound[99]), 240)}, Listener())
    assert (list(found[98]), list(found[99])) == (lines[98], lines[99])
    assert {3, 5} <= set(lines[98]) and {3, 5} <= set(lines[99])


def test_every_track_runs_the_whole_of_a_long_line():
    # Bug: a key's frames are a byte, so a stretch longer than 255 frames was cut short: the track then ended early
    # (its frames no longer add up to the block's length) and the mouth or face jumped to its last pose too soon.
    line = bytes([0] * 10 + [3] * 30 + [0] * 560)
    face = _block("<", 10, [[(0, 127, 1), (20, 127, 9)], [(60, 127, 10)], [(100, 127, 1), (100, 127, 9)]])
    for block in (mouthed(face, line), retimed(face, 600)):
        assert [sum(n for _, _, n in keys) for keys in _tracks(block)] == [600] * 3
        assert all(n <= 255 for keys in _tracks(block) for _, _, n in keys)
    assert shapes(mouthed(face, line)) == line


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
                                                                  and os.environ.get("WOZMOD_SPEECH")),
                    reason="set WOZMOD_LIPS=1, WOZMOD_GAME, WOZMOD_DONORS (the installer's donors folder) and "
                           "WOZMOD_SPEECH (the speech model folder), with ffmpeg")
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

    def capture(*inputs):
        raise Captured(inputs)

    monkeypatch.setattr(build, "english_lips", capture)
    with pytest.raises(Captured) as caught:
        build.build_voice_banks(Path(os.environ["WOZMOD_GAME"]), Path(os.environ["WOZMOD_DONORS"]),
                                Path(os.environ["WOZMOD_SPEECH"]), tmp_path)
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
