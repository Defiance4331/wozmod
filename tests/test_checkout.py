"""The developer tools' view of this machine (tools/checkout.py): where its test inputs are."""

from __future__ import annotations

import sys

import checkout


def test_a_windows_run_takes_the_windows_build_of_each_program_input(tmp_path, monkeypatch):
    # Bug: the Windows run reads only [tests], so the decryption program it starts is the Linux build, which Windows
    # cannot run, and the test of the real program fails for a reason no player meets.
    config = tmp_path / "harness.toml"
    config.write_text('[tests]\nWOZMOD_X_TOOL = "linux/tool"\nWOZMOD_X_GAME = "game"\n\n'
                      '[tests-windows]\nWOZMOD_X_TOOL = "windows/tool.exe"\n', encoding="utf-8")
    monkeypatch.setenv("WOZMOD_HARNESS_CONFIG", str(config))
    monkeypatch.delenv("WOZMOD_X_TOOL", raising=False)
    monkeypatch.delenv("WOZMOD_X_GAME", raising=False)
    tool = "windows/tool.exe" if sys.platform == "win32" else "linux/tool"
    assert checkout.inputs() == {"WOZMOD_X_TOOL": tool, "WOZMOD_X_GAME": "game"}

