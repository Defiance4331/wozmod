#!/usr/bin/env python3
"""List the rebuildable state of the main checkout's work/ and .worktrees/, and delete it with --delete.

    uv run python tools/cleanup.py [--only KIND,...] [--skip KIND,...] [--keep-days N] [--keep-runs K] [--delete]

Run it by hand only: never from a hook, a timer or another tool. Without --delete it deletes nothing and prints each
candidate with its apparent size (reflinks and compression make the space freed smaller), its file count, its age and
why it is one. --delete removes them one at a time, a few thousand entries at a time, and waits while the disk is
busy or btrfs is stalled in a commit, so a large delete does not freeze the desktop.

Kinds:
  runs             game test runs in work/runtime-tests/ older than --keep-days, except the newest --keep-runs
  worktrees        linked worktrees in .worktrees/ whose HEAD is in main and that are older than --keep-days; their
                   uncommitted changes, untracked files and work/handbacks/ are archived first (--archive)
  lettering-cache  work/lettering-cache/ renderer folders, except the newest
  build-cache      work/build-cache/ entries, except each step's newest, and partial entries of ended installs
  install-e2e      work/install-e2e* copies the install test left behind
  packages         work/package/ rounds (NAME, NAME-r2, NAME-r3...), except each name's newest round

It never touches a path work/harness.toml or work/winvm/compose.yml names, work/install, work/donors, the VM storage,
the run template, work/handbacks, work/archive, or anything outside the checkout, and it keeps anything a running
process uses (its working folder or a path in its command line).
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import checkout

KINDS = ("runs", "worktrees", "lettering-cache", "build-cache", "install-e2e", "packages")
DAY = 86400
CHUNK = 2000  # entries removed between two looks at the disk
DISK = "nvme0n1"  # the drive work/ is on; btrfs reports no device per file, so it is named here
BUSY = 80  # percent of the last 2 s the drive was busy, above which the delete waits
STALLS = {"handle_reserve_ticket", "wait_for_commit"}  # btrfs waits seen while the desktop froze
ROUND = re.compile(r"(?P<name>.+?)(?:-r(?P<round>\d+))?")


@dataclass
class Item:
    kind: str
    path: Path
    why: str
    age: float  # seconds since it last changed
    kept: str = ""  # why it is not deleted; empty for a candidate
    quiet: bool = False  # kept only for being new: counted, not listed


# Reading the machine


def processes() -> list[tuple[int, list[str]]]:
    """Each process's pid and the paths it uses: its working folder and the path-like words of its command line."""
    found = []
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            cwd = os.readlink(entry / "cwd")
            words = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        paths = [cwd]
        for word in words:
            text = os.fsdecode(word).partition("=")[2] if b"=" in word else os.fsdecode(word)
            if "/" in text:
                paths.append(os.path.normpath(os.path.join(cwd, text)))
        found.append((int(entry.name), paths))
    return found


def user(path: Path, table: list[tuple[int, list[str]]]) -> int | None:
    """The pid of a process that uses path or anything under it."""
    inside = str(path).rstrip("/") + "/"
    for pid, paths in table:
        if any(p == str(path) or p.startswith(inside) for p in paths) and pid != os.getpid():
            return pid
    return None


def held(lock: Path) -> bool:
    """Whether another process holds this flock file."""
    if not lock.is_file():
        return False
    with lock.open("rb") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def busy(disk: str = DISK, seconds: float = 2.0) -> float:
    """Percent of the next `seconds` the drive spends on I/O (/proc/diskstats io_ticks); 0 without that drive."""
    def ticks() -> int:
        for line in Path("/proc/diskstats").read_text().splitlines():
            fields = line.split()
            if fields[2] == disk:
                return int(fields[12])
        return 0
    before = ticks()
    time.sleep(seconds)
    return (ticks() - before) / (seconds * 10)


