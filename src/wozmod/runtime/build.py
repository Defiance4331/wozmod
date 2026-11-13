"""Build the proxy DLL from a stock executable."""

from __future__ import annotations

from pathlib import Path

from .english_runtime import (
    ENGLISH,
    ONE_ROW,
    TWO_ROWS,
    compile_dll,
    data_header,
    hook_sites,
)
from . import true_rate
from .latin_font import WIDTHS
from . import camp_plate
from .runtime_edits import (
    CODE_EDITS, FONT_ROUTE, PAD_CAMERA, PATH_CELLS, SAVED_PAD, SE_PRECISION, SOUND_3D, STANDARD_CAMERA, V101_FIXES,
    VOICE_EDITS, camp_plate_edits, settings_rows,
)


def edit_lists(camp_width: int, *, moving_fix: bool = False) -> list[tuple[int, str, list]]:
    """(text-mode group, list name, edits) as a build installs them: the name picks the feature that owns the list
    (wozmod.runtime.guards). One of the two row groups installs in either text mode; TWO_ROWS with the English voice
    banks."""
    both = [("PAD_CAMERA", PAD_CAMERA), ("SAVED_PAD", SAVED_PAD), ("STANDARD_CAMERA", STANDARD_CAMERA),
            ("V101_FIXES", V101_FIXES), ("SOUND_3D", SOUND_3D), ("PATH_CELLS", PATH_CELLS),
            ("SE_PRECISION", SE_PRECISION)]
    return [
        (ENGLISH, "FONT_ROUTE", FONT_ROUTE), (ENGLISH, "CODE_EDITS", CODE_EDITS),
        (ENGLISH, "camp plate", camp_plate_edits(camp_width)),
        (ONE_ROW, "settings rows", settings_rows(1, moving_fix)),
        *[(ONE_ROW, name, group_edits) for name, group_edits in both],
        (TWO_ROWS, "settings rows", settings_rows(2, moving_fix)),
        *[(TWO_ROWS, name, group_edits) for name, group_edits in both],
        (TWO_ROWS, "VOICE_EDITS", VOICE_EDITS),
    ]


def build_dll(
    stock_exe: Path,
    output: Path,
    *,
    log: bool = False,
    bootlog: bool = False,
    test: bool = False,
    moving_fix: bool = False,
) -> Path:
    """Write a DLL with checked stock spans and the requested build defines."""
    stock = stock_exe.read_bytes()
    edits = edit_lists(camp_plate.width(camp_plate.stock_plate(stock_exe.parent)), moving_fix=moving_fix)
    defines = (
        ("ENRT_LOG",) * log
        + ("ENRT_BOOTLOG",) * bootlog
        + ("ENRT_TEST",) * test
        + ("ENRT_MOVING_FIX",) * moving_fix
    )
    header = data_header(stock, edits, hook_sites(defines), WIDTHS, defines) + true_rate.header(stock)
    output.parent.mkdir(parents=True, exist_ok=True)
    compile_dll(output, header, defines)
    return output
