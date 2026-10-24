"""Runtime wire formats and compiler boundary checks."""

from __future__ import annotations

import hashlib
import os
import re
import struct
import subprocess
from pathlib import Path

import pytest

from synthetic import pe_image
from wozmod.formats import pe
from wozmod.install import STOCK_FILES
from wozmod.runtime import build, layout
from wozmod.runtime import english_runtime as runtime
from wozmod.runtime.latin_font import LATIN_GLYPH
from wozmod.runtime.prompts import XBOX_PROMPTS
from wozmod.runtime.runtime_edits import (
    CODE_EDITS, FONT_ROUTE, PAD_CAMERA, STANDARD_CAMERA, VOICE_EDITS, camp_plate_edits, settings_rows,
)
from wozmod.runtime import camp_plate

REPO = Path(__file__).resolve().parents[1]


def test_missing_latin_glyphs_use_their_distinct_two_byte_surrogates() -> None:
    assert runtime.encode_english("AŌōŪūïZ") == b"A\x84\x40\x84\x41\x84\x42\x84\x43\x84\x44Z"


def test_colliding_keys_keep_both_rows_and_aligned_shared_name_pointers() -> None:
    # The two independently known FNV-1a hashes select slot four in an eight-slot table.
    expected = (
        struct.pack("<II", 7, 120)
        + bytes(12 * 4)
        + struct.pack("<3I", 0xE40C292C, 104, 106)
        + struct.pack("<3I", 0xEC0C35C4, 111, 113)
        + bytes(12 * 2)
        + b"a\0left\0i\0right\0\0"
        + struct.pack("<4I", 3, 113, 0, 106)
        + struct.pack("<2I", 1, 2)
    )
    assert runtime.table_blob({b"a": b"left", b"i": b"right"}, [b"right", None, b"left"], [2]) == expected


def test_data_header_keeps_group_lengths_terminator_and_hook_return_flags() -> None:
    image = pe_image(0x1200, (b".fixture", 0x1000, 0x9B000, 0x1000, 0x200), image_base=0x700000)
    edits = {0: [(0x79B010, b"abc", b"xyz")], 1: [(0x79B020, b"de", b"fg")],
             2: [(0x79B020, b"de", b"hi")]}
    hooks = [(0x79B030, b"hello", 7, None, True), (0x79B040, b"later", 8, 4, False),
             (0x79B050, b"after", 9, 0, True), (0x79B060, b"world", 10, None, False)]
    for va, before, _ in [entry for group in edits.values() for entry in group]:
        at = 0x200 + va - 0x79B000
        image[at:at + len(before)] = before
    for va, before, *_ in hooks:
        at = 0x200 + va - 0x79B000
        image[at:at + len(before)] = before
    header = runtime.data_header(bytes(image), edits, hooks, b"\x05\x07", "12" * 32)
    assert bytes(layout.c_array("enrt_edits", header)) == (
        b"\0" + struct.pack("<IH", 0x79B010, 3) + b"abcxyz"
        + b"\x01" + struct.pack("<IH", 0x79B020, 2) + b"defg"
        + b"\x02" + struct.pack("<IH", 0x79B020, 2) + b"dehi" + b"\xff"
    )
    assert bytes(layout.c_array("enrt_stock_sha256", header)) == bytes([0x12]) * 32
    assert bytes(layout.c_array("enrt_widths", header)) == b"\x05\x07"
    assert layout.defines(header) == {"WIDTH_POINTER": 0x79BAC0, "GLYPH_LATIN": LATIN_GLYPH, "WIDE_TEXT": 1024,
                                      "PROMPT_SLOTS": len(XBOX_PROMPTS)}
    # The runtime matches the return address of each widened write and draw, as read in the stock code.
    wide = dict(re.findall(r"enrt_wide_(\w+)\[\] = \{([^}]*)\}", header))
    assert {name: sorted(int(v, 16) for v in values.split(",")) for name, values in wide.items()} == {
        "writes": [0x5851E3, 0x593B55, 0x593B7A, 0x598171, 0x5A1A6A], "reads": [0x593B98, 0x593B98, 0x59818B, 0x5A1AC2]}
    assert re.findall(r"^    \{([^{}]+), \{", header, re.MULTILINE) == [
        "0x79b030, 0, 7, 1, 5", "0x79b040, 4, 8, 2, 5",
        "0x79b050, 0, 9, 3, 5", "0x79b060, 0, 10, 0, 5",
    ]


