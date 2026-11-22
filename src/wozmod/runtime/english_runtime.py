"""Compile and validate the proxy runtime."""

from __future__ import annotations

from itertools import pairwise
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from wozmod.formats import pe

from .guards import blob, guard_list
from .latin_font import LATIN_GLYPH
from .layout import C_SOURCE, SOURCE
from .prompts import PROMPT_SLOTS, STICK_ITEMS
from .text_boxes import c_header as text_box_header
from .text_lanes import c_header as text_lane_header
from .text_room import ROOMS, WIDE

COMPILER = "i686-w64-mingw32-gcc"
DLL_BASE = 0x68000000  # the harness finds a --test build's mailbox in the image at this base
WIDTH_POINTER = 0x79BAC0  # per-glyph advance table; stock 0 means every glyph is 24 wide
# Text edits and the two alternative settings-row groups.
ENGLISH, ONE_ROW, TWO_ROWS = 0, 1, 2

# Entry VA, prologue, hook id and replacing-return stack size; English mode only.
HOOKS = [
    (0x54B7D0, "83ec34 8b442448", 1, 0),        # draw text
    (0x54BF80, "515556 8b742410", 2, 0),        # measure text
    (0x54C150, "83ec08 0fb60d0caec200", 3, None),  # wrap text (source)
    (0x54BBB0, "83ec38 5556", 4, 0),            # draw cinematic caption line
    (0x56A23F, "8918 895804 885820", 44, None),  # cinematic cue read, before its split at LF (mid-function)
    (0x69D87C, "558bec 83ec20", 5, None),       # sprintf format
    (0x54E780, "56 8b742408", 6, None),         # window set text
    (0x54E1D0, "81ec0c010000", 7, None),        # message box set text
    (0x4F4B50, "81ec04010000", 9, None),        # script line parse (dialog and battle quotes)
    (0x5D0FE0, "8b442404 8b54240c", 8, None),   # archive read (thiscall)
    (0x55DE3A, "83c404 bab8f1c200", 10, None),  # prompt bar text fetched (mid-function)
    (0x55DE84, "891514f2c200", 11, None),       # prompt bar width store (mid-function)
    (0x58CDF0, "8b442404 56", 12, 0),           # compose officer name
    (0x54C490, "83ec4c 8b442458", 13, 0),       # name plate glyph quads
    (0x58CE90, "8b442404 56", 14, 0),           # compose katakana reading
    (0x54D140, "51 56 8b74240c", 15, None),     # message box text (wrapped in place)
    (0x551440, "51 56 8b74240c", 16, None),     # notice box text (wrapped in place)
    (0x54CD30, "83ec34 53 55", 17, 0),          # draw officer name
    (0x58C580, "56 57 8b7c240c", 18, 0),        # name glyph count
    (0x577450, "81ec9c000000", 32, 0),        # overhead officer name (eax, ecx, edx in registers)
    (0x47D060, "8b442404 8b0485f0e46d00", 19, 0),  # message table entry
    (0x549FB0, "56 8b742408", 20, None),        # glyph count
    (0x54F7A5, "0fb705b8b8c200", 26, None),     # battle dialog, after its plates are queued (mid-function)
    (0x54FC5A, "8bfe 8d9685000000", 33, None),  # banner x, rest 913 (mid-function)
    (0x54FCD0, "8bfe 8d5646", 34, None),        # banner x, rest 986 (mid-function)
    (0x54FD40, "8bfe 8d5650", 35, None),        # banner x, rest 1077 (mid-function)
    (0x590D40, "83ec20 53 8b5c2428", 42, None),  # box: text inside wraps to it, the box grows to fit
    (0x5949E0, "83ec1c 837c243800", 43, None),  # framed box, the same
    (0x593CE0, "83ec48 a1e4057c00", 108, 0),  # skill tag: a wider blank plate and its name in the game font
    (0x58CE50, "8b442408 56", 111, 0),  # officer name by cast id, for a caller listed in text_room
    (0x591430, "51535556 8b742420", 66, None),  # battle label and its plate: width from the vector sheet
    (0x5915C0, "535556 8b74241c", 67, None),    # the alternate battle label plate
    (0x5DCA30, "8b442408 8b4c2404", 68, None),  # two-corner glyph finalizer
    (0x5DCCF0, "8b442408 8b4c2404", 69, None),  # four-corner glyph finalizer
]
# runtime/sound_headroom.h: a hook just before each call that sets a volume the player hears, and the struct frame
# word that holds the volume at the hook (ret: the word at [esp], one already pushed). Ids are consecutive: the C
# indexes by them.
HEADROOM = [
    (0x67A004, "50 8b423c 51", 140, "eax"),      # an effect buffer's SetVolume at 0x67A009 (0x679FD0)
    (0x677EB1, "52 50 8b413c", 141, "edx"),      # a stream's SetVolume at 0x677EB6 (0x677E90): music, dialogue
    (0x678F3D, "8b523c 51 50", 142, "ecx"),      # a stream's first SetVolume at 0x678F42 (0x678BA0)
    (0x5E9D23, "50 ffd2 8d4c2440", 143, "ret"),  # a movie's put_Volume at 0x5E9D24, at its open (0x5E9A90)
    (0x5E9F7C, "8b511c 56 50", 144, "esi"),      # a movie's put_Volume at 0x5E9F81, at its level (0x5E9E90)
]
# runtime/volume_curve.h: a hook where each site that turns a volume option into a gain holds the stock gain in edx
# (after its divide by 15, before the sign fix), and the option byte whose curve it takes. Ids are consecutive: the C
# indexes by them.
BGM, SE, VOICE = 0x80151B, 0x80151C, 0x80151D  # the options, levels 0-15, in the game state block (saved)
VOLUME = [
    (0x5141E7, "8bca c1e91f", 150, BGM),    # set_bgm_volume 0x5141D0 (level, fade)
    (0x514217, "8bc2 c1e81f", 151, BGM),    # fade_bgm_level 0x514200 (level), a 15-unit fade
    (0x513087, "8bc2 c1e81f", 152, SE),     # set_se_level 0x513070 (level)
    (0x512D57, "8bc2 c1e81f", 153, VOICE),  # set_voice_level 0x512D40 (level)
    (0x5138AD, "8bc2 c1e81f", 154, SE),     # init_se_channels 0x513880: the SE option itself
    (0x513A31, "8bf2 c1ee1f", 155, SE),     # set_se_channels_muted 0x513A10, unmute: the SE option itself
]
# runtime/stage_duck.h: a stage line's duck, from its start to its window's end (a close, a release, a decided battle).
STAGE_DUCK_HOOKS = [
    (0x4F4FD5, "0fb6051b158000", 156),  # start_stage_line_voice 0x4F4F80: the line loaded (edi), its setters next
    (0x4F5146, "201d40969c00", 157),    # open_stage_dialog_window 0x4F5090: a window released with no restore
    (0x4ED6D0, "f60540969c0020", 158),  # run_quote_box_queue: the tick's window commands (0x4EECC0) are done
    (0x4F5108, "201d40969c00", 159),    # open_stage_dialog_window: the spoken window closes, its restore next
    (0x4EF4FC, "a1b81a8000", 160),      # run_stage_script_tick 0x4EF4F0 in mode 0x2000000: is the battle decided?
]
FRAME_WORDS = re.search(r"struct frame \{\s*u32 ([^;]*);", SOURCE)[1].replace(" ", "").split(",")  # C, in order
# Installed in both text modes.
ALWAYS_HOOKS = [
    (0x5C9FD0, "83ec1c 53 55", 24, None),       # message pump: table reload, test mailbox
    (0x5ABE26, "83c40c 6828000200", 27, None),  # save.dat loaded into the game state (mid-function)
    (0x56B586, "6bf62a 83c420", 28, None),      # Game Settings row, its plate drawn (mid-function)
    (0x56B7C9, "83c408 393de0a12001", 29, None),  # Game Settings input read (mid-function)
    (0x5D1A30, "6aff 68c4ba6b00", 30, 0x14),    # archive read (thiscall): loose entries
    (0x5D06D0, "8b442404 8bd1", 31, 4),         # archive entry size (thiscall)
    (0x5146FF, "d90538e57000", 105, None),      # movie path made, "./movie/movieNNN.wmv" at [esp] (mid-function)
    (0x4F17E0, "0fb74602 57", 118, None),       # stage speak command (esi at word 1): its line, for the voice slots
    (0x5D7CE0, "53 8b5c2408", 36, 8),           # voice offset (thiscall): English voices
    (0x5D7D30, "8b442404 85c0", 37, 8),         # voice size (thiscall)
    (0x4A1710, "8b442404 f60010", 71, None),    # lip block handed to a slot (cdecl: state, slot, block)
    (0x5D7BF0, "8b542404 83fa01 56", 109, None),  # voice play (thiscall): kept while its clip waits to load
    (0x5D8590, "83ec44 53 55 56 57", 110, None),  # voice frame (thiscall): a kept play made after its load
    (0x513720, "51 6a00 e8b8630c00", 135, 0),     # voice start (cdecl): waits for a busy reader; always replaced
    (0x5D82A0, "8b0d0404e000", 136, None),       # voice stop: what waits to play is dropped
    (0x5D5990, "8b442404 83f87d", 38, 4),       # SE bank header offset (thiscall): English attack voices
    (0x5D59C0, "8b442404 83f87d", 39, 4),       # SE bank header size (thiscall)
    (0x5D59F0, "8b542404 83fa7d", 40, 4),       # SE bank data offset (thiscall)
    (0x5D5A20, "8b442404 83f87d", 41, 4),       # SE bank data size (thiscall)
    (0x5D5E30, "51 55 56 8be9 57", 119, 0),     # sound-effect poll (ecx the manager): version 1.01's, se_cutoff.h
    (0x428E33, "d9ee d99d20020000", 45, None),  # battle camera FOV stored (mid-function)
    (0x428FB4, "8d442438 d9ee", 46, None),      # 40-degree battle camera FOV stored (mid-function)
    (0x5E4F64, "8b8660010000", 47, None),       # joystick state read (mid-function)
    (0x5E38E0, "81ec40020000", 70, 0),          # input map load (cdecl): Xbox names before its auto-setting
    (0x5E3C82, "8b8c244c020000", 120, None),    # input map load, after its fclose: put a cleared map back on the file's joy
    (0x5E3891, "b801000000", 113, None),        # pad auto-setting's success exit: PlayStation buttons (mid-function)
    (0x5E4300, "6aff 686bbd6b00 64a100000000", 121, None),  # a player's input map array is built again: forget the parked actions
    (0x5E2290, "81ec14010000", 122, None),     # input map writer, player 0: a parked player is written on the pad they had
    (0x5AC845, "83c408 833da840c80000", 123, None),  # after the save job's two input map writes: the park goes back
    (0x56FDC0, "e82b5a0700", 106, 0),           # Controller page, Key Config chosen: the pad auto-setting offer; always replaced
    (0x58E2BC, "5f5eb801000000", 48, None),     # 4079 prompt handles made (tail)
    (0x5ADC60, "8b442404 69c0f0000000", 49, 0),  # menu word, pressed (cdecl): A/B for a pad
    (0x5ADCA0, "8b442404 69c0f0000000", 50, 0),  # menu word, repeating (cdecl)
    (0x5E5940, "53555657 6a1e", 51, 0),          # a binding's name (cdecl): Xbox names for a pad
    (0x56FC91, "6a06 6a18 6a18", 52, None),      # Key Config waiting for input, drawn (mid-function)
    (0x5933B0, "8b0c8568ec7900", 65, 0),         # Move List key (eax, edx, ebx in registers): Xbox art for a pad
    # Frame-rate hooks.
    (0x4AF960, "51 d9442408 56", 115, None),   # battle tick (cdecl: dt): the true frame rate's 60 Hz phase
    (0x4AFB70, "51 d9442408 53", 116, None),   # the battle's tick after its end (cdecl: dt): the same
    (0x499AC0, "a170a38900", 117, None),       # an event scene's tick (cdecl: dt): the same
    # The battle scene's states that step by the frame float and run no battle tick (0x4B0600, table 0x4B0858): a
    # tick of the same clock each.
    (0x50FEB0, "a130d4bf00", 124, None),       # state 2, the Musou view's step (cdecl: dt)
    (0x4FA790, "a160ad9c00", 125, None),       # state 4, a cut-in's step (cdecl: dt)
    (0x4B2850, "d9057cad8900", 126, None),     # state 7, the step back from an event scene (cdecl: dt)
    (0x4B0740, "837c240800", 127, None),       # state 6 past its step 0x4B2730, [esp+8] 0: no battle tick (mid)
    (0x5DDAB0, "8b0d08a24001", 21, None),       # draw quad: hi-res sheets
                                                #   back past the beams (ENRT_LOG/ENRT_TEST: every quad logged)
    (0x5DE620, "8b442408 8b4c2404", 22, None),  # draw quad, second builder, the same
    # Display sizes and screen shapes: the size list in place of the stock index.
    (0x475CE0, "0fb6052d158000", 75, 0),       # resolution index
    (0x475CF0, "8a442404 a22d158000", 76, 0),  # set the resolution index (cdecl)
    (0x474470, "8b442404 83f80a", 77, 0),      # an index's width (cdecl)
    (0x474530, "8b442404 83f80a", 78, 0),      # an index's height (cdecl)
    (0x475D00, "0fb6052d158000", 80, 0),       # the width: SCREEN_W's stores get the layout's
    (0x475D20, "0fb6052d158000", 81, 0),       # the 16:9 height: SCREEN_H's stores get the layout's
    (0x475D40, "0fb6052d158000", 101, 0),      # the real height (full-screen back buffer)
    (0x5CB663, "3bc3 0f9ec1", 82, None),       # present parameters, the width read from SCREEN_W (mid-function)
    (0x5CAFAD, "89542420 d98694010000", 83, None),  # the screen's viewport, eax x ecx (mid-function)
    (0x5CB01B, "8d54240c 894c2418", 84, None),      # the screen's scissor, eax x ecx (mid-function)
    (0x4F9985, "d9442454 d95c2410", 85, None),      # camera aspect at [esp+0x54] (mid-function)
    (0x47DE35, "d9442420 d95c2410", 86, None),      # camera aspect at [esp+0x20] (mid-function)
    (0x576D17, "d906 b801000000", 87, None),        # a marker's projected point at [esi] (mid-function)
    (0x475D60, "0fb6052e158000", 88, 0),       # the display mode (stock: the windowed byte)
    (0x475D70, "8a442404 a22e158000", 89, 0),  # set the display mode (cdecl)
    (0x56D34C, "56 e80e8af0ff", 90, 0),        # Graphics display-mode row stepped (cdecl: 0 left, 1 right), past its availability check; always replaced
    (0x5CA219, "8b1540d64001", 91, None),      # device made: the screen buffers' size in eax x ecx (mid-function)
    (0x5CAA7A, "83c408 50 51", 92, None),      # mode set: the same (mid-function)
    (0x5CA238, "50 57 b9181ccd00", 93, None),  # device made: a buffer's height in eax (mid-function)
    (0x5CAAA7, "52 57 b9181ccd00", 98, None),  # mode set: the same height in edx (mid-function)
    (0x6231B0, "d9442408 dc0d60e26c00", 102, None),  # projection matrix (cdecl: out, fov, aspect, near, far, flags, scale)
    (0x5DC183, "8908 8902 894104", 103, None),  # 2D packet queued, its node in eax (mid-function)
    (0x5DD552, "8b7108 8b5104", 104, None),     # frame flush at a node in ecx (mid-function)
    (0x5CCFE0, "83ec10 8b442414", 94, None),   # 2D clip from the packets (stdcall: x0, x1, y0, y1 in layout px)
    (0x60A5C0, "83ec40 8b442444", 95, None),   # a quad in raw px (thiscall: corners, uvs, colour): movies map too
    (0x5CAAD0, "83ec10 55 8b6c241c", 96, 8),   # can the display take a size (thiscall: w, h)
    (0x56CB7F, "53 53 83c60a", 79, None),       # Graphics resolution value, its font set (mid-function)
    (0x5CBFE4, "89442414 8d442428", 97, None),  # window made: SCREEN stored with the window's size (mid-function)
    (0x5CBD60, "6aff 68d8b86b00", 107, None),   # the window procedure (stdcall: window, message, wParam, lParam)
    # The ground rings (ring_upload.h): each Lock and Unlock calls the runtime's in place of the buffer's (call edx).
    (0x5B3F99, "6a00 51 ffd2", 53, None),       # ground ring Lock: 0x5B3E00 x3, 0x5B4100 x3 (mid-function)
    (0x5B4003, "6a00 51 ffd2", 54, None),
    (0x5B406D, "6a00 51 ffd2", 55, None),
    (0x5B4219, "6a00 51 ffd2", 56, None),
    (0x5B4283, "6a00 51 ffd2", 57, None),
    (0x5B42ED, "6a00 51 ffd2", 58, None),
    (0x5B7635, "50 ffd2 8b461c", 59, None),     # ground ring Unlock: 0x5B7600 x3, 0x5B77B0 x3 (mid-function)
    (0x5B7681, "50 ffd2 8b461c", 60, None),
    (0x5B76CD, "50 ffd2 8b461c", 61, None),
    (0x5B77E5, "50 ffd2 8b461c", 62, None),
    (0x5B7831, "50 ffd2 8b461c", 63, None),
    (0x5B787D, "50 ffd2 8b461c", 64, None),
    # An effect batch's Lock (0x6392A0) keeps the buffer's old contents (flags 0), so it waits for the GPU's last draw
    # from it; the batch rewrites the buffer from 0 and draws only what it wrote, so the hook makes it DISCARD.
    (0x63963F, "8b422c ffd0", 23, None),        # effect batch Lock, its flags pushed (mid-function)
    *((va, prologue, hook_id, None) for va, prologue, hook_id, _ in HEADROOM),
    *((va, prologue, hook_id, None) for va, prologue, hook_id, _ in VOLUME),
    *((va, prologue, hook_id, None) for va, prologue, hook_id in STAGE_DUCK_HOOKS),
]
# ENRT_MOVING_FIX builds only: out of release 1, kept for later.
MOVING_FIX_HOOKS = [
    (0x427F61, "8b4704 d9470c", 72, None),      # follow camera facing set: the camera stick keeps it (mid-function)
]
# ENRT_TEST builds only.
TEST_HOOKS = [
    (0x5DDDE0, "8b442424 8b4c241c", 73, None),  # sprite record builder (banner plates): a plain box in `screen`
    (0x590350, "83ec20 53 55", 74, None),       # nine-slice outline (the gallery selection frame), the same
    (0x4EEE60, "53 8a5c2414 80fb07", 112, 0),   # stage-script queue (cdecl): keep's no-defeat refuses kind 8
    (0x4B0480, "51 dd05b0b07a00", 114, None),   # true rate: the battle's ticks begin
    # runtime/sound_trace.h: every sound request and effect start to runtime.log
    (0x510840, "53 55 8b6c240c", 128, None),    # sound request (eax id)
    (0x512EB0, "8b4c2404 8b542414", 129, None),  # direct play by id
    (0x5D5C40, "83ec10 db442424", 130, None),   # effect start, 2D
    (0x5D5CC0, "83ec34 53 56", 131, None),      # effect start, 3D
    (0x67A960, "83ec64 a1e4057c00", 132, None),  # DirectSound buffer creation (its flags)
    (0x6770B0, "51 56 8bf1 57", 134, None),       # the Ksl voice pool's play step (fastcall: the pool)
]
class RuntimeBuildError(ValueError):
    """The stock executable, an edit or a hook site is not what the build expects."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeBuildError(reason)


def hook_sites(defines: tuple[str, ...] = ()) -> list[tuple[int, bytes, int, int | None, bool]]:
    """(VA, prologue, id, pops, English only) for every hook a build with ``defines`` installs."""
    sites = [(*h, True) for h in HOOKS] + [(*h, False) for h in ALWAYS_HOOKS]
    if "ENRT_MOVING_FIX" in defines:
        sites += [(*h, False) for h in MOVING_FIX_HOOKS]
    if "ENRT_TEST" in defines:
        sites += [(*h, False) for h in TEST_HOOKS]
    return [(va, bytes.fromhex(prologue.replace(" ", "")), hook_id, pops, english)
            for va, prologue, hook_id, pops, english in sites]


def check_spans(exe: bytes, edits: dict[int, list[tuple[int, bytes, bytes]]], hooks: list) -> None:
    """Reject stock-byte drift, changed edit sizes and overlapping spans."""
    layout = pe.layout(exe)
    spans = {group: [] for group in edits}
    for group, group_edits in edits.items():
        for va, before, after in group_edits:
            _require(len(before) == len(after), f"edit at {va:#x} changes its length")
            at = pe.file_offset(layout, va)
            _require(exe[at:at + len(before)] == before, f"stock executable lacks the code at {va:#x}")
            spans[group].append((va, va + len(before)))
    at = pe.file_offset(layout, WIDTH_POINTER)
    _require(exe[at:at + 4] == bytes(4), "width pointer is not stock")
    spans[ENGLISH].append((WIDTH_POINTER, WIDTH_POINTER + 4))
    always = []
    for va, prologue, hook_id, _, english in hooks:
        _require(len(prologue) >= 5, f"hook {hook_id} prologue shorter than a jump")
        at = pe.file_offset(layout, va)
        _require(exe[at:at + len(prologue)] == prologue, f"prologue drift at {va:#x}")
        (spans[ENGLISH] if english else always).append((va, va + len(prologue)))
    for group in (ONE_ROW, TWO_ROWS):  # the rows install in both modes, beside the English edits
        ordered = sorted(spans[ENGLISH] + spans[group] + always)
        overlaps = [hex(b[0]) for a, b in pairwise(ordered) if b[0] < a[1]]
        _require(not overlaps, f"spans overlap at {', '.join(overlaps)}")


def _bytes(data: bytes) -> str:
    return ",".join(f"0x{b:02x}" for b in data)


def data_header(exe: bytes, edits: list[tuple[int, str, list[tuple[int, bytes, bytes]]]], hooks: list,
                widths: bytes, defines: tuple[str, ...] = (), features: list | None = None) -> str:
    """Pack checked edits, hooks, glyph widths and the guard list (wozmod.runtime.guards) into a C header. ``edits``
    are (group, list name, edits): the group picks the text mode, the name the feature whose guards hold their stock
    bytes. ``features`` replaces guards.FEATURES (tests)."""
    grouped: dict[int, list] = {}
    for group, _, group_edits in edits:
        grouped.setdefault(group, []).extend(group_edits)
    check_spans(exe, grouped, hooks)
    for name, table in (("headroom", HEADROOM), ("volume", VOLUME)):
        ids = [hook_id for _, _, hook_id, _ in table]
        _require(ids == list(range(ids[0], ids[0] + len(ids))), f"the {name} hook ids are not consecutive")
    chosen, guards = guard_list(exe, edits, hooks, defines, features)
    records = bytearray()
    for group, _, group_edits in edits:
        for va, before, after in group_edits:
            records += bytes([group]) + struct.pack("<IH", va, len(before)) + before + after
    records.append(0xFF)
    sites = [f"    {{{va:#x}, {pops if pops is not None else 0}, {hook_id}, "
             f"{english | (pops is not None) << 1}, {len(prologue)}, {{{_bytes(prologue)}}}}},"
             for va, prologue, hook_id, pops, english in hooks]
    return "\n".join([
        "/* Generated by wozmod.runtime from the stock executable; do not edit. */",
        "/* The guard list (wozmod.runtime.guards, feature_guards.h), exported for the installer to read. */",
        f"__declspec(dllexport) const u8 wozmod_guards[] = {{{_bytes(blob(chosen, guards))}}};",
        "/* group, VA, length (u16), stock bytes, patched bytes; group 0xFF ends the list */",
        f"static const u8 enrt_edits[] = {{{_bytes(records)}}};",
        "static const struct site enrt_hooks[] = {", *sites, "};",
        f"static const u8 enrt_widths[{len(widths)}] = {{{_bytes(widths)}}};",
        f"#define WIDTH_POINTER {WIDTH_POINTER:#x}",
        f"#define GLYPH_LATIN {LATIN_GLYPH}",
        f"#define WIDE_TEXT {WIDE}",
        f"static const u32 enrt_wide_writes[] = {{{', '.join(f'{r.call + 5:#x}' for r in ROOMS if r.room == WIDE)}}};",
        f"static const u32 enrt_wide_reads[] = {{{', '.join(f'{r.read + 5:#x}' for r in ROOMS if r.read)}}};",
        f"#define HEADROOM_FIRST {HEADROOM[0][2]}",
        f"static const u8 headroom_word[] = {{{', '.join(str(FRAME_WORDS.index(w)) for *_, w in HEADROOM)}}};",
        f"#define VOLUME_FIRST {VOLUME[0][2]}",
        f"static const u32 volume_option[] = {{{', '.join(f'{option:#x}' for *_, option in VOLUME)}}};",
        f"#define PROMPT_SLOTS {len(PROMPT_SLOTS)}",
        f"#define PROMPT_ITEMS {len(PROMPT_SLOTS) + len(STICK_ITEMS)}",
        f"#define PROMPT_SLOT_LIST {', '.join(map(str, PROMPT_SLOTS + STICK_ITEMS))}",
        *text_box_header(),
        *text_lane_header(),
        "",
    ])


def compile_dll(output: Path, header: str, defines: tuple[str, ...] = ()) -> None:
    """Compile with MinGW (Fedora's mingw32-gcc; the release build pins what it builds, packaging/pins.py)."""
    flags = ["-std=c11", "-O2", "-Wall", "-Werror", "-shared", "-static-libgcc",
             "-fno-asynchronous-unwind-tables", *(f"-D{d}" for d in defines),
             f"-Wl,--no-insert-timestamp,--kill-at,--disable-dynamicbase,--image-base,{DLL_BASE:#x}",
             # gdi32: the borderless bars' black brush; ole32: the master stage's audio-client probe (sound_attach)
             "-o", "d3d9.dll", "english_runtime.c", "-lgdi32", "-lole32"]
    if shutil.which(COMPILER) is None:
        raise FileNotFoundError(f"{COMPILER} is missing: install Fedora's mingw32-gcc package")
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copyfile(C_SOURCE, Path(tmp, "english_runtime.c"))
        for header_file in C_SOURCE.parent.glob("*.h"):
            shutil.copyfile(header_file, Path(tmp, header_file.name))
        Path(tmp, "enrt_data.h").write_text(header, encoding="utf-8")
        subprocess.run([COMPILER, *flags], cwd=tmp, check=True)
        dll = Path(tmp, "d3d9.dll").read_bytes()
    _require(dll[:2] == b"MZ", "the compiler wrote no PE image")
    output.write_bytes(dll)
