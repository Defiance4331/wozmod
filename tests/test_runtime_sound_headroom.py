"""The 13 dB trim on every volume the player hears, 3 dB less on dialogue (runtime/sound_headroom.h,
english_runtime.HEADROOM)."""

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


GLUE = r'''
#include "sound_headroom.h"
static struct dialogue_level d;
long trim(long mb, long raise) { return headroom_trim(mb, raise); }
void load(const unsigned char *vm, unsigned long record, int gain) { dialogue_load(&d, vm, record, gain); }
void start(unsigned long base, unsigned long count, unsigned long k) { dialogue_start(&d, base, count, k); }
long volume(unsigned long player, long mb) { return headroom_trim(mb, dialogue_raise(&d, player)); }
'''
VOICE = -269  # the volume the game gives a dialogue stream at the Voice option's ceiling (0x2EEE of 0x3FFF)


@pytest.fixture
def level(c_library):
    lib = c_library("sound_headroom", GLUE)
    lib.trim.argtypes, lib.trim.restype = [ctypes.c_long, ctypes.c_long], ctypes.c_long
    lib.load.argtypes = [ctypes.c_char_p, ctypes.c_ulong, ctypes.c_int]
    lib.start.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    lib.volume.argtypes, lib.volume.restype = [ctypes.c_ulong, ctypes.c_long], ctypes.c_long
    channels = ctypes.create_string_buffer(4 * 0x588)  # the channel streams' array, four elements
    engine = ctypes.create_string_buffer(0x5A8)  # the engine, its own stream (the music) at +8
    vm = ctypes.create_string_buffer(0xC0)  # the voice manager, its two clip buffers from +0xC
    lib.memory = (ctypes.addressof(channels), ctypes.addressof(engine), vm)
    lib.keep = (channels, engine)
    return lib


def player(stream: int) -> int:
    return stream + 0x390


def test_the_trim_lowers_by_13_db_less_its_raise_and_never_leaves_the_volume_range(level):
    # Bugs: trimming past -10000 (Wine rejects the value, and the buffer keeps its old, louder volume); a raise that
    # makes a muted stream (-10000) audible; a raised volume over 0, which DirectSound rejects; adding the trim instead
    # of subtracting it.
    expected = {(0, 0): -1300, (-1300, 0): -2600, (-8700, 0): -10000, (-9999, 0): -10000, (-10000, 0): -10000,
                (VOICE, 300): -1269, (VOICE, 570): -999, (-10000, 570): -10000, (-10000, 1500): -10000, (-9000, 570): -9730, (0, 1500): 0}
    assert {key: level.trim(*key) for key in expected} == expected


def test_each_dialogue_stream_and_no_music_stream_plays_3_db_over_the_trim(level):
    # Bugs: the music stream raised with the dialogue (a test on the volume value or the hook, not the stream); a
    # channel stream missed (the array read at the wrong offset, or its stride wrong); a pointer inside an element or
    # past the array taken for a stream; a raise before the voice frame has named the channel streams.
    channels, engine, _ = level.memory
    streams = {f"channel {n}": channels + 0x588 * n + 8 for n in range(4)}
    streams |= {"music": engine + 8, "inside channel 1": channels + 0x588 + 8 + 4, "past the array": channels + 4 * 0x588 + 8}
    before = {name: level.volume(player(stream), VOICE) for name, stream in streams.items()}
    level.start(channels, 4, 0)
    after = {name: level.volume(player(stream), VOICE) for name, stream in streams.items()}
    assert (before, after) == (dict.fromkeys(streams, VOICE - 1300), {
        "channel 0": VOICE - 1000, "channel 1": VOICE - 1000, "channel 2": VOICE - 1000, "channel 3": VOICE - 1000,
        "music": VOICE - 1300, "inside channel 1": VOICE - 1300, "past the array": VOICE - 1300})


