"""Compile and validate the proxy runtime."""

from __future__ import annotations

from itertools import pairwise
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from wozmod.formats import pe

from .latin_font import LATIN_GLYPH, SURROGATE_CODE, SURROGATE_LETTERS
from .layout import C_SOURCE
from .prompts import XBOX_PROMPTS
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
# Installed in both text modes.
ALWAYS_HOOKS = [
    (0x5C9FD0, "83ec1c 53 55", 24, None),       # message pump: table reload, test mailbox
    (0x5ABE26, "83c40c 6828000200", 27, None),  # save.dat loaded into the game state (mid-function)
    (0x56B586, "6bf62a 83c420", 28, None),      # Game Settings row, its plate drawn (mid-function)
    (0x56B7C9, "83c408 393de0a12001", 29, None),  # Game Settings input read (mid-function)
    (0x5D1A30, "6aff 68c4ba6b00", 30, 0x14),    # archive read (thiscall): loose entries
    (0x5D06D0, "8b442404 8bd1", 31, 4),         # archive entry size (thiscall)
    (0x5146FF, "d90538e57000", 105, None),      # movie path made, "./movie/movieNNN.wmv" at [esp] (mid-function)
    (0x5D7CE0, "53 8b5c2408", 36, 8),           # voice offset (thiscall): English voices
    (0x5D7D30, "8b442404 85c0", 37, 8),         # voice size (thiscall)
    (0x4A1710, "8b442404 f60010", 71, None),    # lip block handed to a slot (cdecl: state, slot, block)
    (0x5D7BF0, "8b542404 83fa01 56", 109, None),  # voice play (thiscall): kept while its clip waits to load
    (0x5D8590, "83ec44 53 55 56 57", 110, None),  # voice frame (thiscall): a kept play made after its load
    (0x5D5990, "8b442404 83f87d", 38, 4),       # SE bank header offset (thiscall): English attack voices
    (0x5D59C0, "8b442404 83f87d", 39, 4),       # SE bank header size (thiscall)
    (0x5D59F0, "8b542404 83fa7d", 40, 4),       # SE bank data offset (thiscall)
    (0x5D5A20, "8b442404 83f87d", 41, 4),       # SE bank data size (thiscall)
    (0x428E33, "d9ee d99d20020000", 45, None),  # battle camera FOV stored (mid-function)
    (0x428FB4, "8d442438 d9ee", 46, None),      # 40-degree battle camera FOV stored (mid-function)
    (0x5E4F64, "8b8660010000", 47, None),       # joystick state read (mid-function)
    (0x5E38E0, "81ec40020000", 70, 0),          # input map load (cdecl): Xbox names before its auto-setting
    (0x56FDC0, "e82b5a0700", 106, 0),           # Controller page, Key Config chosen: the pad auto-setting offer; always replaced
    (0x58E2BC, "5f5eb801000000", 48, None),     # 4079 prompt handles made (tail)
    (0x5ADC60, "8b442404 69c0f0000000", 49, 0),  # menu word, pressed (cdecl): A/B for a pad
    (0x5ADCA0, "8b442404 69c0f0000000", 50, 0),  # menu word, repeating (cdecl)
    (0x5E5940, "53555657 6a1e", 51, 0),          # a binding's name (cdecl): Xbox names for a pad
    (0x56FC91, "6a06 6a18 6a18", 52, None),      # Key Config waiting for input, drawn (mid-function)
    (0x5933B0, "8b0c8568ec7900", 65, 0),         # Move List key (eax, edx, ebx in registers): Xbox art for a pad
    # Frame-rate hooks.
    (0x4B0D60, "a1a81a8000 a802", 53, 0),      # battle draw
    (0x5ADE90, "83ec10 53 55", 54, 0),         # input and sound step
    (0x5E0E80, "51 668b0d18a64001", 55, 0),    # fade ramp
    (0x5DFB90, "a18ca34001", 56, 0),           # frame RNG step
    (0x480300, "81ec5c010000", 57, 8),         # effect step (thiscall: slot, float seconds)
    (0x47B050, "833dd8bd9d0000", 58, 0),       # frame end tail: player timers, play time
    (0x47D720, "b9882d8200", 59, 0),           # effect model list release
    (0x512EB0, "8b4c2404 8b542414", 60, 0),    # sound effect request
    (0x48BE10, "833d0848840000", 61, 0),       # pooled object allocation (0 when full)
    (0x576D50, "83ec54 81b8a00e000092000000", 62, None),  # place over a head (eax = character)
    (0x576E13, "8b3570129d00", 64, None),      # the head bone's point, before its projection (mid-function)
    (0x576BA0, "8b442408 83ec54", 63, None),   # project a world point (out, point)
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
]
# Each letter CP932 lacks travels as the character at its surrogate code (latin_font).
SURROGATES = str.maketrans({letter: (SURROGATE_CODE + i).to_bytes(2, "big").decode("cp932")
                            for i, letter in enumerate(SURROGATE_LETTERS)})
_UNSURROGATES = {ord(v): k for k, v in SURROGATES.items()}


