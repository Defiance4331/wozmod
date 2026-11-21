#!/usr/bin/env python3
"""Replay past fixes against today's tree: undo each on a scratch tree and report the first check that goes red.

    tools/replay.py [--list] [--rerun] [FIX...]

The fixes are each commit on main whose message ends "Reported in #N", each revert commit, and the named bugs of
tools/replay.toml (which also narrows what is reverted of a commit). A fix's code is reverted and its tests are kept:
the question is whether today's tests catch the old bug. A hunk that no longer applies is ported when the lines the
fix wrote are still in the file, once (with as much of the hunk's context as still matches); otherwise the fix is
reported as not applying, and its scratch tree is left out of every run.

Per fix, in a scratch worktree in the main checkout's work/replay/ holding the checkout's tree (git write-tree,
uncommitted and untracked files included) with the fix undone, the tiers run cheapest first, each holding the machine
lock (tools/machine_lock.py) for what it is, as tools/verify.py's runs do, and the replay stops at the first red one:

    Linux             the fast suite
    Windows           the fast suite under Windows Python (tools/windows_suite.py)
    reference builds  `tools/verify.py ref` of each step whose key the revert changes, and its readers
    triggered rows    the tools/verify_checks.toml rows the reverted paths trigger
    release           `tools/verify.py release`, only when no step key and no row is chosen for the fix

The reference builds and the rows are the checks chosen for the fix's area: once they ran green, the fix is a gap.
A red tier counts only for the tests that are not red on the tree itself (the same tier, run on it); a red tier with
nothing left is reported as one the replay cannot judge.

Each tier's verdict is a tools/verify.py record on the scratch tree (git notes, runs in work/verify/runs/TREE/), and
each fix's result a record on the checkout's tree, so a rerun on the same tree prints what it found (--rerun replays
again). The scratch trees are removed after each fix.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import verify

TABLE = "tools/replay.toml"
REPORTED = re.compile(r"Reported in (#\d+(?:(?:,| and) #\d+)*)")
REVERTS = re.compile(r"This reverts commit ([0-9a-f]{7,40})")
LINUX, WINDOWS, REFERENCE, ROWS, RELEASE = "Linux", "Windows", "reference builds", "triggered rows", "release"


@dataclasses.dataclass(frozen=True)
class Fix:
    id: str  # the commit's short id, or the named bug's name
    reason: str  # "#20", "reverts 1234abc", or what the named bug was
    commit: str | None = None
    revert: tuple[str, ...] = ()  # path or path#N,M specs; empty: every path outside tests/
    edits: tuple[tuple[str, str, str], ...] = ()  # (path, old, new): a hand port, or a named bug made again
    why: str = ""


class NotApplied(Exception):
    """The fix cannot be undone on today's tree: why."""


def read(path: Path) -> str:
    """A file's text as it is (UTF-8, its line ends kept, any other byte carried through)."""
    return path.read_bytes().decode("utf-8", "surrogateescape")


def write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8", "surrogateescape"))


# The fixes


def fixes(repo: Path, tree: str) -> list[Fix]:
    """The fixes on main, oldest first, then the named bugs, with tree's tools/replay.toml applied."""
    rows = tomllib.loads(verify.git(repo, "show", f"{tree}:{TABLE}", check=False) or "").get("fix", [])
    found = []
    for record in verify.git(repo, "log", "--reverse", verify.MAIN, "--format=%H%x1f%s%x1f%B%x1e").split("\x1e"):
        if not record.strip():
            continue
        commit, subject, body = record.strip("\n").split("\x1f")
        last = body.strip().splitlines()[-1].strip() if body.strip() else ""
        reverted = REVERTS.search(body)
        if reported := REPORTED.fullmatch(last):
            reason = reported.group(1)
        elif reverted or subject.startswith('Revert "'):
            reason = f"reverts {reverted.group(1)[:7]}" if reverted else subject
        else:
            continue
        row = next((row for row in rows if commit.startswith(row.get("commit", "-"))), {})
        found.append(Fix(commit[:7], reason, commit, tuple(row.get("revert", ())), _edits(row), _why(row)))
    found += [Fix(row["name"], row["reason"], None, (), _edits(row), _why(row)) for row in rows if "name" in row]
    return found


def _edits(row: dict) -> tuple[tuple[str, str, str], ...]:
    return tuple((each["path"], each["old"], each["new"]) for each in row.get("edit", []))


def _why(row: dict) -> str:
    return " ".join(row.get("why", "").split())


