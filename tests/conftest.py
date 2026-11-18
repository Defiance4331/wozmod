"""Export this machine's input paths, so tests that need stock or donor files run instead of skipping.

The paths live in the `[tests]` table of the main checkout's `work/harness.toml` (ignored, per machine; a linked
worktree reads the main checkout's; `WOZMOD_HARNESS_CONFIG` names another file). A variable already set in the
environment wins.

With WOZMOD_REQUIRE_INPUTS=1 (tools/verify.py) the run refuses to start without the config or one of its
inputs, and a skip fails unless its reason names a WOZMOD_ switch that is unset or it is a system mark's (linux_only,
real_windows): a slow test left off, or a test of another system, is fine; a test that skipped although everything it
asks for is set is a missing input.
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import checkout
import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
MACHINE = pytest.StashKey[int | None]()  # the run's shared hold on the machine lock
CONFIG = checkout.config()
REQUIRE_INPUTS = os.environ.get("WOZMOD_REQUIRE_INPUTS") == "1"
INPUTS = checkout.inputs()
os.environ.update(INPUTS)
# Test files go to this checkout's work/, on disk: the slow tests build game copies, voice banks and art of several GB
# each, six at a time, and /tmp is often a tmpfs that holds them in RAM. pytest and the programs the tests start
# take their temporary folder from TMPDIR.
TEST_TMP = Path(__file__).resolve().parents[1] / "work" / "test-tmp"
TEST_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMPDIR"] = str(TEST_TMP)  # over an inherited TMPDIR too, which is often under /tmp as well
tempfile.tempdir = None  # read TMPDIR again


# Why each test that uses c_library is linux_only.
C_LIBRARY = "C logic compiled for the host with cc; the Wine stand-in exes cover the DLL on Windows"
# Why each test that uses font_python is linux_only.
FONT_PYTHON = ("draws with the font Python (FontForge and Cairo under the Linux system Python), a developer tool; "
               "players get what it draws in lettering.zip")
# The fixtures whose users are linux_only, and why.
LINUX_FIXTURES = {"c_library": C_LIBRARY, "font_python": FONT_PYTHON}
# Windows, but not Wine: Wine's ntdll names its version.
REAL_WINDOWS = sys.platform == "win32" and not hasattr(ctypes.CDLL("ntdll"), "wine_get_version")
# Each system mark: where its tests run, and what its skips say elsewhere.
SYSTEM_MARKS = {"linux_only": (sys.platform == "linux", "Linux only"),
                "real_windows": (REAL_WINDOWS, "Windows only, not Wine")}
ELSEWHERE = pytest.StashKey[bool]()  # set on a test a system mark skips here: its skip is no missing input


DISPLAY = pytest.StashKey["subprocess.Popen | None"]()  # the run's private X server


def _private_display() -> subprocess.Popen | None:
    """Point the run at a private virtual display (Xvfb), so a Wine program a test starts opens its windows there and
    never on the desktop, where it takes the focus from whatever the user is doing. Without Xvfb, no display at all."""
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ.pop("DISPLAY", None)
    if not shutil.which("Xvfb"):
        return None
    read, write = os.pipe()
    server = subprocess.Popen(["Xvfb", "-displayfd", str(write), "-nolisten", "tcp", "-noreset", "-screen", "0",
                               "1280x1024x24"], pass_fds=(write,), stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    os.close(write)
    with os.fdopen(read, encoding="ascii") as numbers:
        number = numbers.readline().strip()
    if number:
        os.environ["DISPLAY"] = f":{number}"
    return server


def pytest_unconfigure(config) -> None:
    if server := config.stash.get(DISPLAY, None):
        server.terminate()
        server.wait()


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "slow_first: a test of 2 s or more, started before the others")
    config.addinivalue_line("markers", "linux_only(reason): skipped on other systems, with the reason, which is required")
    config.addinivalue_line("markers", "real_windows(reason): skipped but on Windows itself (not Wine), with the reason "
                            "(what Wine cannot show), which is required")
    if os.name == "posix" and not hasattr(config, "workerinput"):  # once, before the workers start: they inherit it
        import machine_lock

        # Six workers beside a benchmark skew its numbers: wait while a heavy run has the machine, and hold it
        # shared while this run lasts.
        config.stash[MACHINE] = machine_lock.share(what="this test run")
        config.stash[DISPLAY] = _private_display()
    if not REQUIRE_INPUTS:
        return
    if not CONFIG.is_file():
        raise pytest.UsageError(f"WOZMOD_REQUIRE_INPUTS=1, but there is no {CONFIG}")
    missing = [name for name in INPUTS if not Path(os.environ[name]).exists()]
    if missing:
        raise pytest.UsageError(f"WOZMOD_REQUIRE_INPUTS=1, but these inputs do not exist: {', '.join(missing)}")


@pytest.hookimpl(tryfirst=True)  # before xdist reads the xdist_group marks
def pytest_collection_modifyitems(items) -> None:
    """Keep the tests that share a module fixture on one worker, and move the slow_first tests to the front.

    xdist hands tests to any worker, and each worker makes its own copy of a module fixture: the step keys' builds
    ran twice at once (31 minutes each), the voice maps' three times. xdist starts tests in the sorted order, so a
    slow test that sorts late keeps one worker busy after the others are done (the fast run took 20 s instead of 15).
    """
    for item in items:
        if any(defs[-1].scope == "module" for defs in item._fixtureinfo.name2fixturedefs.values()):
            item.add_marker(pytest.mark.xdist_group(item.module.__name__))
        _system_marks(item)
    items.sort(key=lambda item: item.get_closest_marker("slow_first") is None)


def _system_marks(item) -> None:
    """Skip a linux_only or real_windows test elsewhere, saying why; refuse the mark without a reason, which would
    hide the why."""
    for fixture, reason in LINUX_FIXTURES.items():
        if fixture in item.fixturenames:
            item.add_marker(pytest.mark.linux_only(reason))
    for name, (here, label) in SYSTEM_MARKS.items():
        for mark in item.iter_markers(name):
            reason = mark.args[0] if mark.args else mark.kwargs.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise pytest.UsageError(f"{item.nodeid}: pytest.mark.{name} needs a reason (why it runs only there)")
            if not here:
                item.add_marker(pytest.mark.skip(reason=f"{label}: {reason}"))
                item.stash[ELSEWHERE] = True


@pytest.fixture(autouse=True)
def _temporary_folder_left_as_found():
    """Bug: a test sets TMPDIR for its own folder, Python caches that folder as the temporary folder, pytest deletes
    it once the test passes, and a later test on the same worker fails to make a temporary file. That later test
    depends on which tests share its worker, so the failure looks random; this names the test that left it."""
    yield
    assert Path(tempfile.gettempdir()).resolve() == TEST_TMP.resolve(), (
        f"this test left Python's temporary folder at {tempfile.gettempdir()}: set tempfile.tempdir with monkeypatch")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    if REQUIRE_INPUTS and report.skipped and not hasattr(report, "wasxfail") and not item.stash.get(ELSEWHERE, False):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        if all(os.environ.get(name) for name in re.findall(r"WOZMOD_[A-Z0-9_]+", reason)):
            report.outcome = "failed"
            report.longrepr = f"skipped although WOZMOD_REQUIRE_INPUTS=1 and every switch it names is set: {reason}"


@pytest.fixture(scope="session")
def c_library(tmp_path_factory):
    """Compile C test glue against runtime/ with the DLL's warning flags, and load it: c_library(name, source).

    Its users are linux_only (C_LIBRARY): the flags, cc and .so are the Linux host's."""
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("a native C compiler is unavailable")

    def build(name: str, source: str) -> ctypes.CDLL:
        directory = tmp_path_factory.mktemp(name)
        (directory / f"{name}.c").write_text(source, encoding="utf-8")
        subprocess.run([compiler, "-std=c11", "-O2", "-Wall", "-Werror", "-shared", "-fPIC", "-I", str(RUNTIME),
                        str(directory / f"{name}.c"), "-o", str(directory / f"{name}.so")], check=True)
        return ctypes.CDLL(str(directory / f"{name}.so"))

    return build