def test_a_clip_gain_holds_on_its_stream_from_its_start_to_the_next(level):
    # Bugs: the gain read from the buffer at each volume, so a fade-out takes the next clip's gain once that clip's load
    # lands; buffer k's gain on the other stream; a load whose record is not one of the two buffers taken as one.
    channels, _, vm = level.memory
    base = ctypes.addressof(vm)
    stream = [player(channels + 0x588 * n + 8) for n in (0, 1)]
    seen = []
    def heard():
        seen.append(tuple(level.volume(p, VOICE) for p in stream))
    level.load(vm, base + 0xC, 270), level.load(vm, base + 0x4C, 0)  # a PSP clip into buffer 0, an Xbox 360 one into 1
    level.start(channels, 4, 0), heard()
    level.start(channels, 4, 1), heard()
    level.load(vm, base + 0xC, 0), heard()  # the next clip's load into buffer 0 while its stream fades out
    level.load(vm, base + 0x10, 500), level.start(channels, 4, 0), heard()  # not a buffer: ignored
    assert seen == [(VOICE - 730, VOICE - 1000), (VOICE - 730, VOICE - 1000), (VOICE - 730, VOICE - 1000),
                    (VOICE - 1000, VOICE - 1000)]


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


HEADER = (Path(__file__).parents[1] / "runtime/sound_headroom.h").read_text(encoding="utf-8")
KSL_OFFSETS = {name: int(value, 0) for name, value in re.findall(r"#define (KSL_\w+) (0x[0-9a-fA-F]+|\d+)u", HEADER)}


def _n(value: int) -> str:
    """A number as capstone prints it."""
    return str(value) if value < 10 else hex(value)


def stream_kind_facts() -> dict[int, str]:
    """What sound_headroom.h's test of a stream's kind reads, as the stock code must hold it: (address, instruction),
    with the header's own offsets."""
    channels = 4  # the channel object's streams, at +4 (0x677AE0's this)
    return {
        0x672BE9: "lea ecx, [esi + 8]",  # the engine makes its own stream at +8 (0x672BE0) ...
        0x672BEE: "call 0x675070",  # ... with the stream class's constructor
        0x67462C: f"lea ecx, [esi + {_n(channels)}]",  # the channel object makes its streams (0x677AE0) ...
        0x674630: "call 0x677ae0",
        0x677B38: f"mov dword ptr [esi + {_n(KSL_OFFSETS['KSL_CHANNELS_COUNT'] - channels)}], eax",  # ... so many ...
        0x677B8D: f"imul edi, edi, {_n(KSL_OFFSETS['KSL_CHANNEL_BYTES'])}",  # ... element n at the array + n x its size ...
        0x677B93: f"add edi, dword ptr [esi + {_n(KSL_OFFSETS['KSL_CHANNELS_ARRAY'] - channels)}]",
        0x677BA7: f"lea ecx, [edi + {_n(KSL_OFFSETS['KSL_CHANNEL_STREAM'])}]",  # ... its stream inside it
        0x677BAC: "call 0x675070",
        0x677E92: "mov esi, ecx",  # set_ksl_stream_volume: its player in esi (hook 141) ...
        0x678BB6: "mov esi, ecx",  # start_ksl_stream_buffer: the same (hook 142)
        0x5D8597: "mov edi, ecx",  # the voice frame: the voice manager in edi ...
        0x5D86AF: "xor ebx, ebx",  # ... its buffer in ebx, from 0 ...
        0x5D872A: "mov ecx, ebx",  # ... the stream mask 1 << ebx (hook 161)
        0x5D8731: "shl eax, cl",
        0x5D87CC: "mov ecx, dword ptr [edi + 4]",  # the channel starters' this: [manager + 4] ...
        0x5D87F7: "mov ecx, dword ptr [edi + 4]",
        0x5D8A24: "mov ecx, dword ptr [esi + 0xc]",  # ... whose +0xC is the channel object
        0x5D8AA4: "mov ecx, dword ptr [esi + 0xc]",
        0x5D81E5: "mov edi, eax",  # the voice load: the buffer it loads in edi at the offset query (hook 36) ...
        0x5D81E7: "call 0x5d7ce0",
        0x5D740C: "lea eax, [eax + ecx + 0xc]",  # ... manager + 0xC + 0x40 x buffer (0x5D7400)
    }


