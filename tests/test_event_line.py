"""The key an event subtitle is looked up by with its voice (runtime/event_line.h), against the installer's key."""

from __future__ import annotations

import ctypes
import os
import struct
from pathlib import Path

import pytest

from wozmod.formats import pe
from wozmod.formats.audio import WO1_EVENT_VOICES, WO2_EVENT_VOICES
from wozmod.text.stock import event_voice_form


def test_runtime_keys_an_event_line_by_its_voice_exactly_as_the_installer_lists_it(c_library):
    """Bug: the runtime spells the voice key otherwise than the installer's catalog (hex digits, no zero padding, the
    WO1 and WO2 bases swapped, the size field read as the index), so no event line ever finds the row keyed by its
    voice and the shared row shows instead; or a place caption, whose text follows its mask with no index, gets a
    key built from the bytes before it."""
    lib = c_library("event_line", r'''
#include "event_line.h"
const char *key_of(const char *s, unsigned int mode, char *key, unsigned int size)
{ return event_line_key(s, mode, key, size); }
''')
    lib.key_of.restype = ctypes.c_char_p
    lib.key_of.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_char_p, ctypes.c_uint]
    text = b"\x82\xa0line"  # any CP932 text: the key appends to the bytes as they are

    def key(prefix: bytes, mode: int, size: int = 512) -> bytes | None:
        buffer = ctypes.create_string_buffer(prefix + text + b"\0")
        out = ctypes.create_string_buffer(size)
        return lib.key_of(ctypes.cast(ctypes.addressof(buffer) + len(prefix), ctypes.c_char_p), mode, out, size)

    wrong = []
    for mode, (base, _, _), indexes in ((1, WO1_EVENT_VOICES, (0, 36, 438, 0x1FE)), (0, WO2_EVENT_VOICES, (0, 7, 0x14F))):
        for index in indexes:
            record = b"\xff" * 4 + struct.pack("<HH", index, 0x70)  # u32 -1, u16 voice index, u16 size, then the text
            if (found := key(record, mode)) != event_voice_form(text, base + index):
                wrong.append((mode, index, found))
    for mode, index in ((1, 0x1FF), (0, 0x150)):  # past the story's voice block: no voice id
        if (found := key(b"\xff" * 4 + struct.pack("<HH", index, 0x70), mode)) is not None:
            wrong.append((mode, index, found))
    if (found := key(b"\x10\0\0\0" + b"\xff" * 4, 1)) is not None:  # a place caption: the mask right before its text
        wrong.append(("caption", found))
    if (found := key(b"\xff" * 4 + struct.pack("<HH", 36, 0x70), 1, size=len(text) + 6)) is not None:
        wrong.append(("no room", found))
    assert not wrong


def test_event_subtitle_the_runtime_keys_is_the_text_inside_its_line_record() -> None:
    """Bug: the subtitle drawn at 0x49DACE and measured at 0x49DB4C is not the speak command's line record plus 8
    (a copy, or another offset), so the runtime reads its voice index from bytes that are not the record and gives
    event lines another line's English."""
    exe = Path(os.environ.get("WOZMOD_GAME") or "", "OROCHI_Z.exe")
    if not exe.is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    stock = exe.read_bytes()
    image = pe.layout(stock)

    def code(va: int, size: int) -> bytes:
        return stock[pe.file_offset(image, va):pe.file_offset(image, va) + size]

    def call(va: int) -> int:
        assert code(va, 1) == b"\xe8", hex(va)
        return va + 5 + struct.unpack("<i", code(va + 1, 4))[0]

    assert code(0x49E662, 6) == b"\x83\xc7\x08\x89\x7e\x20"  # speak: state+0x20 = record + 8 (edi at the record)
    # the subtitle box measures and draws state+0x20: mov eax,[esi+0x20] ... mov edx,[esi+0x20]
    assert (code(0x49EB1F, 3), code(0x49EB36, 3)) == (b"\x8b\x46\x20", b"\x8b\x56\x20")
    assert (call(0x49EB31), call(0x49EB52)) == (0x49DAF0, 0x49DA50)
    # the measure and the draw inside them return to the sites the runtime keys by (event_line in english_runtime.c)
    assert (call(0x49DB47), call(0x49DAC9)) == (0x54BF80, 0x54B7D0) and (0x49DB47 + 5, 0x49DAC9 + 5) == (0x49DB4C, 0x49DACE)


