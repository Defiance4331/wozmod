"""Offline text measurement, wrapping and horizontal fit reports."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .english_runtime import C_SOURCE, SURROGATES, encode_english
from .latin_font import CELL, LATIN_GLYPH

UNSURROGATES = {ord(v): k for k, v in SURROGATES.items()}
SOURCE = C_SOURCE.read_text()
DEFINES = {name: int(value, 16) for name, value in re.findall(r"#define (\w+) (0x[0-9A-Fa-f]+)", SOURCE)}
JAPANESE = re.compile(r"[\u3000-\u30ff\u3400-\u9fff\uff00-\uffef]")
WINDOW_TEXT, NOTICE_FORMATS, NOT_TEXT = 0x4F4D1F, 0x05, {0x08, 0x15, 0x16}

LINES = {("window", 1): 3, ("window", 2): 2, DEFINES["NARRATION_DRAW"]: 5, DEFINES["STILL_DRAW"]: 3,
         DEFINES["NARRATION_Z_DRAW"]: 5, DEFINES["STILL_Z_DRAW"]: 3,
         DEFINES["BRIEFING_DRAW"]: 4, DEFINES["SCENARIO_TEXT_DRAW"]: 5,
         DEFINES["DREAM_SUMMARY_DRAW"]: 7, DEFINES["CAPTION_CUE"]: 2, DEFINES["HISTORY_DRAW"]: 2}

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
    found = {("window", k + 1): (box, False) for k, (_, box) in enumerate(windows())} | {
             DEFINES[f"{name}_DRAW"]: (int(re.search(rf"{name}_WIDTH (\d+)", SOURCE)[1]), True)
             for name in ("SCENARIO_TEXT", "DREAM_SUMMARY")}
    found[DEFINES["CAPTION_CUE"]] = (int(re.search(r"CAPTION_WIDTH (\d+)", SOURCE)[1]), False)
    if "HISTORY_DRAW" in DEFINES and (history := re.search(r"HISTORY_WIDTH (\d+)", SOURCE)):
        found[DEFINES["HISTORY_DRAW"]] = (int(history[1]), False)
    found |= {site: (row[6], True) for site, row in pages().items()}
    found |= {int(va, 16): (int(w), False)
              for va, w in _site_rows("boxed", r"\{0x([0-9A-F]+), (\d+)\}")}
    found |= {int(va, 16): (int(w), True)
              for va, w in _site_rows("described", r"\{0x([0-9A-F]+), (\d+)\}")}
    return found | {site: (width, False) for site, width in single_line_sites().items()}

def single_line_sites() -> dict[int, int]:
    """Read known widths for draw sites that do not wrap."""
    if not re.search(r"\bsingle_line\s*\[", SOURCE):
        return {}
    return {int(va, 16): int(width)
            for va, width in _site_rows("single_line", r"\{0x([0-9A-F]+), (\d+)\}")}

def glyph_sites() -> dict[int, int]:
    """Read the glyph finalizer return addresses the squish rescales, and their glyph register (0 esi, 1 edi)."""
    return {int(va, 16): int(reg) for va, reg in _site_rows("glyph_sites", r"\{0x([0-9A-F]+), ([01])\}")}

def windows() -> list[tuple[int, int]]:
    """Read the two windows' wrap and drawn widths."""
    pairs = re.search(r"\bwindows\s*\[2\]\s*\[2\]\s*=\s*\{\{(\d+), (\d+)\}, \{(\d+), (\d+)\}\}", SOURCE)
    if pairs is None:
        raise ValueError("windows layout array initializer is missing")
    values = list(map(int, pairs.groups()))
    return [(values[0], values[1]), (values[2], values[3])]

def pages() -> dict[int, tuple[int, ...]]:
    """Read each page site's cell width and height, pitch, x, y, wrap width and box."""
    rows = _site_rows("pages", r"\{(\w+_DRAW), (\d+(?:, \d+){6})\}")
    return {DEFINES[name]: tuple(map(int, values.split(", "))) for name, values in rows}

