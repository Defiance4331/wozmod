"""The voice id the stock game plays with each camp-talk and narration line (wozmod.text.narration_voices), on
synthetic executables and containers, and the runtime's half of the camp-talk voice key against it."""

from __future__ import annotations

import struct

from synthetic import mesc, pe_image

from wozmod.text.narration_voices import PAGE_TABLE, SCENE_TABLE, camp_voices, page_voices

TEXT_RVA, TEXT_RAW = 0x159000, 0x400  # VAs 0x559000 to 0x5A9000 are file-backed: both tables live there


def _exe(*tables: tuple[int, list[int], int]) -> bytes:
    """A PE whose code at each va stores the values on its stack as the stock function does: `mov dword [esp + d],
    imm32` with d = first + 4i, in the one-byte form (esp only, or disp8) while d fits and the disp32 form after."""
    image = pe_image(TEXT_RAW + 0x50000, (b".text", 0x50000, TEXT_RVA, 0x50000, TEXT_RAW))
    for va, values, first in tables:
        at = va - 0x400000 - TEXT_RVA + TEXT_RAW
        code = b""
        for i, value in enumerate(values):
            d = first + 4 * i
            code += (b"\xc7\x04\x24" if d == 0 else b"\xc7\x44\x24" + bytes([d]) if d < 0x80
                     else b"\xc7\x84\x24" + struct.pack("<I", d)) + struct.pack("<I", value)
        image[at:at + len(code) + 1] = code + b"\xe8"  # then a call: the run of stores ends
    return bytes(image)


def _container(*entries: bytes) -> bytes:
    """A LINKDATA1 container: magic, count, 8 bytes, (offset, size) pairs in 16-byte units, each entry 16-aligned."""
    head = 16 + 8 * len(entries)
    at, pairs, body = -(-head // 16), [], b""
    for entry in entries:
        pairs += [at + len(body) // 16, len(entry)]
        body += entry + bytes(-len(entry) % 16)
    return (struct.pack(f"<4I{2 * len(entries)}I", 0x33B59, len(entries), 0, 0, *pairs) + bytes(at * 16 - head)
            + body)


def _camp_talk() -> tuple[bytes, bytes]:
    """56 scenes, scene s starting at voice offset 10s: one line each, but scene 0x25 with 23 lines."""
    counts = [23 if s == 0x25 else 1 for s in range(56)]
    scenes = [bytes([n]) + bytes(15) for n in counts]
    return _exe((SCENE_TABLE[0], [10 * s for s in range(56)], SCENE_TABLE[1])), \
        _container(*scenes, mesc(*(b"x" for _ in range(sum(counts)))))


def test_camp_talk_lines_play_their_scene_table_voice_and_scene_37_repeats_line_19():
    # Bugs: the table's entries past the disp8 range (stored with a 32-bit displacement in the stock code) are
    # dropped or read from the wrong store, so late scenes take another scene's voices; the scene 0x25 shift is
    # applied by MESC position instead of line in scene, to the wrong lines (20-22 only), or not at all. Expected
    # values by hand from the voice start 0x59CB8F-0x59CBAE: 0x250 + table[scene] + line, less one for lines 20-22
    # of scene 0x25.
    voices = camp_voices(*_camp_talk())
    assert voices[:2] == [0x250, 0x250 + 10]
    scene_37 = 0x25  # MESC position of its line 0: one line per scene before it
    assert voices[scene_37 + 18:scene_37 + 23] == [0x250 + 370 + 18, 0x250 + 389, 0x250 + 389, 0x250 + 390,
                                                   0x250 + 391]
    assert voices[scene_37 + 23] == 0x250 + 380  # scene 0x26 line 0
    assert voices[-1] == 0x250 + 550  # scene 55: its table entry is stored with a 32-bit displacement
    assert len(voices) == 56 + 22


def test_narration_pages_play_the_page_table_voice_not_their_file_order():
    # Bug: 4073's pages are joined to their voices in file order (the old position join, wrong on all 60 pages), or
    # the table is read from esp + 0 where the stock function keeps it at esp + 0xC. Expected by hand from 0x560600:
    # page p line j plays 0x60 + table[p] + j.
    table = [5 * (59 - p) for p in range(60)]  # the pages play in reverse file order
    exe = _exe((PAGE_TABLE[0], [0xDEAD] * 3 + table, PAGE_TABLE[1] - 12))  # three other stores come first
    voices = page_voices(exe, [[b"a", b"b"]] + [[b"c"]] * 58 + [[b"d", b"e", b"f"]])
    assert voices[0] == [0x60 + 295, 0x60 + 296]
    assert voices[59] == [0x60, 0x61, 0x62]


def test_runtime_shifts_the_same_camp_talk_lines_as_the_installer_keys(c_library):
    # Bug: runtime/event_line.h's still_voice_line and narration_voices disagree on which camp-talk lines take the
    # previous line's voice (another scene, or 19-21), so the rows the installer keys by voice for those lines are
    # never found and the shared row's English shows. Oracle: camp_voices on the synthetic scenes above.
    lib = c_library("still_line", '#include "event_line.h"\n'
                    "unsigned int line_of(unsigned int s, unsigned int j) { return still_voice_line(s, j); }\n")
    voices = camp_voices(*_camp_talk())
    places = [(s, j) for s in range(56) for j in range(23 if s == 0x25 else 1)]
    wrong = [(s, j) for (s, j), voice in zip(places, voices, strict=True)
             if 0x250 + 10 * s + lib.line_of(s, j) != voice]
    assert not wrong
