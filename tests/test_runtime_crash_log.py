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


CRASHER = r"""#include "crash_log.h"
static HANDLE log_file;
static void line(const char *text, unsigned int value)
{
    char out[200];
    DWORD n;
    WriteFile(log_file, out, value ? wsprintfA(out, "%s 0x%08x\r\n", text, value) : wsprintfA(out, "%s\r\n", text), &n, 0);
}
static LONG WINAPI runtime_filter(EXCEPTION_POINTERS *e) { (void)e; ExitProcess(3); }
/* The game's SetUnhandledExceptionFilter import slot, which the DLL points at crash_set_filter. */
static LPTOP_LEVEL_EXCEPTION_FILTER (WINAPI *volatile slot)(LPTOP_LEVEL_EXCEPTION_FILTER) = crash_set_filter;
__attribute__((noinline)) void boom(volatile int *at) { *at = 1; }
__attribute__((noinline)) void runtime_exit(void) { slot(0); ExitProcess(4); }
int main(int argc, char **argv)
{
    (void)argv;
    log_file = CreateFileW(L"runtime.log", GENERIC_WRITE, FILE_SHARE_READ, 0, CREATE_ALWAYS, 0, 0);  /* as the DLL */
    crash_watch(line, L"runtime.log", L"runtime.crash.log");
    slot(runtime_filter);  /* the C runtime's start-up filter, set after ours */
    if (argc > 1)
        runtime_exit();
    {
        void (*volatile pointer)(volatile int *) = boom;  /* a code address on the stack that no call left there */
        pointer((volatile int *)0x10);
    }
    return 0;
}
"""


def _crash(tmp_path: Path, *args: str) -> tuple[int, list[str], bytes | None, dict[str, range]]:
    """Build CRASHER, run it with args: its exit code, runtime.log's lines, runtime.crash.log as the run left it, and
    each function's range as offsets from the image base the linker chose."""
    for tool in ("i686-w64-mingw32-gcc", "i686-w64-mingw32-nm", "wine"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} absent")
    (tmp_path / "boom.c").write_text(CRASHER)
    exe = tmp_path / "boom.exe"
    subprocess.run(["i686-w64-mingw32-gcc", "-std=c11", "-O1", "-Wall", "-Werror", "-I", str(RUNTIME),
                    str(tmp_path / "boom.c"), "-o", str(exe)], check=True)
    symbols = [s.split() for s in subprocess.run(["i686-w64-mingw32-nm", "-n", str(exe)], check=True,
                                                 capture_output=True, text=True).stdout.splitlines()]
    text = [(int(a, 16), n) for a, kind, n in (s for s in symbols if len(s) == 3) if kind in "Tt"]
    base = next(int(a, 16) for a, _, n in (s for s in symbols if len(s) == 3) if n == "___ImageBase")
    # each function ends at the next symbol past its start (a section symbol can share its address)
    spans = {n: range(a - base, next(b for b, _ in text if b > a) - base) for a, n in text if a < text[-1][0]}
    env = {**os.environ, "WINEPREFIX": str(tmp_path / "prefix"), "WINEDEBUG": "-all", "WINEDLLOVERRIDES": "mscoree,mshtml="}
    run = subprocess.run(["wine", str(exe), *args], cwd=tmp_path, env=env, capture_output=True, timeout=60)  # Wine waits on a crash dialog
    crash = tmp_path / "runtime.crash.log"
    return (run.returncode, (tmp_path / "runtime.log").read_text().splitlines(),
            crash.read_bytes() if crash.exists() else None, spans)


def _values(lines: list[str]) -> dict[str, int]:
    return {l.rsplit(" 0x", 1)[0]: int(l.rsplit(" 0x", 1)[1], 16) for l in lines if " 0x" in l}


@pytest.mark.skipif(os.environ.get("WOZMOD_RUNTIME_COMPILE") != "1", reason="set WOZMOD_RUNTIME_COMPILE=1")
def test_a_crash_is_logged_and_kept_as_it_happens(tmp_path: Path) -> None:
    """Bugs: the C runtime's start-up filter, set after ours, replaces it (no crash lines: the game's case), or ours
    drops it (exit code not 3); runtime.crash.log waits for the next start, or its copy fails on the open log's share
    mode; the offset is the absolute address, the stack is walked the wrong way (no caller), a code address no call left
    (a function pointer) is listed as a caller, a system module is named without its folder (so Windows' d3d9.dll reads
    as the game's), or the access is misread."""
    code, lines, crash, spans = _crash(tmp_path)
    value = _values(lines)
    stack = [int(l.rsplit(" 0x", 1)[1], 16) for l in lines if l.startswith("crash: stack holds boom.exe, offset")]
    # a module from outside the game's folder keeps its folder (Windows' own d3d9.dll beside the game's)
    others = [l for l in lines if l.startswith("crash: stack holds ") and "boom.exe" not in l]
    assert (code, value.get("crash: exception"), value.get("crash: access (0 read, 1 write, 8 run)"),
            value.get("crash: of address"), value.get("crash: in boom.exe, offset") in spans["_boom"],
            any(o in spans["_main"] for o in stack), spans["_boom"].start in stack,
            bool(others) and all("\\" in l for l in others), crash == (tmp_path / "runtime.log").read_bytes()
            ) == (3, 0xC0000005, 1, 0x10, True, True, False, True, True), lines


@pytest.mark.skipif(os.environ.get("WOZMOD_RUNTIME_COMPILE") != "1", reason="set WOZMOD_RUNTIME_COMPILE=1")
def test_the_c_runtime_ending_the_run_is_logged_and_kept(tmp_path: Path) -> None:
    """Bugs: abort or a failed buffer check, which clear the filter and end the run with no exception, leave no line
    and no runtime.crash.log, or the line names crash_set_filter's own caller wrongly (not the runtime's exit)."""
    code, lines, crash, spans = _crash(tmp_path, "exit")
    assert (code, "crash: the C runtime is ending the run" in lines,
            _values(lines).get("crash: called from boom.exe, offset") in spans["_runtime_exit"],
            crash == (tmp_path / "runtime.log").read_bytes()) == (4, True, True, True), lines
