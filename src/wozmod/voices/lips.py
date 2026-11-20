"""English-timed lip blocks for event lines, keyed by voice id."""

from __future__ import annotations

import bisect
import itertools
import struct
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from wozmod import native
from wozmod.formats.audio import MOZ_VOICE_IDS, OnDemand, parse_kovs_record

from . import phones


TAG = b"PAC02_00JM20"
X360_TAG = b"00_20CAP02MJ"  # the first eight tag bytes swapped as one u64, the last four as a u32
FRAMES_PER_SECOND = 30
SPEECH_RATE = 24_000  # samples a second of the speech a mouth is generated from


def lip_blocks(data: bytes, order: str = "<") -> dict[int, list[bytes]]:
    """Each event line's lip blocks by line index, in file order, as little-endian blocks.

    A line record is FFFFFFFF, u16 index, u16 size, text, and its block follows at record + 4 + size:
    tag, u32 length in frames, three u16 key counts, a u16 at +0x16, a u32 at +0x18, rest values at +0x1C,
    then 3-byte keys from +0x20.
    """
    found: dict[int, list[bytes]] = {}
    at = -1
    while (at := data.find(TAG if order == "<" else X360_TAG, at + 1)) >= 0:
        record = next((r for r in range(at - 20, max(at - 0x400, -1), -16)  # a line may have no text
                       if data[r:r + 4] == b"\xff" * 4
                       and struct.unpack_from(f"{order}H", data, r + 6)[0] == at - r - 4), None)
        if at % 16 or record is None:
            continue
        index = struct.unpack_from(f"{order}H", data, record + 4)[0]
        length, *counts, word, dword = struct.unpack_from(f"{order}I4HI", data, at + 12)
        block = bytearray(data[at:at + 0x20 + 3 * sum(counts)])
        struct.pack_into("<12sI4HI", block, 0, TAG, length, *counts, word, dword)
        found.setdefault(index, []).append(bytes(block))
    return found


def frames(block: bytes) -> int:
    return struct.unpack_from("<I", block, 12)[0]


# A block's three tracks pose the face at the lip time (0x4A2110, 0x4BCA80). Each key is (value, weight, frames): value
# a frame of the actor's face motion, which weight / 127 blends with the track's rest value (+0x1C, +0x1D, +0x1E);
# frames is the length of the stretch that ends at the key, over which the pose moves on from the key before (key 0's
# frames is outside the timeline: Koei's blocks give it 1, so their keys' frames add up to the block's length and
# their last key falls on its last frame). Track B, bones 8 to 13, is the mouth: its rest, 55, closes it.
#
# Track B's values are mouth shapes, in three sets of ten a set apart: 55-64, 70-79 and 85-94, 0 in a set (55, 70,
# 85) shut, and a block keeps mostly to one set, which goes with the actor's expression; so a generated mouth uses
# the set its face block's mouth uses most. Measured on Koei's 506 English blocks made for the clip we install
# (2026-11-16): 70 and 85 key silence (a median 111 dB under the line's peak, 55 59 dB) and the rest of each set
# speech; at loud keys 57 has the highest low-band centroid (the open "a"), 64 the lowest (rounded) and 63 the
# brightest (spread), so a shape is not a degree of opening. 0.965 of a block's open keys are in its most used set;
# the stock block's most used set is the set of 0.75 of Koei's open keys for the same id (6460 keys, 352 lines),
# track A's value at the key 0.63.
#
# A line with no block of its own gets its shapes from lips.rs (wozmod_native.lip_shapes), learned at install from
# the lines that have one (nothing learned ships with wozmod), each shape keyed where it starts and ends (EASE).
# Trained on the even voice ids, on the odd ones it gives Koei's shape on 0.40 of a line's speaking frames
# (median; p10 0.20, p90 0.55; 232 lines), where the loudness generator before it gave 0.13 and Koei's
# most common shape 0.06, and changes shape 6.5 times a speaking second (Koei 7.4). Holding each shape to
# EASE frames from its ends gives Koei's blend of shapes on 0.47 of all frames (a key in each run's middle 0.45: the
# mouth then eases shut over half of each pause).
# Koei keys a mouth mostly 3 or 4 frames apart and almost never 1 or 2, so lips.rs holds each shape as long as the
# known lines' labels hold theirs. Before it did, the path changed shape at any frame and 0.136 of its open runs
# lasted 1-2 frames (Koei 0.056): twitches inside a syllable. Now 0.019 do, and on the 229 odd lines the changes
# of shape land within a frame of Koei's more often (F1 median 0.655 -> 0.677, Wilcoxon p 3e-5), the blend
# overlaps Koei's more (p 0.02) and the shape agreement per frame is the same (p 0.68).
# The sound alone tells when the mouth changes better than which shape it takes, so lips.rs also scores each frame by
# the phone sounding at its middle and the phones either side (phones.py): PocketSphinx aligns the line's English to
# its clip, and the chance of each shape after each phone context is learned at install from the known lines, aligned
# the same way. On the 229 odd lines (learned from the even ones) Koei's shape goes from 0.390 to 0.490 of a line's
# speaking frames (mean; 83% of lines better, Wilcoxon p 6e-26), the blend overlaps Koei's on 0.482 (was 0.386), and
# the changes of shape stay as near Koei's (F1 0.662 -> 0.661, p 0.90); open runs of 1-2 frames 0.055 (Koei 0.056).
Key = tuple[int, int, int]
MOUTH = 1
FULL = 127
SHUT = 55        # track B's rest, the first set's shut mouth
SET = 15         # values between one set's shape and the same shape in the next set
SHAPES = 10      # shapes per set, 0 shut
EASE = 2         # frames either side of a change over which the mouth moves from one shape to the next
SILENCE = -60.0  # dB of full scale under which a lip frame is silent and its mouth shut, however quiet the line
UNLABELLED = 255  # a lip frame whose shape lips.rs leaves out of what it learns from


