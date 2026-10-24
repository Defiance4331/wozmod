"""The voice id the stock game plays with each narration, still-event and camp-talk line, as OROCHI_Z.exe numbers it.

Verified in the stock executable (disassembly, 2026-10-24); the tables are read from the player's executable:

- 4073, WO1 chapter narration: the draw (0x5610DF) shows line j of page p (MESC block p), from the state the voice
  start (0x5617AE) reads; it plays 0x560600(p) + j (0x5617B5 to 0x5617C8), and
  0x560600 returns T[p] + 0x60 from a 60-entry table it stores on its stack from 0x560615 (read at 0x56085E,
  esp + 0xC + 4p).
- 4105, WO1 still event (camp talk): its container holds 56 scene blocks (the first byte of each is its line count)
  and the MESC. The draw (0x59C784) shows MESC line 0x59B3D0(s) + j, 0x59B3D0 summing the line counts of the scenes
  before s; the voice start (0x59CB8F to 0x59CBAE) plays 0x59B420(s) + j, one line less for lines 20 to 22 of scene
  0x25 (runtime/event_line.h still_voice_line). 0x59B420 returns T2[s] + 0x250 from a 56-entry table it stores on
  its stack from 0x59B426. Each scene block holds a 16-byte record per line, byte 1 the line's officer. The advance
  (0x59C870) steps through a line's alternative and ends it early at a next line whose officer is 0x4F or above
  (0x59C8D0, 0x59C8D6), jumping to the group's end (0x59C8E0 to 0x59C8EB); the draw (0x59C784 scene, 0x59C78C line
  byte) shows only that line byte, so such a filler line is never drawn (stock: scene 0x25 line 19, a copy of line
  18's text, and why lines 20 to 22 take one voice less).
- 4103, Z still event: the start routine 0x59AEB0 takes voice [command+8] + 0x4C4 (0x250 in WO1 voice mode, unless
  [0x801A9C] is 4) and text [command+0xC]; the two are equal in all 1,072 stock commands, so line k plays 0x4C4 + k.
- 4071, Z chapter narration: record word 0 + 0x15A (0x55FB26), and word 0 equals the text index in every record.
  The records of stage s are container entry s + 1 (0x55FB0E, the stage [0x801AA0]).
- 4190, briefings: stage s plays voice s + 0x200 (0x548C1D), or s + 0x1C0 from stage 0x84 (0x548C10), and shows
  entry s - 0x28, or s - 0x68 from 0x84 (0x548C4A).

Under the WO1 voice mode the Z screens would add 0x250 or 0x60; the Z story plays them in mode 0, as modelled here.
"""

from __future__ import annotations

import struct

from wozmod.formats import pe
from wozmod.formats.scripts import mesc_blocks

PAGE_TABLE = (0x560615, 0xC, 60, 0x60)  # 4073: first table store, its esp offset of entry 0, entries, added base
SCENE_TABLE = (0x59B426, 0x0, 56, 0x250)  # 4105
STILL_LINES = (0x25, range(20, 23))  # 4105: the scene whose lines 20-22 take the voice of the line before
FILLER_OFFICER = 0x4F  # 4105: a line whose record names this officer or above is never drawn (0x59C8D0)
Z_STILL_BASE, Z_NARRATION_BASE = 0x4C4, 0x15A
BRIEFING_VOICES = ((0x548C1D, range(0x28, 0x44), 0x28), (0x548C10, range(0x84, 0x90), 0x68))  # add, stages, entry
# The entries whose lines the runtime looks up with their voice id first (runtime page_voice): the WO1 pages and
# both still events, whose draws read the state their voice starts read. The Z pages (4071) by their text alone.
VOICE_KEYED = (4073, 4103, 4105)


class NarrationVoiceError(ValueError):
    """The executable or a narration entry is not laid out as the stock game's."""