def test_compiler_gets_every_header_and_the_requested_defines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/usr/bin/" + name)

    def fake_run(command, **kwargs):
        calls.append(command)
        build = Path(kwargs["cwd"])
        assert (build / "enrt_data.h").read_text() == "synthetic header\n"
        assert (build / "english_runtime.c").is_file()
        # Bug: a header the runtime includes is left out of the compiler's folder.
        included = set(re.findall(r'#include "(\w+\.h)"', "".join(
            p.read_text() for p in (REPO / "runtime").iterdir() if p.suffix in (".c", ".h")))) - {"enrt_data.h"}
        assert included and sorted(name for name in included if not (build / name).is_file()) == []
        (build / "d3d9.dll").write_bytes(b"MZsynthetic-dll")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    output = tmp_path / "d3d9.dll"
    runtime.compile_dll(output, "synthetic header\n", ("ENRT_TEST",))
    assert output.read_bytes() == b"MZsynthetic-dll"
    assert [(command[0], "-DENRT_TEST" in command) for command in calls] == [("i686-w64-mingw32-gcc", True)]


OPTIONS = {
    "release": ({}, ()),
    "test": ({"log": True, "bootlog": True, "preview": True, "test": True, "cell": 48},
             ("ENRT_LOG", "ENRT_BOOTLOG", "ENRT_TAG_PREVIEW", "ENRT_TEST", "ENRT_CELL=48")),
    "render distance": ({"render_distance": True}, ("ENRT_RENDER_DISTANCE",)),
    "moving fix": ({"moving_fix": True}, ("ENRT_MOVING_FIX",)),
}


@pytest.fixture(scope="module")
def stock_builds(tmp_path_factory: pytest.TempPathFactory) -> tuple[bytes, dict[str, tuple[str, tuple[str, ...]]]]:
    """The stock exe, and the header and defines each OPTIONS build of it hands the compiler."""
    exe = Path(os.environ.get("WOZMOD_GAME") or "", "OROCHI_Z.exe")
    if not exe.is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    stock = exe.read_bytes()
    assert (len(stock), hashlib.sha256(stock).hexdigest()) == STOCK_FILES["OROCHI_Z.exe"]
    handed = {}
    with pytest.MonkeyPatch.context() as patch:
        for name, (options, _) in OPTIONS.items():
            patch.setattr(build, "compile_dll", lambda path, header, defines, name=name: handed.update(
                {name: (header, defines)}))
            build.build_dll(exe, tmp_path_factory.mktemp("build") / "d3d9.dll", **options)
    return stock, handed


def edit_groups(header: str) -> dict[int, list[tuple[int, bytes, bytes]]]:
    """The header's edits by group, read as the runtime reads them: group, VA, u16 length, stock, patched."""
    data, at, groups = bytes(layout.c_array("enrt_edits", header)), 0, {}
    while data[at] != 0xFF:
        va, size = struct.unpack_from("<IH", data, at + 1)
        groups.setdefault(data[at], []).append((va, data[at + 7:at + 7 + size], data[at + 7 + size:at + 7 + 2 * size]))
        at += 7 + 2 * size
    return groups


@pytest.mark.parametrize("variant", OPTIONS)
def test_build_puts_each_edit_list_in_its_text_mode_group(stock_builds, variant: str) -> None:
    """Bugs: a release build keeps the cursor slot of a compiled-out row (RENDER DISTANCE, MOVING CAMERA FIX); an
    install with the English voice banks keeps the stock sound pool, which they overflow; the Japanese text mode (no
    English group) leaves the Xbox defaults without the camera; the preview's unlock jumps reach another build; a
    requested define or its hooks do not reach the compiler. The build also checks each edit and hook against the
    stock bytes it replaces, so a shifted address fails the fixture."""
    options, expected_defines = OPTIONS[variant]
    header, defines = stock_builds[1][variant]
    assert defines == expected_defines
    assert [int(row.split(",")[0], 16) for row in re.findall(r"^    \{([^{}]+), \{", header, re.MULTILINE)] == [
        va for va, *_ in runtime.hook_sites(expected_defines)]
    rows = options.get("render_distance", False), options.get("moving_fix", False)
    lists = {"FONT_ROUTE": (FONT_ROUTE, {0}), "CODE_EDITS": (CODE_EDITS, {0}),
             "one-row settings": (settings_rows(1, *rows), {1}), "two-row settings": (settings_rows(2, *rows), {2}),
             "PAD_CAMERA": (PAD_CAMERA, {1, 2}), "STANDARD_CAMERA": (STANDARD_CAMERA, {1, 2}),
             "VOICE_EDITS": (VOICE_EDITS, {2}),
             "camp plate": (camp_plate_edits(camp_plate.width(camp_plate.stock_plate(Path(os.environ["WOZMOD_GAME"])))),
                            {0})}
    groups = {group: set(edits) for group, edits in edit_groups(header).items()}
    assert {name: {group for group, edits in groups.items() if set(edit_list) <= edits}
            for name, (edit_list, _) in lists.items()} == {name: where for name, (_, where) in lists.items()}
    unlisted = {group: edits - set().union(*(edit_list for edit_list, _ in lists.values()))
                for group, edits in groups.items()}
    assert {group: {(before, after) for _, before, after in edits} for group, edits in unlisted.items()} == {
        0: {(b"\x74\x04", b"\x90\x90")} if options.get("preview") else set(), 1: set(), 2: set()}