def test_page_draws_the_runtime_keys_read_the_state_their_voice_start_reads() -> None:
    """Bug: the runtime keys a WO1 narration page's or still event's line (runtime page_voice) by a voice read from
    another state, field or draw site than the game's: the page draw shows page [s+0xB4] line [s+0xB9] and its voice
    start plays the same pair, the WO1 still draw shows scene [s+0x30] line [s+0x35] and its voice start plays the
    same pair, the Z still draw shows text [s+0x30] that the voice start copies with the voice of the same command,
    and voice and text are the same number in every Z command. Otherwise those lines show another line's English."""
    import re

    from wozmod.formats.linkdata import archive

    game = Path(os.environ.get("WOZMOD_GAME") or "")
    if not (game / "OROCHI_Z.exe").is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    stock = (game / "OROCHI_Z.exe").read_bytes()
    image = pe.layout(stock)
    source = (Path(__file__).resolve().parents[1] / "runtime/english_runtime.c").read_text()

    def define(name: str) -> int:
        return int(re.search(rf"^#define {name} .*?(0x[0-9A-Fa-f]+)", source, re.M).group(1), 16)

    def code(va: int, size: int) -> bytes:
        return stock[pe.file_offset(image, va):pe.file_offset(image, va) + size]

    def call(va: int) -> int:
        assert code(va, 1) == b"\xe8", hex(va)
        return va + 5 + struct.unpack("<i", code(va + 1, 4))[0]

    pages = struct.pack("<I", define("NARRATION_STATE"))
    # WO1 page draw: esi = state; movzx eax, byte [esi+0xB9]; ecx = [esi+0xB4]; the draw returns to NARRATION_DRAW
    assert code(0x56106E, 6) == b"\x8b\x35" + pages
    assert code(0x5610DF, 7) + code(0x5610E6, 6) == b"\x0f\xb6\x86\xb9\0\0\0" + b"\x8b\x8e\xb4\0\0\0"
    assert call(0x56110C) == 0x54B7D0 and 0x56110C + 5 == define("NARRATION_DRAW")
    # WO1 page voice start: edi = state; NARRATION_VOICE_START([edi+0xB4]) + byte [edi+0xB9]
    assert code(0x561730, 5) + code(0x561739, 2) == b"\xa1" + pages + b"\x8b\xf8"
    assert code(0x5617AE, 6) + code(0x5617BA, 7) == b"\x8b\x87\xb4\0\0\0" + b"\x0f\xb6\x8f\xb9\0\0\0"
    assert call(0x5617B5) == define("NARRATION_VOICE_START")
    state, z_state = struct.pack("<I", define("STILL_STATE")), struct.pack("<I", define("STILL_Z_STATE"))
    # WO1 draw: esi = state; ebx = [esi+0x30]; ... movzx edx, byte [esi+0x35]; the draw returns to STILL_DRAW
    assert code(0x59C703, 6) == b"\x8b\x35" + state
    assert (code(0x59C784, 3), code(0x59C78C, 4)) == (b"\x8b\x5e\x30", b"\x0f\xb6\x56\x35")
    assert call(0x59C7B8) == 0x54B7D0 and 0x59C7B8 + 5 == define("STILL_DRAW")
    # WO1 voice start: ebp = state; eax = [ebp+0x30]; movzx ecx, byte [ebp+0x35]; call STILL_VOICE_START
    assert code(0x59CA9A, 6) == b"\x8b\x2d" + state
    assert (code(0x59CB8F, 3), code(0x59CB95, 4)) == (b"\x8b\x45\x30", b"\x0f\xb6\x4d\x35")
    assert call(0x59CBA6) == define("STILL_VOICE_START")
    # Z draw: esi = state + 0x28, text index [esi+8]; the draw returns to STILL_Z_DRAW
    assert code(0x59A6EE, 6) == b"\x8b\x35" + z_state and code(0x59A719, 3) == b"\x83\xc6\x28"
    assert code(0x59A75C, 3) == b"\x8b\x76\x08"
    assert call(0x59A780) == 0x54B7D0 and 0x59A780 + 5 == define("STILL_Z_DRAW")
    # Z voice start: [s+0x30] = [s+0x34] (the text the start routine took from [command+0xC]) as [s+0x38] plays
    assert code(0x59AD13, 3) + code(0x59AD19, 3) == b"\x8b\x48\x34" + b"\x89\x48\x30"
    assert code(0x59AEC0, 3) + code(0x59AEDE, 3) == b"\x8b\x47\x0c" + b"\x8b\x47\x08"
    data = archive(game / "LINKDATA1")[4103]
    count = struct.unpack_from("<I", data, 4)[0]
    pairs = struct.unpack_from(f"<{2 * count}I", data, 16)
    commands = [struct.unpack_from("<HHIII", data, 16 * o + k) for o, n in zip(pairs[2::2], pairs[3::2], strict=True)
                for k in range(0, n - 15, 16)]
    texts = [(voice, text) for kind, _, _, voice, text in commands if kind == 2]
    assert len(texts) == 1072 and all(voice == text for voice, text in texts)
