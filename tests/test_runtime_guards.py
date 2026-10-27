"""Per-feature stock guards: the one list (wozmod.runtime.guards), the DLL's check of it (runtime/feature_guards.h) and
the installer's reading of it back from the DLL (wozmod.runtime.guard_report)."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from synthetic import pe_image
from wozmod.formats import pe
from wozmod.runtime import camp_plate, guard_report, guards, layout
from wozmod.runtime.build import build_dll, edit_lists
from wozmod.runtime.english_runtime import ALWAYS_HOOKS, HOOKS, MOVING_FIX_HOOKS, TEST_HOOKS, hook_sites
from wozmod.runtime.guards import Feature, Guard

REPO = Path(__file__).resolve().parents[1]
ALL_DEFINES = ("ENRT_LOG", "ENRT_BOOTLOG", "ENRT_TAG_PREVIEW", "ENRT_TEST", "ENRT_RENDER_DISTANCE", "ENRT_MOVING_FIX")
TEXT = (0x401000, 0x6CB000)  # version 1.00's .text, from its section table
V101 = (0x4B5E99A7, 0x1194000)  # the 1.01 update's OROCHI_Z.exe: stamp 2010-01-26 07:28:39 UTC, SizeOfImage
RANGE = "range bound: compares a return address with a whole code region, which no span can guard"
# .text addresses the runtime names that no guard covers, each with its reason.
UNGUARDED = {
    0x401000: "the whole of .text, made writable for the coverage probe's breakpoints (true_rate_probe.h, test build)",
    0x520000: RANGE,  # menu_word: a caller in the menus' code, 0x520000 to 0x5B0000
    layout.DEFINES["NARRATION_CODE"]: RANGE, layout.DEFINES["NARRATION_CODE_END"]: RANGE,  # a glyph count's caller
    layout.DEFINES["STILL_CODE"]: RANGE, layout.DEFINES["STILL_CODE_END"]: RANGE,
}
FORWARDED = "a window import slot: the DLL keeps what it held and calls through it, so another mod's hook there works"
# Stock bytes the runtime rewrites that no guard covers, each with its reason.
UNGUARDED_WRITES = {0x6CC248: FORWARDED, 0x6CC25C: FORWARDED, 0x6CC278: FORWARDED, 0x6CC28C: FORWARDED}


def _spans() -> list[tuple[str, int, int]]:
    """Every guard of every build variant: (feature, VA, size)."""
    edits = edit_lists(camp_plate.STOCK_WIDTH, preview=True, render_distance=True, moving_fix=True)
    return guards.guard_spans(edits, hook_sites(ALL_DEFINES), ALL_DEFINES)


def _named_addresses() -> dict[int, list[str]]:
    """Each .text address in the code (not the comments) of runtime/*.c and *.h, with where it is named."""
    found: dict[int, list[str]] = {}
    for path in sorted((REPO / "runtime").glob("*.[ch]")):
        code = re.sub(r"/\*.*?\*/|//[^\n]*", lambda m: re.sub(r"[^\n]", " ", m[0]), path.read_text(encoding="utf-8"),
                      flags=re.S)
        for number, line in enumerate(code.splitlines(), 1):
            for value in re.findall(r"\b0x([0-9A-Fa-f]+)[uU]?\b", line):
                if TEXT[0] <= int(value, 16) < TEXT[1]:
                    found.setdefault(int(value, 16), []).append(f"{path.name}:{number}")
    return found


def test_every_stock_code_address_the_runtime_names_is_guarded() -> None:
    """Bug: the runtime calls a stock routine, compares a return address or reads code at an address no feature
    guards, so another mod's edit there leaves the feature on and it calls into their code or never matches (a
    misdrawn line, a crash) instead of turning off with its log line. An address is guarded when it lies in a guard
    (a hook's prologue, an edit's stock bytes, a called routine's first bytes, a span) or follows one directly (a
    return address after its call). The rest are listed in UNGUARDED with their reason."""
    spans = _spans()
    named = _named_addresses()
    red = [(hex(va), where[0]) for va, where in sorted(named.items()) if va not in UNGUARDED
           and not any(start <= va < start + size or start <= va - 5 and va <= start + size for _, start, size in spans)]
    stale = [hex(va) for va in UNGUARDED if va not in named]
    assert (red, stale) == ([], [])