@pytest.fixture(scope="session")
def font_python() -> str:
    """The font Python (lettering.FONT_PYTHON) when it has FontForge, Cairo, NumPy and Pillow.

    Its users are linux_only (FONT_PYTHON)."""
    from wozmod.art import lettering

    python = lettering.FONT_PYTHON
    if not Path(python).is_file():
        pytest.skip("font Python absent")
    probe = subprocess.run([python, "-c", "import importlib.util; print([m for m in ('fontforge', 'cairo', 'numpy', "
                                          "'PIL', 'psMat') if importlib.util.find_spec(m) is None])"],
                           capture_output=True, encoding="utf-8")
    if probe.stdout.strip() != "[]":
        pytest.skip(f"font libraries absent: {probe.stdout.strip() or probe.stderr.strip()}")
    return python


@pytest.fixture(scope="session")
def built():
    """built(step, folder): the folder of an installer step's build on the real inputs, holding out/ (what the step
    wrote, laid out as the game folder), staging/ (what it left in its staging folder) and trace.jsonl (what it read,
    install/gate.py).

    tools/verify.py ref names its reference build of this tree in WOZMOD_REFERENCE_<STEP>, and that is read, once its
    step key is this tree's; else the step is built here, in folder, as an install builds it (holding it to its
    manifest)."""
    import json

    def build(step: str, folder: Path) -> Path:
        from wozmod import install
        from wozmod.install import gate, step_keys, steps

        if named := os.environ.get(f"WOZMOD_REFERENCE_{step.upper()}"):
            made = json.loads((Path(named) / "build.json").read_text(encoding="utf-8"))["key"]
            mine = step_keys.keys()[step]["key"]
            assert made == mine, f"{named} is the {step} build of another tree: its key is {made}, this tree's {mine}"
            return Path(named)
        folder.mkdir(parents=True, exist_ok=True)
        steps.run_here(step, gate.ENFORCE, trace=folder / "trace.jsonl", **steps.Inputs.environment(
            install.worker_count()).arguments(folder / "staging", folder / "out"))
        return folder

    return build


@pytest.fixture
def linked_worktree(tmp_path):
    """(main, worktree): a main checkout in tmp_path and a linked worktree of it, as `git worktree add` leaves them."""
    main, worktree = tmp_path / "main", tmp_path / "worktree"
    (main / ".git/worktrees/worktree").mkdir(parents=True)
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {main / '.git/worktrees/worktree'}\n", encoding="utf-8")
    return main, worktree


@pytest.fixture
def letters(monkeypatch):
    """letters(draw, **more): the art build letters each request with draw(request) -> (image, metrics) instead of
    the font Python; more gives the producer's other methods (native, latin, outlines)."""
    from types import SimpleNamespace

    from wozmod.art import lettering

    return lambda draw, **more: monkeypatch.setattr(lettering, "PRODUCER", SimpleNamespace(produce=draw, **more))
