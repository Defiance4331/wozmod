"""The 13 dB trim on every volume the player hears (runtime/sound_headroom.h, english_runtime.HEADROOM)."""

from __future__ import annotations

import ctypes
import os
import re
from pathlib import Path

import pytest

from wozmod.formats import pe
from wozmod.runtime import english_runtime as runtime

KSL = (0x670000, 0x680000)  # the Ksl sound library: it makes and keeps every DirectSound buffer
IID_BUFFER8 = bytes.fromhex("49a425682475824d920f50e36ab3ab1e")  # IID_IDirectSoundBuffer8, as stored
IID_BASIC_AUDIO = bytes.fromhex("b368a856d40ace11b03a0020af0ba770")  # IID_IBasicAudio, as stored (twice)

# Every call in the stock executable that sets a volume the player hears: a DirectSound buffer's SetVolume (vtable
# +0x3C) and the movie graph's IBasicAudio put_Volume (+0x1C). Each must be trimmed.
VOLUME_CALLS = {
    0x67A009: "effect buffer SetVolume (0x679FD0): every sound effect, shout and menu sound",
    0x677EB6: "stream SetVolume (0x677E90): music and dialogue, every change",
    0x678F42: "stream SetVolume (0x678BA0): music and dialogue, the first volume",
    0x5E9D24: "movie put_Volume (0x5E9A90): at its open, 0 or mute",
    0x5E9F81: "movie put_Volume (0x5E9E90): its level",
}
# Every instruction that names an IID_IBasicAudio copy: the volume call its interface reaches, or why none is heard.
BASIC_AUDIO_USERS = {
    0x5EA339: 0x5E9D24,  # 0x5EA330, the open's QueryInterface: one put_Volume, then Release
    0x5E9F5E: 0x5E9F81,  # 0x5E9E90's own QueryInterface: one put_Volume, then Release
    0x66F837: None,  # ksg2::CDX9VideoPlayer (VMR9): made only by 0x66F550, from 0x5D3B70, which nothing calls
    0x698AF5: None,  # DirectShow's CBasicAudio answering QueryInterface: it implements the interface, calls nothing
    0x698B73: None,  # CBasicAudio's GetTypeInfo
    0x698B90: None,  # CBasicAudio's GetIDsOfNames
}


def test_the_trim_lowers_by_13_db_and_never_goes_under_silence(c_library):
    # Bugs: trimming past -10000 (Wine rejects the value, and the buffer keeps its old, louder volume); trimming
    # silence itself; adding the trim instead of subtracting it.
    lib = c_library("sound_headroom", '#include "sound_headroom.h"\nlong trim(long mb) { return headroom_trim(mb); }\n')
    lib.trim.argtypes, lib.trim.restype = [ctypes.c_long], ctypes.c_long
    expected = {0: -1300, -1300: -2600, -8700: -10000, -9999: -10000, -10000: -10000}
    assert {mb: lib.trim(mb) for mb in expected} == expected


def test_every_volume_the_player_hears_goes_through_the_trim():
    # Bugs: a volume call with no headroom hook (a movie left at full level); a hook placed after the volume is
    # pushed, or naming a register other than the one pushed, so DirectSound gets the stock level; a SetVolume call
    # in the sound library, a DirectSound buffer made outside it, or an IBasicAudio user that the tables miss.
    capstone = pytest.importorskip("capstone")
    exe = Path(os.environ.get("WOZMOD_GAME") or "", "OROCHI_Z.exe")
    if not exe.is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    image = exe.read_bytes()
    layout = pe.layout(image)
    x86 = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    x86.detail = x86.skipdata = True

    def code(start: int, end: int) -> list:
        at = pe.file_offset(layout, start)
        return list(x86.disasm(image[at:at + end - start], start))

    def written(insn) -> set[str]:
        return {x86.reg_name(r) for r in insn.regs_access()[1]} if insn.id else set()

    def volume_reaches(start: int, word: str, call: int) -> bool:
        """Whether the volume, in frame word ``word`` at ``start``, is the call's argument after the interface:
        followed through pushes and register writes, in a straight run up to the call."""
        held, stack = (set(), ["volume"]) if word == "ret" else ({word}, [])
        for insn in code(start, call + 8):
            if insn.address == call:
                return stack[-2:-1] == ["volume"]
            if insn.mnemonic == "call" or insn.group(capstone.CS_GRP_JUMP) or insn.group(capstone.CS_GRP_RET):
                return False
            if insn.mnemonic == "push":
                stack.append("volume" if insn.op_str in held else "other")
            elif "esp" in written(insn):
                return False
            held -= written(insn)
        return False

    wrong = [("not trimmed", hex(call), what) for call, what in VOLUME_CALLS.items()
             if not any(0 < call - va <= 16 and volume_reaches(va, word, call) for va, _, _, word in runtime.HEADROOM)]
    slot = {}  # register -> it holds a vtable's +0x3C entry
    for insn in code(*KSL):
        if insn.mnemonic == "call":
            if (insn.op_str in slot or insn.op_str.endswith(" + 0x3c]")) and insn.address not in VOLUME_CALLS:
                wrong.append(("SetVolume not in the table", hex(insn.address)))
            slot.clear()
            continue
        slot = {r for r in slot if r not in written(insn)}
        if load := re.fullmatch(r"mov (e\w\w), dword ptr \[e\w\w \+ 0x3c\]", f"{insn.mnemonic} {insn.op_str}"):
            slot.add(load[1])
    text = next(s for s in layout["sections"] if s["name"] == b".text")
    base = layout["image_base"] + text["rva"]
    body = image[text["raw"]:text["raw"] + text["raw_size"]]
    for iid in (IID_BUFFER8, IID_BASIC_AUDIO):
        for copy in (m.start() for m in re.finditer(re.escape(iid), image)):
            section = next(s for s in layout["sections"] if s["raw"] <= copy < s["raw"] + s["raw_size"])
            va = layout["image_base"] + section["rva"] + copy - section["raw"]
            for ref in (base + m.start() for m in re.finditer(re.escape(va.to_bytes(4, "little")), body)):
                user = next((a for a in (ref - 1, ref - 4) if [i.address + i.size for i in code(a, ref + 4)][:1]
                             == [ref + 4]), ref)  # push imm32, or mov dword ptr [esp + 8], imm32
                if iid == IID_BUFFER8 and not KSL[0] <= user < KSL[1]:
                    wrong.append(("DirectSound buffer made outside the sound library", hex(user)))
                if iid == IID_BASIC_AUDIO and user not in BASIC_AUDIO_USERS:
                    wrong.append(("IBasicAudio user not in the table", hex(user)))
    assert wrong == []