def stack_table(exe: bytes, va: int, first: int, count: int) -> list[int]:
    """The dwords a run of `mov dword [esp + d], imm32` from va stores at esp + first, + 4, ... (count of them)."""
    image = pe.layout(exe)
    at, stored = pe.file_offset(image, va), {}
    while True:
        if exe[at:at + 3] == b"\xc7\x04\x24":
            disp, at = 0, at + 3
        elif exe[at:at + 3] == b"\xc7\x44\x24":
            disp, at = exe[at + 3], at + 4
        elif exe[at:at + 3] == b"\xc7\x84\x24":
            disp, at = struct.unpack_from("<I", exe, at + 3)[0], at + 7
        else:
            break
        stored[disp] = struct.unpack_from("<I", exe, at)[0]
        at += 4
    wanted = [first + 4 * i for i in range(count)]
    if any(d not in stored for d in wanted):
        raise NarrationVoiceError(f"the table stored from {va:#x} lacks entries")
    return [stored[d] for d in wanted]


def container(data: bytes) -> list[bytes]:
    """The entries of a LINKDATA1 container (or an X360 ENS one, big-endian): u32 magic, u32 count, 8 bytes, then
    (offset, size) pairs in 16 bytes."""
    order = ">" if data[:4] == b"\0\x03\x3b\x59" else "<"
    count = struct.unpack_from(f"{order}I", data, 4)[0]
    pairs = struct.unpack_from(f"{order}{2 * count}I", data, 16)
    return [data[16 * o:16 * o + n] for o, n in zip(pairs[0::2], pairs[1::2], strict=True)]


def page_voices(exe: bytes, blocks: list[list[bytes]]) -> list[list[int]]:
    """4073: each page's line voice ids."""
    va, first, count, base = PAGE_TABLE
    table = stack_table(exe, va, first, count)
    if len(blocks) != count:
        raise NarrationVoiceError(f"{len(blocks)} narration pages, the table has {count}")
    return [[base + table[p] + j for j in range(len(block))] for p, block in enumerate(blocks)]


def camp_voices(exe: bytes, data: bytes) -> list[int]:
    """4105: the voice id of each MESC line, by its place in the MESC."""
    va, first, count, base = SCENE_TABLE
    table = stack_table(exe, va, first, count)
    entries = container(data)
    if len(entries) != count + 1 or entries[count][:4] != b"MESC":
        raise NarrationVoiceError("the camp talk container is not 56 scenes and a MESC")
    scene, line = STILL_LINES
    voices = [base + table[s] + j - (s == scene and j in line) for s in range(count) for j in range(entries[s][0])]
    if len(voices) != sum(map(len, mesc_blocks(entries[count]))):
        raise NarrationVoiceError("the camp talk scenes do not cover its MESC")
    return voices


def briefing_voices(exe: bytes) -> dict[int, int]:
    """4190: each entry's voice id, from the stage that shows it."""
    image = pe.layout(exe)
    found = {}
    for at, stages, entry_shift in BRIEFING_VOICES:
        code = exe[pe.file_offset(image, at):pe.file_offset(image, at) + 5]
        if code[0] != 0x05:  # add eax, imm32
            raise NarrationVoiceError(f"{at:#x} is not the briefing voice's add")
        found |= {s - entry_shift: s + struct.unpack_from("<I", code, 1)[0] for s in stages}
    return found


def camp_lines_drawn(data: bytes) -> list[bool]:
    """4105: whether the game ever draws each MESC line, in MESC order: not a filler line, whose scene record names
    an officer of FILLER_OFFICER or above."""
    entries = container(data)
    scenes = entries[:SCENE_TABLE[2]]
    if any(len(scene) < 16 * scene[0] for scene in scenes):
        raise NarrationVoiceError("a camp talk scene holds fewer line records than lines")
    return [scene[16 * j + 1] < FILLER_OFFICER for scene in scenes for j in range(scene[0])]


def line_voices(exe: bytes, entry: int, data: bytes) -> list[int]:
    """The voice id of every MESC line of a narration entry, in MESC order (blocks one after another)."""
    blocks = mesc_blocks(data)
    if entry == 4073:
        return [v for page in page_voices(exe, blocks) for v in page]
    if entry == 4105:
        return camp_voices(exe, data)
    base = {4103: Z_STILL_BASE, 4071: Z_NARRATION_BASE}[entry]
    return [base + k for k in range(sum(map(len, blocks)))]
