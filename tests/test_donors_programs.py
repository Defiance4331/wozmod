"""Running a player's decryption program: fake programs in tmp_path stand in for the real ones."""

from __future__ import annotations

import hashlib
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from wozmod.donors.programs import ProgramError, ProgramIdentity, run_tool, verify_program

PAYLOAD = b"an authored test program"
ENCRYPTED = b"an authored encrypted input"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake programs are scripts with a #! line")

SHAPES = {  # how each known program is called, written out from the real command lines
    "psp": "args[0] == '-o' and len(args) == 3", "xex2ool": "args[0] == 'basefile' and args[2] == '-o' and len(args) == 4",
    "xextool": "args[0] == '-b' and len(args) == 3",
}
OUTPUT_AT = {"psp": "args[1]", "xex2ool": "args[3]", "xextool": "args[1]"}
INPUT_AT = {"psp": "args[2]", "xex2ool": "args[1]", "xextool": "args[2]"}


def _identity(platform: str = "x360") -> ProgramIdentity:
    return ProgramIdentity("Test program", len(PAYLOAD), hashlib.sha256(PAYLOAD).hexdigest(), platform)


def fake(path: Path, shape: str, *, produces: bytes = PAYLOAD, zeros: int = 0, body: str = "") -> Path:
    """A program that behaves like `shape` and only works when its input is ENCRYPTED."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\nimport sys, time\nfrom pathlib import Path\nargs = sys.argv[1:]\n{body}\n"
                    f"if not ({SHAPES[shape]}):\n    print('usage: wrong arguments'); sys.exit(2)\n"
                    f"if Path({INPUT_AT[shape]}).read_bytes() != {ENCRYPTED!r}:\n    sys.exit(3)\n"
                    f"print('working...')\nPath({OUTPUT_AT[shape]}).write_bytes({produces!r} + bytes({zeros}))\n",
                    encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_verify_program_rejects_wrong_size_even_when_hash_matches(tmp_path: Path) -> None:
    source = tmp_path / "program"
    source.write_bytes(PAYLOAD)
    smaller = ProgramIdentity("Test program", len(PAYLOAD) - 1, hashlib.sha256(PAYLOAD).hexdigest(), "x360")
    with pytest.raises(ProgramError, match="size"):
        verify_program(source, smaller)


def test_a_program_given_by_a_relative_path_still_runs(tmp_path, monkeypatch):
    # Bug: the program runs in a temporary folder, so a path typed relative to where the player is
    # (`--x360-tool ./xex2ool`) names nothing there and every install fails.
    fake(tmp_path / "tools/xex2ool", "xex2ool")
    monkeypatch.chdir(tmp_path)
    program, transcript = run_tool(Path("tools/xex2ool"), _identity("x360"), ENCRYPTED)
    assert program == PAYLOAD, transcript


@pytest.mark.parametrize("shape, platform, zeros", [("psp", "psp", 0), ("xex2ool", "x360", 0), ("xextool", "x360", 32_768)])
def test_each_known_program_is_recognised_whatever_its_file_is_called(tmp_path, shape, platform, zeros):
    # Bug: a profile has the wrong argument order or output place (the program fails with its usage text),
    # the program is only tried under the first profile, or XexTool's zero padding makes its output refused.
    tool = fake(tmp_path / "my decryptor", shape, zeros=zeros)
    program, transcript = run_tool(tool, _identity(platform), ENCRYPTED)
    assert program == PAYLOAD, transcript
    assert "working..." in transcript


def test_output_that_does_not_match_the_pin_is_refused(tmp_path):
    # Bug: a zero exit code or the right size is trusted, so a wrong file (a changed byte) is installed.
    tool = fake(tmp_path / "psp tool", "psp", produces=b"X" + PAYLOAD[1:])
    program, transcript = run_tool(tool, _identity("psp"), ENCRYPTED)
    assert program is None and "output not accepted" in transcript


def test_a_program_that_hangs_is_stopped_by_the_timeout(tmp_path):
    # Bug: no timeout, so the install waits forever on a program that never ends (or holds the output open).
    tool = fake(tmp_path / "hang", "psp", body="time.sleep(60)")
    started = time.monotonic()
    program, transcript = run_tool(tool, _identity("psp"), ENCRYPTED, timeout=1)
    assert program is None and "stopped after 1 seconds" in transcript
    assert time.monotonic() - started < 10


def test_paths_with_spaces_and_shell_characters_are_run_as_given_with_no_shell(tmp_path, monkeypatch):
    # Bug: the command is joined into one string or given to a shell, so a space splits the path and
    # "; touch" or "$(...)" in a name runs a second command.
    folder = tmp_path / "my tools; touch pwned $(echo x)"
    tool = fake(folder / "de crypt & go", "xex2ool")
    work = tmp_path / "temp with spaces"
    work.mkdir()
    monkeypatch.setenv("TMPDIR", str(work))
    monkeypatch.chdir(tmp_path)
    program, transcript = run_tool(tool, _identity(), ENCRYPTED)
    assert program == PAYLOAD, transcript
    assert not (tmp_path / "pwned").exists() and not (folder / "pwned").exists()


def test_a_program_that_cannot_start_gives_a_plain_failure(tmp_path):
    # Bug: a file that is not executable raises and the window shows a traceback.
    tool = tmp_path / "not a program"
    tool.write_text("text")
    assert run_tool(tool, _identity(), ENCRYPTED)[0] is None