@dataclasses.dataclass
class FileDiff:
    path: str
    head: list[str]  # the lines before its first hunk
    hunks: list[list[str]]  # each from its @@ line on
    binary: bool = False

    @property
    def created(self) -> bool:
        return any(line.startswith("new file mode") for line in self.head)


def parse(text: str) -> list[FileDiff]:
    """A git diff, file by file and hunk by hunk."""
    files: list[FileDiff] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            files.append(FileDiff(line.rstrip("\n").split(" b/", 1)[1], [line], []))
        elif line.startswith("@@"):
            files[-1].hunks.append([line])
        elif files[-1].hunks:
            files[-1].hunks[-1].append(line)
        else:
            files[-1].head.append(line)
            files[-1].binary |= line.startswith(("GIT binary patch", "Binary files"))
    return files


def chosen(files: list[FileDiff], specs: tuple[str, ...]) -> list[FileDiff]:
    """The parts of a fix's diff that specs name (path, or path#N,M for those hunks); all but tests/ when none."""
    if not specs:
        return [each for each in files if not each.path.startswith("tests/")]
    wanted: dict[str, set[int] | None] = {}
    for spec in specs:
        path, _, numbers = spec.partition("#")
        wanted[path] = {int(n) for n in numbers.split(",")} if numbers else None
    missing = set(wanted) - {each.path for each in files}
    if missing:
        raise NotApplied(f"tools/replay.toml names paths the commit does not change: {', '.join(sorted(missing))}")
    picked = []
    for each in files:
        if each.path in wanted:
            numbers = wanted[each.path]
            hunks = [h for n, h in enumerate(each.hunks, 1) if numbers is None or n in numbers]
            picked.append(dataclasses.replace(each, hunks=hunks))
    return picked


def commit_diff(repo: Path, commit: str) -> list[FileDiff]:
    run = subprocess.run(["git", "diff", "--no-color", "--no-ext-diff", "--full-index", "--binary", f"{commit}^",
                          commit], cwd=repo, capture_output=True, encoding="utf-8", errors="surrogateescape",
                         check=True)
    return parse(run.stdout)


def kept_tests(repo: Path, fix: Fix) -> list[str]:
    """The tests the fix's commit changed, which the replay keeps as the tree has them."""
    return [each.path for each in commit_diff(repo, fix.commit) if each.path.startswith("tests/")] if fix.commit else []


# Undoing a fix


def _apply(folder: Path, patch: str, check: bool) -> bool:
    return subprocess.run(["git", "apply", "-R", *(["--check"] if check else [])], cwd=folder, input=patch,
                          encoding="utf-8", errors="surrogateescape", capture_output=True).returncode == 0


def _place(lines: list[str], before: list[str], new: list[str], after: list[str], old: list[str]) -> list[str]:
    """lines with the one run of new (the lines a fix wrote) put back to old, found with as much of the context the
    hunk shows around it as still matches; NotApplied when no such run is found exactly once."""
    sides = sorted(((b, a) for b in range(len(before) + 1) for a in range(len(after) + 1)), key=lambda p: -sum(p))
    for b, a in sides:
        if not new and not b + a:
            break  # a deletion to undo needs a place: some context
        needle = before[len(before) - b:] + new + after[:a]
        hits = [i for i in range(len(lines) - len(needle) + 1) if lines[i:i + len(needle)] == needle]
        if len(hits) == 1:
            at = hits[0] + b
            return lines[:at] + old + lines[at + len(new):]
    if not new:
        raise NotApplied("its context is no longer in the file")
    count = sum(lines[i:i + len(new)] == new for i in range(len(lines) - len(new) + 1))
    raise NotApplied(f"its lines occur {count} times, with too little of its context left to tell which" if count else
                     "its lines are no longer in the file")


def port(text: str, hunk: list[str]) -> str:
    """text with one hunk of a fix undone, block by block (a run of removed and added lines between context)."""
    segments: list[list] = []  # [" ", context lines] or ["-", old, new]
    for line in hunk[1:]:
        tag, rest = line[:1], line[1:].rstrip("\n")
        if tag == "\\":
            continue
        if tag == " ":
            if segments and segments[-1][0] == " ":
                segments[-1][1].append(rest)
            else:
                segments.append([" ", [rest]])
        else:
            if not segments or segments[-1][0] != "-":
                segments.append(["-", [], []])
            segments[-1][1 if tag == "-" else 2].append(rest)
    lines = text.split("\n")
    for k, segment in enumerate(segments):
        if segment[0] == "-":
            before = segments[k - 1][1] if k and segments[k - 1][0] == " " else []
            after = segments[k + 1][1] if k + 1 < len(segments) and segments[k + 1][0] == " " else []
            lines = _place(lines, before, segment[2], after, segment[1])
    return "\n".join(lines)


