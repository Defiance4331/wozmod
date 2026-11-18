"""tools/windows_suite.py: which Windows environment a tree gets, and what a run's results say."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux_only("tools/windows_suite.py drives Wine from the Linux host and locks with fcntl")
if sys.platform == "linux":  # it imports fcntl, so elsewhere it cannot load
    import windows_suite


def _tree(root: Path) -> Path:
    for name, text in {"uv.lock": "lock", windows_suite.PIN: "{}",
                       "native/Cargo.toml": "cargo", "native/src/lib.rs": "codecs", "native/target/built": "old"}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root


def test_the_environment_is_rebuilt_exactly_when_what_it_is_built_from_changes(tmp_path, monkeypatch):
    # Bug: the key leaves out a file the environment is built from, so a run tests the old codecs against the new
    # Python (the stale 0.3.0 wheel); or it hashes the build output in native/target/, so every build rebuilds it.
    compiler = {"version": "rustc 1.0"}
    monkeypatch.setattr(windows_suite, "rustc", lambda tree: compiler["version"])
    tree = _tree(tmp_path)
    first = windows_suite.key(tree)
    changes = {"native/src/lib.rs": True, "native/Cargo.toml": True, "uv.lock": True, windows_suite.PIN: True,
               "native/target/built": False, "rustc": True}
    rebuilt = {}
    for name in changes:
        if name == "rustc":
            compiler["version"] = "rustc 2.0"
        else:
            before = (tree / name).read_text(encoding="utf-8")
            (tree / name).write_text(before + " edited", encoding="utf-8")
        rebuilt[name] = windows_suite.key(tree) != first
        if name == "rustc":
            compiler["version"] = "rustc 1.0"
        else:
            (tree / name).write_text(before, encoding="utf-8")
    assert rebuilt == changes


def test_a_run_counts_errors_and_failures_as_broken_and_lists_each_skip_reason(tmp_path):
    # Bug: an error (a fixture or collection that broke) is not counted, so the run reports success.
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuites><testsuite><testcase classname="t" name="ok"/>'
        '<testcase classname="t" name="bad"><failure message="assert 1 == 2">tb</failure></testcase>'
        '<testcase classname="t" name="setup"><error message="fixture broke">tb</error></testcase>'
        '<testcase classname="t" name="off"><skipped message="Linux only: why">s</skipped></testcase>'
        '</testsuite></testsuites>', encoding="utf-8")
    text, broken = windows_suite.summary(junit)
    assert broken == 2
    assert text.splitlines() == ["1 passed, 1 failure, 1 error, 1 skipped", "FAILURE t::bad: assert 1 == 2",
                                 "ERROR t::setup: fixture broke", "skips:", "     1 Linux only: why"]