def test_every_stock_byte_the_runtime_rewrites_is_guarded() -> None:
    """Bug: the runtime writes stock code (the prompt icon widths, the display's sites) where no guard checks the
    bytes, so with another mod's code there it writes into the middle of their instructions. Each write must lie
    wholly inside one guard, or be listed in UNGUARDED_WRITES with its reason."""
    spans = _spans()
    writes = layout.runtime_writes()
    unguarded = [(name, hex(va)) for name, va, _, size in writes if va not in UNGUARDED_WRITES
                 and not any(start <= va and va + size <= start + length for _, start, length in spans)]
    stale = [hex(va) for va in UNGUARDED_WRITES if all(va != written for _, written, _, _ in writes)]
    assert (unguarded, stale) == ([], [])


def test_every_stock_routine_an_edit_calls_is_a_guard_of_its_feature() -> None:
    """Bug: a detour's cave calls a stock routine (V101_FIXES's draw-state reset 0x58E370) that no guard of the
    edit's feature covers, so with another mod's hook on that routine the cave calls into it, and the feature is
    not turned off."""
    capstone = pytest.importorskip("capstone")
    disassembler = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    owner = {name: f for f in guards.FEATURES for name in f.edits}
    spans = _spans()
    missing = []
    for _, name, edits in edit_lists(camp_plate.STOCK_WIDTH, preview=True, render_distance=True, moving_fix=True):
        own = [(va, va + len(before)) for va, before, _ in edits]
        for va, _, after in edits:
            for instruction in disassembler.disasm(after, va):
                if instruction.mnemonic != "call":
                    continue
                target = int(instruction.op_str, 16)
                if not any(start <= target < end for start, end in own) and not any(
                        key == owner[name].key and start <= target < start + size for key, start, size in spans):
                    missing.append((name, hex(instruction.address), hex(target)))
    assert missing == []


def test_each_hook_and_edit_list_belongs_to_exactly_one_feature() -> None:
    """Bug: a hook or edit list in two features is installed by the later one only, so a refusal of the first
    leaves it written; one in none fails the build (guards.GuardError)."""
    hooks = [h[2] for h in HOOKS + ALWAYS_HOOKS + MOVING_FIX_HOOKS + TEST_HOOKS]
    names = {name for _, name, _ in edit_lists(camp_plate.STOCK_WIDTH)}
    claims = {h: [f.key for f in guards.FEATURES if h in f.hooks] for h in hooks}
    claims |= {name: [f.key for f in guards.FEATURES if name in f.edits] for name in names}
    assert {thing: keys for thing, keys in claims.items() if len(keys) != 1} == {}


# A synthetic guard list of three features, one with two guards.
SECTION, RAW, BASE = 0x79B000, 0x200, 0x700000
SYNTHETIC = [Feature("base", "base"), Feature("mid", "mid"), Feature("top", "top")]
SYNTHETIC_GUARDS = [Guard(0, SECTION + 0x10, b"abc"), Guard(1, SECTION + 0x20, b"de"), Guard(2, SECTION + 0x30, b"fghij"),
                    Guard(2, SECTION + 0x40, b"k")]


def synthetic_exe(build: tuple[int, int] = guards.V100) -> bytearray:
    image = pe_image(RAW + 0x1000, (b".fixture", 0x1000, SECTION - BASE, 0x1000, RAW), image_base=BASE)
    struct.pack_into("<I", image, 0x80 + 8, build[0])
    struct.pack_into("<I", image, 0x80 + 24 + 56, build[1])
    for g in SYNTHETIC_GUARDS:
        image[RAW + g.va - SECTION:RAW + g.va - SECTION + len(g.stock)] = g.stock
    return image


