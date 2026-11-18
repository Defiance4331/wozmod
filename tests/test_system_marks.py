"""pytest.mark.linux_only and real_windows (tests/conftest.py): each runs on its own system, skips elsewhere with its
reason, and needs one."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CASE = "import pytest\n\n@pytest.mark.{mark}{arguments}\ndef test_it():\n    pass\n"
ARGUMENTS = {"given": "('drives a Linux-only tool')", "keyword": "(reason='drives a Linux-only tool')", "missing": "",
             "empty": "(' ')"}
WINE = sys.platform == "win32" and hasattr(ctypes.CDLL("ntdll"), "wine_get_version")
HERE = {"linux_only": (sys.platform == "linux", "Linux only"),
        "real_windows": (sys.platform == "win32" and not WINE, "Windows only, not Wine")}


@pytest.mark.parametrize("mark", HERE)
def test_a_system_mark_runs_on_its_system_skips_elsewhere_with_its_reason_and_needs_one(tmp_path, mark):
    # Bugs: the mark skips on its own system too, so its tests never run anywhere (real_windows: also when Wine is
    # taken for Windows, so a check Wine cannot make passes there without meaning anything); it skips elsewhere
    # without saying why; or a mark with no reason (or a blank one) is accepted, so nobody can tell why it never runs.
    from concurrent.futures import ThreadPoolExecutor

    def check(name: str) -> tuple:
        folder = tmp_path / name
        folder.mkdir()
        shutil.copy(REPO / "tests/conftest.py", folder / "conftest.py")
        (folder / "test_case.py").write_text(CASE.format(mark=mark, arguments=ARGUMENTS[name]), encoding="utf-8")
        # A config of its own: else pytest takes this checkout's (tmp_path is inside it) and walks folders outside.
        (folder / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("WOZMOD_")}
        env |= {"WOZMOD_HARNESS_CONFIG": str(folder / "harness.toml"),
                "PYTHONPATH": os.pathsep.join([str(REPO / "tools"), str(REPO / "src")])}
        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", "-n0",
                              "--rootdir", str(folder), str(folder)], capture_output=True, encoding="utf-8", env=env,
                             cwd=folder)
        summary = [line for line in run.stdout.splitlines() if line.startswith(("PASSED", "SKIPPED"))]
        refusal = "test_case.py::test_it" in run.stderr and f"{mark} needs a reason" in run.stderr
        return run.returncode, summary, refusal

    with ThreadPoolExecutor(len(ARGUMENTS)) as pool:  # each case is its own pytest process
        seen = dict(zip(ARGUMENTS, pool.map(check, ARGUMENTS), strict=True))
    here, label = HERE[mark]
    runs = (0, ["PASSED test_case.py::test_it" if here
                else f"SKIPPED [1] test_case.py:3: {label}: drives a Linux-only tool"], False)
    refused = (4, [], True)  # pytest's usage error, naming the test, before anything runs
    assert seen == {"given": runs, "keyword": runs, "missing": refused, "empty": refused}
