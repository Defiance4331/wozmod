#!/usr/bin/env python3
"""List the rebuildable state of the main checkout's work/, and delete it with --delete.

    uv run python tools/cleanup.py [--only KIND,...] [--skip KIND,...] [--keep-days N] [--keep-runs K] [--delete]

Run it by hand only: never from a hook, a timer or another tool. Without --delete it deletes nothing and prints each
candidate with its apparent size (reflinks and compression make the space freed smaller), its file count, its age and
why it is one. --delete removes them one at a time, a few thousand entries at a time, and waits while the disk is
busy or btrfs is stalled in a commit, so a large delete does not freeze the desktop.

Kinds:
  runs             game test runs in work/runtime-tests/ older than --keep-days, except the newest --keep-runs
  lettering-cache  work/lettering-cache/ renderer folders, except the newest
  install-e2e      work/install-e2e* copies the install test left behind
  packages         work/package/ rounds (NAME, NAME-r2, NAME-r3...), except each name's newest round
  verify           tools/verify.py's reference builds (work/verify/builds/STEP/KEY) whose step key is not the key of
                   main or of any checkout (the main one or a linked one, as it is now, or its merge base with main), the
                   Python environments (work/verify/venvs/) none of those uses, and what a stopped run left; all of it
                   is kept while any run (a heavy one, a test run, a game) holds work/.machine.lock

It never touches a path work/harness.toml or work/winvm/compose.yml names, work/install, work/donors, the VM storage,
the run template, work/handbacks, work/archive, or anything outside the checkout's work/, and it keeps anything a
running process uses (its working folder or a path in its command line).
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import checkout
import verify

KINDS = ("runs", "lettering-cache", "install-e2e", "packages", "verify")
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
    """Whether another process holds this flock file, exclusively or shared: a test run, a game or the background
    Windows run holds work/.machine.lock shared while it uses an export or an environment."""
    if not lock.is_file():
        return False
    with lock.open("rb") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def busy(disk: str = DISK, seconds: float = 2.0) -> float:
    """Percent of the next `seconds` the drive spends on I/O (/proc/diskstats io_ticks); 0 without that drive."""
    def ticks() -> int:
        for line in Path("/proc/diskstats").read_text(encoding="utf-8").splitlines():
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
            if wchan.read_text(encoding="utf-8").strip() in STALLS:
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
        if not form.is_relative_to(root / "work") or form == root / "work":
            return "outside the checkout's work/"
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


def newest_kept(kind: str, found: list[Path], why: str, now: float) -> list[Item]:
    """found, newest first, all but the newest as candidates."""
    found = sorted(found, key=changed, reverse=True)
    items = [Item(kind, p, why, now - changed(p)) for p in found]
    for item in items[:1]:
        item.kept, item.quiet = "the newest", True
    return items


def caches(repo: Path, now: float) -> list[Item]:
    return newest_kept("lettering-cache", folders(repo / "work/lettering-cache"), "layers an older renderer drew", now)


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


def checkouts(repo: Path) -> list[Path]:
    """The main checkout and every linked worktree whose folder exists."""
    listing = git("worktree", "list", "--porcelain", cwd=repo).stdout.decode()
    found = [Path(line.split(" ", 1)[1]) for line in listing.splitlines() if line.startswith("worktree ")]
    return [path for path in found if path.is_dir()]


def live_keys(repo: Path) -> tuple[set[tuple[str, str]], set[Path], list[str]]:
    """((step, key) of each reference build a live tree has, the environments those trees use, what could not be
    worked out). The live trees: main's, and each checkout's as it is now and its merge base with main."""
    trees = {verify.git(repo, "rev-parse", f"{verify.MAIN}^{{tree}}")}
    for path in checkouts(repo):
        trees.add(verify.tree_id(path))
        if base := verify.git(path, "merge-base", "HEAD", verify.MAIN, check=False):
            trees.add(verify.git(path, "rev-parse", f"{base}^{{tree}}"))
    keys, environments, unknown = set(), set(), []
    for tree in sorted(trees):
        environment = verify.venv_folder(repo, tree)
        environments.add(environment)
        if not (environment / "bin/python").exists():
            continue  # never built from: no build has this tree's packages
        with tempfile.TemporaryDirectory(prefix="cleanup-keys-") as scratch:
            folder = verify.export(repo, tree, Path(scratch) / "tree")
            try:
                found = verify.step_keys([str(environment / "bin/python")], folder, verify.side_environment(repo, folder))
            except SystemExit as error:
                unknown.append(f"the step keys of tree {tree[:12]}: {str(error).strip().splitlines()[-1]}")
                continue
        keys |= set(found.items())
    return keys, environments, unknown


def verify_state(repo: Path, keep_days: float, now: float) -> list[Item]:
    """work/verify/: the builds no live tree's step key names, the environments no live tree uses, and leftovers."""
    root = verify.work(repo) / "verify"
    found = [p for step in folders(root / "builds") for p in folders(step)] + folders(root / "venvs") \
        + folders(root / "trees")
    if not found:
        return []
    keys, environments, unknown = live_keys(repo)
    for line in unknown:
        print(f"cleanup: could not work out {line}; its builds are listed as no tree's", file=sys.stderr)
    lock = held(verify.work(repo) / ".machine.lock")
    items = []
    for path in found:
        age = now - changed(path)
        if path.parent.name == "trees":
            item = Item("verify", path, "a tree a stopped verify run exported", age)
        elif path.parent.parent.name == "builds" and "." not in path.name:
            live = (path.parent.name, path.name) in keys
            item = Item("verify", path, f"a {path.parent.name} build no live tree's step key names", age,
                        "the step key of a live tree" if live else "", live)
        elif path.parent.name == "venvs" and "." not in path.name:
            live = path in environments
            item = Item("verify", path, "an environment no live tree uses", age, "a live tree's" if live else "", live)
        elif path.suffix == ".failed":
            item = Item("verify", path, f"a failed build older than {keep_days:g} days", age)
            if age < keep_days * DAY:
                item.kept, item.quiet = f"failed in the last {keep_days:g} days", True
        else:
            item = Item("verify", path, "what a stopped verify run or cleanup left", age)
        if lock and not item.kept:
            item.kept = "a run holds work/.machine.lock"
        items.append(item)
    return items