@pytest.fixture(scope="module")
def evaluator(c_library):
    lib = c_library("feature_guards", r'''
#include "feature_guards.h"
static const unsigned char *image;
static unsigned section, raw, *calls, called;
static const unsigned char *at(unsigned va) { return image + raw + (va - section); }
static void refuse(unsigned feature, unsigned va) { calls[2 * called] = feature, calls[2 * called + 1] = va, called++; }
unsigned evaluate(const unsigned char *blob, const unsigned char *img, unsigned sec, unsigned r, unsigned *out,
                  unsigned *count)
{
    unsigned differ;
    image = img, section = sec, raw = r, calls = out, called = 0;
    if (!guard_blob_ok(blob))
        return 0xFFFFFFFFu;
    differ = guards_differ(blob, at, refuse);
    *count = called;
    return differ;
}
int build(const unsigned char *blob, const unsigned char *img, unsigned *stamp) { return guard_build(blob, img, stamp); }
void name(const unsigned char *blob, unsigned f, char *out, unsigned size) { guard_name(blob, f, out, size); }
''')
    lib.evaluate.restype = ctypes.c_uint
    lib.evaluate.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint,
                             ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    lib.build.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint)]
    lib.name.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_char_p, ctypes.c_uint]
    return lib


def _refusals(evaluator, blob: bytes, image: bytes) -> tuple[int, list[tuple[str, int]]]:
    """What the DLL's check returns for this image, and the (feature, VA) of each refusal line it logs."""
    out, count = (ctypes.c_uint * 64)(), ctypes.c_uint()
    differ = evaluator.evaluate(blob, image, SECTION, RAW, out, ctypes.byref(count))
    return differ, [(SYNTHETIC[out[2 * i]].key, out[2 * i + 1]) for i in range(count.value)]


def test_any_changed_guard_byte_refuses_all_of_wozmod_and_names_its_feature_in_the_dll_and_the_installer(evaluator
                                                                                                          ) -> None:
    """Bugs: one changed byte in one feature passes the check, so the other features are patched beside it (a guard
    after the first, or a byte after a guard's first, is never compared: a misread length or record size); a
    second changed feature is hidden behind the first, so the log and the installer name only one; a refusal line
    names the wrong feature or address; the installer's report (guard_report) disagrees with the DLL. Flips each byte
    of each guard alone, then one byte in each pair of guards; the expected refusals are the flipped guards
    themselves."""
    blob = guards.blob(SYNTHETIC, SYNTHETIC_GUARDS)
    listed = guard_report.decode(blob)
    names = []
    for f in range(len(SYNTHETIC)):
        out = ctypes.create_string_buffer(48)
        evaluator.name(blob, f, out, 48)
        names.append(out.value.decode())
    assert names == [f.name for f in SYNTHETIC]
    assert _refusals(evaluator, blob, bytes(synthetic_exe())) == (0, [])
    assert guard_report.refused(listed, bytes(synthetic_exe())) == []
    cases = [[(g, position)] for g in SYNTHETIC_GUARDS for position in range(len(g.stock))]
    cases += [[(a, 0), (b, len(b.stock) - 1)] for i, a in enumerate(SYNTHETIC_GUARDS) for b in SYNTHETIC_GUARDS[i + 1:]]
    wrong = []
    for flips in cases:
        image = synthetic_exe()
        for g, position in flips:
            image[RAW + g.va - SECTION + position] ^= 0x40
        expected = [(SYNTHETIC[g.feature].key, g.va) for g, _ in flips]
        dll = _refusals(evaluator, blob, bytes(image))
        installer = guard_report.refused(listed, bytes(image))
        named = [f.name for i, f in enumerate(SYNTHETIC) if any(g.feature == i for g, _ in flips)]
        if dll != (len(flips), expected) or installer != named:
            wrong.append(([(hex(g.va), p) for g, p in flips], dll, installer))
    assert wrong == []


