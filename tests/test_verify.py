"""tools/verify.py: how a run names its tree, which tree a verdict counts for, where the judge comes from, which
checklist rows a diff triggers, and when a run's test set is narrower than a full one."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux_only("tools/verify.py locks with fcntl and runs on the Linux machine that drives Wine")
if sys.platform == "linux":  # it imports fcntl, so elsewhere it cannot load
    import verify

REPO = Path(__file__).resolve().parents[1]


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True, encoding="utf-8").stdout


def _repository(root: Path, files: dict[str, str]) -> Path:
    """A repository at root whose main branch holds files, with work/ ignored as in ours."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    for name, text in {".gitignore": "work/\nignored/\n", **files}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def test_the_tree_name_holds_untracked_files_and_not_ignored_ones(tmp_path):
    # Bug: the name is the index's tree (a new, untracked test file leaves it unchanged, so the verdict of the tree
    # without it is reused), or it holds ignored files (every build in work/ renames the tree), or naming the tree
    # stages the files in the checkout's own index.
    repo = _repository(tmp_path / "repo", {"a.py": "a"})
    clean = verify.tree_id(repo)
    (repo / "ignored").mkdir()
    (repo / "ignored/build.bin").write_text("built", encoding="utf-8")
    ignored = verify.tree_id(repo)
    (repo / "new_test.py").write_text("new", encoding="utf-8")
    untracked = verify.tree_id(repo)
    assert (clean == _git(repo, "rev-parse", "HEAD^{tree}").strip(), ignored == clean, untracked != clean) == (
        True, True, True)
    assert _git(repo, "status", "--porcelain") == "?? new_test.py\n"


def test_a_verdict_counts_only_for_the_tree_it_ran_on(tmp_path):
    # Bug: a verdict is attached to HEAD or found by check name alone, so a pass of the tree before an uncommitted
    # edit is reused for the edited tree, which shares its HEAD.
    repo = _repository(tmp_path / "repo", {"a.py": "a"})
    before = verify.tree_id(repo)
    (repo / "a.py").write_text("edited", encoding="utf-8")
    after = verify.tree_id(repo)
    verify.record(repo, before, {"check": "linux", "verdict": "pass", "at": "then"})
    assert (verify.recorded(repo, before, "linux")["verdict"], verify.recorded(repo, after, "linux"),
            verify.recorded(repo, before, "windows")) == ("pass", None, None)


def test_a_run_in_the_checkout_gets_its_callers_environment_with_only_strict_mode_and_the_switches(tmp_path, monkeypatch):
    # Bug: verify sets a variable that says where work/ is (WOZMOD_WORK, run of 2026-11-18 11:51) for every run, which
    # overrides the fake checkouts tests set up for the tools they run: test_machine_lock wrote the real
    # work/machine-lock.log and 14 game harness tests failed. Or a slow switch the caller set leaks into the fast run.
    repo = _repository(tmp_path / "repo", {"a.py": "a"})
    for name in ("WOZMOD_REQUIRE_INPUTS", *(f"WOZMOD_{switch}" for switch in verify.SWITCHES)):
        monkeypatch.delenv(name, raising=False)  # as a run under verify inherits them
    monkeypatch.setenv("WOZMOD_AUDIO", "1")
    monkeypatch.setenv("WOZMOD_GAME", "game")
    before = {name: value for name, value in os.environ.items() if name != "WOZMOD_AUDIO"}
    fast, slow = verify.environment(repo, False), verify.environment(repo, True)
    added = lambda env: {name: value for name, value in env.items() if before.get(name) != value}
    assert added(fast) == {"WOZMOD_REQUIRE_INPUTS": "1"}
    assert added(slow) == {"WOZMOD_REQUIRE_INPUTS": "1", **{f"WOZMOD_{name}": "1" for name in verify.SWITCHES}}
    assert "WOZMOD_AUDIO" not in fast


CHECKLIST = """
[[row]]
name = "R1"
paths = ["a/*"]
run = ["tests/test_r1.py"]
from = "base"

[[row]]
name = "R3"
paths = ["c/*"]
run = ["tests/test_r3.py"]
from = "base"
"""


