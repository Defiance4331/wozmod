"""The runtime's C source, and the defines and site tables read out of it (the DLL build, its tests and tools).

The installer's build steps read only runtime/shared.h, through wozmod.runtime.shared: they must not load this module,
so that a change to the runtime keeps their keys (wozmod.install.step_keys)."""

from __future__ import annotations

import re
import struct

from wozmod import resources

from .shared import _COMMENTS, _INTEGER, SHARED_H, defines

C_SOURCE = resources.root() / "runtime" / "english_runtime.c"
SOURCE = SHARED_H.read_text(encoding="utf-8") + C_SOURCE.read_text(encoding="utf-8")  # as the compiler sees it


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


DEFINES = defines(SOURCE)
if _twice := sorted(defines(SHARED_H.read_text(encoding="utf-8")).keys() & defines(C_SOURCE.read_text(encoding="utf-8"))):
    raise ValueError(f"runtime/shared.h and english_runtime.c both define {', '.join(_twice)}: keep each in one file")


def sites(source: str = SOURCE) -> dict:
    """Wrapping widths and description flags by draw site: the lanes' from text_lanes, the pages' from text_boxes, the
    single lines' from the runtime source."""
    from . import text_boxes, text_lanes
    known, lane = defines(source), text_lanes.lane
    found = {("window", 1): (lane("window1").width, False), ("window", 2): (lane("window2").width, False),
             known["SCENARIO_TEXT_DRAW"]: (lane("scenario").width, True),
             known["DREAM_SUMMARY_DRAW"]: (lane("dream").width, True),
             known["CAPTION_CUE"]: (lane("caption").width, False), known["HISTORY_DRAW"]: (lane("history").width, False)}
    found |= {site: (row[6], True) for site, row in text_boxes.page_rows().items()}
    found |= {site: (row.width, row.kind == "described") for row in text_lanes.LANES
              if row.kind in ("boxed", "described") for site in row.sites}
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

def runtime_writes(source: str = SOURCE) -> list[tuple[str, int, bytes | None, int]]:
    """List the stock code the runtime checks or rewrites itself as the game starts, beside the build's edits: name,
    VA, the stock bytes the runtime expects there (None where it writes without looking) and length. Rows come from
    the site arrays the display, the weapon texture table and edge HUD code loop over, and from every stock_at or
    write_at call at a fixed address; a call at a loop variable writes one of those arrays' sites."""
    known = defines(source)
    code = _COMMENTS.sub("", source)
    rows = [(name, va, struct.pack("<I", value), 4) for name in ("to_window", "to_scene", "weapon_slot_sites")
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