# Each call that starts a channel stream, or makes a stream: the only callers (direct calls in .text).
ONLY_CALLERS = {0x670870: {0x5D8A2F}, 0x670880: {0x5D8AAF}, 0x5D8A00: {0x5D87FB}, 0x5D8A80: {0x5D87D0},
                0x675070: {0x672BEE, 0x677BAC}, 0x677AE0: {0x674630}}


def test_every_stream_volume_call_names_its_stream_where_the_kind_test_reads_it():
    # Bugs: a caller of the stream volume (a fade step, a resume) passing another object than stream + 0x390, so that
    # stream's volume misses the dialogue raise or takes it wrongly; a register at a hook that no longer holds the player;
    # a channel stream made or started somewhere the kind test does not know; the array's layout read wrong.
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

    def text(insn) -> str:
        return f"{insn.mnemonic} {insn.op_str}"

    def written(insn) -> set[str]:
        return {x86.reg_name(r) for r in insn.regs_access()[1]} if insn.id else set()

    wrong = [(hex(va), want, text(code(va, va + 16)[0])) for va, want in stream_kind_facts().items()
             if text(code(va, va + 16)[0]) != want]
    section = next(s for s in layout["sections"] if s["name"] == b".text")
    base = layout["image_base"] + section["rva"]
    body = image[section["raw"]:section["raw"] + section["raw_size"]]
    calls = {}
    for at in (m.start() for m in re.finditer(b"\xe8", body)):
        target = (base + at + 5 + int.from_bytes(body[at + 1:at + 5], "little", signed=True)) & 0xFFFFFFFF
        if target in ONLY_CALLERS or target in (0x677E90, 0x679030):
            insn = code(base + at, base + at + 5)
            if insn and insn[0].size == 5 and text(insn[0]) == f"call {target:#x}":
                calls.setdefault(target, set()).add(base + at)
    wrong += [("callers", hex(target), sorted(map(hex, calls.get(target, ())))) for target, want in ONLY_CALLERS.items()
              if calls.get(target, set()) != want]
    # Every stream volume call (0x677E90) and stream buffer start (0x679030, to 0x678BA0): this = stream + 0x390,
    # with the stream the function's own this, in esi (the register it hands 0x674E40).
    for call in sorted(calls.get(0x677E90, set()) | calls.get(0x679030, set())):
        held = {}  # register -> what it holds, in a straight run back from the call to the function's start
        run = next(r for r in (code(call - back, call) for back in range(0x180, 0, -1))
                   if r and r[-1].address + r[-1].size == call)
        for insn in run:
            if text(insn) in (f"lea ecx, [esi + {_n(KSL_OFFSETS['KSL_PLAYER'])}]", f"lea edi, [esi + {_n(KSL_OFFSETS['KSL_PLAYER'])}]"):
                held[insn.op_str.split(",")[0]] = "player"
            elif text(insn) == "mov ecx, edi" and held.get("edi") == "player":
                held["ecx"] = "player"
            else:
                for r in written(insn) | ({"eax", "ecx", "edx"} if insn.mnemonic == "call" else set()):
                    held.pop(r, None)
        if held.get("ecx") != "player":
            wrong.append(("this is not stream + 0x390", hex(call)))
    # Both hook sites: esi, set from this at the function's start, unchanged up to the hook but on the exits before it.
    for start, hook in ((0x677E92, 0x677EB1), (0x678BB6, 0x678F3D)):
        run = code(start + 2, hook)
        if any("esi" in written(insn) and not (text(insn) == "pop esi" and any(
                later.mnemonic == "ret" for later in run[i:i + 7])) for i, insn in enumerate(run)):
            wrong.append(("esi rewritten before the hook", hex(hook)))
    assert wrong == []
