"""The manual cleanup (tools/cleanup.py): what it deletes from a hand-built checkout, and what it never does."""

from __future__ import annotations

import os
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

import cleanup

NOW = time.time()


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _folder(path: Path, days: float, files: int = 1) -> Path:
    """A folder last changed `days` ago, with a few files."""
    path.mkdir(parents=True)
    for n in range(files):
        (path / f"f{n}").write_bytes(b"x" * 100)
    os.utime(path, (NOW - days * cleanup.DAY,) * 2)
    return path


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A main checkout with one commit on main, and no process using any of it."""
    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "t")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "t@example.invalid")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("WOZMOD_HARNESS_CONFIG", raising=False)
    monkeypatch.setattr(cleanup, "pace", lambda: None)
    monkeypatch.setattr(cleanup, "processes", lambda: [])
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    (root / ".gitignore").write_text("work/\n.worktrees/\n")
    (root / "tracked.txt").write_text("one\n")
    _git("add", ".", cwd=root)
    _git("commit", "-q", "-m", "first", cwd=root)
    (root / "work/runtime-tests").mkdir(parents=True)
    return root


def _clean(repo: Path, *args: str) -> int:
    return cleanup.main(["--repo", str(repo), "--delete", *args])


@pytest.mark.parametrize("ages, keep_runs, kept", [
    ({"a": 6.9, "b": 7.1, "c": 30}, "0", ["a"]),
    ({"old": 40, "older": 50, "newest": 20, "second": 30}, "2", ["newest", "second"]),
], ids=["day-cut", "keep-newest"])
def test_runs_go_past_keep_days_except_the_newest_keep_runs(repo, ages, keep_runs, kept):
    # Bugs: an off-by-one in the 7-day cut-off (N - 1 or N + 1 days) deletes a 6.9-day run or keeps a 7.1-day one; or
    # the newest-K cut sorts the wrong way (keeps the oldest K) or keeps one too few or too many.
    runs = repo / "work/runtime-tests"
    for name, days in ages.items():
        _folder(runs / name, days)
    assert _clean(repo, "--only", "runs", "--keep-days", "7", "--keep-runs", keep_runs) == 0
    assert sorted(p.name for p in runs.iterdir()) == kept


def test_a_run_a_process_uses_is_kept(repo, monkeypatch):
    # Bug: the live-process check is skipped or matches the wrong path, and a running game's run is deleted.
    runs = repo / "work/runtime-tests"
    live, dead = runs / "live", _folder(runs / "dead", 30)
    _folder(live / "game", 30)
    os.utime(live, (NOW - 30 * cleanup.DAY,) * 2)
    monkeypatch.setattr(cleanup, "processes", lambda: [(4242, [str(live / "game"), "/usr/bin/wine"])])
    assert _clean(repo, "--only", "runs", "--keep-runs", "0") == 0
    assert live.is_dir() and not dead.exists()


def test_protected_paths_are_never_deleted(repo):
    # Bug: a path named in harness.toml or compose.yml, or one that holds or sits inside one, is deleted because
    # only exact matches are checked; or the run template goes with the old runs.
    work = repo / "work"
    old = {name: _folder(work / name, 60) for name in (
        "runtime-tests/_template", "runtime-tests/named-inside", "runtime-tests/holds-input",
        "package/zips", "package/zips-r2", "package/zips-r3", "package/player", "package/player-r2",
        "build-cache/text/older")}
    _folder(work / "build-cache/text/newer", 1)
    _folder(work / "runtime-tests/holds-input/donors", 60)
    (work / "harness.toml").write_text(
        f'[tests]\nWOZMOD_DONORS = "{work}/runtime-tests/holds-input/donors"\n'
        f'WOZMOD_GAME = "{work}/runtime-tests/named-inside"\n'
        f'[more]\nlist = ["{work}/package/player", "{work}/build-cache/text"]\n')
    (work / "winvm").mkdir()
    (work / "winvm/compose.yml").write_text(
        "services:\n  win11:\n    volumes:\n      - ../package/zips-r2/w.zip:/shared/in/w.zip:ro\n")
    assert _clean(repo, "--keep-runs", "0") == 0
    gone = sorted(name for name, path in old.items() if not path.exists())
    assert gone == ["package/zips"]  # zips-r3 is the newest round; player-r2 the newest player round


def test_older_package_rounds_go_by_round_number(repo):
    # Bug: rounds are ordered by name, so r10 sorts before r9 and the newest round is deleted.
    package = repo / "work/package"
    for name in ("player", "player-r9", "player-r10", "player-win", "vm-r5"):
        _folder(package / name, 1)
    assert _clean(repo, "--only", "packages") == 0
    assert sorted(p.name for p in package.iterdir()) == ["player-r10", "player-win", "vm-r5"]


def test_caches_keep_each_steps_newest_entry(repo):
    # Bug: the newest is taken across all steps instead of per step, or the oldest is kept.
    work = repo / "work"
    for path, days in (("build-cache/text/aa", 3), ("build-cache/text/bb", 1), ("build-cache/voices/cc", 5),
                       ("build-cache/voices/.dd.999999999", 9), ("lettering-cache/k1", 2),
                       ("lettering-cache/k2", 4), ("install-e2e", 1)):
        _folder(work / path, days)
    assert _clean(repo, "--only", "build-cache,lettering-cache,install-e2e") == 0
    left = sorted(p.relative_to(work).as_posix() for p in work.glob("*/*/*") if p.is_dir()) + \
        sorted(p.relative_to(work).as_posix() for p in work.glob("lettering-cache/*"))
    assert left == ["build-cache/text/bb", "build-cache/voices/cc", "lettering-cache/k1"]
    assert not (work / "install-e2e").exists()


def test_delete_removes_folders_overlayfs_left_unreadable(repo):
    # Bug: the delete does not give mode-000 overlay work folders u+rwx first and fails on every new-style run.
    run = _folder(repo / "work/runtime-tests/run", 30)
    locked = run / "overlay-work/prefix/work"
    locked.mkdir(parents=True)
    (locked.parent / "x").write_text("x")
    locked.chmod(0)
    os.utime(run, (NOW - 30 * cleanup.DAY,) * 2)
    assert _clean(repo, "--only", "runs", "--keep-runs", "0") == 0
    assert not run.exists()


def test_a_dry_run_lists_candidates_and_deletes_nothing(repo, capsys):
    # Bug: the default deletes, or a candidate is deleted without being listed.
    run = _folder(repo / "work/runtime-tests/old", 30)
    assert cleanup.main(["--repo", str(repo), "--keep-runs", "0"]) == 0
    assert run.is_dir() and "work/runtime-tests/old" in capsys.readouterr().out


def _worktree(repo: Path, name: str) -> Path:
    path = repo / ".worktrees" / name
    _git("worktree", "add", "-q", "--detach", str(path), "main", cwd=repo)
    return path


def test_a_removed_worktree_can_be_rebuilt_from_its_archive(repo, tmp_path):
    # Bug: the worktree is removed before (or without) archiving its tracked edits, staged files, untracked files
    # or handbacks, so restoring the archive does not give back what was there.
    tree = _worktree(repo, "dirty")
    (tree / "tracked.txt").write_text("one\nedited\n")
    (tree / "staged.txt").write_text("staged\n")
    _git("add", "staged.txt", cwd=tree)
    (tree / "notes").mkdir()
    (tree / "notes/untracked.bin").write_bytes(bytes(range(256)))
    (tree / "work/handbacks").mkdir(parents=True)
    (tree / "work/handbacks/h.md").write_text("handback\n")
    (tree / "work/build").mkdir()  # ignored and rebuildable: not archived
    (tree / "work/build/big").write_bytes(b"0" * 1000)
    wanted = {p.relative_to(tree).as_posix(): p.read_bytes() for p in tree.rglob("*")
              if p.is_file() and ".git" not in p.parts and "work" not in p.parts}
    archives = tmp_path / "archives"
    assert _clean(repo, "--only", "worktrees", "--keep-days", "0", "--archive", str(archives)) == 0
    assert not tree.exists() and str(tree) not in _git("worktree", "list", cwd=repo)
    saved = next(archives.glob("worktree-archive-*/dirty"))
    rebuilt = _worktree(repo, "rebuilt")
    _git("apply", "--index", str(saved / "changes.patch"), cwd=rebuilt)
    with tarfile.open(saved / "untracked.tar.gz") as tar:
        tar.extractall(rebuilt, filter="data")
    got = {p.relative_to(rebuilt).as_posix(): p.read_bytes() for p in rebuilt.rglob("*")
           if p.is_file() and ".git" not in p.parts}
    assert got == wanted
    assert (saved / "handbacks/h.md").read_text() == "handback\n"


def test_a_worktree_with_a_commit_not_in_main_is_kept(repo, tmp_path):
    # Bug: the ancestry check runs the wrong way (main in HEAD), so a worktree whose only copy of a commit is its
    # detached HEAD is removed, and a worktree behind main is kept.
    ahead, behind = _worktree(repo, "ahead"), _worktree(repo, "behind")
    (ahead / "new.txt").write_text("new\n")
    _git("add", "new.txt", cwd=ahead)
    _git("commit", "-q", "-m", "unmerged", cwd=ahead)
    (repo / "tracked.txt").write_text("two\n")
    _git("commit", "-q", "-am", "main moves on", cwd=repo)
    assert _clean(repo, "--only", "worktrees", "--keep-days", "0", "--archive", str(tmp_path / "a")) == 0
    assert ahead.is_dir() and not behind.exists()