def cells() -> dict[int, int]:
    """Read the cell width of sites that draw at their own cell (cell 0 keeps the stock cell)."""
    return {site: row[0] for site, row in pages().items() if row[0]}

def reflow_rules() -> dict[int, int]:
    """Read each site's wrap width where it differs from its box."""
    rules = {site: row[5] for site, row in pages().items()}
    rules |= {("window", k + 1): wrap for k, (wrap, _) in enumerate(windows())}
    if re.search(r"\breflow\s*\[", SOURCE):
        rules |= {int(site, 16): int(width) for site, width in _site_rows("reflow", r"\{0x([0-9A-F]+), (\d+)\}")}
    return rules

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

def _laid_out(site, cell: int, text: str, table: bytes, boxes: dict, sample_text: str,
              no_wrap: dict | None = None, reflow: dict | None = None) -> str:
    for spec, value in (("%s", sample_text), ("%d", "1000")):
        text = text.replace(spec, value)
    width, flat = boxes[site]
    text = text.replace("\n", " ") if flat else text
    if site in (single_line_sites() if no_wrap is None else no_wrap):
        return text
    return wrap(text, (reflow_rules() if reflow is None else reflow).get(site, width), table, cell)

def squish_geometry(text_width: float, box_width: float | None, alignment: str = "left",
                    enabled: bool = True) -> tuple[float, float, float]:
    """Return factor, x shift from the aligned full-width origin, and drawn width."""
    if alignment not in {"left", "centre", "center", "right"}:
        raise ValueError(f"unsupported text alignment: {alignment}")
    factor = box_width / text_width if enabled and box_width is not None and 0 < box_width < text_width else 1.0
    drawn = text_width * factor
    inset = {"left": 0.0, "centre": 0.5, "center": 0.5, "right": 1.0}[alignment]
    return factor, (text_width - drawn) * inset, drawn

def squish_report(rows: Iterable[tuple[str, object, int, str]], table: bytes, boxes: dict,
                  sample_text: str, *, no_wrap_sites: Iterable[object] = (), reflow: dict | None = None) -> dict:
    """Report each site's minimum factor, limiting rows and rows below 70 percent."""
    reported = {site: {"width": width, "rows": 0, "min_factor": None, "limiting_rows": [],
                       "below_70": [], "vertical_overflow": []} for site, (width, _) in boxes.items()}
    unknown = []
    no_wrap = dict.fromkeys((*single_line_sites(), *no_wrap_sites))
    reflow = reflow_rules() if reflow is None else reflow
    own = cells()
    for key, site, cell, text in rows:
        cell = own.get(site, cell)
        if site not in boxes or JAPANESE.search(text):
            unknown.append({"row": key, "site": site, "cell": cell,
                            "reason": "unknown-width" if site not in boxes else "unmeasured-script"})
            continue
        lines = _laid_out(site, cell, text, table, boxes, sample_text, no_wrap, reflow)
        width = measure(lines, table, cell)
        factor = squish_geometry(width, boxes[site][0])[0]
        row = {"row": key, "cell": cell, "text_width": width, "factor": factor,
               "line_count": lines.count("\n") + 1}
        report = reported[site]
        report["rows"] += 1
        if report["min_factor"] is None or factor < report["min_factor"]:
            report["min_factor"], report["limiting_rows"] = factor, [row]
        elif factor == report["min_factor"]:
            report["limiting_rows"].append(row)
        if factor < 0.7:
            report["below_70"].append(row)
        if lines.count("\n") >= LINES.get(site, 99):
            report["vertical_overflow"].append(row)
    return {"sites": reported, "unknown": unknown}

def overflows(site, cell: int, text: str, table: bytes, boxes: dict, sample_text: str,
              *, reflow: dict | None = None) -> str | None:
    """Return text exceeding its line limit; horizontal fit is reported separately."""
    if site not in boxes or JAPANESE.search(text):
        return None
    lines = _laid_out(site, cells().get(site, cell), text, table, boxes, sample_text, reflow=reflow)
    return lines if lines.count("\n") >= LINES.get(site, 99) else None
