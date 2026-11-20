"""Find canonical stock strings for the hash-keyed text data."""

from __future__ import annotations

from collections import ChainMap
from pathlib import Path

from wozmod import game_files
from wozmod.formats.audio import event_voice
from wozmod.formats.pe import pointer_strings
from wozmod.formats.scripts import EVENT_TEXT, event_lines, mesc_blocks, quote_tables, quote_text

from .keys import english_voice_form, event_voice_form, stock_key
from .build import NAME_PARTS, NAME_TABLE, TableBuildError
from .inventory import EVENTS, inventory
from .narration_voices import VOICE_KEYED, line_voices
from .script_alignment import QUOTES, moz_scripts


def stage_line_form(text: bytes, stage: int, line: int) -> bytes:
    """A spoken stage line (its text with its @NNNN suffix) with its stage and dialog line, as the runtime looks it up
    before the line alone (runtime spoken_text, voice_slots.h): two lines can carry one string yet play different
    takes (wozmod.voices.maps.voice_slots), and each needs its own row."""
    return text + b"@L%d.%d" % (stage, line)


def stock_strings(stock: Path) -> ChainMap:
    """SHA-256 of canonical CP932 bytes mapped to those bytes, without donors, to look keys up in.

    A spoken line or officer quote is also listed with its @NNNN voice suffix: the runtime looks the whole line up
    first (runtime voiced_line), so a row keyed that way gives one voiced line its own English where another line
    with the same text is voiced from another donor. An event line, a WO1 narration page line and a still event's
    line (camp talk) are listed with their voice id the same way, and a spoken stage line with its stage and line.
    Last come the exe's other strings (game_files.StockBytes.strings), which only a lookup reads."""
    found: dict[str, bytes] = {}

    def add(value: bytes) -> None:
        if keyed := stock_key(value):
            found[keyed[0]] = keyed[1]

    for source in inventory(stock).values():
        for value in source:
            add(value)
    linkdata2 = game_files.archive(stock / "LINKDATA2")
    for ordinal, data in linkdata2.read(o for o in EVENTS if o in linkdata2).items():
        for index, value in event_lines(data).items():
            add(value)
            if (voice := event_voice(ordinal, index)) is not None:  # the Z Dream scenes too, as the runtime keys them
                add(event_voice_form(value, voice))
                add(english_voice_form(value, voice))
        for match in EVENT_TEXT.finditer(data):
            add(match.group(1) or match.group(2))
    linkdata1 = game_files.archive(stock / "LINKDATA1")
    exe = game_files.exe(stock)
    for entry in (e for e in VOICE_KEYED if e in linkdata1):
        data = linkdata1[entry]
        if lines := [line for block in mesc_blocks(data) for line in block]:
            for value, voice in zip(lines, line_voices(exe, entry, data), strict=True):
                add(event_voice_form(value, voice))
                add(english_voice_form(value, voice))
    for stage, script in moz_scripts(stock / "LINKDATA2").items():
        for record in script.notices.records:  # every record, also those no command shows (the inventory's are shown)
            add(record.text_bytes)
        for line, record in enumerate(script.dialog.records):
            add(record.text_bytes)
            if record.dialog_suffix:
                add(record.value[2:])
                add(stage_line_form(record.value[2:], stage, line))
    for value in pointer_strings(exe, NAME_TABLE, NAME_PARTS, 8):
        add(value)
    quote_data = linkdata1[QUOTES[0]]
    for table in quote_tables(quote_data):
        for line in table:  # every slot, also those no game shows (the inventory lists only the shown ones)
            add(line[2:])
            add(quote_text(line))
    return ChainMap(found, exe.strings(stock_key))


def resolve_stock_ref(stock: Path, reference: list, key: str) -> bytes:
    """Resolve [exe, file offset, byte length] and verify its canonical hash."""
    if not isinstance(reference, list) or len(reference) != 3 or reference[0] != "exe":
        raise TableBuildError(f"row {key}: invalid stock reference")
    _, offset, length = reference
    if type(offset) is not int or type(length) is not int or offset < 0 or length <= 0:
        raise TableBuildError(f"row {key}: invalid stock reference span")
    raw = game_files.exe(stock)[offset:offset + length]
    if len(raw) != length:
        raise TableBuildError(f"row {key}: stock reference exceeds executable")
    keyed = stock_key(raw)
    if keyed is None:
        raise TableBuildError(f"row {key}: stock reference is not CP932")
    if keyed[0] != key:
        raise TableBuildError(f"row {key}: stock reference hash differs")
    return keyed[1]
