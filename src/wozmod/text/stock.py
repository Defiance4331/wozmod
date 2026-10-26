"""Find canonical stock strings for the hash-keyed text data."""

from __future__ import annotations

import hashlib
from pathlib import Path

from wozmod.formats.audio import MOZ_EVENT_LINES, WO1_EVENT_VOICES, WO2_EVENT_VOICES
from wozmod.formats.linkdata import archive
from wozmod.formats.pe import pointer_strings
from wozmod.formats.scripts import EVENT_TEXT, event_lines, mesc_blocks, quote_tables, quote_text

from .build import NAME_PARTS, NAME_TABLE, TableBuildError
from .inventory import EVENTS, inventory
from .narration_voices import VOICE_KEYED, line_voices
from .psp_exe import EXE_STRING
from .script_alignment import EVENTS as PAIRED_EVENTS
from .script_alignment import QUOTES, moz_scripts


def stock_key(raw: bytes) -> tuple[str, bytes] | None:
    """(SHA-256 hex, canonical CP932 bytes) that key a translation row; None when ``raw`` is not CP932."""
    try:
        canonical = raw.decode("cp932").encode("cp932")
    except UnicodeError:
        return None
    return hashlib.sha256(canonical).hexdigest(), canonical


def event_voice_form(text: bytes, voice: int) -> bytes:
    """An event, narration page or still event line with its voice id, as the runtime looks it up before the text alone
    (runtime event_line)."""
    return text + b"@V%04d" % voice


def stage_line_form(text: bytes, stage: int, line: int) -> bytes:
    """A spoken stage line (its text with its @NNNN suffix) with its stage and dialog line, as the runtime looks it up
    before the line alone (runtime spoken_text, voice_slots.h): two lines can carry one string yet play different
    takes (wozmod.voices.maps.voice_slots), and each needs its own row."""
    return text + b"@L%d.%d" % (stage, line)


def event_voices(linkdata2) -> dict[int, tuple[int, int]]:
    """Stock event ordinal to (voice id of line 0, voiced line count): the voice start adds a line's index to its
    story's base."""
    blocks = {"wo1-x360": WO1_EVENT_VOICES, "wo2-x360": WO2_EVENT_VOICES}
    return {o: (blocks[source][0], MOZ_EVENT_LINES[source]) for ordinals, source, _ in PAIRED_EVENTS for o in ordinals
            if o in linkdata2}


def stock_strings(stock: Path) -> dict[str, bytes]:
    """SHA-256 of canonical CP932 bytes mapped to those bytes, without donors.

    A spoken line or officer quote is also listed with its @NNNN voice suffix: the runtime looks the whole line up
    first (runtime voiced_line), so a row keyed that way gives one voiced line its own English where another line
    with the same text is voiced from another donor. An event line, a WO1 narration page line and a still event's
    line (camp talk) are listed with their voice id the same way, and a spoken stage line with its stage and line."""
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
    for ordinal, (base, count) in event_voices(linkdata2).items():
        for index, value in event_lines(linkdata2[ordinal]).items():
            if index < count:
                add(event_voice_form(value, base + index))
    linkdata1 = archive(stock / "LINKDATA1")
    exe = (stock / "OROCHI_Z.exe").read_bytes()
    for entry in (e for e in VOICE_KEYED if e in linkdata1):
        data = linkdata1[entry]
        if lines := [line for block in mesc_blocks(data) for line in block]:
            for value, voice in zip(lines, line_voices(exe, entry, data), strict=True):
                add(event_voice_form(value, voice))
    for stage, script in moz_scripts(stock / "LINKDATA2").items():
        for record in script.notices.records:  # every record, also those no command shows (the inventory's are shown)
            add(record.text_bytes)
        for line, record in enumerate(script.dialog.records):
            add(record.text_bytes)
            if record.dialog_suffix:
                add(record.value[2:])
                add(stage_line_form(record.value[2:], stage, line))
    for match in EXE_STRING.finditer(exe):
        add(match.group())
    for value in pointer_strings(exe, NAME_TABLE, NAME_PARTS, 8):
        add(value)
    quote_data = archive(stock / "LINKDATA1")[QUOTES[0]]
    for table in quote_tables(quote_data):
        for line in table:  # every slot, also those no game shows (the inventory lists only the shown ones)
            add(line[2:])
            add(quote_text(line))
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
