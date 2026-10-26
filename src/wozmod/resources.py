"""Where wozmod finds its own files: data/, runtime/ and the bundled ffmpeg."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# The files and folders under root() that the release bundles (packaging/wozmod.spec reads this list).
PACKAGED = ("data/art", "data/text", "data/movies.json", "data/stock_pieces.json", "runtime")


def root() -> Path:
    """The folder that holds data/, runtime/ and ffmpeg/: the PyInstaller bundle, else the repository."""
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle) if bundle else Path(__file__).resolve().parents[2]


def ffmpeg() -> str | None:
    """The bundled ffmpeg when the release ships one, else the ffmpeg on PATH (None when neither)."""
    bundled = root() / "ffmpeg/bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    return str(bundled) if bundled.is_file() else shutil.which("ffmpeg")