@pytest.mark.parametrize("build, accepted", [(guards.V100, True), (V101, False), ((guards.V100[0], V101[1]), False),
                                            ((V101[0], guards.V100[1]), False)],
                         ids=["1.00", "1.01", "1.00 stamp, other size", "other stamp, 1.00 size"])
def test_only_version_1_00_s_header_is_patched(evaluator, build: tuple[int, int], accepted: bool) -> None:
    """Bugs: the build check reads the stamp or the image size at a shifted offset, or compares only one of them, so a
    1.01 or rebuilt executable is patched at 1.00's addresses. The DLL logs the stamp it found; the installer's
    report names every feature, as the DLL installs nothing."""
    blob = guards.blob(SYNTHETIC, SYNTHETIC_GUARDS)
    stamp = ctypes.c_uint()
    image = bytes(synthetic_exe(build))
    assert (bool(evaluator.build(blob, image, ctypes.byref(stamp))), stamp.value) == (accepted, build[0])
    refused = guard_report.refused(guard_report.decode(blob), image)
    assert refused == ([] if accepted else [f.name for f in SYNTHETIC])


def test_guard_list_reads_each_kind_of_guard_from_the_stock_bytes_it_names() -> None:
    """Bugs: a return address is guarded at itself instead of the call before it; a called routine's guard is not
    its first bytes; a hook or edit is filed under another feature; a return address that follows no call (a
    shifted address) is accepted."""
    image = synthetic_exe()
    code = {SECTION + 0x100: b"\xe8\x01\x02\x03\x04", SECTION + 0x110: b"routine!", SECTION + 0x120: b"span",
            SECTION + 0x130: b"hook!", SECTION + 0x140: b"ed", SECTION + 0xAC0: bytes(4)}
    for va, data in code.items():
        image[RAW + va - SECTION:RAW + va - SECTION + len(data)] = data
    chosen = [Feature("text", "text", hooks=(7,), edits=("CODE_EDITS",)),
              Feature("other", "other", calls=(SECTION + 0x110,), returns=(SECTION + 0x105,), spans=((SECTION + 0x120, 4),))]
    edits = [(0, "CODE_EDITS", [(SECTION + 0x140, b"ed", b"ED")])]
    hooks = [(SECTION + 0x130, b"hook!", 7, None, True)]
    _, found = guards.guard_list(bytes(image), edits, hooks, (), chosen)
    assert found == sorted([Guard(0, SECTION + 0x130, b"hook!"), Guard(0, SECTION + 0x140, b"ed"),
                            Guard(0, SECTION + 0xAC0, bytes(4)),  # the width pointer, English text's
                            Guard(1, SECTION + 0x110, b"routine!"), Guard(1, SECTION + 0x100, b"\xe8\x01\x02\x03\x04"),
                            Guard(1, SECTION + 0x120, b"span")], key=lambda g: (g.feature, g.va, len(g.stock)))
    shifted = [chosen[0], Feature("other", "other", returns=(SECTION + 0x106,))]
    with pytest.raises(guards.GuardError, match="not a return address"):
        guards.guard_list(bytes(image), edits, hooks, (), shifted)


@pytest.fixture(scope="module")
def release(tmp_path_factory) -> tuple[bytes, bytes]:
    """The stock executable and the release DLL built from it."""
    if os.environ.get("WOZMOD_RUNTIME_COMPILE") != "1" or not os.environ.get("WOZMOD_GAME"):
        pytest.skip("set WOZMOD_RUNTIME_COMPILE=1 and WOZMOD_GAME")
    exe = Path(os.environ["WOZMOD_GAME"]) / "OROCHI_Z.exe"
    return exe.read_bytes(), build_dll(exe, tmp_path_factory.mktemp("guards") / "d3d9.dll").read_bytes()


