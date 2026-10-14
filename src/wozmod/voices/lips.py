"""English-timed lip blocks for event lines, keyed by voice id."""

from __future__ import annotations

import struct
from collections.abc import Callable

from wozmod.formats.audio import MOZ_VOICE_IDS, parse_kovs_record


TAG = b"PAC02_00JM20"
X360_TAG = b"00_20CAP02MJ"  # the first eight tag bytes swapped as one u64, the last four as a u32
FRAMES_PER_SECOND = 30


def lip_blocks(data: bytes, order: str = "<") -> dict[int, list[bytes]]:
    """Each event line's lip blocks by line index, in file order, as little-endian blocks.

    A line record is FFFFFFFF, u16 index, u16 size, text, and its block follows at record + 4 + size:
    tag, u32 length in frames, three u16 key counts, rest values at +0x1C, then 3-byte keys from +0x20.
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
        length, *counts = struct.unpack_from(f"{order}I3H", data, at + 12)
        block = bytearray(data[at:at + 0x20 + 3 * sum(counts)])
        struct.pack_into("<12sI3H", block, 0, TAG, length, *counts)
        found.setdefault(index, []).append(bytes(block))
    return found


def frames(block: bytes) -> int:
    return struct.unpack_from("<I", block, 12)[0]


def retimed(block: bytes, length: int) -> bytes:
    """The block with each track's key durations scaled to length frames, every key kept at one frame or more."""
    out = bytearray(block)
    struct.pack_into("<I", out, 12, length)
    at = 0x20
    for count in struct.unpack_from("<3H", block, 16):
        if not count:
            continue
        keys = range(at + 2, at + 3 * count, 3)
        old = [block[k] for k in keys]
        scale = length / max(sum(old), 1)
        new = [max(1, int(n * scale)) for n in old]
        by_remainder = sorted(range(count), key=lambda i: old[i] * scale - new[i], reverse=True)
        while sum(new) < length:
            for i in by_remainder[:length - sum(new)]:
                new[i] += 1
        while sum(new) > length and max(new) > 1:
            new[new.index(max(new))] -= 1
        for k, n in zip(keys, new, strict=True):
            out[k] = min(n, 255)  # shortcut: a key stretched past 255 frames is cut short; split it if one ever is
        at += 3 * count
    return bytes(out)


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


X360_LINES = (1398, 0xF1)  # 0x8219F8D8 plays clip 0x576 + line index, no voice from 0xF1
WO1_LINES = (957, 0x1FF)  # 0x4AD290 plays record 0x3BD + line index, no voice from 0x1FF


def x360_fits(block: bytes, clip: int) -> bool:
    return abs(frames(block) - clip) <= 4  # X360 blocks run the whole clip


def wo1_fits(block: bytes, clip: int) -> bool:
    return -4 <= clip - frames(block) <= 24  # WO1 blocks end with the speech, at most 0.8 s before the clip ends


def english_lips(clips: dict[int, bytes], sources: dict[int, str], stock: dict[int, bytes],
                 donors: dict[str, tuple[dict[int, int], dict[int, list[bytes]], Callable[[bytes, int], bool]]],
                 ) -> tuple[dict[int, bytes], dict[str, int]]:
    """Pick one lip block per English voice id.

    donors maps a clip source to (voice id to donor record, donor blocks by record, fit test). The donor game
    plays those blocks with the very clip we install, so one that fits the clip is the line's own. A clip
    whose donor shipped no fitting block gets the donor's (or the stock) block stretched to its length.
    """
    chosen: dict[int, bytes] = {}
    stretched: dict[int, bytes] = {}
    for voice, clip in sorted(clips.items()):
        mapping, blocks, fits = donors[sources[voice]]
        theirs, length = blocks.get(mapping.get(voice), []), clip_frames(clip)
        if matched := [b for b in theirs if fits(b, length)]:
            chosen[voice] = matched[0]
        elif theirs or voice in stock:
            stretched[voice] = retimed((theirs or [stock[voice]])[0], length)
    return chosen | stretched, {"donor": len(chosen), "stretched": len(stretched)}


def lip_bank(blocks: dict[int, bytes]) -> bytes:
    """u32 id count, then (u32 offset, u32 size) per voice id, then the blocks; size 0 keeps the stock block."""
    table, body = bytearray(4 + 8 * MOZ_VOICE_IDS), bytearray()
    struct.pack_into("<I", table, 0, MOZ_VOICE_IDS)
    for voice, block in sorted(blocks.items()):
        struct.pack_into("<II", table, 4 + 8 * voice, len(table) + len(body), len(block))
        body += block
    return bytes(table + body)
