"""The manual cleanup (tools/cleanup.py): what it deletes from a hand-built checkout, and what it never does."""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux_only("tools/cleanup.py is a Linux developer tool that locks with fcntl")
if sys.platform == "linux":  # cleanup imports fcntl, so elsewhere it cannot load
    import cleanup
    import verify

NOW = time.time()


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8").stdout


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
    (root / ".gitignore").write_text("work/\n.worktrees/\n", encoding="utf-8")
    (root / "tracked.txt").write_text("one\n", encoding="utf-8")
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
        "lettering-cache/older")}
    _folder(work / "lettering-cache/newer", 1)
    _folder(work / "runtime-tests/holds-input/donors", 60)
    (work / "harness.toml").write_text(
        f'[tests]\nWOZMOD_DONORS = "{work}/runtime-tests/holds-input/donors"\n'
        f'WOZMOD_GAME = "{work}/runtime-tests/named-inside"\n'
        f'[more]\nlist = ["{work}/package/player", "{work}/lettering-cache"]\n', encoding="utf-8")
    (work / "winvm").mkdir()
    (work / "winvm/compose.yml").write_text(
        "services:\n  win11:\n    volumes:\n      - ../package/zips-r2/w.zip:/shared/in/w.zip:ro\n", encoding="utf-8")
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


def test_caches_keep_the_newest_renderer_folder(repo):
    # Bug: the oldest renderer folder is kept, or the install test's game copy is left.
    work = repo / "work"
    for path, days in (("lettering-cache/k1", 2), ("lettering-cache/k2", 4), ("install-e2e", 1)):
        _folder(work / path, days)
    assert _clean(repo, "--only", "lettering-cache,install-e2e") == 0
    assert sorted(p.name for p in (work / "lettering-cache").iterdir()) == ["k1"]
    assert not (work / "install-e2e").exists()


def test_delete_removes_folders_overlayfs_left_unreadable(repo):
    # Bug: the delete does not give mode-000 overlay work folders u+rwx first and fails on every new-style run.
    run = _folder(repo / "work/runtime-tests/run", 30)
    locked = run / "overlay-work/prefix/work"
    locked.mkdir(parents=True)
    (locked.parent / "x").write_text("x", encoding="utf-8")
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
    (tree / "tracked.txt").write_text("one\nedited\n", encoding="utf-8")
    (tree / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git("add", "staged.txt", cwd=tree)
    (tree / "notes").mkdir()
    (tree / "notes/untracked.bin").write_bytes(bytes(range(256)))
    (tree / "work/handbacks").mkdir(parents=True)
    (tree / "work/handbacks/h.md").write_text("handback\n", encoding="utf-8")
    (tree / "work/build").mkdir()  # ignored and rebuildable: not archived
    (tree / "work/build/big").write_bytes(b"0" * 1000)
    wanted = {p.relative_to(tree).as_posix(): p.read_bytes() for p in tree.rglob("*")
              if p.is_file() and ".git" not in p.relative_to(tree).parts and "work" not in p.relative_to(tree).parts}
    archives = tmp_path / "archives"
    assert _clean(repo, "--only", "worktrees", "--keep-days", "0", "--archive", str(archives)) == 0
    assert not tree.exists() and str(tree) not in _git("worktree", "list", cwd=repo)
    saved = next(archives.glob("worktree-archive-*/dirty"))
    rebuilt = _worktree(repo, "rebuilt")
    _git("apply", "--index", str(saved / "changes.patch"), cwd=rebuilt)
    with tarfile.open(saved / "untracked.tar.gz") as tar:
        tar.extractall(rebuilt, filter="data")
    got = {p.relative_to(rebuilt).as_posix(): p.read_bytes() for p in rebuilt.rglob("*")
           if p.is_file() and ".git" not in p.relative_to(rebuilt).parts}
    assert got == wanted
    assert (saved / "handbacks/h.md").read_text(encoding="utf-8") == "handback\n"


def test_a_worktree_with_a_commit_not_in_main_is_kept(repo, tmp_path):
    # Bug: the ancestry check runs the wrong way (main in HEAD), so a worktree whose only copy of a commit is its
    # detached HEAD is removed, and a worktree behind main is kept.
    ahead, behind = _worktree(repo, "ahead"), _worktree(repo, "behind")
    (ahead / "new.txt").write_text("new\n", encoding="utf-8")
    _git("add", "new.txt", cwd=ahead)
    _git("commit", "-q", "-m", "unmerged", cwd=ahead)
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    _git("commit", "-q", "-am", "main moves on", cwd=repo)
    assert _clean(repo, "--only", "worktrees", "--keep-days", "0", "--archive", str(tmp_path / "a")) == 0
    assert ahead.is_dir() and not behind.exists()


# A step_keys module whose one key is the SHA-256 of the tree's data.txt, as tools/verify.py's KEYS reads it.
STEP_KEYS = '''import hashlib, pathlib
def keys():
    data = (pathlib.Path(__file__).parents[3] / "data.txt").read_bytes()
    return {"text": {"key": hashlib.sha256(data).hexdigest()}}
'''


def test_only_reference_builds_no_live_tree_keys_are_listed(repo):
    # Bug: a live worktree's reference build is listed for deletion: its key is worked out from its HEAD instead of
    # its files as they are now, or the worktrees are left out and only main's keys are kept; or an old build, an
    # environment no tree uses, or what a stopped run left is never listed.
    import hashlib

    for name, text in {"src/wozmod/__init__.py": "", "src/wozmod/install/__init__.py": "",
                       "src/wozmod/install/step_keys.py": STEP_KEYS, "uv.lock": "lock\n", "data.txt": "main\n"}.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-q", "-m", "keys", cwd=repo)
    edited = _worktree(repo, "edited")
    (edited / "data.txt").write_text("edited\n", encoding="utf-8")  # not committed
    environment = verify.venv_folder(repo, _git("rev-parse", "main^{tree}", cwd=repo).strip())
    (environment / "bin").mkdir(parents=True)
    python = environment / "bin/python"  # without site: the project's own wozmod would shadow the tree's
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" -S "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    builds = repo / "work/verify/builds/text"
    key = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()  # noqa: E731
    for name in (key("main\n"), key("edited\n"), key("gone\n"), f"{key('gone')}.partial"):
        _folder(builds / name, 30)
    stale = _folder(repo / "work/verify/venvs/0123456789abcdef", 30)
    found = cleanup.plan(repo, {"verify"}, 7, 10, table=[], now=NOW)
    assert sorted(i.path.relative_to(repo / "work/verify").as_posix() for i in found if not i.kept) == sorted([
        f"builds/text/{key('gone\n')}", f"builds/text/{key('gone')}.partial", stale.relative_to(repo / "work/verify").as_posix()])


def test_what_verify_left_is_kept_while_a_run_holds_the_machine_shared(repo):
    # Bug: cleanup probes the machine lock shared, so it sees only heavy runs, and deletes an export or an environment
    # a test run or the background Windows run (which hold it shared) is using.
    import fcntl

    _folder(repo / "work/verify/trees/0123456789abcdef.4242", 30)
    (repo / "work/.machine.lock").write_bytes(b"")
    with open(repo / "work/.machine.lock", "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        held = cleanup.plan(repo, {"verify"}, 7, 10, table=[], now=NOW)
    free = cleanup.plan(repo, {"verify"}, 7, 10, table=[], now=NOW)
    assert [i.kept for i in held] == ["a run holds work/.machine.lock"] and [i.kept for i in free] == [""]
