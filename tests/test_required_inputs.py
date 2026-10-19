"""With WOZMOD_REQUIRE_INPUTS=1 a run fails on a missing input instead of skipping the tests that need it."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEST = "import os, pytest\n\ndef test_needs_inputs():\n    pytest.skip(os.environ['REASON'])\n"

# name, strict, harness.toml [tests] value (None: no config), switches set, skip reason, pytest exit code
CASES = [
    ("a slow test left off", True, ".", {}, "set WOZMOD_FLAG=1", 0),
    ("a skip although every switch it names is set", True, ".", {"WOZMOD_FLAG": "1"},
     "set WOZMOD_FLAG=1, with ffmpeg", 1),
    ("a configured input that is set but skipped", True, ".", {}, "set WOZMOD_INPUT", 1),
    ("a skip that names no switch", True, ".", {}, "a native C compiler is unavailable", 1),
    ("no config", True, None, {}, "set WOZMOD_FLAG=1", 4),
    ("a configured input that does not exist", True, "absent", {}, "set WOZMOD_FLAG=1", 4),
    ("an ordinary run", False, None, {"WOZMOD_FLAG": "1"}, "set WOZMOD_FLAG=1", 0),
]


def test_required_inputs_turn_unexpected_skips_into_failures(tmp_path: Path) -> None:
    # Bug: a linked worktree (or a push from one) finds no work/harness.toml, so every test that reads the game
    # or the donors skips, and verify.sh still reports a pass; or a strict run refuses a slow test left off.
    wrong = []
    for index, (name, strict, value, switches, reason, code) in enumerate(CASES):
        folder = tmp_path / str(index)
        folder.mkdir()
        shutil.copy(REPO / "tests/conftest.py", folder / "conftest.py")
        (folder / "test_case.py").write_text(TEST)
        config = folder / "harness.toml"
        if value is not None:
            config.write_text(f"[tests]\nWOZMOD_INPUT = {str(folder / value)!r}\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith("WOZMOD_")}
        env |= switches | {"REASON": reason, "WOZMOD_HARNESS_CONFIG": str(config),
                           "PYTHONPATH": os.pathsep.join([str(REPO / "tools"), str(REPO / "src")])}
        if strict:
            env["WOZMOD_REQUIRE_INPUTS"] = "1"
        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(folder),
                              str(folder)], capture_output=True, text=True, env=env, cwd=folder)
        if run.returncode != code:
            wrong.append(f"{name}: exit {run.returncode}, expected {code}\n{run.stdout[-400:]}{run.stderr[-400:]}")
    assert wrong == []
