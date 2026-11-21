"""The commit-msg hook keeps commit messages to the changelog format in AGENTS.md, and the reference-transaction hook
lets main move only to a commit whose tree has passing Linux, Windows and triggered-row verdicts (tools/verify.py)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

if sys.platform == "linux":  # it imports fcntl, so elsewhere it cannot load
    import verify

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "tools/githooks/commit-msg"
GATE = "tools/githooks/reference-transaction"
WHY = "Players who are not developers could not tell what to pick."
GOOD = ["- Add -v/--version, and show the version in the window's corner.",
        "- Say why Install is disabled, and what to do after an error."]


def _message(subject: str = "Show the version and redesign the setup window", changes: list[str] = GOOD,
             extra: str = "") -> str:
    return "\n".join([subject, "", WHY + extra, "", "Changes:", *changes, ""])


CASES = [
    ("the AGENTS.md example", _message(), True),
    ("a merge", "Merge branch 'x'\n", True),
    ("git's comment lines", _message() + "# Please enter the commit message\n", True),
    ("a 73-character subject", _message("S" * 73), False),
    ("one change", _message(changes=GOOD[:1]), False),
    ("no Changes section", "Fix the thing\n\nIt broke.\n", False),
    ("seven changes", _message(changes=GOOD * 3 + GOOD[:1]), False),
    ("a test count", _message(extra=" 574 passed."), False),
    ("a file list", _message(changes=GOOD + ["- src/wozmod/window.py: redesign"]), False),
    ("an open to-do", _message(changes=GOOD + ["- Open: the PSP rows"]), False),
    ("a private ADR", _message(extra=" See ADR 0026."), False),
    ("the private folder", _message(extra=" Notes in private/layout-decisions.md."), False),
    ("the vault", _message(extra=" Filed in the vault."), False),
    ("Japanese text", _message(extra=" Quote \u30a2."), False),
    ("an issue reference", _message() + "Reported in #16 and #17\n", True),
    ("a closing keyword", _message() + "Fixes #17\n", False),
    ("a closing keyword in the why", _message(extra=" This resolved: #17."), False),
]


@pytest.mark.linux_only("the hook is a POSIX shell script, which git runs with sh on the contributor's machine")
def test_commit_msg_hook_accepts_the_changelog_format_and_refuses_the_rest(tmp_path: Path) -> None:
    # Bug: a regex typo lets a file list, a test count, a private ADR number, quoted Japanese or a 'Fixes #N' that
    # closes the issue before the release into a public commit message, or the hook starts refusing the format
    # AGENTS.md documents (including its 'Reported in #N' line).
    wrong = []
    for name, message, accepted in CASES:
        path = tmp_path / "COMMIT_EDITMSG"
        path.write_text(message, encoding="utf-8")
        run = subprocess.run(["sh", str(HOOK), str(path)], capture_output=True, text=True, encoding="utf-8")
        if (run.returncode == 0) != accepted:
            wrong.append(f"{name}: exit {run.returncode} {run.stderr.strip()}")
    assert wrong == []



def _git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {name: value for name, value in os.environ.items() if not name.startswith(("GIT_", "WOZMOD_"))}
    return subprocess.run(["git", *arguments], cwd=repo, check=check, capture_output=True, encoding="utf-8", env=env)


def _identify(repo: Path) -> None:
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")


def _commit(repo: Path, name: str) -> subprocess.CompletedProcess:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", "-A")
    return _git(repo, "commit", "-q", "-m", name, check=False)


CHECKLIST = """[[row]]
name = "runtime builds"
paths = ["runtime/*"]
run = ["tests/test_runtime_build.py"]
from = "a cited incident"
"""


def _gated(root: Path) -> Path:
    """A clone of an upstream whose main holds one commit, with the gate (and the verify.py it runs) installed as in
    ours: core.hooksPath tools/githooks. Upstream then gets a commit the clone has not fetched; no tree has a verdict."""
    upstream = root / "upstream"
    _git(root, "init", "-q", "-b", "main", str(upstream))
    _identify(upstream)
    for name in ("tools/verify.py", GATE):
        (upstream / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, upstream / name)
    (upstream / "tools/verify_checks.toml").write_text(CHECKLIST, encoding="utf-8")
    (upstream / ".gitignore").write_text("work/\n", encoding="utf-8")
    _commit(upstream, "base.txt")
    repo = root / "repo"
    _git(root, "clone", "-q", str(upstream), str(repo))
    _commit(upstream, "upstream.txt")
    _identify(repo)
    _git(repo, "config", "core.hooksPath", "tools/githooks")
    return repo


def _verdicts(repo: Path, tree: str, **checks: str) -> None:
    for check, verdict in checks.items():
        verify.record(repo, tree, {"check": check, "verdict": verdict, "summary": "1 passed", "at": "then"})


def _rev(repo: Path, rev: str) -> str:
    return _git(repo, "rev-parse", rev).stdout.strip()


GATED = pytest.mark.linux_only("the gate is a POSIX shell hook that runs tools/verify.py, which locks with fcntl")


@GATED
def test_main_moves_only_to_a_commit_whose_tree_has_a_passing_linux_and_windows_verdict(tmp_path):
    # Bug: main moves to a commit whose tree has no Windows verdict, or a red one (the gate reads only the Linux
    # verdict, or any record of a check rather than its latest); a reset slips past a gate that watches only
    # commits; or the refusal does not name the tree and the verdict, nor the command that records it.
    repo = _gated(tmp_path)
    (repo / "a.txt").write_text("a", encoding="utf-8")
    _git(repo, "add", "-A")
    tree = _git(repo, "write-tree").stdout.strip()
    wrong = []
    for name, checks, accepted, says in [
            ("no verdicts", {}, False, ["no Linux verdict; no Windows verdict", f"--commit {tree}\n"]),
            ("Linux only", {"linux": "pass"}, False, [f"{tree} has no Windows verdict.", f"--commit {tree}\n"]),
            ("a red Windows verdict", {"windows": "fail"}, False, [f"{tree} has a red Windows verdict",
                                                                  f"--commit {tree} --rerun"]),
            ("a Windows pass after it", {"windows": "pass"}, True, [])]:
        _verdicts(repo, tree, **checks)
        run = _git(repo, "commit", "-q", "-m", "a", check=False)
        if (run.returncode == 0) != accepted or not all(text in run.stderr for text in says):
            wrong.append(f"{name}: exit {run.returncode} {run.stderr.strip()}")
    verified = _rev(repo, "main")
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, "c.txt")
    _git(repo, "checkout", "-q", "main")
    reset = _git(repo, "reset", "-q", "--hard", "feature", check=False)
    if reset.returncode == 0 or _rev(repo, "main") != verified:
        wrong.append(f"a reset to an unverified commit: exit {reset.returncode}")
    assert wrong == []


@GATED
def test_main_needs_a_passing_verdict_of_each_checklist_row_the_diff_triggers(tmp_path):
    # Bugs: main moves to a tree whose diff triggers a checklist row with no verdict for that row (the gate reads only
    # the fast suite's), or a red one; a change that edits the checklist drops its own row and lands without it (every
    # row counts then, as tools/verify.py rules); or a row the diff does not trigger is asked for.
    repo = _gated(tmp_path)
    wrong = []
    for name, files, checks, accepted, says in [
            ("a runtime change, no row verdict", {"runtime/a.c": "1"}, {}, False,
             ["has no row runtime builds verdict.", "--commit "]),
            ("a red row verdict", {"runtime/a.c": "2"}, {"row runtime builds": "fail"}, False,
             ["a red row runtime builds verdict"]),
            ("a passing row verdict", {"runtime/a.c": "3"}, {"row runtime builds": "pass"}, True, []),
            ("an edit to the checklist that drops the row", {"tools/verify_checks.toml": "", "b.txt": "b"}, {}, False,
             ["has no row runtime builds verdict."]),
            ("a change no row is for", {"docs.txt": "d"}, {}, True, [])]:
        for path, text in files.items():
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(text, encoding="utf-8")
        _git(repo, "add", "-A")
        tree = _git(repo, "write-tree").stdout.strip()
        _verdicts(repo, tree, linux="pass", windows="pass", **checks)
        run = _git(repo, "commit", "-q", "-m", name, check=False)
        if (run.returncode == 0) != accepted or not all(text in run.stderr for text in says):
            wrong.append(f"{name}: exit {run.returncode} {run.stderr.strip()}")
        if run.returncode:
            _git(repo, "reset", "-q", "--hard", "HEAD")  # main stays; the next case starts from it
    assert wrong == []


@GATED
def test_upstream_commits_unchanged_trees_and_deleting_main_pass_the_gate(tmp_path):
    # Bug: a pull of an upstream commit is refused (no local verdict, as no one ran verify on it), also when one fetch
    # moves main and origin/main together, before origin/main holds it; a re-stamped commit (the post-commit hook
    # rebuilds HEAD with UTC dates: a new commit, main's tree) is refused; or deleting main fails in the gate.
    repo = _gated(tmp_path)
    wrong = []

    def moves(name: str, *command: str) -> None:
        if (run := _git(repo, *command, check=False)).returncode:
            wrong.append(f"{name}: {run.stderr.strip()}")

    moves("a pull", "pull", "-q", "--ff-only", "origin", "main")
    _commit(tmp_path / "upstream", "later.txt")
    _git(repo, "checkout", "-q", "-b", "other")
    moves("a fetch into main", "fetch", "-q", "origin", "main:main")
    restamp = _git(repo, "commit-tree", _rev(repo, "main^{tree}"), "-p", _rev(repo, "main^"), "-m", "re-stamped")
    moves("a re-stamp", "update-ref", "refs/heads/main", restamp.stdout.strip())
    moves("deleting main", "branch", "-q", "-D", "main")
    assert wrong == []


@GATED
def test_the_gate_lets_every_other_ref_move(tmp_path):
    # Bug: the gate checks more than main (a branch whose name starts main, any branch, the notes), so it blocks a
    # commit on a feature branch or a verdict landing in refs/notes/verify, or it fails on a symbolic ref (HEAD).
    repo = _gated(tmp_path)
    wrong = []
    for name in ("feature", "main-old"):
        _git(repo, "checkout", "-q", "-b", name)
        if (run := _commit(repo, f"{name}.txt")).returncode:
            wrong.append(f"a commit on {name}: {run.stderr.strip()}")
    tree = _rev(repo, "HEAD^{tree}")
    _verdicts(repo, tree, linux="pass")
    if verify.recorded(repo, tree, "linux") is None:
        wrong.append("the verdict never landed in refs/notes/verify")
    if (run := _git(repo, "checkout", "-q", "main", check=False)).returncode:
        wrong.append(f"a checkout of main: {run.stderr.strip()}")
    assert wrong == []