def undo(folder: Path, repo: Path, fix: Fix) -> list[str]:
    """Undo fix in the checkout folder: how each part went ("path#N reverted", "path#N ported", "path edited").
    NotApplied names the first part that cannot be undone; the caller restores the tree then."""
    done = []
    for path, old, new in fix.edits:
        file = folder / path
        text = read(file) if file.is_file() else ""
        if text.count(old) != 1:
            raise NotApplied(f"{path}: the text to change occurs {text.count(old)} times")
        write(file, text.replace(old, new))
        done.append(f"{path} edited")
    if not fix.commit:
        return done
    for each in chosen(commit_diff(repo, fix.commit), fix.revert):
        parts = [each.hunks[i:i + 1] for i in range(len(each.hunks))] if not each.binary and each.hunks else [[]]
        for number, hunks in enumerate(parts, 1):
            patch = "".join(each.head + [line for hunk in hunks for line in hunk])
            name = f"{each.path}#{number}" if hunks else each.path
            if _apply(folder, patch, check=True):
                _apply(folder, patch, check=False)
                done.append(f"{name} reverted")
                continue
            file = folder / each.path
            if each.binary or not hunks or each.created or not file.is_file():
                raise NotApplied(f"{name}: the file changed since the fix")
            try:
                ported = port(read(file), hunks[0])
            except NotApplied as error:
                raise NotApplied(f"{name}: {error}") from None
            write(file, ported)
            done.append(f"{name} ported")
    return done


# The scratch tree


def restore(folder: Path, tree: str) -> None:
    """Put the checkout folder back to tree, whatever was changed or added in it."""
    verify.git(folder, "read-tree", "--reset", "-u", tree)
    verify.git(folder, "checkout-index", "-a", "-f")
    verify.git(folder, "clean", "-fdq")
    if verify.tree_id(folder) != tree:
        raise SystemExit(f"replay: {folder} did not go back to tree {tree[:12]}")


def drop(repo: Path, folder: Path) -> None:
    if (folder / ".git").exists():
        verify.git(repo, "worktree", "remove", "--force", str(folder), check=False)
    shutil.rmtree(folder, ignore_errors=True)
    verify.git(repo, "worktree", "prune")


def scratch(repo: Path, tree: str, base: str, folder: Path) -> None:
    """A worktree at folder holding tree, at the merge base (so verify diffs and triggers as for the checkout)."""
    drop(repo, folder)  # one a crashed run left
    folder.parent.mkdir(parents=True, exist_ok=True)
    verify.git(repo, "worktree", "add", "--detach", "--no-checkout", str(folder), base)
    restore(folder, tree)


# The tiers


def tiers(steps: list[str], rows: list[dict]) -> list[str]:
    """The tiers a replay runs, cheapest first: the fast suite on both systems, then the checks chosen for the fix's
    area (the reference builds whose key it changes, the rows its paths trigger), or release when none is."""
    chosen_ = ([REFERENCE] if steps else []) + ([ROWS] if rows else [])
    return [LINUX, WINDOWS, *(chosen_ or [RELEASE])]


def judged(red: list[str], red_on_tree: bool, on_tree: list[str]) -> tuple[str, list[str]]:
    """A red tier's verdict: caught by the tests red only with the fix undone; one red on the tree as well (or a red
    with no test named, when the tree is red too) cannot judge it."""
    only = [name for name in red if name not in set(on_tree)]
    if only or (not red and not red_on_tree):
        return "caught", only
    return "cannot judge", red


def broken(junit: Path) -> list[str]:
    return verify.outcome(junit, 0)["broken"] if junit.is_file() else []


def without_pythonpath(run):
    """run (the Windows run) without this process's PYTHONPATH, which names Linux folders: Wine passes it on."""
    def wrapped(*arguments, **options):
        saved = os.environ.pop("PYTHONPATH", None)
        try:
            return run(*arguments, **options)
        finally:
            if saved is not None:
                os.environ["PYTHONPATH"] = saved
    return wrapped


