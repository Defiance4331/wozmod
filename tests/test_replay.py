"""tools/replay.py: which commits are fixes, how a fix is undone on today's tree, and how a red tier is judged."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux_only("tools/replay.py drives tools/verify.py, which locks with fcntl")
if sys.platform == "linux":  # verify imports fcntl, so elsewhere it cannot load
    import replay
    import verify


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True, encoding="utf-8").stdout


def _commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _repository(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    (root / ".gitignore").write_text("work/\n", encoding="utf-8")
    return root


def _lines(*lines: str) -> str:
    return "".join(f"{line}\n" for line in lines)


def test_every_commit_ending_reported_in_and_every_revert_is_a_fix(tmp_path):
    # Bug: a fix that names two issues ("Reported in #3 and #4") or a revert commit is left out, so it is never
    # replayed and its gap never shows; or a commit that mentions an issue mid-message is taken for a fix.
    repo = _repository(tmp_path / "repo")
    _commit(repo, "Start", {"a.txt": "a\n"})
    two = _commit(repo, "Fix two\n\nChanges:\n- Both.\n\nReported in #3 and #4", {"a.txt": "b\n"})
    _commit(repo, "Mention\n\nReported in #5 is still open, see below.\n\nChanges:\n- None.", {"b.txt": "c\n"})
    _git(repo, "revert", "--no-edit", two)
    reverted = _git(repo, "rev-parse", "HEAD").strip()
    found = {fix.id: fix.reason for fix in replay.fixes(repo, _git(repo, "rev-parse", "HEAD^{tree}").strip())}
    assert found == {two[:7]: "#3 and #4", reverted[:7]: f"reverts {two[:7]}"}


def test_a_fix_is_put_back_exactly_where_its_lines_still_are(tmp_path):
    # Bug: a hunk whose context changed since the fix is put back at a second place its lines also occur, or with the
    # line it replaced left out; or a fix whose lines were rewritten since is undone anyway.
    repo = _repository(tmp_path / "repo")
    _commit(repo, "Start", {"a.txt": _lines(*"abcdefgh"), "b.txt": _lines(*"mnop", "q", "r")})
    fix = _commit(repo, "Fix\n\nReported in #1", {"a.txt": _lines(*"abcd", "FIX", "e", "f", "G2", "h"),
                                                  "b.txt": _lines(*"mnop", "dup", "q", "r")})
    rewritten = _commit(repo, "Fix b\n\nReported in #2", {"b.txt": _lines(*"mnop", "dup", "q", "r", "X")})
    # since: a's context before FIX and b's around dup change, b gains a second dup, and X is rewritten
    _commit(repo, "Later", {"a.txt": _lines("top", "a", "b", "c2", "d", "FIX", "e", "f", "G2", "h"),
                            "b.txt": _lines("dup", *"mno", "p2", "dup", "q2", "r", "Y")})
    replay.undo(repo, repo, replay.Fix("a", "#1", fix, ("a.txt",)))
    assert (repo / "a.txt").read_text(encoding="utf-8") == _lines("top", "a", "b", "c2", "d", "e", "f", "g", "h")
    for fixed, why in ((fix, "its lines occur 2 times"), (rewritten, "its lines are no longer in the file")):
        with pytest.raises(replay.NotApplied, match=why):
            replay.undo(repo, repo, replay.Fix("b", "#", fixed, ("b.txt",)))


def _fake_checks(monkeypatch, tier, rows: list[dict]) -> None:
    """Replays that run tier in place of the real checks, with rows as the checklist and no environment to build."""
    monkeypatch.setenv(verify.JUDGED, "tree")
    monkeypatch.setenv("PYTHONPATH", "")
    monkeypatch.setattr(verify, "LIVE", verify.LIVE)
    monkeypatch.setattr(verify, "venv", lambda repo, tree, folder: [sys.executable])
    monkeypatch.setattr(verify, "step_keys", lambda python, folder, env: dict.fromkeys(verify.STEPS, "key"))
    monkeypatch.setattr(verify, "checklist", lambda repo: {"row": rows})
    monkeypatch.setattr(replay.Run, "tier", tier)


def test_a_fix_that_no_longer_applies_is_reported_so_and_never_tested_half_undone(tmp_path, monkeypatch):
    # Bug: a revert that applies to one file but not the other is tested half done, and the broken tree it leaves
    # goes red, so the fix is reported as caught; or the scratch worktree is left behind.
    repo = _repository(tmp_path / "repo")
    _commit(repo, "Start", {"x.txt": _lines(*"abc"), "y.txt": _lines(*"klm")})
    half = _commit(repo, "Fix both\n\nReported in #7", {"x.txt": _lines("a", "FIX", "b", "c"),
                                                        "y.txt": _lines("k", "FIY", "l", "m")})
    whole = _commit(repo, "Fix x\n\nReported in #8", {"x.txt": _lines("a", "FIX", "b", "c", "WHOLE")})
    _commit(repo, "Later", {"y.txt": _lines("k", "FIY rewritten", "l", "m")})
    tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()
    tested = []

    def tier(self, name, on):  # red whenever the tree it runs on is not the checkout's
        tested.append((name, on))
        return on != tree, ["tests.test_x::test_y"] if on != tree else [], "fake"

    _fake_checks(monkeypatch, tier, [])
    found = {fix.id: replay.replay(repo, tree, fix) for fix in replay.fixes(repo, tree)}
    assert [(e["verdict"], e.get("tier"), e.get("tests")) for e in found.values()] == [
        ("does not apply", None, None), ("caught", replay.LINUX, ["tests.test_x::test_y"])]
    assert [name for name, on in tested] == [replay.LINUX, replay.LINUX]  # the whole fix, then the tree itself
    assert (found[half[:7]]["summary"], found[whole[:7]]["how"]) == (
        "y.txt#1: its lines are no longer in the file", ["x.txt#1 reverted"])
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_a_check_red_on_the_tree_too_does_not_end_the_replay(tmp_path, monkeypatch):
    # Bug: the first triggered row is red on today's tree as well (the DLL pin after a compiler update), so the replay
    # stops there as unjudged and never runs the rows after it, one of which catches the fix.
    repo = _repository(tmp_path / "repo")
    _commit(repo, "Start", {"x.txt": _lines(*"abc")})
    fix = _commit(repo, "Fix x\n\nReported in #9", {"x.txt": _lines("a", "FIX", "b", "c")})
    tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()
    rows = [{"name": "pin", "paths": ["*"], "run": ["pin"]}, {"name": "reader", "paths": ["*"], "run": ["reader"]}]

    def tier(self, name, on):  # the pin is red on every tree, the reader only with the fix undone
        for row in self.rows if name == replay.ROWS else []:
            if row["name"] == "pin" or on != tree:
                self.red_row = row
                return True, [f"tests.test_{row['name']}::test_it"], f"row {row['name']}"
        return False, [], "green"

    _fake_checks(monkeypatch, tier, rows)
    entry = replay.replay(repo, tree, replay.Fix(fix[:7], "#9", fix))
    assert (entry["verdict"], entry["tier"], entry["tests"], entry["unjudged"]) == (
        "caught", replay.ROWS, ["tests.test_reader::test_it"],
        ["triggered rows (row pin): tests.test_pin::test_it, red on the tree too"])


def test_a_test_already_red_on_the_tree_does_not_count_as_catching_the_fix():
    # Bug: a tier that is red on today's tree as well (a flaky or broken test) is credited with catching the fix.
    assert replay.judged(["a", "b"], True, ["a"]) == ("caught", ["b"])
    assert replay.judged(["a"], True, ["a"]) == ("cannot judge", ["a"])
    assert replay.judged([], True, ["c"]) == ("cannot judge", [])  # red with no test named, and red on the tree
    assert replay.judged([], False, []) == ("caught", [])  # red with no test named (a crash), green on the tree


def test_a_fix_no_check_is_chosen_for_is_replayed_up_to_release():
    # Bug: a fix whose paths trigger no row and change no step key stops after the fast suites and is reported as a
    # gap no slower tier was asked about; or the rows are dropped when the reference builds are chosen too.
    row = {"name": "row"}
    assert replay.tiers([], []) == [replay.LINUX, replay.WINDOWS, replay.RELEASE]
    assert replay.tiers(["text"], [row]) == [replay.LINUX, replay.WINDOWS, replay.REFERENCE, replay.ROWS]