def test_the_judge_comes_from_the_merge_base_and_an_edit_to_it_is_reported(tmp_path):
    # Bug: the checklist or verify.py is read from the tree under test, so a change that drops the row its own edit
    # triggers (here R1), or edits verify.py, decides what judges it; or the edit goes unreported.
    repo = _repository(tmp_path / "repo", {"tools/verify.py": (REPO / "tools/verify.py").read_text(encoding="utf-8"),
                                           "tools/verify_checks.toml": CHECKLIST, "a/file.py": "a"})
    (repo / "tools/verify_checks.toml").write_text(CHECKLIST.replace('"a/*"', '"zzz/*"').replace('"R3"', '"R2"'),
                                                   encoding="utf-8")
    edited = (repo / "tools/verify.py").read_text(encoding="utf-8").replace(
        '    print(f"judge: ', '    print("THE TREE\'S OWN VERIFY")\n    print(f"judge: ')
    (repo / "tools/verify.py").write_text(edited, encoding="utf-8")
    (repo / "a/file.py").write_text("edited", encoding="utf-8")
    env = {name: value for name, value in os.environ.items() if not name.startswith("WOZMOD_")}
    run = subprocess.run([sys.executable, "tools/verify.py", "--plan"], cwd=repo, env=env, capture_output=True,
                         encoding="utf-8")
    assert run.returncode == 0, run.stderr
    rows = [line.split(":")[0].strip() for line in run.stdout.splitlines() if line.startswith("  ")]
    assert (rows, "THE TREE'S OWN VERIFY" in run.stdout,
            "the tree changes the judge (tools/verify.py, tools/verify_checks.toml)" in run.stdout) == (
        ["R1", "R3"], False, True), run.stdout


def test_a_row_triggers_on_a_matching_path_anywhere_below_its_folder_and_every_row_cites_its_source():
    # Bug: a pattern matches only the folder's own files (runtime/* missing runtime/sub/x.c) or matches a sibling
    # by prefix (runtime_x/), so a runtime change skips the DLL pin; or a row cites no incident to prune it by.
    rows = [{"name": "runtime", "paths": ["runtime/*"]}, {"name": "lock", "paths": ["uv.lock"]}]
    cases = {"runtime/english_runtime.c": ["runtime"], "runtime/sub/guards.h": ["runtime"], "runtime_x/a.c": [],
             "uv.lock": ["lock"], "src/uv.lock": []}
    assert {path: [row["name"] for row in verify.triggered(rows, [path])] for path in cases} == cases
    real = tomllib.loads((REPO / "tools/verify_checks.toml").read_text(encoding="utf-8"))
    assert [row["name"] for row in real["row"] if not (row["paths"] and row["run"] and row["from"].strip())] == []


def test_a_triggered_run_exports_and_builds_once_for_all_the_rows_that_read_the_reference_builds():
    # Bug: each row with `ref` items exports both trees and reruns every reader again (three times a run before
    # 2026-11-20), or a step or test one of them names is left out of the shared run.
    rows = [{"name": "install", "run": ["ref text", "ref art", "tests/test_e2e.py::test_a"]},
            {"name": "plain", "run": ["tests/test_p.py"]},
            {"name": "voices", "run": ["ref voices", "tests/test_v.py"]},
            {"name": "references", "run": ["ref text", "tests/test_e2e.py::test_a"]}]
    assert [(c["rows"], c["steps"], c["tests"]) for c in verify.checks(rows)] == [
        (["install", "voices", "references"], ["text", "voices", "art"], ["tests/test_e2e.py::test_a", "tests/test_v.py"]),
        (["plain"], [], ["tests/test_p.py"])]


TESTS = """
import pytest

def test_one():
    pass

@pytest.mark.xdist_group("shared")
def test_two():
    pass

@pytest.mark.parametrize("n", [1, 2])
def test_each(n):
    pass

class TestGroup:
    def test_in_class(self):
        pass

def test_needs_an_input():
    pytest.skip("set WOZMOD_GAME")

def test_of_another_system():
    pytest.skip("Linux only: drives a Linux tool")
"""


