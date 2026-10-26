"""A stage line with a voice slot plays the slot, and only that line's speak (runtime/voice_slots.h, read from the
installer's voice/Slots.bin)."""

from __future__ import annotations

import ctypes

import pytest

from wozmod.text.stock import stage_line_form
from wozmod.voices import VoiceSlot, slot_table


@pytest.fixture(scope="module")
def runtime(c_library):
    lib = c_library("voice_slots", r'''
#include "voice_slots.h"
unsigned int slot(const unsigned char *table, unsigned int size, unsigned int stage, unsigned int line,
                  unsigned int voice)
{
    return voice_slot(table, size, stage, line, voice);
}
char *key(const char *s, unsigned int stage, unsigned int line, char *out, unsigned int size)
{
    return stage_line_key(s, stage, line, out, size);
}
''')
    lib.slot.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
    lib.key.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_char_p, ctypes.c_uint]
    lib.key.restype = ctypes.c_char_p
    return lib


def test_only_the_slotted_line_plays_its_slot_and_its_partner_keeps_the_shared_id(runtime) -> None:
    # Bugs: a line keyed to its partner's slot (the line not compared), so both lines play one take; the partner
    # losing its clip to the slot; a slot taken by the same line number on another stage; a speak's other voice id
    # (the other bank flag) left on the shared take; a row read off by one (stride or field order), so every row
    # after the first misses; another voice the game loads after the speak swapped too. Rows as the installer writes
    # them (voices.maps.slot_table), read as the runtime reads them. Expected by hand from the slots below.
    slots = {(1, 12): VoiceSlot(22665, (12473,), "wo2-x360", 1710),
             (96, 84): VoiceSlot(22666, (3800, 14663), "wo1-x360", 7361),
             (113, 8): VoiceSlot(22667, (3195,), "wo1-x360", 1526)}
    table = slot_table(slots)
    cases = {  # (stage, line, the id its speak plays): the id the runtime plays
        (1, 12, 12473): 22665, (1, 11, 12473): 12473, (2, 12, 12473): 12473, (1, 12, 12474): 12474,
        (96, 84, 3800): 22666, (96, 84, 14663): 22666, (33, 50, 14663): 14663, (96, 85, 14663): 14663,
        (113, 8, 3195): 22667, (73, 8, 3195): 3195, (8, 113, 3195): 3195,
        (0xFFFFFFFF, 0xFFFFFFFF, 3195): 3195,  # no speak since the last load
    }
    got = {case: runtime.slot(table, len(table), *case) for case in cases}
    assert got == cases


def test_the_runtime_keys_a_spoken_line_as_the_rows_are_keyed(runtime) -> None:
    # Bugs: the runtime builds another key than stock.stage_line_form (padded digits, stage and line swapped, no
    # separator), so no line's own row is ever found and it shows its partner's line.
    text = b"Fixture@0066"
    got = {}
    for stage, line in ((1, 12), (12, 1), (0, 0), (113, 8), (65535, 65535)):
        out = ctypes.create_string_buffer(64)
        got[stage, line] = runtime.key(text, stage, line, out, len(out))
    assert got == {line: stage_line_form(text, *line) for line in got}
