"""Find canonical stock strings for the hash-keyed text data."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from wozmod.formats.linkdata import archive
from wozmod.formats.scripts import EVENT_TEXT, event_lines, quote_tables
from wozmod.runtime.english_runtime import file_offset, pe_layout

from .build import NAME_PARTS, NAME_TABLE, TableBuildError
from .inventory import EVENTS, inventory
from .psp_exe import EXE_STRING
from .script_alignment import QUOTES


def stock_strings(stock: Path) -> dict[str, bytes]:
    """SHA-256 of canonical CP932 bytes mapped to those bytes, without donors."""
    found: dict[str, bytes] = {}

    def add(value: bytes) -> None:
        try:
            canonical = value.decode("cp932").encode("cp932")
        except UnicodeError:
            return
        found[hashlib.sha256(canonical).hexdigest()] = canonical

    for source in inventory(stock).values():
        for value in source:
            add(value)
    linkdata2 = archive(stock / "LINKDATA2")
    for data in linkdata2.read(o for o in EVENTS if o in linkdata2).values():
        for value in event_lines(data).values():
            add(value)
        for match in EVENT_TEXT.finditer(data):
            add(match.group(1) or match.group(2))
    exe = (stock / "OROCHI_Z.exe").read_bytes()
    for match in EXE_STRING.finditer(exe):
        add(match.group())
    layout = pe_layout(exe)
    for index in range(NAME_PARTS):
        pointer = file_offset(layout, NAME_TABLE + 8 * index)
        at = file_offset(layout, struct.unpack_from("<I", exe, pointer)[0])
        add(exe[at:exe.index(b"\0", at)])
    quote_data = archive(stock / "LINKDATA1")[QUOTES[0]]
    for table in quote_tables(quote_data):
        for line in table:
            add(line[2:])
    return found


def resolve_stock_ref(stock: Path, reference: list, key: str) -> bytes:
    """Resolve [exe, file offset, byte length] and verify its canonical hash."""
    if not isinstance(reference, list) or len(reference) != 3 or reference[0] != "exe":
        raise TableBuildError(f"row {key}: invalid stock reference")
    _, offset, length = reference
    if type(offset) is not int or type(length) is not int or offset < 0 or length <= 0:
        raise TableBuildError(f"row {key}: invalid stock reference span")
    exe = (stock / "OROCHI_Z.exe").read_bytes()
    raw = exe[offset:offset + length]
    if len(raw) != length:
        raise TableBuildError(f"row {key}: stock reference exceeds executable")
    try:
        canonical = raw.decode("cp932").encode("cp932")
    except UnicodeError as error:
        raise TableBuildError(f"row {key}: stock reference is not CP932") from error
    if hashlib.sha256(canonical).hexdigest() != key:
        raise TableBuildError(f"row {key}: stock reference hash differs")
    return canonical