def test_the_dll_exports_the_guard_list_its_build_made(release) -> None:
    """Bug: the DLL exports no list, a truncated one or another build's, so the installer names other features than
    the DLL finds changed. The export must equal guards.blob for the same stock executable and build."""
    stock, dll = release
    edits = edit_lists(camp_plate.width(camp_plate.stock_plate(Path(os.environ["WOZMOD_GAME"]))))
    expected = guards.blob(*guards.guard_list(stock, edits, hook_sites(())))
    va = pe.exports(dll)["wozmod_guards"]
    at = pe.file_offset(pe.layout(dll), va)
    assert dll[at:at + len(expected)] == expected


def test_another_mods_edit_names_only_the_feature_whose_code_it_changed(release) -> None:
    """Bug: one changed byte at the frame rate limiter (0x5CA36D) or in the frame hook (core, 0x5C9FD0) is reported as
    nothing, so the installer lets it through and the DLL turns all of wozmod off in game; or it is reported as
    other features than its own, so the installer names the wrong ones."""
    stock, dll = release
    where = pe.layout(stock)
    reports = {}
    for va in (None, 0x5CA36D, 0x5C9FD0):
        image = bytearray(stock)
        if va is not None:
            image[pe.file_offset(where, va)] ^= 1
        reports[va] = guard_report.refused_features(bytes(image), dll)
    assert reports == {None: [], 0x5CA36D: ["frame rate"], 0x5C9FD0: ["core"]}


# A stand-in for the game: version 1.00's build stamp and image size (patched in below), and an image that reaches past
# every address a guard names (zeros, so every guard differs). It loads the DLL as the game does, beside it.
STAND_IN = r'''
#include <windows.h>
static volatile unsigned char image[0x400000];
int main(void)
{
    image[0] = 1;
    return !LoadLibraryW(L"d3d9.dll");
}
'''


def test_a_run_wozmod_turns_off_logs_no_line_that_says_it_serves_anything(release, tmp_path) -> None:
    """Bug: a check logs what it found as if wozmod used it (the entry index's "entries served" line) before all the
    checks have decided, so the log of a run that turned wozmod off reads as if wozmod were serving its files. The
    release DLL runs under Wine in a stand-in game whose code differs from stock, with an install that has one entry
    and none of the other files: besides its start, its refusals and its exit, the log holds the off line only."""
    if not (shutil.which("i686-w64-mingw32-gcc") and shutil.which("wine")):
        pytest.skip("i686-w64-mingw32-gcc or wine absent")
    _, dll = release
    game = tmp_path / "game"
    (game / "wozmod/entries").mkdir(parents=True)
    (game / "wozmod/entries/12.bin").write_bytes(b"entry")
    (game / "d3d9.dll").write_bytes(dll)
    (tmp_path / "game.c").write_text(STAND_IN)
    subprocess.run(["i686-w64-mingw32-gcc", "-O2", "-Wl,--disable-dynamicbase", "-Wl,--image-base,0x400000",
                    str(tmp_path / "game.c"), "-o", str(tmp_path / "game.exe")], check=True)
    image = bytearray((tmp_path / "game.exe").read_bytes())
    nt = struct.unpack_from("<I", image, 0x3C)[0]
    assert struct.unpack_from("<I", image, nt + 24 + 56)[0] <= guards.V100[1]  # the loader refuses a smaller size
    struct.pack_into("<I", image, nt + 8, guards.V100[0])
    struct.pack_into("<I", image, nt + 24 + 56, guards.V100[1])
    (game / "OROCHI_Z.exe").write_bytes(bytes(image))
    env = {**os.environ, "WINEPREFIX": str(tmp_path / "prefix"), "WINEDEBUG": "-all",
           "WINEDLLOVERRIDES": "mscoree,mshtml=;d3d9=n"}
    subprocess.run(["wine", str(game / "OROCHI_Z.exe")], cwd=game, env=env, check=True, capture_output=True)
    lines = (game / "wozmod/runtime.log").read_text(encoding="ascii").splitlines()
    assert any(line.startswith("refused: core, the code differs from stock at") for line in lines)
    assert [line for line in lines[1:] if not line.startswith(("refused: ", "process exit"))] == [
        "wozmod is off: it patched nothing, and the game runs as stock"]
