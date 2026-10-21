"""Find canonical stock strings for the hash-keyed text data."""

from __future__ import annotations

import hashlib
from pathlib import Path

from wozmod.formats.linkdata import archive
from wozmod.formats.pe import pointer_strings
from wozmod.formats.scripts import EVENT_TEXT, event_lines, quote_tables

from .build import NAME_PARTS, NAME_TABLE, TableBuildError
from .inventory import EVENTS, inventory
from .psp_exe import EXE_STRING
from .script_alignment import QUOTES


def stock_key(raw: bytes) -> tuple[str, bytes] | None:
    """(SHA-256 hex, canonical CP932 bytes) that key a translation row; None when ``raw`` is not CP932."""
    try:
        canonical = raw.decode("cp932").encode("cp932")
    except UnicodeError:
        return None
    return hashlib.sha256(canonical).hexdigest(), canonical


def stock_strings(stock: Path) -> dict[str, bytes]:
    """SHA-256 of canonical CP932 bytes mapped to those bytes, without donors."""
    found: dict[str, bytes] = {}

    def add(value: bytes) -> None:
        if keyed := stock_key(value):
            found[keyed[0]] = keyed[1]

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
    for value in pointer_strings(exe, NAME_TABLE, NAME_PARTS, 8):
        add(value)
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
    keyed = stock_key(raw)
    if keyed is None:
        raise TableBuildError(f"row {key}: stock reference is not CP932")
    if keyed[0] != key:
        raise TableBuildError(f"row {key}: stock reference hash differs")
    return keyed[1]
