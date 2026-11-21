"""The DLLs a Windows library needs beside it: what its import table names, less what Windows itself provides.

A release carries libraries no packaging tool looked into (ONNX Runtime, in the Dream voice separator's folder). On a
PC without the Visual C++ redistributable, a library whose imports are not in the download fails to load. The set is
read from the library, not listed by hand: an import nobody listed stops the build here instead of an install there.

    python packaging/windows_imports.py FILE...    prints each file's imports that Windows does not provide; for a
                                                   release zip, what each of its libraries needs and the zip lacks
"""

from __future__ import annotations

import struct
import sys
import zipfile
from pathlib import Path

# What Windows 10 and later provide by themselves, lower case: the API sets by prefix, the rest by name. A name that
# is neither is the release's to carry.
PROVIDED_PREFIXES = ("api-ms-win-", "ext-ms-win-")
PROVIDED = frozenset({
    "advapi32.dll", "bcrypt.dll", "bcryptprimitives.dll", "comctl32.dll", "comdlg32.dll", "crypt32.dll", "dbghelp.dll",
    "dxgi.dll", "gdi32.dll", "imm32.dll", "iphlpapi.dll", "kernel32.dll", "msvcrt.dll", "netapi32.dll", "ntdll.dll",
    "ole32.dll", "oleaut32.dll", "propsys.dll", "psapi.dll", "rpcrt4.dll", "secur32.dll", "setupapi.dll",
    "shell32.dll", "shlwapi.dll", "user32.dll", "userenv.dll", "version.dll", "winmm.dll", "winspool.drv",
    "ws2_32.dll",
})


def imports(data: bytes) -> list[str]:
    """The DLL names in a PE file's import table and its delay-load table, as written, in the file's order."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[:2] != b"MZ" or data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("not a PE file")
    sections, optional_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    directories = optional + {0x10B: 96, 0x20B: 112}[struct.unpack_from("<H", data, optional)[0]]  # PE32, PE32+
    count = struct.unpack_from("<I", data, directories - 4)[0]
    table = [struct.unpack_from("<IIIII", data, optional + optional_size + 40 * k + 8) for k in range(sections)]

    def offset(rva: int) -> int:  # where a virtual address lies in the file
        for size, address, raw_size, raw, _ in table:
            if address <= rva < address + max(size, raw_size):
                return raw + rva - address
        raise ValueError(f"address {rva:#x} lies in no section")

    def name(rva: int) -> str:
        start = offset(rva)
        return data[start:data.index(b"\0", start)].decode("ascii")

    names = []
    # directory 1: import descriptors of 20 bytes, the name's address fourth; 13: delay-load ones of 32, it second
    for directory, step, field in ((1, 20, 12), (13, 32, 4)):
        rva = struct.unpack_from("<I", data, directories + 8 * directory)[0] if directory < count else 0
        at = offset(rva) if rva else None
        while at is not None and any(data[at:at + step]):
            names.append(name(struct.unpack_from("<I", data, at + field)[0]))
            at += step
    return names


def needed(data: bytes) -> list[str]:
    """The imports of a PE file that Windows does not provide, lower case."""
    found = dict.fromkeys(each.lower() for each in imports(data))
    return [each for each in found if each not in PROVIDED and not each.startswith(PROVIDED_PREFIXES)]


def bundle(library: Path, wheel: Path, also: Path | None = None) -> list[str]:
    """Put beside library each DLL it needs that is not there yet, and each DLL those need, taken from wheel (a
    pinned wheel that holds the Visual C++ runtime's files); return the names written. A DLL in the folder `also`
    counts as there: the program's own folder, which Windows searches for what a library it loads needs. Stops the
    build when a needed DLL is nowhere and the wheel has no such file."""
    there = {path.name.lower() for folder in (library.parent, also) if folder for path in folder.iterdir()}
    written, todo = [], [library]
    with zipfile.ZipFile(wheel) as archive:
        members = {member.rsplit("/", 1)[-1].lower(): member for member in sorted(archive.namelist())}
        while todo:
            needs = todo.pop()
            for each in needed(needs.read_bytes()):
                if each in there:
                    continue
                if each not in members:
                    raise SystemExit(f"{needs.name} imports {each}, which Windows does not provide (packaging/"
                                     f"windows_imports.py PROVIDED), is not in the release and is not in {wheel.name}")
                (library.parent / each).write_bytes(archive.read(members[each]))
                there.add(each)
                written.append(each)
                todo.append(library.parent / each)
    return written


# The Visual C++ runtime's DLLs, by how their names start. Many PCs hold an older copy of them in System32, and
# Windows looks there before the program's own DLL folder, so one of these counts as the release's only where Windows
# looks before System32.
RUNTIME_PREFIXES = ("vcruntime", "msvcp", "concrt", "vccorlib", "vcomp", "vcamp")


def missing(zip_path: Path) -> dict[str, list[str]]:
    """Per program or library in a release zip, the DLLs it needs that Windows would not find in the zip, or would
    find elsewhere first: empty for a zip every library of which loads with the release's own files.

    Where Windows looks for what a library imports, as the release loads its libraries (Python its extension
    modules, the separator ONNX Runtime: both with the library's own folder first): a DLL already in the process,
    the importing library's folder, the program's folder, System32, and last the program's DLL folder
    (_internal/). So a DLL beside its importer is found, and so is one the program has loaded before any library
    of ours (what python*.dll imports, from its folder). A DLL that lies only in the program's folders is found
    there unless System32 has one of its name: true of the Visual C++ runtime (RUNTIME_PREFIXES), not of the rest."""
    found = {}
    with zipfile.ZipFile(zip_path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        folders: dict[str, set[str]] = {}
        for name in names:
            folder, _, file = name.rpartition("/")
            folders.setdefault(folder, set()).add(file.lower())
        top = names[0].split("/", 1)[0]
        internal = f"{top}/_internal"
        own = folders.get(top, set()) | folders.get(internal, set())
        loaded = set()  # in the process from its start: the Python library and what it imports from beside it
        for name in names:
            folder, _, file = name.rpartition("/")
            if folder == internal and file.lower().startswith("python") and file.lower().endswith(".dll"):
                loaded |= {file.lower(), *(each for each in needed(archive.read(name)) if each in folders[internal])}
        for name in names:
            if name.lower().endswith((".dll", ".pyd", ".exe")):
                beside = folders[name.rpartition("/")[0]]
                lacking = [each for each in needed(archive.read(name))
                           if each not in beside | loaded and (each.startswith(RUNTIME_PREFIXES) or each not in own)]
                if lacking:
                    found[name] = lacking
    return found


def differing(zip_path: Path) -> dict[str, list[str]]:
    """The DLL names a release zip holds more than once with different bytes, each with where: a process takes the
    first of a name it loads for every library that imports it, so two versions of one name mean a library may run
    on an older one than it was shipped with."""
    seen: dict[str, dict[int, list[str]]] = {}
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.filename.lower().endswith(".dll"):
                seen.setdefault(info.filename.rpartition("/")[2].lower(), {}).setdefault(info.CRC, []).append(info.filename)
    return {name: sorted(path for paths in versions.values() for path in paths) for name, versions in seen.items() if len(versions) > 1}


if __name__ == "__main__":
    for file in sys.argv[1:]:
        print(file, (missing(Path(file)), differing(Path(file))) if file.endswith(".zip") else needed(Path(file).read_bytes()))
