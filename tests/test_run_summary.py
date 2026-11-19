"""The last line of a test run names the tests that failed or errored (tests/conftest.py pytest_sessionfinish)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TESTS = """import pytest

def test_passes():
    pass

def test_fails():
    assert False

@pytest.fixture
def broken():
    raise RuntimeError

def test_errors(broken):
    pass
"""


def test_the_last_line_names_failed_and_errored_tests_and_a_clean_run_ends_on_its_count(tmp_path: Path) -> None:
    """Bugs: `pytest -q | tail -1` shows only the count, so finding the failed test takes a second run; the line
    leaves out errored tests; or a run with nothing failed ends on an empty list instead of its count."""
    shutil.copy(REPO / "tests/conftest.py", tmp_path / "conftest.py")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")  # not this checkout's six workers
    (tmp_path / "test_mixed.py").write_text(TESTS, encoding="utf-8")
    (tmp_path / "test_clean.py").write_text("def test_passes():\n    pass\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("WOZMOD_")}
    env["PYTHONPATH"] = os.pathsep.join([str(REPO / "tools"), str(REPO / "src")])
    last = {}
    for name in ("test_mixed.py", "test_clean.py"):
        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir",
                              str(tmp_path), name], capture_output=True, text=True, env=env, cwd=tmp_path,
                             encoding="utf-8")
        last[name] = run.stdout.rstrip().splitlines()[-1]
    assert last["test_mixed.py"] == "failed: test_mixed.py::test_fails; error: test_mixed.py::test_errors"
    assert last["test_clean.py"].startswith("1 passed")