def _tracks(block: bytes) -> list[list[Key]]:
    out, at = [], 0x20
    for count in struct.unpack_from("<3H", block, 16):
        out.append([(block[k], block[k + 1], block[k + 2]) for k in range(at, at + 3 * count, 3)])
        at += 3 * count
    return out


def _held(key: Key, frames: int) -> list[Key]:
    return [(key[0], key[1], min(255, frames - k)) for k in range(0, frames, 255)]


def _block(block: bytes, length: int, tracks: list[list[Key]]) -> bytes:
    """block's header with length and tracks. A stretch past a key's byte (255 frames) holds the key before it until
    the last 255 frames; key 0's frames past 255, which lie after the last key, hold the last key."""
    fitted = []
    for keys in tracks:
        out: list[Key] = []
        for value, weight, n in keys:
            if out and n > 255:
                out += _held(out[-1], n - 255)
                n = 255
            out.append((value, weight, n if out or n <= 255 else 1))
        if keys and keys[0][2] > 255:
            out += _held(out[-1], keys[0][2] - 1)
        fitted.append(out)
    head = bytearray(block[:0x20])
    struct.pack_into("<I3H", head, 12, length, *map(len, fitted))
    return bytes(head) + b"".join(bytes(key) for keys in fitted for key in keys)


def retimed(block: bytes, length: int) -> bytes:
    """The block with each track's key durations scaled to length frames, every key kept at one frame or more."""
    tracks = []
    for keys in _tracks(block):
        old = [n for _, _, n in keys]
        scale = length / max(sum(old), 1)
        new = [max(1, int(n * scale)) for n in old]
        by_remainder = sorted(range(len(keys)), key=lambda i: old[i] * scale - new[i], reverse=True)
        while keys and sum(new) < length:
            for i in by_remainder[:length - sum(new)]:
                new[i] += 1
        while keys and sum(new) > length and max(new) > 1:
            new[new.index(max(new))] -= 1
        tracks.append([(v, w, n) for (v, w, _), n in zip(keys, new, strict=True)])
    return _block(block, length, tracks)


def _shape(value: int) -> int:
    """The shape of a track B value, or UNLABELLED for one outside the three sets."""
    shape = (value - SHUT) % SET
    return shape if SHUT <= value < SHUT + 3 * SET and shape < SHAPES else UNLABELLED


