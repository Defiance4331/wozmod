"""Offline text measurement, wrapping and horizontal fit reports."""

from __future__ import annotations

import re
import struct
from collections.abc import Iterable

from .english_runtime import C_SOURCE, decode_english, encode_english
from .latin_font import CELL, LATIN_COUNT, LATIN_FIRST, LATIN_GLYPH

SOURCE = C_SOURCE.read_text(encoding="utf-8")
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_INTEGER = re.compile(r"(-?(?:0x[0-9A-Fa-f]+|\d+))[uU]?")


def defines(source: str = SOURCE) -> dict[str, int]:
    """Read every #define whose value is one integer: bare, in parentheses or behind casts (``0x1F``, ``(-3)``,
    ``((void (*)(u32))0x4F4230)``). Defines of anything else (an expression, a float, a macro) are left out."""
    found = {}
    for name, value in re.findall(r"^[ \t]*#define[ \t]+(\w+)[ \t]+(.*?)[ \t]*$", _COMMENTS.sub("", source), re.M):
        literal = re.search(rf"{_INTEGER.pattern}(\)*)$", value)
        if literal is None:
            continue
        head = value[:literal.start()].rstrip()
        if head and (head[-1] not in "()" or head.count("(") != head.count(")") + len(literal[2])):
            continue  # the number ends an expression, not a cast
        found[name] = int(literal[1], 0)
    return found


def c_array(name: str, source: str = SOURCE) -> list:
    """Read the initializer of C array ``name``: an int per element of a one-dimensional array, a tuple per row of a
    two-dimensional one. An element must be an integer or a define; any other raises ValueError rather than
    dropping its row."""
    array = re.search(rf"\b{name}\s*((?:\[[^\]]*\]\s*){{1,2}})=\s*\{{(.*?)\}}\s*;", _COMMENTS.sub("", source), re.S)
    if array is None:
        raise ValueError(f"{name} array initializer is missing")
    sizes, body = re.findall(r"\[\s*(\w*)\s*\]", array[1]), array[2]
    if len(sizes) == 1:
        rows = [body]
    else:
        rows = re.findall(r"\{([^{}]*)\}", body)
        if not rows or re.sub(r"\{[^{}]*\}", "", body).strip(" \t\r\n,"):
            raise ValueError(f"{name} has an unsupported array initializer")
    known = {}
    values = []
    for index, row in enumerate(rows, 1):
        row_values = []
        for token in (t.strip() for t in row.strip(" \t\r\n,").split(",")):
            if number := _INTEGER.fullmatch(token):
                row_values.append(int(number[1], 0))
            elif token in (known := known or defines(source)):
                row_values.append(known[token])
            else:
                raise ValueError(f"{name} initializer {index} is not integers or defines: {row.strip()!r}")
        if len(sizes) == 2 and sizes[1] and len(row_values) != int(sizes[1], 0):
            raise ValueError(f"{name} initializer {index} does not have {sizes[1]} values: {row.strip()!r}")
        values.append(tuple(row_values))
    if sizes[0] and len(values if len(sizes) == 2 else values[0]) != int(sizes[0], 0):
        raise ValueError(f"{name} does not have {sizes[0]} initializers")
    return values if len(sizes) == 2 else list(values[0])


DEFINES = defines()
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
        elif LATIN_FIRST <= c < LATIN_FIRST + LATIN_COUNT and c != 0x5D:
            glyph, n = LATIN_GLYPH + c - LATIN_FIRST, 1
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
    return decode_english(b"".join(raw for _, raw in out))

def sites(source: str = SOURCE) -> dict:
    """Read wrapping widths and reflow flags from the runtime source."""
    known = defines(source)
    found = {("window", k + 1): (box, False) for k, (_, box) in enumerate(windows(source))} | {
             known[f"{name}_DRAW"]: (known[f"{name}_WIDTH"], True) for name in ("SCENARIO_TEXT", "DREAM_SUMMARY")}
    found[known["CAPTION_CUE"]] = (known["CAPTION_WIDTH"], False)
    if "HISTORY_DRAW" in known and "HISTORY_WIDTH" in known:
        found[known["HISTORY_DRAW"]] = (known["HISTORY_WIDTH"], False)
    found |= {site: (row[6], True) for site, row in pages(source).items()}
    found |= {va: (width, False) for va, width in c_array("boxed", source)}
    found |= {va: (width, True) for va, width in c_array("described", source)}
    return found | {site: (width, False) for site, width in single_line_sites(source).items()}

def single_line_sites(source: str = SOURCE) -> dict[int, int]:
    """Read known widths for draw sites that do not wrap."""
    if not re.search(r"\bsingle_line\s*\[", source):
        return {}
    return dict(c_array("single_line", source))

def glyph_sites(source: str = SOURCE) -> dict[int, int]:
    """Read the glyph finalizer return addresses the squish rescales, and their glyph register (0 esi, 1 edi)."""
    found = dict(c_array("glyph_sites", source))
    if not set(found.values()) <= {0, 1}:
        raise ValueError(f"glyph_sites names a glyph register other than 0 or 1: {found}")
    return found