SYNTHETIC_HEADER = "\n".join([
    "static const u8 enrt_stock_sha256[32] = {0};",
    "static const u8 enrt_edits[] = {0xff};",
    "static const struct site enrt_hooks[] = {{0x5c9fd0, 0, 24, 0, 5, {0x83,0xec,0x1c,0x53,0x55}}};",
    "static const u8 enrt_widths[1] = {24};",
    "#define WIDTH_POINTER 0x79bac0", f"#define GLYPH_LATIN {LATIN_GLYPH}",
    "#define WIDE_TEXT 1024", "static const u32 enrt_wide_writes[] = {0};", "static const u32 enrt_wide_reads[] = {0};",
    "#define PROMPT_SLOTS 1", "#define PROMPT_SLOT_LIST 0", "",
])


RELEASE, TEST = (), ("ENRT_LOG", "ENRT_TEST")


def compiles(test):
    """Gate a test that runs the MinGW compiler. They share one worker, so each variant compiles once."""
    gate = pytest.mark.skipif(os.environ.get("WOZMOD_RUNTIME_COMPILE") != "1", reason="set WOZMOD_RUNTIME_COMPILE=1")
    return pytest.mark.xdist_group("runtime-compile")(gate(test))


@pytest.fixture(scope="module")
def compiled(tmp_path_factory: pytest.TempPathFactory):
    """compiled(defines): the DLL SYNTHETIC_HEADER compiles to with these defines, compiled once per module."""
    directory, built = tmp_path_factory.mktemp("runtime-compile"), {}

    def dll(defines: tuple[str, ...]) -> bytes:
        if defines not in built:
            output = directory / f"{len(built)}.dll"
            runtime.compile_dll(output, SYNTHETIC_HEADER, defines)
            built[defines] = output.read_bytes()
        return built[defines]

    return dll


@compiles
def test_release_runtime_has_no_render_distance_row_key_or_patch(compiled) -> None:
    """Bug: the release build still shows the unreleased RENDER DISTANCE row, reads or writes its settings.ini
    key, or patches the view getter. The development build is compiled as the control that the probes see them."""
    probes = [b"RENDER DISTANCE", "distance".encode("utf-16-le"), "refused: RENDER DISTANCE".encode()]
    release, development = compiled(RELEASE), compiled(("ENRT_RENDER_DISTANCE",))
    assert [probe in release for probe in probes] == [False] * 3
    assert [probe in development for probe in probes] == [True] * 3


def test_shipped_builds_never_install_the_moving_camera_fix_hook() -> None:
    """Bug: release 1 still installs hook 72 (0x427F61), so the moving camera fix engages although its row is gone
    in release 1. The ENRT_MOVING_FIX build is the control that the probe sees it."""
    shipped = [("ENRT_LOG",), ("ENRT_TEST",), ("ENRT_LOG", "ENRT_TEST"), ()]
    assert [any(va == 0x427F61 for va, *_ in runtime.hook_sites(d)) for d in shipped] == [False] * 4
    assert any(va == 0x427F61 for va, *_ in runtime.hook_sites(("ENRT_MOVING_FIX",)))


@compiles
def test_release_runtime_has_no_moving_camera_fix_row_or_key(compiled) -> None:
    """Bug: the release or test build still draws the MOVING CAMERA FIX row or reads its settings.ini key
    in release 1. The ENRT_MOVING_FIX build is the control that the probes see them."""
    probes = [b"MOVING CAMERA FIX", "moving_fix".encode("utf-16-le")]
    dlls = [compiled(defines) for defines in (RELEASE, TEST, ("ENRT_MOVING_FIX",))]
    assert [[probe in dll for probe in probes] for dll in dlls] == [[False, False], [False, False], [True, True]]


@compiles
def test_test_build_keeps_the_harness_mailbox_version_and_size(compiled) -> None:
    """Bug: the runtime's mailbox gains, loses or resizes a field the harness does not mirror, so the harness
    refuses the build or reads every text and quad at the wrong offset."""
    import game_harness
    dll = compiled(TEST)
    at = dll.index(game_harness.TEST_MAGIC)
    assert struct.unpack_from("<2I", dll, at + 16) == (game_harness.TEST_VERSION, game_harness.TEST_SIZE)


