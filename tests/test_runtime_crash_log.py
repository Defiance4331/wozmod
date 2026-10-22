"""The runtime keeps a crashed run's log (runtime/crash_log.h) before the next launch overwrites runtime.log."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"

# Long enough that the exit line sits past the first 64 bytes, as in a real log.
RUN = b"started 2026-10-22 08:59:59 UTC\r\n" + b"installed: English text, hooks 0x00000067\r\n" * 4
CLEAN, CRASHED = RUN + b"process exit 0x00000002\r\n", RUN


@pytest.mark.skipif(os.environ.get("WOZMOD_RUNTIME_COMPILE") != "1", reason="set WOZMOD_RUNTIME_COMPILE=1")
def test_only_a_crashed_runs_log_is_kept(tmp_path: Path) -> None:
    """Bugs: the tail is read from the start of the file (a clean log looks crashed), a clean log is kept too, or an
    older runtime.crash.log blocks the rename (no MOVEFILE_REPLACE_EXISTING), so the newest crash is lost."""
    if not (shutil.which("i686-w64-mingw32-gcc") and shutil.which("wine")):
        pytest.skip("i686-w64-mingw32-gcc or wine absent")
    (tmp_path / "keep.c").write_text('#include "crash_log.h"\n'
                                     'int main(void) { keep_crash_log(L"runtime.log", L"runtime.crash.log"); return 0; }\n')
    subprocess.run(["i686-w64-mingw32-gcc", "-std=c11", "-O2", "-Wall", "-Werror", "-I", str(RUNTIME),
                    str(tmp_path / "keep.c"), "-o", str(tmp_path / "keep.exe")], check=True)
    env = {**os.environ, "WINEPREFIX": str(tmp_path / "prefix"), "WINEDEBUG": "-all", "WINEDLLOVERRIDES": "mscoree,mshtml="}
    # (last run's log, an older crash log) -> (runtime.log, runtime.crash.log) after the launch; None: no file
    cases = {
        "clean": ((CLEAN, None), (CLEAN, None)),
        "crashed": ((CRASHED, None), (None, CRASHED)),
        "crashed after an earlier crash": ((CRASHED, b"older crash"), (None, CRASHED)),
        "clean after an earlier crash": ((CLEAN, b"older crash"), (CLEAN, b"older crash")),
    }
    got = {}
    for name, ((log, crash), _) in cases.items():
        folder = tmp_path / name.replace(" ", "_")
        folder.mkdir()
        (folder / "runtime.log").write_bytes(log)
        if crash is not None:
            (folder / "runtime.crash.log").write_bytes(crash)
        subprocess.run(["wine", str(tmp_path / "keep.exe")], cwd=folder, env=env, check=True, capture_output=True)
        got[name] = tuple((folder / f).read_bytes() if (folder / f).exists() else None
                          for f in ("runtime.log", "runtime.crash.log"))
    assert got == {name: after for name, (_, after) in cases.items()}
