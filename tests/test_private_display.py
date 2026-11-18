"""tests/conftest.py gives the whole run a private display."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux_only("the desktop a Wine program could open on is an X or Wayland display")


def _xvfb_children(parent: int) -> list[int]:
    """The Xvfb processes whose parent is parent."""
    found = []
    for entry in Path("/proc").iterdir():
        try:
            stat = (entry / "stat").read_text(encoding="ascii")
        except (OSError, ValueError):
            continue
        name, rest = stat[stat.index("(") + 1:stat.rindex(")")], stat[stat.rindex(")") + 2:].split()
        if name == "Xvfb" and int(rest[1]) == parent:
            found.append(int(entry.name))
    return found


def test_programs_the_tests_start_cannot_reach_the_desktop():
    """Bug: a test starts a Wine program with the run's environment, which names the user's display, so its window
    opens on the desktop and takes the focus from the user's game."""
    assert "WAYLAND_DISPLAY" not in os.environ
    if not shutil.which("Xvfb"):
        assert "DISPLAY" not in os.environ
        return
    # The run's own X server: a child of the process that started the run (this one, or xdist's controller).
    assert os.environ.get("DISPLAY") and (_xvfb_children(os.getpid()) or _xvfb_children(os.getppid()))