@dataclasses.dataclass
class Run:
    repo: Path  # the scratch worktree
    base: str
    steps: list[str]
    rows: list[dict]
    red_row: dict | None = None  # the row the last red rows tier stopped at

    def tier(self, name: str, tree: str) -> tuple[bool, list[str], str]:
        """Run tier name on tree, which the scratch worktree holds: (red, the tests that went red, a summary)."""
        runs = verify.runs(self.repo, tree)
        if name == LINUX:
            found = verify.linux(self.repo, tree, rerun=False)
            return found["verdict"] != "pass", broken(runs / "linux/junit.xml"), found["summary"]
        if name == WINDOWS:
            found = verify.recorded(self.repo, tree, "windows") or verify.windows_run(self.repo, tree, [])
            return found["verdict"] != "pass", broken(runs / "windows/junit.xml"), found["summary"]
        if name == REFERENCE:
            if (found := verify.recorded(self.repo, tree, f"readers {' '.join(self.steps)}")) and \
                    found["verdict"] == "pass":
                return False, [], found["summary"]
            try:
                results = verify.references(self.repo, tree, self.base, self.steps)
            except SystemExit as error:  # a step that no longer builds (or keys) is red; any other stop is not ours
                if not re.match(r"verify: the (\w+ build of tree|step keys of) ", str(error)):
                    raise
                return True, [str(error).splitlines()[0]], str(error).splitlines()[0]
            if not results:
                return False, [], "built; no readers"
            return (results[0]["verdict"] != "pass", results[0]["broken"] + results[0]["problems"],
                    results[0]["summary"])
        if name == ROWS:
            order = lambda row: (row.get("alone", False), any(i.startswith("ref ") for i in row["run"]))
            for row in sorted(self.rows, key=order):
                if verify.run_rows(self.repo, tree, self.base, [row], rerun=False):
                    continue
                self.red_row = row
                refs = any(item.startswith("ref ") for item in row["run"])
                junit = runs / ("readers" if refs else "rows/" + re.sub(r"\W+", "-", row["name"])) / "junit.xml"
                return True, broken(junit), f"row {row['name']}"
            return False, [], f"{len(self.rows)} rows passed"
        ok = verify.release(self.repo, tree, self.base, [])
        red = [n for part in ("release/linux", "readers", "release/e2e", "windows") for n in broken(runs / part / "junit.xml")]
        return not ok, red, "release passed" if ok else "release failed"


def replay(repo: Path, tree: str, fix: Fix) -> dict:
    """Replay fix on tree, in a scratch worktree: its record."""
    base = verify.merge_base(repo)
    folder = verify.work(repo) / "replay" / f"{tree[:12]}-{fix.id}"
    entry = {"check": f"replay {fix.id}", "reason": fix.reason, "kept": kept_tests(repo, fix), "why": fix.why}
    scratch(repo, tree, base, folder)
    try:
        python = verify.venv(repo, tree, folder)
        keys = verify.step_keys(python, folder, verify.side_environment(repo, folder))
        try:
            entry["how"] = undo(folder, repo, fix)
        except NotApplied as error:
            return entry | {"verdict": "does not apply", "summary": str(error)}
        undone = verify.tree_id(folder)
        if undone == tree:
            return entry | {"verdict": "does not apply", "summary": "undoing it changes nothing: the tree lacks it"}
        paths = verify.git(folder, "diff", "--name-only", tree, undone).splitlines()
        diff = verify.runs(repo, undone) / "replay.diff"
        diff.parent.mkdir(parents=True, exist_ok=True)
        diff.write_text(verify.git(folder, "diff", tree, undone) + "\n", encoding="utf-8")
        python_undone = verify.venv(repo, undone, folder)
        try:
            changed = verify.step_keys(python_undone, folder, verify.side_environment(repo, folder))
        except SystemExit:  # the keys no longer compute: every step is to be built, and the reference tier says why
            changed = {}
        steps = [step for step in verify.STEPS if changed.get(step) != keys[step]]
        rows = verify.triggered(verify.checklist(folder).get("row", []), paths)
        run = Run(folder, base, steps, rows)
        entry |= {"undone": undone, "paths": paths, "steps": steps, "rows": [row["name"] for row in rows],
                  "diff": str(diff)}
        os.environ["PYTHONPATH"] = os.pathsep.join([str(folder / "src"), str(folder / "tools")])
        unjudged: list[str] = []  # the red checks that are red on the tree too: noted, and the replay goes on
        queue = tiers(steps, rows)
        while queue:
            tier = queue.pop(0)
            started = time.monotonic()
            verify.LIVE = python_undone
            red, tests, summary = run.tier(tier, undone)
            print(f"replay {fix.id}: {tier}: {'red' if red else 'green'} in {time.monotonic() - started:.0f} s: "
                  f"{summary}", flush=True)
            if not red:
                continue
            restore(folder, tree)
            verify.LIVE = python
            row = run.red_row if tier == ROWS else None
            red_on_tree, on_tree, summary_on_tree = (dataclasses.replace(run, rows=[row]) if row else run).tier(tier,
                                                                                                              tree)
            verdict, named = judged(tests, red_on_tree, on_tree)
            if verdict == "caught":
                return entry | {"verdict": verdict, "tier": tier, "tests": named, "summary": summary,
                                "unjudged": unjudged}
            unjudged.append(f"{tier}{' (row ' + row['name'] + ')' if row else ''}: {', '.join(named) or summary}, "
                            f"red on the tree too")
            if row:
                run.rows = [each for each in run.rows if each is not row]
                queue[:0] = [ROWS] if run.rows else []  # the rows after it still judge
            undo(folder, repo, fix)
            if verify.tree_id(folder) != undone:
                raise SystemExit(f"replay: undoing {fix.id} again gave another tree")
        return entry | {"verdict": "gap", "tier": tier, "tests": [], "summary": summary, "unjudged": unjudged}
    finally:
        drop(repo, folder)


