"""Build the proxy DLL from a stock executable."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .english_runtime import (
    ENGLISH,
    ONE_ROW,
    TWO_ROWS,
    compile_dll,
    data_header,
    hook_sites,
)
from .latin_font import WIDTHS
from . import camp_plate
from .runtime_edits import (
    CODE_EDITS, FONT_ROUTE, PAD_CAMERA, SAVED_PAD, STANDARD_CAMERA, VOICE_EDITS, camp_plate_edits, settings_rows,
)


def build_dll(
    stock_exe: Path,
    output: Path,
    *,
    log: bool = False,
    bootlog: bool = False,
    preview: bool = False,
    test: bool = False,
    cell: int = 24,
    render_distance: bool = False,
    moving_fix: bool = False,
) -> Path:
    """Write a DLL with checked stock spans and the requested build defines."""
    stock = stock_exe.read_bytes()
    unlocks = [
        (va, b"\x74\x04", b"\x90\x90")
        for va in (0x57296B, 0x572991, 0x5729B4, 0x5729DA, 0x572A01)
    ]
    edits = {
        ENGLISH: (FONT_ROUTE + CODE_EDITS + camp_plate_edits(camp_plate.width(camp_plate.stock_plate(stock_exe.parent)))
                  + unlocks * preview),
        # one of the row groups installs in either text mode; TWO_ROWS with the English voice banks
        ONE_ROW: settings_rows(1, render_distance, moving_fix) + PAD_CAMERA + SAVED_PAD + STANDARD_CAMERA,
        TWO_ROWS: settings_rows(2, render_distance, moving_fix) + PAD_CAMERA + SAVED_PAD + STANDARD_CAMERA + VOICE_EDITS,
    }
    defines = (
        ("ENRT_LOG",) * log
        + ("ENRT_BOOTLOG",) * bootlog
        + ("ENRT_TAG_PREVIEW",) * preview
        + ("ENRT_TEST",) * test
        + ("ENRT_RENDER_DISTANCE",) * render_distance
        + ("ENRT_MOVING_FIX",) * moving_fix
        + (f"ENRT_CELL={cell}",) * (cell != 24)
    )
    header = data_header(
        stock, edits, hook_sites(defines), WIDTHS, hashlib.sha256(stock).hexdigest()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    compile_dll(output, header, defines)
    return output