def stalled() -> bool:
    """Whether any task waits in a btrfs transaction stall. /proc/pressure/io is no signal on this desktop: idle
    terminal io_uring waits read as nearly 100% pressure."""
    for wchan in Path("/proc").glob("[0-9]*/wchan"):
        try:
            if wchan.read_text().strip() in STALLS:
                return True
        except OSError:
            continue
    return False


def pace() -> None:
    """Before each chunk: a pause, then wait until the drive is under BUSY and btrfs is not stalled."""
    time.sleep(1)
    while stalled() or busy() > BUSY:
        time.sleep(10)


def git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=check)


# What is protected


def protected(repo: Path) -> list[Path]:
    """Paths no candidate may equal, hold or sit inside."""
    work = repo / "work"
    paths = [work / name for name in ("install", "donors", "winvm/storage", "runtime-tests/_template", "handbacks",
                                      "archive", "harness.toml", "saves")]
    config = Path(os.environ.get("WOZMOD_HARNESS_CONFIG", work / "harness.toml"))
    if config.is_file():
        paths.append(config)
        stack = [tomllib.loads(config.read_text(encoding="utf-8"))]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                stack += value.values()
            elif isinstance(value, list):
                stack += value
            elif isinstance(value, str) and "/" in value:
                paths.append(repo / value)  # an absolute value replaces repo
    compose = work / "winvm/compose.yml"
    if compose.is_file():  # each volume's source: `- SOURCE:TARGET[:ro]`
        for source in re.findall(r"^\s*-\s*([./~][^:\s]*):", compose.read_text(encoding="utf-8"), re.MULTILINE):
            paths.append(compose.parent / Path(source).expanduser())
    return [Path(os.path.abspath(p)) for p in paths] + [p.resolve() for p in paths]


def refusal(path: Path, repo: Path, guarded: list[Path], table: list[tuple[int, list[str]]]) -> str:
    """Why path must not be deleted, or ''."""
    root = repo.resolve()
    for form in {Path(os.path.abspath(path)), path.resolve()}:
        if not form.is_relative_to(root) or form in (root, root / "work", root / ".worktrees") \
                or form.is_relative_to(root / ".git"):
            return "outside the checkout's work/ and .worktrees/"
        for guard in guarded:
            if form == guard or form.is_relative_to(guard) or guard.is_relative_to(form):
                return f"protected: {guard}"
    if pid := user(path, table):
        return f"in use by process {pid}"
    return ""


# Finding candidates


