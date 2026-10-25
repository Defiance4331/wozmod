"""The runtime's C source, and the defines and site tables read out of it."""

from __future__ import annotations

import re
import struct

from wozmod import resources

C_SOURCE = resources.root() / "runtime" / "english_runtime.c"
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


def sites(source: str = SOURCE) -> dict:
    """Read wrapping widths and reflow flags from the runtime source, and the pages' from text_boxes."""
    from . import text_boxes
    known = defines(source)
    found = {("window", k + 1): (box, False) for k, (_, box) in enumerate(windows(source))} | {
             known[f"{name}_DRAW"]: (known[f"{name}_WIDTH"], True) for name in ("SCENARIO_TEXT", "DREAM_SUMMARY")}
    found[known["CAPTION_CUE"]] = (known["CAPTION_WIDTH"], False)
    if "HISTORY_DRAW" in known and "HISTORY_WIDTH" in known:
        found[known["HISTORY_DRAW"]] = (known["HISTORY_WIDTH"], False)
    found |= {site: (row[6], True) for site, row in text_boxes.page_rows().items()}
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

def reflow_rules(source: str = SOURCE) -> dict[int, int]:
    """Read each site's wrap width where it differs from its box (the pages' from text_boxes)."""
    from . import text_boxes
    rules = {site: row[5] for site, row in text_boxes.page_rows().items()}
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
