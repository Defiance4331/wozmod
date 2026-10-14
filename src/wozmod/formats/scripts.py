"""Readers for stage, message, event, caption and quote records."""


from __future__ import annotations


import re
import struct
from dataclasses import dataclass
from itertools import pairwise


DONOR_LETTERS = {0xB2: "ï", 0xD3: "Ō", 0xD4: "ō", 0xD5: "Ū", 0xD6: "ū"}
SECTIONS = struct.Struct("<5I")
DIALOG_SUFFIX = re.compile(rb"@[0-9]{4}$")
EVENT_LINE = re.compile(rb"\xff{4}(.{2})(.{2})([^\0]+)\0", re.S)
LEAD = rb"[\x81-\x9f\xe0-\xfc]"
EVENT_TEXT = re.compile(rb"\xff{4}(?:(" + LEAD + rb"[^\0]*)|.{4}(" + LEAD + rb"[^\0]*))\0", re.S)
KANA_KANJI = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


class ScriptError(ValueError):
    """Raised when bytes are not a four-section stage script."""


@dataclass(frozen=True)
class Line:
    """One script table slot: a two-digit control prefix, the text and, on a
    spoken line, an @NNNN voice suffix."""

    value: bytes
    spoken: bool

    @property
    def dialog_suffix(self) -> bytes | None:
        match = DIALOG_SUFFIX.search(self.value) if self.spoken else None
        return match.group(0) if match else None

    @property
    def text_bytes(self) -> bytes:
        suffix = self.dialog_suffix
        return self.value[2:-len(suffix)] if suffix else self.value[2:]


@dataclass(frozen=True)
class Table:
    records: tuple[Line, ...]


@dataclass(frozen=True)
class Script:
    """A stage script's battle notices (section 2) and spoken lines (section 3)."""

    notices: Table
    dialog: Table


def _table(data: bytes, spoken: bool, order: str = "<") -> Table:
    """u32 count, count u32 offsets, then NUL-terminated CP932 strings."""
    kind = "dialog" if spoken else "notice"
    if len(data) < 4:
        raise ScriptError(f"{kind} table header is truncated")
    count = struct.unpack_from(f"{order}I", data)[0]
    header = 4 * (count + 1)
    if header > len(data):
        raise ScriptError(f"{kind} offset vector is truncated")
    offsets = struct.unpack_from(f"{order}{count}I", data, 4)
    if (offsets[0] if count else len(data)) != header:
        raise ScriptError(f"{kind} strings do not follow their offset vector")
    if any(b <= a for a, b in pairwise(offsets)) or (count and offsets[-1] >= len(data)):
        raise ScriptError(f"{kind} string offsets are out of bounds")
    records = []
    for ordinal, (start, end) in enumerate(pairwise((*offsets, len(data)))):
        span = data[start:end]
        value = span.split(b"\0", 1)[0]
        if len(value) < 2 or not value[:2].isdigit():
            raise ScriptError(f"{kind} slot {ordinal} has no two-byte control prefix")
        try:
            value.decode("cp932")
        except UnicodeDecodeError as exc:
            raise ScriptError(f"{kind} slot {ordinal} is not valid CP932") from exc
        if spoken and (b"\0" not in span or value[:2] != b"00"):
            raise ScriptError(f"{kind} slot {ordinal} is not a terminated 00 line")
        records.append(Line(value, spoken))
    return Table(tuple(records))


def parse_script(data: bytes, order: str = "<") -> Script:
    """A four-section stage script: u32 4, then four ascending 4-aligned section offsets.

    order is the struct byte order of its words: ">" for the Xbox 360 release."""
    if len(data) < SECTIONS.size:
        raise ScriptError("script section header is truncated")
    count, *offsets = struct.unpack_from(f"{order}5I", data)
    if count != 4 or offsets[0] != SECTIONS.size:
        raise ScriptError("script does not start with four sections")
    if any(b <= a for a, b in pairwise(offsets)) or offsets[-1] >= len(data) or any(o % 4 for o in offsets):
        raise ScriptError("script section offsets are invalid")
    return Script(_table(data[offsets[2]:offsets[3]], False, order), _table(data[offsets[3]:], True, order))