# The command


def table(fixes_: list[Fix], found: dict[str, dict]) -> str:
    lines = ["| fix | issue or reason | reverted | first red tier | red tests |", "|---|---|---|---|---|"]
    for fix in fixes_:
        entry = found.get(fix.id)
        if not entry:
            lines.append(f"| {fix.id} | {fix.reason} | | not replayed | |")
            continue
        how = ", ".join(entry.get("how", [])) or "nothing"
        kept = f"; tests kept: {', '.join(entry['kept'])}" if entry.get("kept") else ""
        if entry["verdict"] == "does not apply":
            tier, tests = "does not apply", entry["summary"]
        elif entry["verdict"] == "gap":
            tier, tests = f"none (gap; ran up to {entry['tier']})", "none"
            if entry.get("unjudged"):
                tests += "; cannot judge: " + "; ".join(entry["unjudged"])
        else:
            tier = entry["tier"] + (" (cannot judge)" if entry["verdict"] == "cannot judge" else "")
            tests = ", ".join(entry["tests"]) or entry["summary"]
        lines.append(f"| {fix.id} | {fix.reason} | {how}{kept} | {tier} | {tests} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    repo = Path(verify.git(Path.cwd(), "rev-parse", "--show-toplevel"))
    os.environ.setdefault(verify.JUDGED, "tree")  # the replay's tree judges itself: it is what is measured
    verify.windows_run = without_pythonpath(verify.windows_run)
    if argv[:1] == ["_one"]:  # one fix
        tree, name = argv[1], argv[2]
        fix = next(fix for fix in fixes(repo, tree) if fix.id == name)
        print(f"replay {fix.id} ({fix.reason}) on tree {tree[:12]}", flush=True)
        entry = replay(repo, tree, fix)
        verify.record(repo, tree, entry | {"judge": os.environ[verify.JUDGED],
                                           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return 0
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("fixes", nargs="*", help="the fixes to replay (their ids); default: all")
    parser.add_argument("--list", action="store_true", help="list the fixes and what each reverts; run nothing")
    parser.add_argument("--rerun", action="store_true", help="replay again what is recorded")
    options = parser.parse_args(argv)
    tree = verify.tree_id(repo)
    every = fixes(repo, tree)
    wanted = [fix for fix in every if not options.fixes or fix.id in options.fixes]
    if unknown := set(options.fixes) - {fix.id for fix in every}:
        parser.error(f"no such fix: {', '.join(sorted(unknown))}")
    print(f"tree {tree}: {len(wanted)} fixes")
    if options.list:
        for fix in wanted:
            parts = [f"{path} edited" for path, _, _ in fix.edits]
            parts += [each.path for each in chosen(commit_diff(repo, fix.commit), fix.revert)] if fix.commit else []
            print(f"  {fix.id} ({fix.reason}): {', '.join(parts)}{'; ' + fix.why if fix.why else ''}")
        return 0
    found = {}
    for fix in wanted:
        if options.rerun or not verify.recorded(repo, tree, f"replay {fix.id}"):
            subprocess.run([sys.executable, __file__, "_one", tree, fix.id], cwd=repo, check=True)
        found[fix.id] = verify.recorded(repo, tree, f"replay {fix.id}")
    print(table(wanted, found))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
