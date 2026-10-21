"""Export this machine's input paths, so tests that need stock or donor files run instead of skipping.

The paths live in the `[tests]` table of the main checkout's `work/harness.toml` (ignored, per machine; a linked
worktree reads the main checkout's; `WOZMOD_HARNESS_CONFIG` names another file). A variable already set in the
environment wins.

With WOZMOD_REQUIRE_INPUTS=1 (verify.sh) the run refuses to start without the config or one of its
inputs, and a skip fails unless its reason names a WOZMOD_ switch that is unset: a slow test left off is fine,
a test that skipped although everything it asks for is set is a missing input.
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import checkout
import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
CONFIG = checkout.config()
REQUIRE_INPUTS = os.environ.get("WOZMOD_REQUIRE_INPUTS") == "1"
INPUTS = tomllib.loads(CONFIG.read_text(encoding="utf-8")).get("tests", {}) if CONFIG.is_file() else {}

for name, value in INPUTS.items():
    os.environ.setdefault(name, str(value))


def pytest_configure(config) -> None:
    if not REQUIRE_INPUTS:
        return
    if not CONFIG.is_file():
        raise pytest.UsageError(f"WOZMOD_REQUIRE_INPUTS=1, but there is no {CONFIG}")
    missing = [name for name in INPUTS if not Path(os.environ[name]).exists()]
    if missing:
        raise pytest.UsageError(f"WOZMOD_REQUIRE_INPUTS=1, but these inputs do not exist: {', '.join(missing)}")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    if REQUIRE_INPUTS and report.skipped and not hasattr(report, "wasxfail"):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        if all(os.environ.get(name) for name in re.findall(r"WOZMOD_[A-Z0-9_]+", reason)):
            report.outcome = "failed"
            report.longrepr = f"skipped although WOZMOD_REQUIRE_INPUTS=1 and every switch it names is set: {reason}"


@pytest.fixture(scope="session")
def c_library(tmp_path_factory):
    """Compile C test glue against runtime/ with the DLL's warning flags, and load it: c_library(name, source)."""
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("a native C compiler is unavailable")

    def build(name: str, source: str) -> ctypes.CDLL:
        directory = tmp_path_factory.mktemp(name)
        (directory / f"{name}.c").write_text(source)
        subprocess.run([compiler, "-std=c11", "-O2", "-Wall", "-Werror", "-shared", "-fPIC", "-I", str(RUNTIME),
                        str(directory / f"{name}.c"), "-o", str(directory / f"{name}.so")], check=True)
        return ctypes.CDLL(str(directory / f"{name}.so"))

    return build


@pytest.fixture
def linked_worktree(tmp_path):
    """(main, worktree): a main checkout in tmp_path and a linked worktree of it, as `git worktree add` leaves them."""
    main, worktree = tmp_path / "main", tmp_path / "worktree"
    (main / ".git/worktrees/worktree").mkdir(parents=True)
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {main / '.git/worktrees/worktree'}\n")
    return main, worktree


@pytest.fixture
def letters(monkeypatch):
    """letters(draw, **more): the art build letters each request with draw(request) -> (image, metrics) instead of
    the font Python; more gives the producer's other methods (native, latin, outlines)."""
    from types import SimpleNamespace

    from wozmod.art import lettering

    return lambda draw, **more: monkeypatch.setattr(lettering, "PRODUCER", SimpleNamespace(produce=draw, **more))
