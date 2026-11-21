"""packaging/windows_imports.py: the DLLs a Windows library needs beside it, read from its import table."""

from __future__ import annotations

import importlib.util
import os
import struct
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_imports", REPO / "packaging/windows_imports.py")
windows_imports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(windows_imports)


def _library(imports: list[str], delayed: list[str] = ()) -> bytes:
    """A 64-bit PE file whose import table names imports and whose delay-load table names delayed: the headers as a
    linker writes them, and one section holding both tables and the names."""
    base, raw = 0x1000, 0x200  # the section's virtual address and its place in the file
    tables = 20 * (len(imports) + 1) + 32 * (len(delayed) + 1)
    names, body = {}, b""
    for name in (*imports, *delayed):
        names[name] = base + tables + len(body)
        body += name.encode("ascii") + b"\0"
    section = b"".join(struct.pack("<IIIII", 0, 0, 0, names[name], 0) for name in imports) + bytes(20)
    delay_at = base + len(section)
    section += b"".join(struct.pack("<IIIIIIII", 1, names[name], 0, 0, 0, 0, 0, 0) for name in delayed) + bytes(32) + body
    directories = [(0, 0)] * 16
    directories[1] = (base, 20 * (len(imports) + 1))
    directories[13] = (delay_at, 32 * (len(delayed) + 1)) if delayed else (0, 0)
    optional = struct.pack("<H", 0x20B) + bytes(106) + struct.pack("<I", 16) + b"".join(struct.pack("<II", *each) for each in directories)
    header = (b"MZ" + bytes(0x3A) + struct.pack("<I", 0x40) + b"PE\0\0"
              + struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, len(optional), 0x2022) + optional
              + b".rdata\0\0" + struct.pack("<IIII", len(section), base, len(section), raw) + bytes(16))
    return header + bytes(raw - len(header)) + section


def test_a_library_gets_every_dll_it_imports_that_windows_lacks_beside_it(tmp_path):
    # Bug: a library the release carries imports a DLL nobody listed (ONNX Runtime 1.32 imports vcruntime140_1.dll,
    # which the hand-kept list lacked), an import after the first or in the delay-load table is not read, or what a
    # bundled DLL itself imports is forgotten: on a PC without the Visual C++ redistributable the library does not
    # load and the install stops. Or a DLL no source has is passed over in silence.
    folder, program = tmp_path / "library", tmp_path / "program"
    folder.mkdir()
    program.mkdir()
    (program / "Python3.dll").write_bytes(b"the program's")
    library = folder / "runtime.dll"
    library.write_bytes(_library(["KERNEL32.dll", "MSVCP140.dll", "api-ms-win-crt-heap-l1-1-0.dll", "python3.dll"], ["VCRUNTIME140_1.dll"]))
    with zipfile.ZipFile(tmp_path / "runtime.whl", "w") as wheel:
        wheel.writestr("x.data/data/msvcp140.dll", _library(["kernel32.dll", "vcruntime140.dll"]))
        wheel.writestr("x.data/data/Scripts/vcruntime140.dll", _library(["kernel32.dll"]))
        wheel.writestr("x.data/data/Scripts/vcruntime140_1.dll", _library(["vcruntime140.dll"]))
        wheel.writestr("x.data/data/concrt140.dll", _library([]))

    written = windows_imports.bundle(library, tmp_path / "runtime.whl", also=program)

    assert sorted(written) == ["msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"]
    assert sorted(path.name for path in folder.iterdir()) == ["msvcp140.dll", "runtime.dll", "vcruntime140.dll", "vcruntime140_1.dll"]
    library.write_bytes(_library(["msvcp140.dll", "msvcp140_9.dll"]))
    with pytest.raises(SystemExit, match="msvcp140_9.dll"):
        windows_imports.bundle(library, tmp_path / "runtime.whl")


def test_a_zip_is_told_which_dlls_windows_would_not_find_in_it_first(tmp_path):
    # Bug: the check of the Windows download passes a library whose import Windows would take from elsewhere: it
    # counts a DLL in some other folder of the zip, where Windows never looks, or a Visual C++ runtime DLL in the
    # program's DLL folder, which Windows reaches only after System32 (where a PC's own, older copy wins: ONNX
    # Runtime then crashes), or it looks only at the first library.
    with zipfile.ZipFile(tmp_path / "made.zip", "w") as made:
        made.writestr("wozmod/wozmod.exe", _library(["kernel32.dll", "python314.dll"]))
        made.writestr("wozmod/_internal/python314.dll", _library(["VCRUNTIME140.dll"]))
        made.writestr("wozmod/_internal/vcruntime140.dll", _library([]))
        made.writestr("wozmod/_internal/msvcp140.dll", _library([]))
        made.writestr("wozmod/_internal/libffi-8.dll", _library([]))
        # found: what Python loaded at the start, and a DLL of ours that only the program's folder holds
        made.writestr("wozmod/_internal/sub/fine.pyd", _library(["python314.dll", "vcruntime140.dll", "libffi-8.dll"]))
        # not found first: a runtime DLL that only the program's folder holds, behind System32
        made.writestr("wozmod/_internal/sub/shadowed.pyd", _library(["MSVCP140.dll"]))
        made.writestr("wozmod/_internal/voice/windows/runtime.dll", _library(["MSVCP140.dll", "msvcp140_1.dll", "nowhere.dll"]))
        made.writestr("wozmod/_internal/voice/windows/msvcp140_1.dll", _library(["vcruntime140_1.dll"]))
        made.writestr("wozmod/_internal/other/vcruntime140_1.dll", _library([]))

    assert windows_imports.missing(tmp_path / "made.zip") == {
        "wozmod/_internal/sub/shadowed.pyd": ["msvcp140.dll"],
        "wozmod/_internal/voice/windows/runtime.dll": ["msvcp140.dll", "nowhere.dll"],
        "wozmod/_internal/voice/windows/msvcp140_1.dll": ["vcruntime140_1.dll"]}


def test_a_zip_is_told_which_dll_names_it_holds_in_two_versions(tmp_path):
    # Bug: the download carries an older Visual C++ runtime DLL in one folder and a newer one in another: whichever
    # the process loads first serves every library, so one built for the newer runs on the older.
    with zipfile.ZipFile(tmp_path / "made.zip", "w") as made:
        made.writestr("wozmod/_internal/VCRUNTIME140.dll", _library(["kernel32.dll"]))
        made.writestr("wozmod/_internal/voice/vcruntime140.dll", _library([]))
        made.writestr("wozmod/_internal/msvcp140.dll", _library([]))
        made.writestr("wozmod/_internal/voice/msvcp140.dll", _library([]))

    assert windows_imports.differing(tmp_path / "made.zip") == {
        "vcruntime140.dll": ["wozmod/_internal/VCRUNTIME140.dll", "wozmod/_internal/voice/vcruntime140.dll"]}


@pytest.mark.skipif(not os.environ.get("WOZMOD_RELEASE"), reason="set WOZMOD_RELEASE to release zips (os.pathsep list)")
def test_the_windows_release_holds_every_dll_its_programs_and_libraries_import():
    # Bug: the Windows download lacks a DLL one of its libraries imports, holds it where Windows looks only after
    # System32, or holds two versions of it: the library then loads only on PCs that happen to have the right one.
    # A new version of a bundled library that adds an import, or a packaging step that drops or moves a file.
    zips = [release for release in os.environ["WOZMOD_RELEASE"].split(os.pathsep) if "windows" in Path(release).name]

    assert {release: (windows_imports.missing(Path(release)), windows_imports.differing(Path(release))) for release in zips} \
        == dict.fromkeys(zips, ({}, {}))
