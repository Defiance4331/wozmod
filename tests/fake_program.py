"""Fake programs: Python source that the code under test starts by its path, as it starts ffmpeg or a player's program."""

from __future__ import annotations

import importlib.resources
import io
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest


def program(path: Path, source: str) -> Path:
    """Write `source` as a program at `path` (".exe" added on Windows) and return the path that starts it.

    POSIX starts a file with a #! line naming this Python. Windows starts only executables by path, so there the
    program is the console launcher pip puts in front of its scripts, followed by the #! line and the source in a zip:
    the launcher runs this Python on itself, which runs the zip's __main__.py with the arguments.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path
    path = path if path.suffix.lower() == ".exe" else path.with_name(path.name + ".exe")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("__main__.py", source)
    path.write_bytes(_launcher() + f'#!"{sys.executable}"\n'.encode() + archive.getvalue())
    return path


def _launcher() -> bytes:
    for package in ("pip._vendor.distlib", "distlib"):
        try:
            return importlib.resources.files(package).joinpath("t64.exe").read_bytes()
        except (ImportError, FileNotFoundError):
            continue
    pytest.skip("a fake program on Windows needs the script launcher from pip or distlib")