def changed(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def folders(parent: Path) -> list[Path]:
    """parent's subfolders, not symlinks."""
    if not parent.is_dir():
        return []
    return sorted(p for p in parent.iterdir() if p.is_dir() and not p.is_symlink())


def runs(repo: Path, keep_days: float, keep_runs: int, now: float) -> list[Item]:
    work = repo / "work"
    found = [p for p in folders(work / "runtime-tests") if p.name != "_template"]
    ages = {p: now - max(changed(p), changed(p / "evidence")) for p in found}
    newest = set(sorted(found, key=ages.get)[:keep_runs])
    items = []
    for run in found:
        item = Item("runs", run, f"a game test run older than {keep_days:g} days", ages[run])
        if run in newest:
            item.kept, item.quiet = f"among the newest {keep_runs}", True
        elif ages[run] < keep_days * DAY:
            item.kept, item.quiet = f"newer than {keep_days:g} days", True
        items.append(item)
    return items


def worktrees(repo: Path, keep_days: float, now: float) -> list[Item]:
    listing = git("worktree", "list", "--porcelain", cwd=repo).stdout.decode()
    items = []
    for block in listing.strip().split("\n\n"):
        fields = dict((line.split(" ", 1) + [""])[:2] for line in block.splitlines())
        path = Path(fields["worktree"])
        if not path.is_relative_to(repo / ".worktrees") or not path.is_dir():
            continue
        head = fields.get("HEAD", "")
        gitdir = Path(git("rev-parse", "--absolute-git-dir", cwd=path).stdout.decode().strip())
        status = git("--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=all", cwd=path).stdout.split(b"\0")
        dirty = [os.fsdecode(e[3:]) for e in status if e]
        # its last commit or checkout, or its newest edit; not the index, which any git status rewrites
        age = now - max([changed(gitdir / n) for n in ("HEAD", "logs/HEAD")]
                        + [changed(path / name) for name in dirty])
        untracked = sum(1 for e in status if e.startswith(b"??"))
        stat_line = git("diff", "--shortstat", "HEAD", cwd=path).stdout.decode().strip() or "no changes"
        why = (f"HEAD {head[:9]} is in main; {stat_line}; {untracked} untracked file(s); archived first"
               if dirty else f"HEAD {head[:9]} is in main; clean")
        item = Item("worktrees", path, why, age)
        if "locked" in fields:
            item.kept = "locked (git worktree unlock it first)"
        elif git("merge-base", "--is-ancestor", head, "main", cwd=repo, check=False).returncode != 0:
            item.kept = f"HEAD {head[:9]} is not in main"
        elif age < keep_days * DAY:
            item.kept, item.quiet = f"changed in the last {keep_days:g} days", True
        items.append(item)
    return items


def newest_kept(kind: str, found: list[Path], why: str, now: float) -> list[Item]:
    """found, newest first, all but the newest as candidates."""
    found = sorted(found, key=changed, reverse=True)
    items = [Item(kind, p, why, now - changed(p)) for p in found]
    for item in items[:1]:
        item.kept, item.quiet = "the newest", True
    return items


def caches(repo: Path, now: float) -> list[Item]:
    work = repo / "work"
    items = newest_kept("lettering-cache", folders(work / "lettering-cache"),
                        "layers an older renderer drew", now)
    for step in folders(work / "build-cache"):
        entries = [p for p in folders(step) if not p.name.startswith(".")]
        items += newest_kept("build-cache", entries, f"an older {step.name} build", now)
        for partial in (p for p in folders(step) if p.name.startswith(".")):  # .<key>.<pid>, written as it is kept
            pid = partial.name.rsplit(".", 1)[-1]
            if not (pid.isdigit() and Path(f"/proc/{pid}").exists()):
                items.append(Item("build-cache", partial, "a partial entry of an install that ended",
                                  now - changed(partial)))
    return items


def install_e2e(repo: Path, now: float) -> list[Item]:
    work = repo / "work"
    lock = held(work / "install-e2e.lock")
    items = []
    for copy in (p for p in folders(work) if p.name.startswith("install-e2e")):
        item = Item("install-e2e", copy, "a game copy the install test left", now - changed(copy))
        if lock:
            item.kept = "an install test holds work/install-e2e.lock"
        items.append(item)
    return items


def packages(repo: Path, now: float) -> list[Item]:
    rounds: dict[str, list[tuple[int, Path]]] = {}
    numbered = set()
    for folder in folders(repo / "work/package"):
        match = ROUND.fullmatch(folder.name)
        rounds.setdefault(match["name"], []).append((int(match["round"] or 1), folder))
        if match["round"]:
            numbered.add(match["name"])
    items = []
    for name in sorted(numbered):
        ordered = sorted(rounds[name], reverse=True)
        for number, folder in ordered[1:]:
            items.append(Item("packages", folder, f"round {number} of {name}; round {ordered[0][0]} is newer",
                              now - changed(folder)))
    return items


def plan(repo: Path, kinds: set[str], keep_days: float, keep_runs: int,
         table: list[tuple[int, list[str]]] | None = None, now: float | None = None) -> list[Item]:
    """Every item of the chosen kinds, each a candidate or kept with its reason."""
    now = time.time() if now is None else now
    table = processes() if table is None else table
    items = []
    if "runs" in kinds:
        items += runs(repo, keep_days, keep_runs, now)
    if "worktrees" in kinds:
        items += worktrees(repo, keep_days, now)
    if kinds & {"lettering-cache", "build-cache"}:
        items += [i for i in caches(repo, now) if i.kind in kinds]
    if "install-e2e" in kinds:
        items += install_e2e(repo, now)
    if "packages" in kinds:
        items += packages(repo, now)
    guarded = protected(repo)
    for item in items:
        if not item.kept:
            item.kept = refusal(item.path, repo, guarded, table)
    return items


# Measuring and deleting


def measure(path: Path) -> tuple[int, int, int, bool]:
    """Allocated bytes (each inode once), entries, bytes in files with other names, and whether every folder could
    be read."""
    seen, size, entries, linked, complete = set(), 0, 0, 0, True
    stack = [path]
    while stack:
        try:
            listing = list(os.scandir(stack.pop()))
        except OSError:
            complete = False
            continue
        for entry in listing:
            entries += 1
            found = entry.stat(follow_symlinks=False)
            if entry.is_dir(follow_symlinks=False):
                stack.append(entry.path)
            if (found.st_dev, found.st_ino) in seen:
                continue
            seen.add((found.st_dev, found.st_ino))
            size += found.st_blocks * 512
            if found.st_nlink > 1 and not stat.S_ISDIR(found.st_mode):
                linked += found.st_blocks * 512
    return size, entries, linked, complete


def remove_tree(path: Path, keep: frozenset[str] = frozenset()) -> None:
    """Delete path (or, with keep, its contents except those top-level names), CHUNK entries at a time with pace()
    before each. Folders without u+rwx (overlayfs leaves its work folders at mode 000) get it first."""
    order: list[tuple[str, bool]] = []

    def visit(folder: str) -> None:
        mode = os.lstat(folder).st_mode
        if mode & 0o700 != 0o700:
            os.chmod(folder, mode | 0o700)
        with os.scandir(folder) as listing:
            entries = list(listing)
        for entry in entries:
            if folder == str(path) and entry.name in keep:
                continue
            if entry.is_dir(follow_symlinks=False):
                visit(entry.path)
            else:
                order.append((entry.path, False))
        order.append((folder, True))

    if path.is_symlink() or not path.is_dir():
        order.append((str(path), False))
    else:
        visit(str(path))
        if keep:
            order.pop()  # path itself stays
    for start in range(0, len(order), CHUNK):
        pace()
        for name, folder in order[start:start + CHUNK]:
            os.rmdir(name) if folder else os.unlink(name)


def archive(worktree: Path, folder: Path) -> None:
    """Save what removing worktree would lose: HEAD, status, the diff from HEAD, untracked files and handbacks."""
    folder.mkdir(parents=True)
    head = git("rev-parse", "HEAD", cwd=worktree).stdout.decode().strip()
    branch = git("branch", "--show-current", cwd=worktree).stdout.decode().strip()
    (folder / "HEAD").write_text(f"{head}\n{branch}\n" if branch else f"{head}\n")
    (folder / "status.txt").write_bytes(git("status", "--short", cwd=worktree).stdout)
    (folder / "changes.patch").write_bytes(git("diff", "--binary", "HEAD", cwd=worktree).stdout)
    names = [os.fsdecode(n) for n in
             git("ls-files", "-z", "--others", "--exclude-standard", cwd=worktree).stdout.split(b"\0") if n]
    if names:
        with tarfile.open(folder / "untracked.tar.gz", "w:gz") as tar:
            for name in names:
                tar.add(worktree / name, arcname=name, recursive=False)
    if (worktree / "work/handbacks").is_dir():
        shutil.copytree(worktree / "work/handbacks", folder / "handbacks", symlinks=True)


def archive_root(repo: Path) -> Path:
    """[cleanup] archive in harness.toml, else work/archive."""
    config = Path(os.environ.get("WOZMOD_HARNESS_CONFIG", repo / "work/harness.toml"))
    table = tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    return Path(table.get("cleanup", {}).get("archive", repo / "work/archive"))


def delete(item: Item, repo: Path, archives: Path) -> str:
    """Delete one candidate after checking it again; returns what was done."""
    if reason := refusal(item.path, repo, protected(repo), processes()):
        return f"kept: {reason}"
    if item.kind == "worktrees":
        folder = archives / item.path.name
        number = 1
        while folder.exists():
            number += 1
            folder = archives / f"{item.path.name}-{number}"
        archive(item.path, folder)
        remove_tree(item.path, keep=frozenset({".git"}))
        git("worktree", "remove", "--force", str(item.path), cwd=repo)
        return f"archived to {folder}, removed"
    remove_tree(item.path)
    return "deleted"


# The command


def size_text(count: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if count < 1024 or unit == "GiB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024
    return ""


def show(repo: Path, items: list[Item]) -> int:
    """Print the plan; returns the total apparent bytes of the candidates."""
    total = 0
    for kind in KINDS:
        mine = [i for i in items if i.kind == kind]
        if not mine:
            continue
        candidates = [i for i in mine if not i.kept]
        print(f"{kind}: {len(candidates)} candidate(s)")
        for item in candidates:
            size, entries, linked, complete = measure(item.path)
            total += size
            note = "" if complete else ", some folders unreadable"
            note += f", {size_text(linked)} in hard-linked files" if linked else ""
            print(f"  {size_text(size):>10} {entries:>8} files {item.age / DAY:6.1f} d  "
                  f"{item.path.relative_to(repo)}  ({item.why}{note})")
        for item in mine:
            if item.kept and not item.quiet:
                print(f"  kept: {item.path.relative_to(repo)}  ({item.kept})")
        quiet = [i.kept for i in mine if i.quiet]
        for reason in sorted(set(quiet)):
            print(f"  kept: {quiet.count(reason)} {reason}")
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default=",".join(KINDS), metavar="KIND,...", help="only these kinds (default: all)")
    parser.add_argument("--skip", default="", metavar="KIND,...", help="leave these kinds out")
    parser.add_argument("--keep-days", type=float, default=7, metavar="N",
                        help="keep runs and worktrees changed in the last N days (default 7)")
    parser.add_argument("--keep-runs", type=int, default=10, metavar="K",
                        help="always keep the newest K runs (default 10)")
    parser.add_argument("--archive", type=Path, metavar="DIR",
                        help="where removed worktrees are archived (default: [cleanup] archive in harness.toml, "
                             "else work/archive), in a worktree-archive-DATE folder")
    parser.add_argument("--repo", type=Path, default=checkout.main_checkout(), help=argparse.SUPPRESS)
    parser.add_argument("--delete", action="store_true", help="delete the candidates (default: only list them)")
    args = parser.parse_args(argv)
    kinds = set(filter(None, args.only.split(","))) - set(filter(None, args.skip.split(",")))
    if unknown := kinds - set(KINDS):
        parser.error(f"unknown kind {', '.join(sorted(unknown))}; the kinds are {', '.join(KINDS)}")
    repo = args.repo.resolve()
    items = plan(repo, kinds, args.keep_days, args.keep_runs)
    total = show(repo, items)
    candidates = [i for i in items if not i.kept]
    print(f"{len(candidates)} candidate(s), {size_text(total)} apparent (the space freed is smaller: reflinks, "
          "compression, and snapshots that still hold them).")
    if not args.delete:
        if candidates:
            print("Nothing deleted. Add --delete to delete them, one at a time.")
        return 0
    lock_path = repo / "work/.cleanup.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("cleanup: another cleanup is deleting; two at once stall the disk", file=sys.stderr)
            return 1
        archives = (args.archive or archive_root(repo)) / f"worktree-archive-{time.strftime('%Y-%m-%d')}"
        failed = 0
        for item in candidates:
            start = time.monotonic()
            try:
                done = delete(item, repo, archives)
            except (OSError, subprocess.CalledProcessError) as error:
                done, failed = f"failed: {error}", failed + 1
            print(f"{item.path.relative_to(repo)}: {done} ({time.monotonic() - start:.0f} s)", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
