"""The runtime build command."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from wozmod.runtime import build


def run_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *flags: str) -> dict:
    seen: dict = {}

    def fake_build(stock_exe, output, **options):
        seen.update(options, stock=stock_exe, output=output)
        return output

    monkeypatch.setattr(build, "build_dll", fake_build)
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"dll")
    monkeypatch.setattr(sys, "argv", ["wozmod.runtime", "--game", str(tmp_path / "g"),
                                      "--output-dir", str(tmp_path / "o"), *flags])
    with pytest.raises(SystemExit) as done:
        runpy.run_module("wozmod.runtime", run_name="__main__")
    assert done.value.code == 0
    return seen


def test_no_flags_is_the_release_build_and_flags_reach_the_build(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Bugs: the packaging call (no flags, packaging/release_assets.py) gets a test or log build, or --test does not
    reach the build, so a hand-built check DLL has no harness mailbox."""
    seen = run_cli(monkeypatch, tmp_path, "--test", "--log")
    assert (seen["test"], seen["log"], seen["output"]) == (True, True, tmp_path / "o" / "d3d9.dll")
    release = run_cli(monkeypatch, tmp_path)
    assert [release[flag] for flag in ("test", "log", "bootlog", "moving_fix")] == [False] * 4
