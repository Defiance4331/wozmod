"""Which wozmod features another mod's OROCHI_Z.exe edits touch, read from the DLL's own guard list.

The DLL exports the guard list it checks as the game starts (``wozmod_guards``, wozmod.runtime.guards). This reads
those bytes back and compares each guard with the player's executable, as the DLL compares them with the game as
loaded. When any guard differs the DLL patches nothing at all, so the installer stops and names these features.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from wozmod.formats import pe

from .guards import MAGIC, VERSION


@dataclass(frozen=True)
class GuardList:
    build: tuple[int, int]  # TimeDateStamp and SizeOfImage of the executable the DLL patches
    names: list[str]  # per feature
    guards: list[tuple[int, int, bytes]]  # (feature, VA, stock bytes)


def decode(data: bytes) -> GuardList:
    """Parse a wozmod_guards blob (layout in guards.blob)."""
    if data[:8] != MAGIC or struct.unpack_from("<H", data, 8)[0] != VERSION:
        raise ValueError("not a wozmod guard list of a known version")
    count, stamp, size, guard_count = struct.unpack_from("<HIII", data, 10)
    at, names, guards = 24, [], []
    for _ in range(count):
        length = data[at]
        names.append(data[at + 1:at + 1 + length].decode("ascii"))
        at += 1 + length
    for _ in range(guard_count):
        feature, va, length = struct.unpack_from("<BIH", data, at)
        guards.append((feature, va, data[at + 7:at + 7 + length]))
        at += 7 + length
    return GuardList((stamp, size), names, guards)


def dll_guards(dll: bytes) -> GuardList:
    """The guard list a built d3d9.dll exports."""
    va = pe.exports(dll).get("wozmod_guards")
    if va is None:
        raise ValueError("the DLL exports no wozmod_guards")
    return decode(dll[pe.file_offset(pe.layout(dll), va):])


def refused(guards: GuardList, exe_bytes: bytes) -> list[str]:
    """The names of the features with a guard whose bytes differ in this executable, in the list's order; any name
    means the DLL patches nothing. Every feature when the executable is not the build the list is for."""
    if pe.build_stamp(exe_bytes) != guards.build:
        return list(guards.names)
    where = pe.layout(exe_bytes)
    differ = 0
    for feature, va, stock in guards.guards:
        try:
            at = pe.file_offset(where, va)
        except pe.PEError:
            differ |= 1 << feature
            continue
        if exe_bytes[at:at + len(stock)] != stock:
            differ |= 1 << feature
    return [name for i, name in enumerate(guards.names) if differ >> i & 1]


def refused_features(exe_bytes: bytes, dll_bytes: bytes) -> list[str]:
    """The names of the features whose code another mod changed, as the DLL ``dll_bytes`` finds them when the game's
    OROCHI_Z.exe is ``exe_bytes``. Any name means the DLL leaves all of wozmod off."""
    return refused(dll_guards(dll_bytes), exe_bytes)