def plan(repo: Path, kinds: set[str], keep_days: float, keep_runs: int,
         table: list[tuple[int, list[str]]] | None = None, now: float | None = None) -> list[Item]:
    """Every item of the chosen kinds, each a candidate or kept with its reason."""
    now = time.time() if now is None else now
    table = processes() if table is None else table
    items = []
    if "runs" in kinds:
        items += runs(repo, keep_days, keep_runs, now)
    if "lettering-cache" in kinds:
        items += caches(repo, now)
    if "install-e2e" in kinds:
        items += install_e2e(repo, now)
    if "packages" in kinds:
        items += packages(repo, now)
    if "verify" in kinds:
        items += verify_state(repo, keep_days, now)
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


def remove_tree(path: Path) -> None:
    """Delete path, CHUNK entries at a time with pace() before each. Folders without u+rwx (overlayfs leaves its work
    folders at mode 000) get it first."""
    order: list[tuple[str, bool]] = []

    def visit(folder: str) -> None:
        mode = os.lstat(folder).st_mode
        if mode & 0o700 != 0o700:
            os.chmod(folder, mode | 0o700)
        with os.scandir(folder) as listing:
            entries = list(listing)
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                visit(entry.path)
            else:
                order.append((entry.path, False))
        order.append((folder, True))

    if path.is_symlink() or not path.is_dir():
        order.append((str(path), False))
    else:
        visit(str(path))
    for start in range(0, len(order), CHUNK):
        pace()
        for name, folder in order[start:start + CHUNK]:
            os.rmdir(name) if folder else os.unlink(name)


def delete(item: Item, repo: Path) -> str:
    """Delete one candidate after checking it again; returns what was done."""
    if reason := refusal(item.path, repo, protected(repo), processes()):
        return f"kept: {reason}"
    if item.kind == "verify":  # out of verify's sight first: a build or environment it finds is whole
        item.path = item.path.rename(item.path.with_name(item.path.name + ".deleting"))
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
                        help="keep runs and failed verify builds changed in the last N days (default 7)")
    parser.add_argument("--keep-runs", type=int, default=10, metavar="K",
                        help="always keep the newest K runs (default 10)")
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
    with lock_path.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("cleanup: another cleanup is deleting; two at once stall the disk", file=sys.stderr)
            return 1
        failed = 0
        for item in candidates:
            start = time.monotonic()
            try:
                done = delete(item, repo)
            except (OSError, subprocess.CalledProcessError) as error:
                done, failed = f"failed: {error}", failed + 1
            print(f"{item.path.relative_to(repo)}: {done} ({time.monotonic() - start:.0f} s)", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