def _set(block: bytes) -> int:
    """The set of shapes block's mouth uses most (the lowest of a tie), 0 when it never opens."""
    sets = Counter((value - SHUT) // SET for value, _, _ in _tracks(block)[MOUTH] if _shape(value) not in (0, UNLABELLED))
    return max(sorted(sets), key=sets.__getitem__, default=0)


def shapes(block: bytes) -> bytes:
    """Per lip frame of block, the shape of its nearest track B key (the earlier of two as near), as 0x4BCA80 times
    them: key 0 at frame 0, each later key its frames after the one before."""
    keys = _tracks(block)[MOUTH]
    if not keys:
        return bytes([UNLABELLED]) * frames(block)
    times = list(itertools.accumulate((n for _, _, n in keys[1:]), initial=0))
    out = bytearray()
    for t in range(frames(block)):
        near = bisect.bisect_left(times, t)
        if near == len(times) or near and t - times[near - 1] <= times[near] - t:
            near -= 1
        out.append(_shape(keys[bisect.bisect_left(times, times[near])][0]))  # the first of keys at one time
    return bytes(out)


def mouth(shapes: bytes, group: int) -> list[Key]:
    """Track B for len(shapes) lip frames, its shapes from set group: each run of one shape keyed EASE frames in from
    either end (a run of 2 * EASE + 1 frames or fewer once, at its middle), and on the first and last frames."""
    points, at = [], 0
    for shape, run in itertools.groupby(shapes):
        n, value = len(list(run)), SHUT + SET * group + shape
        points += [(at + (n - 1) // 2, value)] if n <= 2 * EASE + 1 else [(at + EASE, value), (at + n - 1 - EASE, value)]
        at += n
    if points[0][0]:
        points.insert(0, (0, points[0][1]))
    if points[-1][0] < len(shapes) - 1:
        points.append((len(shapes) - 1, points[-1][1]))
    return [(points[0][1], FULL, 1)] + [(v, FULL, t - points[i][0]) for i, (t, v) in enumerate(points[1:])]


def mouthed(block: bytes, shapes: bytes) -> bytes:
    """block stretched to len(shapes) lip frames, its mouth keyed to those shapes in the set block's mouth uses."""
    tracks = _tracks(retimed(block, len(shapes)))
    tracks[MOUTH] = mouth(shapes, _set(block))
    return _block(block, len(shapes), tracks)


@dataclass(frozen=True)
class Speech:
    """A line's speech, as 16-bit little-endian mono samples at SPEECH_RATE (its sound) and at phones.PHONE_RATE (its
    phones), and the English it says, or None when that is not known."""
    sound: bytes
    phones: bytes
    text: str | None


def learned_shapes(known: dict[int, tuple[Speech, bytes]], lines: dict[int, tuple[Speech, int]],
                   listener: phones.Listener) -> dict[int, bytes]:
    """Per line in lines (speech, lip frames), its shapes, learned from known (speech, a block keyed for it), the
    phones of each from listener: aligned to its text, else, for a line in lines whose text is not known or does not
    align, heard with no words (and then those of the known lines heard the same way)."""
    if not hasattr(native.module, "lip_shapes"):
        raise RuntimeError("the mouths need the wozmod-native package (uv sync --extra native)")
    every = {voice: speech for voice, (speech, _) in (known | lines).items()}
    aligned = listener.aligned({v: (s.phones, s.text) for v, s in every.items() if s.text is not None})
    unaligned = [voice for voice in lines if aligned.get(voice) is None]
    heard = listener.heard({v: every[v].phones for v in sorted(set(known) | set(unaligned))}) if unaligned else {}

    def contexts(voice: int, frames: int) -> tuple[bytes | None, bytes | None]:
        return tuple(None if (found := which.get(voice)) is None else phones.frame_phones(found, frames)
                     for which in (aligned, heard))

    found = native.module.lip_shapes(
        [(speech.sound, labels, *contexts(voice, len(labels)))
         for voice, (speech, block) in sorted(known.items()) for labels in [shapes(block)]],
        [(speech.sound, n, *contexts(voice, n)) for voice, (speech, n) in sorted(lines.items())], SILENCE)
    return dict(zip(sorted(lines), found, strict=True))


def clip_frames(clip: bytes) -> int:
    ogg = parse_kovs_record(clip, verify_checksums=False)[2]
    return round(ogg.sample_count * FRAMES_PER_SECOND / ogg.sample_rate)


def stock_lips(events: list[tuple[int, bytes]]) -> dict[int, bytes]:
    """The first stock block of each voice id, from (voice base, event) pairs."""
    found: dict[int, bytes] = {}
    for base, data in events:
        for index, blocks in lip_blocks(data).items():
            found.setdefault(base + index, blocks[0])
    return found


def donor_lips(events, order: str, base: int, limit: int) -> dict[int, list[bytes]]:
    """Donor blocks by the voice record their line plays: line index i plays base + i below limit, in any event."""
    found: dict[int, list[bytes]] = {}
    for data in events:
        for index, blocks in lip_blocks(data, order).items():
            if index < limit:
                found.setdefault(base + index, []).extend(blocks)
    return found


def x360_fits(block: bytes, clip: int) -> bool:
    return abs(frames(block) - clip) <= 4  # X360 blocks run the whole clip


def wo1_fits(block: bytes, clip: int) -> bool:
    return -4 <= clip - frames(block) <= 24  # WO1 blocks end with the speech, at most 0.8 s before the clip ends


Donors = dict[str, tuple[dict[int, int], dict[int, list[bytes]], Callable[[bytes, int], bool]]]


def own_blocks(clips: dict[int, bytes], sources: dict[int, str], stock: dict[int, bytes], donors: Donors,
               ) -> tuple[dict[int, bytes], dict[int, bytes]]:
    """(the block made for each clip, a face block for each other clip of an event line with a donor or stock block).

    donors maps a clip source to (voice id to donor record, donor blocks by record, fit test). The donor game
    plays those blocks with the very clip we install, so one that fits the clip is the line's own. Another clip's face
    is the donor's block, else the stock one.
    """
    own: dict[int, bytes] = {}
    faces: dict[int, bytes] = {}
    for voice in sorted(clips):  # one clip at a time: clips may read each from the bank (formats.audio.BankClips)
        mapping, blocks, fits = donors[sources[voice]]
        theirs = blocks.get(mapping.get(voice), [])
        frames = clip_frames(clips[voice]) if theirs else 0
        if matched := [b for b in theirs if fits(b, frames)]:
            own[voice] = matched[0]
        elif theirs or voice in stock:
            faces[voice] = (theirs or [stock[voice]])[0]
    return own, faces


def english_lips(clips: dict[int, bytes], sources: dict[int, str], stock: dict[int, bytes], donors: Donors,
                 decode: Callable[[dict[int, bytes], int], dict[int, bytes]], texts: dict[int, str],
                 listener: phones.Listener) -> tuple[dict[int, bytes], dict[str, int]]:
    """One lip block per English voice id of an event line: the block made for its clip (own_blocks), else its face
    block stretched to the clip with a mouth from learned_shapes, learned from the clips that have their own block.
    decode(clips, rate) gives clips' speech as 16-bit mono samples at rate; texts holds the English a clip says, where
    it is known; listener gives their phones."""
    own, faces = own_blocks(clips, sources, stock, donors)
    if not faces:
        return own, {"donor": len(own), "generated": 0}
    wanted = OnDemand(sorted(own | faces), clips.__getitem__)  # each clip read as its decode starts
    sound, heard = decode(wanted, SPEECH_RATE), decode(wanted, phones.PHONE_RATE)
    speech = {voice: Speech(sound[voice], heard[voice], texts.get(voice)) for voice in wanted}
    found = learned_shapes({voice: (speech[voice], block) for voice, block in own.items()},
                           {voice: (speech[voice], clip_frames(clips[voice])) for voice in faces}, listener)
    generated = {voice: mouthed(face, found[voice]) for voice, face in faces.items()}
    return own | generated, {"donor": len(own), "generated": len(generated)}


def lip_bank(blocks: dict[int, bytes]) -> bytes:
    """u32 id count, then (u32 offset, u32 size) per voice id, then the blocks; size 0 keeps the stock block."""
    table, body = bytearray(4 + 8 * MOZ_VOICE_IDS), bytearray()
    struct.pack_into("<I", table, 0, MOZ_VOICE_IDS)
    for voice, block in sorted(blocks.items()):
        struct.pack_into("<II", table, 4 + 8 * voice, len(table) + len(body), len(block))
        body += block
    return bytes(table + body)