def test_a_run_that_left_out_or_skipped_tests_a_full_run_collects_is_narrowed(tmp_path):
    # Bug: the release check counts only failures, so a run narrowed by -k, a path or a skip (a missing input, a
    # switch the judge does not know) passes; or it misnames parametrized, class or xdist-grouped tests (junit names
    # those test@group) and flags a full run.
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (project / "tests/test_cases.py").write_text(TESTS, encoding="utf-8")
    env = {name: value for name, value in os.environ.items() if not name.startswith(("WOZMOD_", "PYTEST"))}
    every = verify.collected(project, env, [sys.executable])
    full = verify.pytest(project, tmp_path / "full", ["-n2", "--dist", "loadgroup"], env, [sys.executable])
    narrow = verify.pytest(project, tmp_path / "narrow", ["-n0", "-k", "one or each"], env, [sys.executable])
    assert len(every) == 7
    assert verify.narrowed(every, full) == ["skipped: tests.test_cases::test_needs_an_input: set WOZMOD_GAME"]
    assert verify.narrowed(every, narrow) == [
        f"not run: tests.test_cases{name}" for name in ("::test_two", ".TestGroup::test_in_class",
                                                        "::test_needs_an_input", "::test_of_another_system")]
    shutil.rmtree(project)


def test_a_run_a_signal_stopped_is_no_verdict(tmp_path):
    # Bug: pytest killed mid-run (a stopped replay or verify) returns a negative code, which reads as a failed run, so
    # the tree is recorded red for no fault of its own and every later call reuses that verdict.
    killed = ["sh", "-c", "kill -TERM $$", "sh"]  # stands in for the Python that runs pytest
    with pytest.raises(SystemExit, match="stopped by signal 15; nothing is recorded"):
        verify.pytest(tmp_path, tmp_path / "out", [], dict(os.environ), killed)


def test_a_commit_run_tests_the_commits_own_tree_not_the_checkout(tmp_path, monkeypatch):
    # Bug: --commit runs the suite in the checkout (or imports the checkout's code), so an uncommitted edit or an
    # untracked test decides the verdict recorded for the commit's tree; or it records under the checkout's tree.
    repo = _repository(tmp_path / "repo", {"src/thing.py": "VALUE = 1\n",
                                           "tests/test_thing.py": "import thing\n\ndef test_value():\n"
                                                                  "    assert thing.VALUE == 1\n"})
    for name in ("machine_lock.py", "checkout.py"):  # the shared machine lock it takes, from the checkout's tools/
        (repo / "tools").mkdir(exist_ok=True)
        shutil.copy2(REPO / "tools" / name, repo / "tools" / name)
    # This test run holds the machine already: waiting for it again would wait behind a heavy run that waits for us.
    monkeypatch.setenv("WOZMOD_MACHINE_LOCK", "1")
    committed = _git(repo, "rev-parse", "HEAD^{tree}").strip()
    (repo / "src/thing.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "tests/test_untracked.py").write_text("def test_red():\n    assert False\n", encoding="utf-8")
    monkeypatch.setenv(verify.JUDGED, "tree")
    monkeypatch.setattr(verify, "venv", lambda repo, tree, folder: [sys.executable])  # the tree's environment
    found = verify.linux(repo, committed, rerun=False, exact=True)
    assert (found["verdict"], found["summary"].split(" (")[0], verify.recorded(repo, committed, "linux")["verdict"],
            verify.recorded(repo, verify.tree_id(repo), "linux")) == ("pass", "1 passed", "pass", None), found


def test_a_hand_back_exits_on_the_linux_verdict_and_wait_on_both(tmp_path):
    # Bug: the default mode fails a hand-back on the Windows verdict (as before this was decided), or --wait passes
    # with a red Windows verdict, which main would then refuse.
    repo = _repository(tmp_path / "repo", {"tools/verify.py": (REPO / "tools/verify.py").read_text(encoding="utf-8")})
    tree = verify.tree_id(repo)
    for check, verdict in (("linux", "pass"), ("windows", "fail")):
        verify.record(repo, tree, {"check": check, "verdict": verdict, "summary": "1 passed", "at": "then",
                                   "judge": "tree"})
    env = {name: value for name, value in os.environ.items() if not name.startswith("WOZMOD_")}
    codes = {}
    for flags in ((), ("--wait",)):
        run = subprocess.run([sys.executable, "tools/verify.py", *flags], cwd=repo, env=env, capture_output=True,
                             encoding="utf-8")
        assert "Windows: fail" in run.stdout, run.stdout + run.stderr
        codes[flags] = run.returncode
    assert codes == {(): 0, ("--wait",): 1}
