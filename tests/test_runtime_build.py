"""Runtime wire formats and compiler boundary checks."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from wozmod.install import STOCK_FILES
from wozmod.runtime import build
from wozmod.runtime import english_runtime as runtime


def synthetic_pe() -> bytes:
    image = bytearray(0x800)
    struct.pack_into("<I", image, 0x3C, 0x88)
    image[0x88:0x8C] = b"PE\0\0"
    struct.pack_into("<HH", image, 0x8C, 0x14C, 2)
    struct.pack_into("<H", image, 0x9C, 0x68)
    struct.pack_into("<I", image, 0xBC, 0x400000)
    struct.pack_into("<8sIIII", image, 0x108, b".first", 0x280, 0x1000, 0x200, 0x400)
    struct.pack_into("<8sIIII", image, 0x130, b".second", 0x40, 0x2000, 0x100, 0x600)
    return bytes(image)


def test_optional_header_size_does_not_shift_section_table() -> None:
    assert runtime.pe_layout(synthetic_pe()) == {
        "image_base": 0x400000,
        "sections": [
            {"name": b".first", "vsize": 0x280, "rva": 0x1000, "raw_size": 0x200, "raw": 0x400},
            {"name": b".second", "vsize": 0x40, "rva": 0x2000, "raw_size": 0x100, "raw": 0x600},
        ],
    }


def test_file_offsets_use_raw_size_and_refuse_the_virtual_tail() -> None:
    layout = {
        "image_base": 0x400000,
        "sections": [
            {"rva": 0x1000, "raw_size": 0x200, "vsize": 0x280, "raw": 0x400},
            {"rva": 0x2000, "raw_size": 0x100, "vsize": 0x40, "raw": 0x600},
        ],
    }
    assert [runtime.file_offset(layout, va) for va in [0x401000, 0x4011FF, 0x4020FF]] == [
        0x400, 0x5FF, 0x6FF,
    ]
    for va in (0x400FFF, 0x401200, 0x402100):
        with pytest.raises(runtime.RuntimeBuildError, match="not file-backed"):
            runtime.file_offset(layout, va)


@pytest.mark.parametrize("machine, signature", [(0x8664, b"PE\0\0"), (0x14C, b"PX\0\0")])
def test_non_i386_or_bad_signature_cannot_be_used_as_stock_pe(machine: int, signature: bytes) -> None:
    image = bytearray(synthetic_pe())
    image[0x88:0x8C] = signature
    struct.pack_into("<H", image, 0x8C, machine)
    with pytest.raises(runtime.RuntimeBuildError, match="not an i386 PE image"):
        runtime.pe_layout(bytes(image))


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
    )
    assert runtime.table_blob({b"a": b"left", b"i": b"right"}, [b"right", None, b"left"]) == expected


def c_array(header: str, name: str) -> bytes:
    found = re.search(rf"\b{name}(?:\[\d*\])?\s*=\s*\{{([^}}]*)\}}", header)
    assert found is not None
    return bytes(int(value, 16) for value in re.findall(r"0x[0-9a-f]+", found[1]))


def test_data_header_keeps_group_lengths_terminator_and_hook_return_flags() -> None:
    image = bytearray(0x1200)
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", image, 0x84, 0x14C, 1)
    struct.pack_into("<H", image, 0x94, 0xE0)
    struct.pack_into("<I", image, 0xB4, 0x700000)
    struct.pack_into("<8sIIII", image, 0x178, b".fixture", 0x1000, 0x9B000, 0x1000, 0x200)
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
    assert c_array(header, "enrt_edits") == (
        b"\0" + struct.pack("<IH", 0x79B010, 3) + b"abcxyz"
        + b"\x01" + struct.pack("<IH", 0x79B020, 2) + b"defg"
        + b"\x02" + struct.pack("<IH", 0x79B020, 2) + b"dehi" + b"\xff"
    )
    assert c_array(header, "enrt_stock_sha256") == bytes([0x12]) * 32
    assert c_array(header, "enrt_widths") == b"\x05\x07"
    assert "#define WIDTH_POINTER 0x79bac0" in header
    assert re.findall(r"^    \{([^{}]+), \{", header, re.MULTILINE) == [
        "0x79b030, 0, 7, 1, 5", "0x79b040, 4, 8, 2, 5",
        "0x79b050, 0, 9, 3, 5", "0x79b060, 0, 10, 0, 5",
    ]


@pytest.mark.parametrize("native", [True, False], ids=["native", "docker"])
def test_compiler_prefers_native_and_falls_back_to_pinned_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: bool,
) -> None:
    calls = []
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "native-gcc" if native else None)

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "docker":
            build = Path(command[command.index("-v") + 1].split(":", 1)[0])
        else:
            build = Path(kwargs["cwd"])
        assert (build / "enrt_data.h").read_text() == "synthetic header\n"
        assert (build / "english_runtime.c").is_file()
        assert (build / "text_fit.h").is_file() and (build / "glyph_raster.h").is_file()
        (build / "d3d9.dll").write_bytes(b"MZsynthetic-dll")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    output = tmp_path / "d3d9.dll"
    runtime.compile_dll(output, "synthetic header\n", ("ENRT_TEST",))
    assert output.read_bytes() == b"MZsynthetic-dll"
    assert len(calls) == 1
    command = calls[0]
    if native:
        assert command[0] == "i686-w64-mingw32-gcc"
    else:
        assert command[:4] == ["docker", "run", "--rm", "--network=none"]
        assert command[command.index("-w") + 2:command.index("-w") + 4] == [
            "musou-dw5s-mingw:fc44-gcc16.1.1", "i686-w64-mingw32-gcc",
        ]
    assert "-DENRT_TEST" in command


@pytest.mark.parametrize("options, expected_defines", [
    ({}, ()),
    ({"log": True, "preview": True, "test": True, "cell": 48},
     ("ENRT_LOG", "ENRT_TAG_PREVIEW", "ENRT_TEST", "ENRT_CELL=48")),
    ({"render_distance": True}, ("ENRT_RENDER_DISTANCE",)),
])
def test_build_options_do_not_drop_preview_edits_or_runtime_defines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: dict, expected_defines: tuple,
) -> None:

    stock = b"synthetic stock image"
    source = tmp_path / "stock.exe"
    source.write_bytes(stock)
    output = tmp_path / "nested" / "d3d9.dll"
    font = [(0x1000, b"a", b"b")]
    code = [(0x2000, b"c", b"d")]
    rows = {1: [(0x3000, b"e", b"f")], 2: [(0x3000, b"e", b"g")]}
    asked = []
    calls = []
    monkeypatch.setattr(build, "FONT_ROUTE", font)
    monkeypatch.setattr(build, "CODE_EDITS", code)
    monkeypatch.setattr(build, "WIDTHS", b"widths")
    monkeypatch.setattr(build, "settings_rows", lambda count, distance: asked.append((count, distance)) or rows[count])
    monkeypatch.setattr(build, "hook_sites", lambda defines: [("hooks", defines)])

    def fake_header(*args):
        calls.append(args)
        return "synthetic generated header"

    def fake_compile(path, header, defines):
        assert (path, header, defines) == (output, "synthetic generated header", expected_defines)
        path.write_bytes(b"synthetic result")

    monkeypatch.setattr(build, "data_header", fake_header)
    monkeypatch.setattr(build, "compile_dll", fake_compile)
    assert build.build_dll(source, output, **options) == output
    assert output.read_bytes() == b"synthetic result"
    # Bug: a release build keeps the cursor slot of the compiled-out RENDER DISTANCE row.
    assert asked == [(1, options.get("render_distance", False)), (2, options.get("render_distance", False))]
    preview_edits = [(va, b"\x74\x04", b"\x90\x90")
                     for va in (0x57296B, 0x572991, 0x5729B4, 0x5729DA, 0x572A01)] if options.get("preview") else []
    assert calls == [(stock, {0: font + code + preview_edits, 1: rows[1], 2: rows[2]},
                      [("hooks", expected_defines)], b"widths", hashlib.sha256(stock).hexdigest())]


def _need_compiler() -> None:
    if shutil.which("i686-w64-mingw32-gcc"):
        return
    if not shutil.which("docker"):
        pytest.skip("native compiler and Docker executable absent")
    inspection = subprocess.run(
        ["docker", "image", "inspect", runtime.MINGW_IMAGE], capture_output=True, text=True,
    )
    if inspection.returncode:
        if "No such image" in inspection.stderr:
            pytest.skip("pinned Docker image absent")
        if "Cannot connect to the Docker daemon" in inspection.stderr:
            pytest.skip("Docker service unavailable")
        pytest.fail(f"Docker image inspection failed: {inspection.stderr.strip()}")


SYNTHETIC_HEADER = "\n".join([
    "static const u8 enrt_stock_sha256[32] = {0};",
    "static const u8 enrt_edits[] = {0xff};",
    "static const struct site enrt_hooks[] = {{0x5c9fd0, 0, 24, 0, 5, {0x83,0xec,0x1c,0x53,0x55}}};",
    "static const u8 enrt_widths[1] = {24};",
    "#define WIDTH_POINTER 0x79bac0", "",
])


@pytest.fixture(scope="module")
def compiled_test_dlls(tmp_path_factory: pytest.TempPathFactory) -> tuple[bytes, bytes]:
    _need_compiler()
    directory = tmp_path_factory.mktemp("runtime-compile")
    first, second = directory / "first.dll", directory / "second.dll"
    for output in (first, second):
        runtime.compile_dll(output, SYNTHETIC_HEADER, ("ENRT_TEST", "ENRT_LOG"))
    return first.read_bytes(), second.read_bytes()


@pytest.mark.skipif(os.environ.get("WOZMOD_RUNTIME_COMPILE") != "1",
                    reason="set WOZMOD_RUNTIME_COMPILE=1")
def test_release_runtime_has_no_render_distance_row_key_or_patch(tmp_path: Path) -> None:
    """Bug: the release build still shows the unreleased RENDER DISTANCE row, reads or writes its settings.ini
    key, or patches the view getter. The development build is compiled as the control that the probes see them."""
    _need_compiler()
    probes = [b"RENDER DISTANCE", "distance".encode("utf-16-le"), "refused: RENDER DISTANCE".encode()]
    dlls = {}
    for name, defines in [("release", ()), ("development", ("ENRT_RENDER_DISTANCE",))]:
        runtime.compile_dll(tmp_path / f"{name}.dll", SYNTHETIC_HEADER, defines)
        dlls[name] = (tmp_path / f"{name}.dll").read_bytes()
    assert [probe in dlls["release"] for probe in probes] == [False] * 3
    assert [probe in dlls["development"] for probe in probes] == [True] * 3


@pytest.mark.skipif(os.environ.get("WOZMOD_RUNTIME_COMPILE") != "1",
                    reason="set WOZMOD_RUNTIME_COMPILE=1")
def test_test_build_keeps_the_harness_mailbox_version_and_size(compiled_test_dlls: tuple[bytes, bytes]) -> None:
    dll, _ = compiled_test_dlls
    at = dll.index(b"ENRT-TEST-BLOCK\0")
    assert struct.unpack_from("<2I", dll, at + 16) == (2, 105796)


@pytest.mark.skipif(os.environ.get("WOZMOD_RUNTIME_COMPILE") != "1",
                    reason="set WOZMOD_RUNTIME_COMPILE=1")
def test_repeated_dll_builds_do_not_add_wall_clock_timestamps(compiled_test_dlls: tuple[bytes, bytes]) -> None:
    first, second = compiled_test_dlls
    assert first == second


def test_every_edit_and_hook_sits_on_the_supported_stock_exe(tmp_path: Path, monkeypatch) -> None:
    """Bug: an edit or hook prologue sits over the wrong stock bytes (a shifted address), or overlaps another,
    in a release or development build; the DLL build against the real exe is the oracle, minus the compiler."""
    exe = Path(os.environ.get("WOZMOD_GAME") or "", "OROCHI_Z.exe")
    if not exe.is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    stock = exe.read_bytes()
    assert (len(stock), hashlib.sha256(stock).hexdigest()) == STOCK_FILES["OROCHI_Z.exe"]
    compiled = []
    monkeypatch.setattr(build, "compile_dll", lambda path, header, defines: compiled.append(defines))
    for options in ({}, {"log": True, "preview": True, "test": True, "render_distance": True}):
        build.build_dll(exe, tmp_path / "d3d9.dll", **options)
    assert len(compiled) == 2


def test_xbox_prompt_writes_land_on_the_stock_icon_width_and_advance_bytes() -> None:
    """A shifted address would write 60 or 0x15 into another instruction byte."""
    game = os.environ.get("WOZMOD_GAME")
    exe = Path(game or "", "OROCHI_Z.exe")
    if not game or not exe.is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    stock = exe.read_bytes()
    source = (Path(__file__).resolve().parents[1] / "runtime/english_runtime.c").read_text()
    layout = runtime.pe_layout(stock)
    expected = {"START_WIDTH": b"\x6a\x1e", "STICK_WIDTH": b"\x6a\x1e",
                "WIDE_LAST_DRAW": b"\x83\xf8\x14", "WIDE_LAST_MEASURE": b"\x83\xf8\x14"}
    wrong = []
    for name, opcode in expected.items():
        va = int(re.search(rf"#define {name} (0x[0-9A-Fa-f]+)", source)[1], 16)
        at = runtime.file_offset(layout, va) + 1 - len(opcode)
        if stock[at:at + len(opcode)] != opcode:
            wrong.append((name, hex(va), stock[at:at + len(opcode)].hex()))
    assert wrong == []
