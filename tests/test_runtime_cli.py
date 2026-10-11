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


def test_test_build_keeps_the_production_cell_unless_cell_is_given(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A default cell other than 24 would make every --test layout verdict 17% too wide."""
    seen = run_cli(monkeypatch, tmp_path, "--test", "--log")
    assert (seen["cell"], seen["test"], seen["log"], seen["preview"]) == (24, True, True, False)
    assert seen["output"] == tmp_path / "o" / "d3d9.dll"
    assert run_cli(monkeypatch, tmp_path, "--cell", "28")["cell"] == 28