class RuntimeBuildError(ValueError):
    """The stock executable, an edit or a hook site is not what the build expects."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeBuildError(reason)


def encode_english(text: str) -> bytes:
    """CP932 bytes for English text, with macron vowels on their surrogate glyphs."""
    return text.translate(SURROGATES).encode("cp932")


def decode_english(raw: bytes, errors: str = "strict") -> str:
    """The text ``encode_english`` gave ``raw`` for."""
    return raw.decode("cp932", errors).translate(_UNSURROGATES)


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


def data_header(exe: bytes, edits: dict[int, list[tuple[int, bytes, bytes]]], hooks: list, widths: bytes,
                stock_sha256: str) -> str:
    """Pack checked edits, hooks and glyph widths into a C header."""
    check_spans(exe, edits, hooks)
    records = bytearray()
    for group, group_edits in edits.items():
        for va, before, after in group_edits:
            records += bytes([group]) + struct.pack("<IH", va, len(before)) + before + after
    records.append(0xFF)
    sites = [f"    {{{va:#x}, {pops if pops is not None else 0}, {hook_id}, "
             f"{english | (pops is not None) << 1}, {len(prologue)}, {{{_bytes(prologue)}}}}},"
             for va, prologue, hook_id, pops, english in hooks]
    return "\n".join([
        "/* Generated by wozmod.runtime from the stock executable; do not edit. */",
        f"static const u8 enrt_stock_sha256[32] = {{{_bytes(bytes.fromhex(stock_sha256))}}};",
        "/* group, VA, length (u16), stock bytes, patched bytes; group 0xFF ends the list */",
        f"static const u8 enrt_edits[] = {{{_bytes(records)}}};",
        "static const struct site enrt_hooks[] = {", *sites, "};",
        f"static const u8 enrt_widths[{len(widths)}] = {{{_bytes(widths)}}};",
        f"#define WIDTH_POINTER {WIDTH_POINTER:#x}",
        f"#define GLYPH_LATIN {LATIN_GLYPH}",
        f"#define WIDE_TEXT {WIDE}",
        f"static const u32 enrt_wide_writes[] = {{{', '.join(f'{r.call + 5:#x}' for r in ROOMS if r.room == WIDE)}}};",
        f"static const u32 enrt_wide_reads[] = {{{', '.join(f'{r.read + 5:#x}' for r in ROOMS if r.read)}}};",
        f"#define PROMPT_SLOTS {len(XBOX_PROMPTS)}",
        f"#define PROMPT_SLOT_LIST {', '.join(map(str, XBOX_PROMPTS))}",
        "",
    ])


def compile_dll(output: Path, header: str, defines: tuple[str, ...] = ()) -> None:
    """Compile with MinGW (Fedora's mingw32-gcc; the DLL pin follows its updates)."""
    flags = ["-std=c11", "-O2", "-Wall", "-Werror", "-shared", "-static-libgcc",
             "-fno-asynchronous-unwind-tables", *(f"-D{d}" for d in defines),
             f"-Wl,--no-insert-timestamp,--kill-at,--disable-dynamicbase,--image-base,{DLL_BASE:#x}",
             "-o", "d3d9.dll", "english_runtime.c", "-lgdi32"]  # gdi32: the borderless bars' black brush
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


def table_blob(pairs: dict[bytes, bytes], names: list[bytes | None] = (), overridden: list[int] = ()) -> bytes:
    """Pack the runtime lookup table, the part slots' English offsets (build.PART_TABLES in order) and the slots a
    per-index override gives, which the runtime serves by the slot's stock pointer."""
    size = 1
    while size < 2 * len(pairs) + 2:
        size *= 2
    header = 8 + 12 * size
    pool, offsets = bytearray(), {}

    def put(value: bytes) -> int:
        _require(b"\0" not in value, "embedded NUL")
        if value not in offsets:
            offsets[value] = header + len(pool)
            pool.extend(value + b"\0")
        return offsets[value]

    slots = [(0, 0, 0)] * size
    for jp, en in pairs.items():
        _require(bool(jp), "empty key")
        h = fnv1a(jp)
        i = h & (size - 1)
        while slots[i][1]:
            i = (i + 1) & (size - 1)
        slots[i] = (h, put(jp), put(en))
    name_offsets = [0 if en is None else put(en) for en in names]
    pool.extend(bytes(-len(pool) % 4))
    return (struct.pack("<II", size - 1, header + len(pool))
            + b"".join(struct.pack("<3I", *s) for s in slots) + bytes(pool)
            + struct.pack(f"<{len(names) + 1}I", len(names), *name_offsets)
            + struct.pack(f"<{len(overridden) + 1}I", len(overridden), *overridden))

def fnv1a(value: bytes) -> int:
    h = 2166136261
    for b in value:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def table_lookup(blob: bytes, key: bytes) -> bytes | None:
    """The English table_blob holds for key, found as enrt_translate finds it: start at hash & mask and step one slot
    (wrapping) until the key or an empty slot."""
    mask = struct.unpack_from("<I", blob)[0]
    h = fnv1a(key)
    i = h & mask
    for _ in range(mask + 1):  # a table with no empty slot would loop forever in the game; here it fails
        slot_hash, jp, en = struct.unpack_from("<3I", blob, 8 + 12 * i)
        if not jp:
            return None
        if slot_hash == h and blob[jp:blob.index(b"\0", jp)] == key:
            return blob[en:blob.index(b"\0", en)]
        i = (i + 1) & mask
    raise ValueError("the table has no empty slot")
