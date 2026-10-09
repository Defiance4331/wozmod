"""Offline text measurement and wrapping checks."""

from __future__ import annotations

import re

from .english_runtime import C_SOURCE, SURROGATES, encode_english
from .latin_font import CELL, LATIN_GLYPH

UNSURROGATES = {ord(v): k for k, v in SURROGATES.items()}
SOURCE = C_SOURCE.read_text()
DEFINES = {name: int(value, 16) for name, value in re.findall(r"#define (\w+) (0x[0-9A-Fa-f]+)", SOURCE)}
JAPANESE = re.compile(r"[\u3000-\u30ff\u3400-\u9fff\uff00-\uffef]")
WINDOW_TEXT, NOTICE_FORMATS, NOT_TEXT = 0x4F4D1F, 0x05, {0x08, 0x15, 0x16}

LINES = {("window", 1): 3, ("window", 2): 2, DEFINES["NARRATION_DRAW"]: 4, DEFINES["STILL_DRAW"]: 3,
         DEFINES["BRIEFING_DRAW"]: 5, DEFINES["SCENARIO_TEXT_DRAW"]: 4,
         DEFINES["DREAM_SUMMARY_DRAW"]: 8, DEFINES["CAPTION_CUE"]: 2}

def units(data: bytes) -> list[tuple[int | None, bytes]]:
    """Split CP932 bytes into glyphs and zero-width controls."""
    out, i = [], 0
    while i < len(data):
        c = data[i]
        if c == 0x5B and i + 2 < len(data):
            glyph, n = None, 3
        elif 0x20 <= c <= 0x7D and c != 0x5D:
            glyph, n = LATIN_GLYPH + c - 0x20, 1
        elif 0x81 <= c <= 0x9F or c >= 0xE0:
            glyph, n = 0, 2
        else:
            glyph, n = None, 1
        out.append((glyph, data[i:i + n]))
        i += n
    return out

def _width(units_: list, table: bytes, cell: int) -> int:
    return sum(table[g] * cell // CELL for g, _ in units_ if g is not None)

def measure(text: str, table: bytes, cell: int = CELL) -> int:
    """Return the widest line at the requested cell size."""
    return max(_width(units(line), table, cell) for line in encode_english(text).split(b"\n"))

def wrap(text: str, width: int, table: bytes, cell: int = CELL) -> str:
    """Replace the last space before a width threshold with a newline."""
    out, line, space = [], 0, None
    for glyph, raw in units(encode_english(text)):
        out.append((glyph, raw))
        if raw == b"\n":
            line, space = len(out), None
        elif raw == b" ":
            space = len(out) - 1 if len(out) - 1 > line else None
        elif space is not None and _width(out[line:], table, cell) >= width:
            out[space] = (None, b"\n")
            line, space = space + 1, None
    return b"".join(raw for _, raw in out).decode("cp932").translate(UNSURROGATES)

def _site_rows(name: str, pattern: str) -> list[tuple[str, str]]:
    """Reject any initializer the value pattern cannot read."""
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", SOURCE, flags=re.S)
    array = re.search(rf"\b{name}\s*\[\s*\]\s*\[\s*\d+\s*\]\s*=\s*\{{(.*?)\}}\s*;", source, re.S)
    if array is None:
        raise ValueError(f"{name} layout array initializer is missing")
    body = array[1]
    rows = re.findall(r"\{[^{}]*\}", body)

    if not rows or re.sub(r"\{[^{}]*\}", "", body).strip(" \t\r\n,"):
        raise ValueError(f"{name} has an unsupported layout array initializer")
    values = []
    for index, row in enumerate(rows, 1):
        match = re.fullmatch(pattern, row)
        if match is None:
            raise ValueError(f"{name} initializer {index} is not a supported layout site: {row!r}")
        values.append((match[1], match[2]))
    return values

def sites() -> dict:
    """Read wrapping widths and reflow flags from the runtime source."""
    one, two = map(int, re.search(r"f->arg\[0\] == 1 \? (\d+) : (\d+)", SOURCE).groups())
    found = {("window", 1): (one, False), ("window", 2): (two, False),
             **{DEFINES[f"{name}_DRAW"]: (int(re.search(rf"{name}_WIDTH (\d+)", SOURCE)[1]), True)
                for name in ("SCENARIO_TEXT", "DREAM_SUMMARY")}}
    found[DEFINES["CAPTION_CUE"]] = (int(re.search(r"CAPTION_WIDTH (\d+)", SOURCE)[1]), False)
    found |= {DEFINES[name]: (int(width), True)
              for name, width in _site_rows("pages", r"\{(\w+_DRAW), (\d+), \d+, \d+\}")}
    found |= {int(va, 16): (int(w), False)
              for va, w in _site_rows("boxed", r"\{0x([0-9A-F]+), (\d+)\}")}
    return found | {int(va, 16): (int(w), True)
                    for va, w in _site_rows("described", r"\{0x([0-9A-F]+), (\d+)\}")}

def entry(line: str) -> tuple | None:
    """Read site, cell, text bytes and translation flag from a log row."""
    field = line.split()
    if len(field) < 8 or int(field[0], 16) & 0x7F in NOT_TEXT:
        return None
    hook, site, cell = int(field[0], 16) & 0x7F, int(field[1], 16), int(field[6], 16) & 0xFF
    if site == WINDOW_TEXT:
        site = ("window", int(field[2], 16)) if int(field[2], 16) in (1, 2) else None
    elif hook == NOTICE_FORMATS:
        site = ("window", 2)
    return site, 24 if isinstance(site, tuple) else cell, bytes.fromhex(field[7]), bool(int(field[0], 16) & 0x80)

def overflows(site, cell: int, text: str, table: bytes, boxes: dict, sample_text: str) -> str | None:
    """Return overflowing text, using the caller's sample for string arguments."""
    if site not in boxes or JAPANESE.search(text):
        return None
    for spec, value in (("%s", sample_text), ("%d", "1000")):
        text = text.replace(spec, value)
    width, flat = boxes[site]
    lines = wrap(text.replace("\n", " ") if flat else text, width, table, cell)
    inked = "\n".join(line.rstrip(" ") for line in lines.split("\n"))
    return lines if measure(inked, table, cell) > width or lines.count("\n") >= LINES.get(site, 99) else None
