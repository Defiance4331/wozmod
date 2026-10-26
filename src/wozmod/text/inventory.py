"""Read stock strings and their drawing sites without running the game."""

from __future__ import annotations

import re
import struct
from pathlib import Path

from wozmod.formats.linkdata import entry_bytes, moz_entries, parse_index
from wozmod.formats.pe import pointer_strings
from wozmod.formats.scripts import NOTICE, SPEAK, event_texts, mesc_blocks, parse_script, quote_tables, quote_text
from wozmod.runtime.shared import DEFINES

from .script_alignment import BRIEFINGS, MOZ_SCRIPTS, QUOTE_SLOTS, QUOTES

JAPANESE = re.compile(rb"[\x81-\x9f\xe0-\xfc]")
EVENTS = range(177, 414)
CAPTIONS = range(145, 177)
SCANNED = {*MOZ_SCRIPTS, *CAPTIONS, *EVENTS}  # the LINKDATA2 entries the inventory reads (a scan of all finds no more)
MESSAGES, MESSAGE_COUNT = 0x6DE4F0, 911
MESC_SITES = {
    4071: DEFINES["NARRATION_DRAW"],
    4073: DEFINES["NARRATION_DRAW"],
    4103: DEFINES["STILL_DRAW"],
    4105: DEFINES["STILL_DRAW"],
    BRIEFINGS[0]: DEFINES["BRIEFING_DRAW"],
}


def inventory(stock: Path) -> dict[str, dict[bytes, object]]:
    """Source -> {stock CP932 string: drawing site or None}."""
    found: dict[str, dict[bytes, object]] = {}

    def add(source: str, text: bytes, site) -> None:
        if JAPANESE.search(text) and text.strip(b" \n\x81\x40"):
            found.setdefault(source, {})[text] = site

    bin_path = stock / "LINKDATA2.BIN"
    index = parse_index((stock / "LINKDATA2.IDX").read_bytes(), bin_path.stat().st_size)
    with bin_path.open("rb") as stream:
        for entry in index.entries:
            if entry.ordinal not in SCANNED:
                continue
            if len(entry.stored_sizes) != 1 and entry.ordinal not in EVENTS:  # the Dream events come in several parts
                continue
            data = entry_bytes(stream, entry)
            if entry.ordinal in CAPTIONS and len(data) >= 4:
                count = struct.unpack_from("<I", data)[0]
                texts = data[4 + 8 * count:].split(b"\0")
                for text in texts[:count]:
                    add("captions", text, DEFINES["CAPTION_CUE"])
                continue
            if entry.ordinal in EVENTS:
                for text in event_texts(data):
                    add("events", text, None)
                continue
            try:
                script = parse_script(data)
            except ValueError:
                continue
            # A stage shows a notice or a line only through a command (scripts.NOTICE, SPEAK), so a record no command
            # shows is never seen and needs no row.
            shown = {(c[0], c[1] if c[0] == NOTICE else c[2]) for event in script.events for c in event}
            for r, record in enumerate(script.notices.records):
                if (NOTICE, r) in shown:
                    add("notices", record.text_bytes, ("window", 2))
            for r, record in enumerate(script.dialog.records):
                if (SPEAK, r) in shown:
                    add("spoken", record.text_bytes, ("window", 1))
    data = moz_entries(stock / "LINKDATA1.BIN", stock / "LINKDATA1.IDX", [*MESC_SITES, QUOTES[0]])
    for ordinal, site in MESC_SITES.items():
        for block in mesc_blocks(data[ordinal]):
            for text in block:
                add("briefings" if ordinal == BRIEFINGS[0] else f"mesc {ordinal}", text, site)
    formats, notices, *officers = quote_tables(data[QUOTES[0]])
    for line in formats + notices:
        add("quote formats", quote_text(line), ("window", 2))
    for line in (table[k] for table in officers for k in QUOTE_SLOTS if k < len(table)):  # the slots a game shows
        add("officer quotes", quote_text(line), ("window", 1))
    exe = (stock / "OROCHI_Z.exe").read_bytes()
    for value in pointer_strings(exe, MESSAGES, MESSAGE_COUNT, 4):
        add("exe messages", value, None)
    return found