def mesc_blocks(data: bytes) -> list[list[bytes]]:
    """The strings of each MESC block (u32 count, u32 size, u32 offsets[count]) in data."""
    blocks, at = [], data.find(b"MESC")
    while at >= 0:
        count, size = struct.unpack_from("<II", data, at + 4)
        offsets = struct.unpack_from(f"<{count}I", data, at + 12)
        blocks.append([data[at + o:data.index(b"\0", at + o)] for o in offsets])
        at = data.find(b"MESC", at + size)
    return blocks


def event_lines(data: bytes, order: str = "<") -> dict[int, bytes]:
    """Event subtitle text by line index; an index carrying two texts is dropped."""
    lines: dict[int, bytes | None] = {}
    for match in EVENT_LINE.finditer(data):
        index, size = struct.unpack(f"{order}HH", match.group(1) + match.group(2))
        if match.start() % 16 == 12 and index < 5000 and size % 16 == 0 and size < 0x400:
            text = match.group(3)
            lines[index] = text if lines.get(index, text) == text else None
    return {i: t for i, t in lines.items() if t is not None}


def caption_cues(data: bytes) -> list[tuple[int, int, bytes]]:
    """A MovieTelop schedule's (start, end, text) cues: u32 count, count (start, end), the texts."""
    count = struct.unpack_from("<I", data)[0]
    times = struct.iter_unpack("<II", data[4:4 + 8 * count])
    return [(*t, text) for t, text in zip(times, data[4 + 8 * count:].split(b"\0", count)[:count], strict=True)]


def _strings(data: bytes, order: str = "<") -> list[bytes]:
    """u32 count, then that many u32 offsets of NUL-terminated strings in data."""
    count = struct.unpack_from(f"{order}I", data)[0]
    return [data[o:data.index(b"\0", o)] for o in struct.unpack_from(f"{order}{count}I", data, 4)]


def _spans(data: bytes, at: int, count: int, order: str = "<") -> list[bytes]:
    """count (u32 offset, u32 size) spans of data listed from at."""
    return [data[o:o + n] for o, n in struct.iter_unpack(f"{order}II", data[at:at + 8 * count])]


def quote_tables(data: bytes, order: str = "<") -> list[list[bytes]]:
    """A battle-quote file's two notice format tables, then one quote table per officer."""
    formats, notices, quotes = _spans(data, 4, struct.unpack_from(f"{order}I", data)[0], order)
    officers = _spans(quotes, 0, struct.unpack_from(f"{order}I", quotes)[0] // 8, order)  # the first span follows the list
    return [_strings(t, order) for t in (formats, notices, *officers)]


def quote_text(line: bytes) -> bytes:
    """A quote-file line as the game shows it: two-digit prefix and @voice suffix removed."""
    text, i, at = line[2:], 0, None
    while i < len(text):  # 0x40 is also a cp932 trail byte: only an @ between characters counts
        at = i if text[i] == 0x40 else at
        i += 2 if 0x81 <= text[i] <= 0x9F or 0xE0 <= text[i] <= 0xFC else 1
    return text[:at] if at is not None and text[at + 1:].isdigit() else text


def event_texts(data: bytes) -> list[bytes]:
    """The subtitle and caption texts of an event file: printable CP932 with two kana or kanji.

    The marker also turns up inside the files' binary tables; the filter drops those hits.
    """
    found = []
    for match in EVENT_TEXT.finditer(data):
        text = match.group(1) or match.group(2)
        try:
            decoded = text.decode("cp932")
        except UnicodeDecodeError:
            continue
        if len(KANA_KANJI.findall(decoded)) >= 2 and all(
                (c.isprintable() or c in "\n\u3000") and not "\uff61" <= c <= "\uff9f"
                and not "\ue000" <= c <= "\uf8ff" for c in decoded):
            found.append(text)
    return found


def donor_text(data: bytes) -> str | None:
    """Donor English as text, or None when it carries anything but ASCII and Koei letters."""
    if any(b > 0x7F and b not in DONOR_LETTERS for b in data):
        return None
    return "".join(DONOR_LETTERS.get(b, chr(b)) for b in data)
