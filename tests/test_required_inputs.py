"""With WOZMOD_REQUIRE_INPUTS=1 a run fails on a missing input instead of skipping the tests that need it."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEST = "import os, pytest\n\ndef test_needs_inputs():\n    pytest.skip(os.environ['REASON'])\n"
# A test of another system: its system mark skips it here (tests/conftest.py SYSTEM_MARKS).
ELSEWHERE = "real_windows" if sys.platform == "linux" else "linux_only"
OTHER_SYSTEM = f"import pytest\n\n@pytest.mark.{ELSEWHERE}('another system')\ndef test_needs_inputs():\n    pass\n"

# name, strict, harness.toml [tests] value (None: no config), switches set, skip reason (None: OTHER_SYSTEM), exit code
CASES = [
    ("a slow test left off", True, ".", {}, "set WOZMOD_FLAG=1", 0),
    ("a skip although every switch it names is set", True, ".", {"WOZMOD_FLAG": "1"},
     "set WOZMOD_FLAG=1, with ffmpeg", 1),
    ("a configured input that is set but skipped", True, ".", {}, "set WOZMOD_INPUT", 1),
    ("a skip that names no switch", True, ".", {}, "a native C compiler is unavailable", 1),
    ("a test of another system", True, ".", {}, None, 0),
    ("no config", True, None, {}, "set WOZMOD_FLAG=1", 4),
    ("a configured input that does not exist", True, "absent", {}, "set WOZMOD_FLAG=1", 4),
    ("an ordinary run", False, None, {"WOZMOD_FLAG": "1"}, "set WOZMOD_FLAG=1", 0),
]


@pytest.mark.slow_first
def test_required_inputs_turn_unexpected_skips_into_failures(tmp_path: Path) -> None:
    # Bug: a linked worktree (or a push from one) finds no work/harness.toml, so every test that reads the game
    # or the donors skips, and tools/verify.py still reports a pass; or a strict run refuses a slow test left off,
    # or a test its system mark skips here (every real_windows test failed every strict Linux run).
    from concurrent.futures import ThreadPoolExecutor

    def check(index: int) -> str | None:
        name, strict, value, switches, reason, code = CASES[index]
        folder = tmp_path / str(index)
        folder.mkdir()
        shutil.copy(REPO / "tests/conftest.py", folder / "conftest.py")
        (folder / "test_case.py").write_text(TEST if reason is not None else OTHER_SYSTEM, encoding="utf-8")
        # A config of its own: else pytest takes this checkout's (tmp_path is inside it), with its six workers, and
        # walks folders outside the case's, such as the temporary folders other tests are making and removing.
        (folder / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        config = folder / "harness.toml"
        if value is not None:
            config.write_text(f"[tests]\nWOZMOD_INPUT = {str(folder / value)!r}\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("WOZMOD_")}
        env |= switches | {"REASON": reason or "", "WOZMOD_HARNESS_CONFIG": str(config),
                           "PYTHONPATH": os.pathsep.join([str(REPO / "tools"), str(REPO / "src")])}
        if strict:
            env["WOZMOD_REQUIRE_INPUTS"] = "1"
        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(folder),
                              str(folder)], capture_output=True, text=True, env=env, cwd=folder, encoding="utf-8")
        if run.returncode != code:
            return f"{name}: exit {run.returncode}, expected {code}\n{run.stdout[-400:]}{run.stderr[-400:]}"
        return None

    with ThreadPoolExecutor(len(CASES)) as pool:  # each case is its own pytest process
        assert [found for found in pool.map(check, range(len(CASES))) if found] == []


def test_the_test_setup_loads_without_posix_only_modules():
    # Bug: tests/conftest.py imports a POSIX-only module (fcntl, resource), so pytest cannot start on Windows and
    # packaging/build_windows.sh cannot check the Windows codecs.
    script = (
        "import importlib.abc, runpy, sys\n"
        "class Deny(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name in {'fcntl', 'resource'}: raise ImportError(name + ' is POSIX-only')\n"
        "sys.modules.pop('fcntl', None); sys.modules.pop('resource', None)\n"
        "sys.meta_path.insert(0, Deny())\n"
        f"sys.path.insert(0, {str(REPO / 'tools')!r})\n"
        f"runpy.run_path({str(REPO / 'tests/conftest.py')!r})\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