def windows(source: str = SOURCE) -> list[tuple[int, int]]:
    """Read the two windows' wrap and drawn widths."""
    rows = c_array("windows", source)
    if len(rows) != 2:
        raise ValueError("windows layout array initializer does not list two windows")
    return rows

def pages(source: str = SOURCE) -> dict[int, tuple[int, ...]]:
    """Read each page site's cell width and height, pitch, x, y, wrap width and box."""
    return {row[0]: row[1:] for row in c_array("pages", source)}

def cells(source: str = SOURCE) -> dict[int, int]:
    """Read the cell width of sites that draw at their own cell (cell 0 keeps the stock cell)."""
    return {site: row[0] for site, row in pages(source).items() if row[0]}

def reflow_rules(source: str = SOURCE) -> dict[int, int]:
    """Read each site's wrap width where it differs from its box."""
    rules = {site: row[5] for site, row in pages(source).items()}
    rules |= {("window", k + 1): wrap for k, (wrap, _) in enumerate(windows(source))}
    if re.search(r"\breflow\s*\[", source):
        rules |= dict(c_array("reflow", source))
    return rules

def runtime_writes(source: str = SOURCE) -> list[tuple[str, int, bytes | None, int]]:
    """List the stock code the runtime checks or rewrites itself as the game starts, beside the build's edits: name,
    VA, the stock bytes the runtime expects there (None where it writes without looking) and length. Rows come from
    the site arrays the display and edge HUD code loop over, and from every stock_at or write_at call at a fixed
    address; a call at a loop variable writes one of those arrays' sites."""
    known = defines(source)
    code = _COMMENTS.sub("", source)
    rows = [(name, va, struct.pack("<I", value), 4) for name in ("to_window", "to_scene")
            for va, value in c_array(name, source)]
    rows += [(name, va, b"\xe8" + struct.pack("<i", known["FTOL"] - va - 5), 5) for name in ("ftol_x", "ftol_y")
             for va in c_array(name, source)]
    rows += [("hud_calls", va + 1, struct.pack("<i", target - va - 5), 4)
             for va, target, _ in c_array("hud_calls", source)]
    literal = r'\(const u8 \*\)"((?:\\x[0-9A-Fa-f]{2})+)"'
    for name, stock, size in re.findall(rf"\bstock_at\(\s*(\w+)\s*,\s*{literal}\s*,\s*(\d+)\)", code):
        rows.append((name, _address(name, known), bytes.fromhex(stock.replace("\\x", "")), int(size)))
    for name, array in re.findall(r"\bstock_at\(\s*(\w+)\s*,\s*(\w+)\s*,\s*sizeof \2\s*\)", code):
        stock = bytes(c_array(array, source))
        rows.append((name, _address(name, known), stock, len(stock)))
    for name, offset, size in re.findall(r"\bwrite_at\(\s*(\w+)(?:\s*\+\s*(0x[0-9A-Fa-f]+|\d+))?\s*,[^;]*?,\s*(\d+)\s*\)",
                                         code):
        if name in known or _INTEGER.fullmatch(name):  # else a loop variable over the arrays above
            rows.append((name, _address(name, known) + int(offset or "0", 0), None, int(size)))
    return rows

def _address(name: str, known: dict[str, int]) -> int:
    return int(name, 0) if _INTEGER.fullmatch(name) else known[name]

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
              no_wrap: dict, reflow: dict) -> str:
    for spec, value in (("%s", sample_text), ("%d", "1000")):
        text = text.replace(spec, value)
    width, flat = boxes[site]
    text = text.replace("\n", " ") if flat else text
    if site in no_wrap:
        return text
    return wrap(text, reflow.get(site, width), table, cell)

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
                  sample_text: str, *, no_wrap_sites: Iterable[object] = (), reflow: dict | None = None,
                  source: str = SOURCE) -> dict:
    """Report each site's minimum factor, limiting rows and rows below 70 percent."""
    reported = {site: {"width": width, "rows": 0, "min_factor": None, "limiting_rows": [],
                       "below_70": [], "vertical_overflow": []} for site, (width, _) in boxes.items()}
    unknown = []
    no_wrap = dict.fromkeys((*single_line_sites(source), *no_wrap_sites))
    reflow = reflow_rules(source) if reflow is None else reflow
    own = cells(source)
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
              *, reflow: dict | None = None, source: str = SOURCE) -> str | None:
    """Return text exceeding its line limit; horizontal fit is reported separately."""
    if site not in boxes or JAPANESE.search(text):
        return None
    lines = _laid_out(site, cells(source).get(site, cell), text, table, boxes, sample_text,
                      single_line_sites(source), reflow_rules(source) if reflow is None else reflow)
    return lines if lines.count("\n") >= LINES.get(site, 99) else None