@compiles
def test_repeated_dll_builds_do_not_add_wall_clock_timestamps(compiled, tmp_path: Path) -> None:
    runtime.compile_dll(tmp_path / "again.dll", SYNTHETIC_HEADER, TEST)
    assert (tmp_path / "again.dll").read_bytes() == compiled(TEST)


def _dispatched(function: str) -> set[int]:
    """The case labels of the hook switch in C function ``function``."""
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", layout.SOURCE, flags=re.S)
    body = re.search(rf"^static \w+ {function}\(u32 id,[^)]*\)\n\{{\n(.*?)^\}}", code, re.M | re.S)[1]
    return {int(label) for label in re.findall(r"\bcase (\d+):", body)}


def test_every_hook_a_build_installs_has_a_case_in_the_dispatch_of_its_mode() -> None:
    """Bug: a hook the build installs has no case in the runtime's dispatch for its mode (a new hook whose case was
    never written, a case in the English-only switch for a hook that runs in both modes, or a display case outside
    the ids always() hands to display_hook), so the game jumps into the hook and nothing happens."""
    low, high = map(int, re.search(r"if \(id >= (\d+) && id <= (\d+)\)\s*return display_hook\(", layout.SOURCE).groups())
    both = _dispatched("always") | {i for i in _dispatched("display_hook") if low <= i <= high}
    english = both | _dispatched("english_hook")
    installed = {(hook_id, english_only) for defines in (RELEASE, TEST, ("ENRT_MOVING_FIX",), ("ENRT_RENDER_DISTANCE",))
                 for _, _, hook_id, _, english_only in runtime.hook_sites(defines)}
    assert sorted(hook for hook, english_only in installed if hook not in (english if english_only else both)) == []


def test_scene_command_calls_the_scene_starter_the_games_own_scene_command_calls() -> None:
    """Bug: PLAY_SCENE names a shifted address, so the test-build `scene` command jumps into the middle of a function
    and crashes the game instead of playing the scene."""
    exe = Path(os.environ.get("WOZMOD_GAME") or "", "OROCHI_Z.exe")
    if not exe.is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    stock = exe.read_bytes()
    image = pe.layout(stock)
    play_scene = layout.DEFINES["PLAY_SCENE"]
    start, end = 0x4F3020, 0x4F30C1  # the category-39 handler's helper, up to where it marks scripts waiting
    code = stock[pe.file_offset(image, start):pe.file_offset(image, end)]
    targets = {start + at + 5 + struct.unpack_from("<i", code, at + 1)[0]
               for at in range(len(code) - 4) if code[at] == 0xE8}
    assert play_scene in targets


# The instruction bytes under the immediates the runtime rewrites without a stock check: the Xbox prompt icon widths.
UNCHECKED = {"START_WIDTH": b"\x6a\x1e", "STICK_WIDTH": b"\x6a\x1e",
             "WIDE_LAST_DRAW": b"\x83\xf8\x14", "WIDE_LAST_MEASURE": b"\x83\xf8\x14"}


def test_stock_code_the_runtime_writes_is_stock_and_clear_of_every_build_edit(stock_builds) -> None:
    """Bugs: a build edit or hook lands on a site the runtime rewrites itself (a new edit over the ftol call at
    0x5CD69D), so the runtime refuses its display sizes or edge HUD in game; a runtime site's address is shifted off
    the stock bytes it expects, which only the game's log would show; an icon width write lands in another
    instruction."""
    stock, handed = stock_builds
    image = pe.layout(stock)
    writes = layout.runtime_writes()
    wrong = []
    for name, va, expected, _ in writes:
        if expected is None and name in UNCHECKED:
            va, expected = va + 1 - len(UNCHECKED[name]), UNCHECKED[name]
        at = pe.file_offset(image, va)
        if expected is not None and stock[at:at + len(expected)] != expected:
            wrong.append(("not stock", name, hex(va), stock[at:at + len(expected)].hex()))
    for variant, (header, defines) in handed.items():
        spans = [(va, va + len(before)) for edits in edit_groups(header).values() for va, before, _ in edits]
        spans += [(va, va + len(prologue)) for va, prologue, *_ in runtime.hook_sites(defines)]
        spans.append((runtime.WIDTH_POINTER, runtime.WIDTH_POINTER + 4))
        wrong += [(variant, name, hex(va)) for name, va, _, size in writes
                  if any(va < end and start < va + size for start, end in spans)]
    assert wrong == []
