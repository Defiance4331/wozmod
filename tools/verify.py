#!/usr/bin/env python3
"""One command for the checks: what a change needs, bound to the exact tree it ran on.

    tools/verify.py --commit REV [--rerun]
    tools/verify.py [--triggered] [--wait] [--rerun] [--plan]
    tools/verify.py ref STEP...
    tools/verify.py release [-- PYTEST_ARG...]

--commit REV verifies the tree of REV (a commit or a tree) for main: the fast suite on Linux and under Windows Python
(tools/windows_suite.py), then the rows of tools/verify_checks.toml that the diff from the merge base with main
triggers, each on an export of the tree in its own environment, whatever the checkout holds meanwhile. It prints
every verdict and exits on them all. Each commit in a linked worktree starts it in the background
(tools/githooks/post-commit runs `_background`; its output goes to work/verify/runs/TREE/background.log): a newer
commit in the same checkout stops that checkout's run of an older tree, and a call on a tree whose run is going waits
for that run, then reuses its verdicts. So `tools/verify.py --commit HEAD` before landing prints them, waiting for a
run still going.

With no mode: names the tree (git write-tree of the checkout with its untracked files, ignored ones left out), runs
the fast suite on Linux in the checkout, prints its verdict and exits on it; it also starts the same suite under
Windows Python on an export of that tree in the background, whose verdict lands in the notes about 2 minutes later
(a later call prints it; --wait waits for it and exits on both). It prints the triggered rows; --triggered runs them
in the checkout, and records a verdict only while the checkout still holds the tree. --plan prints the tree, the
judge and those rows, and runs nothing.

A verdict already recorded for the tree is shown, not run again (--rerun runs it again; both stay recorded). main
needs them all: tools/githooks/reference-transaction refuses to move refs/heads/main to a commit whose tree lacks a
passing Linux, Windows or triggered-row verdict (gate and owed below), unless a remote-tracking ref already holds the
commit or main's tree stays the same.

ref STEP...: per step (text, voices, art), the reference build of this tree's step key in work/verify/builds/STEP/KEY/
(out/, staging/, the read gate's trace.jsonl and unread.json, build.log, build.json), reused while the key exists,
made with the read gate enforcing; its diff.txt against the merge base's build of the same step; then the slow tests
that read it (the checklist's [readers]). The triggered rows whose run has `ref STEP` items share one such run, with
their tests: each tree is exported once, each step built or reused once, each reader run once.

release: everything, strict (a missing input fails): the slow suite (with the release pins against fresh builds,
tests/test_release_pins.py), the reference builds and their readers (the end-to-end install among them, kept from
the builds), the first install, timed alone, and the Windows run. It fails when a run skipped a test for a reason
other than a system mark (linux_only, real_windows), or left out a test a full collection finds: a narrowed test set.

Verdicts are local git notes (refs/notes/verify) on the tree object, one JSON line per run, so every commit of the
tree has them (an amend, the squashed commit that lands); logs and junit files are in the main checkout's
work/verify/runs/TREE/. The judge (this file and tools/verify_checks.toml) runs from the merge
base with main, so a change cannot edit the check that judges it: when the tree edits either, it is said, and every
checklist row counts as triggered. Each run holds the machine lock (tools/machine_lock.py) for what it is: the
reference builds as a build and the test runs as test runs, both shared, beside games and other runs (a test run on
an export as one in a checkout does, tests/conftest.py); release's timed first install alone, as a measuring run.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

JUDGE = ("tools/verify.py", "tools/verify_checks.toml")
JUDGED = "WOZMOD_VERIFY_JUDGE"  # set once the merge base's judge runs: the commit it came from, or "tree"
MAIN = "main"
STEPS = ("text", "voices", "art")
# The slow switches (tests/*.py skipif). A switch a tree adds and this list lacks makes release fail as narrowed.
SWITCHES = ("ART_COMPOSE", "ART_WORDS", "ASR", "AUDIO", "CONTEXTS", "EVENT_TIMING", "FONT_FACE_SOURCE",
            "FONT_GEOMETRY", "INSTALL_E2E", "LIPS", "MOVIE_CAPTIONS", "RELEASE_PINS", "RUNTIME_COMPILE", "SHOUT_BANKS",
            "STOCK_PIECES", "TEXT_ROOM")
ELSEWHERE = ("Linux only", "Windows only")  # how a system mark's skip starts (tests/conftest.py SYSTEM_MARKS)
E2E = "tests/test_install_e2e.py"
PYTEST = ("-q", "-p", "no:cacheprovider", "-rs")
KEYS = ("import json; from wozmod.install import step_keys; "
        "print(json.dumps({step: each['key'] for step, each in step_keys.keys().items()}))")
# A step's build, as an install builds it: its own worker, the read gate enforcing, every item read in the trace.
BUILD = """
import json, sys
from pathlib import Path
from wozmod import install
from wozmod.install import gate, manifest, steps
from wozmod.progress import Progress
step, folder = sys.argv[1], Path(sys.argv[2])
for name in ("staging", "out"):
    (folder / name).mkdir()
steps.run({step: steps.Inputs.environment(install.worker_count()).arguments(folder / "staging", folder / "out")},
          Progress(), folder / "build.log", mode=gate.ENFORCE, trace=folder / "trace.jsonl")
unread = gate.unread(manifest.manifest(step), gate.records(folder / "trace.jsonl"))
(folder / "unread.json").write_text(json.dumps(unread, indent=1) + "\\n", encoding="utf-8")
"""


# The tree and its records


def git(repo: Path, *arguments: str, env: dict | None = None, check: bool = True) -> str:
    run = subprocess.run(["git", *arguments], cwd=repo, env=env, capture_output=True, encoding="utf-8")
    if check and run.returncode:
        raise SystemExit(f"verify: git {' '.join(arguments)}: {run.stderr.strip()}")
    return run.stdout.strip()


def work(repo: Path) -> Path:
    """The main checkout's work/, from it or any linked worktree."""
    return Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).parent / "work"


def tree_id(repo: Path) -> str:
    """The tree the checkout holds now, untracked files included and ignored ones left out: git write-tree of a copy
    of its index after `git add -A`. The checkout's own index is not touched."""
    index = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / "index"
        if index.is_file():
            shutil.copyfile(index, copy)  # its file stats spare hashing every file again
        env = {**os.environ, "GIT_INDEX_FILE": str(copy)}
        git(repo, "add", "-A", env=env)
        return git(repo, "write-tree", env=env)


def records(repo: Path, tree: str) -> list[dict]:
    """Every run recorded for tree, oldest first."""
    note = git(repo, "notes", "--ref=verify", "show", tree, check=False)
    return [json.loads(line) for line in note.splitlines() if line.startswith("{")]


def recorded(repo: Path, tree: str, check: str) -> dict | None:
    """The latest record of check on tree. Notes are keyed by the tree object, so another tree's never counts."""
    return next((r for r in reversed(records(repo, tree)) if r["check"] == check), None)


def record(repo: Path, tree: str, entry: dict) -> None:
    """Add entry to tree's note: one line per run, under a lock, as the Windows run may land at the same moment."""
    (work(repo) / "verify").mkdir(parents=True, exist_ok=True)
    with (work(repo) / "verify/notes.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        git(repo, "notes", "--ref=verify", "append", "-m", json.dumps(entry, sort_keys=True), tree)


def runs(repo: Path, tree: str) -> Path:
    return work(repo) / "verify/runs" / tree


def export(repo: Path, tree: str, folder: Path) -> Path:
    """tree's files in folder (no .git): what a background or reference run reads, whatever the checkout does next."""
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    archive = subprocess.Popen(["git", "archive", tree], cwd=repo, stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", folder], stdin=archive.stdout, check=True)
    if archive.wait():
        raise SystemExit(f"verify: git archive {tree} failed")
    return folder


# The judge


def merge_base(repo: Path) -> str:
    base = git(repo, "merge-base", "HEAD", MAIN, check=False)
    if not base:
        raise SystemExit(f"verify: no merge base of HEAD and {MAIN}")
    return base


def delegate(repo: Path, argv: list[str]) -> None:
    """Run the merge base's tools/verify.py in place of this one (it returns only when the base has none)."""
    if os.environ.get(JUDGED):
        return
    base = merge_base(repo)
    blob = git(repo, "rev-parse", "--verify", "-q", f"{base}:{JUDGE[0]}", check=False)
    if not blob:
        os.environ[JUDGED] = "tree"
        return
    copy = work(repo) / "verify/judge" / f"{blob}.py"
    if not copy.is_file():
        copy.parent.mkdir(parents=True, exist_ok=True)
        partial = copy.with_suffix(f".{os.getpid()}")
        partial.write_bytes(subprocess.run(["git", "cat-file", "blob", blob], cwd=repo, capture_output=True,
                                           check=True).stdout)
        partial.replace(copy)
    os.execve(sys.executable, [sys.executable, str(copy), *argv], {**os.environ, JUDGED: base})


def checklist(repo: Path) -> dict:
    """The checklist the judge reads: the merge base's, or the tree's when the judge is the tree's own."""
    judge = os.environ[JUDGED]
    if judge == "tree":
        return tomllib.loads((repo / JUDGE[1]).read_text(encoding="utf-8"))
    text = git(repo, "show", f"{judge}:{JUDGE[1]}", check=False)
    return tomllib.loads(text) if text else {}


def judge_edits(repo: Path, base: str, tree: str) -> list[str]:
    """The judge files tree changes from the merge base (all of them when the base has no judge)."""
    if os.environ[JUDGED] == "tree":
        return list(JUDGE)
    return git(repo, "diff", "--name-only", base, tree, "--", *JUDGE).splitlines()


def triggered(rows: list[dict], paths: list[str]) -> list[dict]:
    """The rows one of paths matches."""
    return [row for row in rows if any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in row["paths"])]


def removed_tests(repo: Path, base: str, tree: str) -> list[str]:
    """Test functions the tree deletes from the merge base's tests/ (renames show as removed and added)."""
    diff = git(repo, "diff", "-U0", base, tree, "--", "tests/")
    named = lambda sign: set(re.findall(rf"^{re.escape(sign)}\s*(?:async\s+)?def (test_\w+)", diff, re.M))
    return sorted(named("-") - named("+"))


# Test runs and what they show


def junit_id(nodeid: str) -> str:
    """A pytest node id in junit's form, classname::name: tests/test_a.py::Case::test_b[1] is tests.test_a.Case::test_b[1]."""
    path, *parts = nodeid.split("::")
    return "::".join([".".join([path.removesuffix(".py").replace("/", "."), *parts[:-1]]), parts[-1]])


def within(node: str, name: str) -> bool:
    """Whether name (junit's form) is a test of node, a pytest path or a test's node id."""
    if "::" not in node:
        module = node.removesuffix(".py").replace("/", ".")
        return name.startswith((f"{module}::", f"{module}."))
    return name == junit_id(node) or name.startswith(f"{junit_id(node)}[")


def outcome(junit: Path, code: int) -> dict:
    """What a pytest run did, from its junit file and exit code: its verdict, counts, test ids, skips and failures."""
    found = {"verdict": "fail", "code": code, "ran": [], "skips": {}, "broken": [], "counts": {}}
    if not junit.is_file():
        found["summary"] = f"pytest wrote no results (exit {code})"
        return found
    counts = dict.fromkeys(("passed", "failed", "errors", "skipped"), 0)
    for case in ET.parse(junit).getroot().iter("testcase"):
        test, at, group = case.get("name").rpartition("@")
        # xdist's loadgroup names a grouped test test@group (tests/conftest.py groups those sharing a module fixture)
        name = f"{case.get('classname')}::{test if at and ']' not in group else case.get('name')}"
        found["ran"].append(name)
        kind = next((child.tag for child in case if child.tag in ("failure", "error", "skipped")), "passed")
        counts[{"failure": "failed", "error": "errors"}.get(kind, kind)] += 1
        if kind == "skipped":
            found["skips"][name] = (case.find("skipped").get("message") or "").removeprefix("Skipped: ")
        elif kind != "passed":
            found["broken"].append(name)
    found["counts"] = counts
    found["verdict"] = "pass" if code == 0 and counts["passed"] and not found["broken"] else "fail"
    found["summary"] = ", ".join(f"{n} {kind}" for kind, n in counts.items() if n) + f" (exit {code})"
    return found


def stopped(code: int, what: str) -> None:
    """A run a signal ended is no verdict: stop before anything records it as a fail."""
    if code < 0:
        raise SystemExit(f"verify: {what} was stopped by signal {-code}; nothing is recorded")


def pytest(cwd: Path, out: Path, arguments: list[str], env: dict, python: list[str]) -> dict:
    """Run pytest in cwd with python, its log and junit file in out."""
    out.mkdir(parents=True, exist_ok=True)
    junit = out / "junit.xml"
    junit.unlink(missing_ok=True)
    started = time.monotonic()
    with (out / "pytest.log").open("w", encoding="utf-8") as log:
        code = subprocess.run([*python, "-m", "pytest", *PYTEST, f"--junitxml={junit}", *arguments], cwd=cwd, env=env,
                              stdout=log, stderr=subprocess.STDOUT).returncode
    stopped(code, "pytest")
    found = outcome(junit, code)
    found["summary"] += f" in {time.monotonic() - started:.0f} s, log {out / 'pytest.log'}"
    return found


def collected(cwd: Path, env: dict, python: list[str]) -> list[str]:
    """Every test id a full run in cwd collects, in junit's form."""
    run = subprocess.run([*python, "-m", "pytest", "--collect-only", "-q", "-n0", "-p", "no:cacheprovider"], cwd=cwd,
                         env=env, capture_output=True, encoding="utf-8")
    if run.returncode:
        raise SystemExit(f"verify: collecting the tests failed:\n{run.stdout[-2000:]}{run.stderr[-2000:]}")
    return [junit_id(line) for line in run.stdout.splitlines() if "::" in line]


def narrowed(expected: list[str], found: dict, allowed: tuple[str, ...] = ELSEWHERE) -> list[str]:
    """Why a run's test set is narrower than expected: a test it left out, or one it skipped for a reason that does
    not start with one of allowed (by default, a system mark's)."""
    ran = set(found["ran"])
    problems = [f"not run: {name}" for name in expected if name not in ran]
    problems += [f"skipped: {name}: {why}" for name, why in found["skips"].items() if not why.startswith(allowed)]
    return problems


def environment(repo: Path, switches: bool, exported: bool = False) -> dict:
    """The environment of a run: strict (a missing input fails), and every slow switch on, or none. Nothing else is
    set, but for a run on an export the machine's harness.toml, which it has no .git to find (tools/checkout.py): a
    variable that says where work/ is overrides the folders tests set up for the tools they run."""
    env = {name: value for name, value in os.environ.items()
           if name.removeprefix("WOZMOD_") not in SWITCHES}
    env["WOZMOD_REQUIRE_INPUTS"] = "1"
    if exported:
        env["WOZMOD_HARNESS_CONFIG"] = str(work(repo) / "harness.toml")
    if switches:
        env |= {f"WOZMOD_{name}": "1" for name in SWITCHES}
    return env


def harness_inputs(repo: Path) -> dict:
    config = work(repo) / "harness.toml"
    table = tomllib.loads(config.read_text(encoding="utf-8")).get("tests", {}) if config.is_file() else {}
    return {name: os.environ.get(name, str(value)) for name, value in table.items()}


LIVE = ["uv", "run", "--extra", "native", "python"]  # the checkout's own environment


def machine_lock(repo: Path):
    """tools/machine_lock.py, the checkout's: the machine-wide lock every run of every checkout takes."""
    if str(repo / "tools") not in sys.path:
        sys.path.insert(0, str(repo / "tools"))
    import machine_lock

    return machine_lock


@contextlib.contextmanager
def shared_machine(repo: Path, what: str):
    """Hold the machine shared for the with block, as a test run in a checkout does (tests/conftest.py), and let it
    go when the block ends, so a caller that goes on (--commit's triggered rows, a release, a replay) does not keep a
    benchmark waiting. Yields what a run on an export inside it adds to its environment: HELD, so its conftest does
    not wait for the run it is part of (nothing inside a run that holds the machine already, whose HELD it has)."""
    held = machine_lock(repo).hold("test", what)
    try:
        yield {} if held is None else {machine_lock(repo).HELD: "test"}
    finally:
        if held is not None:
            os.close(held)


def exported_path(folder: Path) -> dict:
    """What a run on the export in folder imports from it: its own code, not the checkout's."""
    return {"PYTHONPATH": os.pathsep.join([str(folder / "src"), str(folder / "tools")])}


def on_export(repo: Path, tree: str, out: Path, arguments: list[str], switches: bool, what: str) -> dict:
    """pytest with arguments on an export of tree, in tree's own environment (venv), so it runs what the tree holds
    whatever the checkout does meanwhile; every slow switch on, or none; holding the machine as a test run."""
    with shared_machine(repo, what) as inside:
        folder = export(repo, tree, work(repo) / "verify/trees" / f"{tree}.{os.getpid()}")
        try:
            return pytest(folder, out, arguments, environment(repo, switches, True) | exported_path(folder) | inside,
                          venv(repo, tree, folder))
        finally:
            shutil.rmtree(folder, ignore_errors=True)


# Default mode


def linux(repo: Path, tree: str, rerun: bool, exact: bool = False) -> dict:
    """The fast suite on Linux, in the checkout, recorded only when the tree is still the one it named after; or
    (exact) on an export of tree, in tree's own environment (venv), so it runs what the tree holds."""
    if not rerun and (found := recorded(repo, tree, "linux")):
        return found | {"reused": True}
    out = runs(repo, tree) / "linux"
    if exact:
        found = on_export(repo, tree, out, [], False, f"the Linux run of {tree[:12]}")
    else:
        found = pytest(repo, out, [], environment(repo, False), LIVE)
    entry = {"check": "linux", "verdict": found["verdict"], "summary": found["summary"], "judge": os.environ[JUDGED],
             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not exact and (now := tree_id(repo)) != tree:
        return entry | {"verdict": "fail", "summary": f"{found['summary']}; not recorded: the checkout changed to tree "
                                                      f"{now[:12]} during the run"}
    record(repo, tree, entry)
    return entry


def tree_lock(repo: Path, tree: str) -> int:
    """Hold tree's run lock until this process ends, first waiting for a run of tree already going (the background
    run of its commit, or another --commit), whose verdicts this run then reuses rather than running them again."""
    runs(repo, tree).mkdir(parents=True, exist_ok=True)
    lock = os.open(runs(repo, tree) / "commit.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"a run of tree {tree[:12]} is going (its log: {runs(repo, tree) / 'background.log'}); waiting for it",
              flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
    return lock


def windows_lock(repo: Path, tree: str):
    runs(repo, tree).mkdir(parents=True, exist_ok=True)
    return (runs(repo, tree) / "windows.lock").open("a")


def windows_run(repo: Path, tree: str, arguments: list[str], held: int | None = None) -> dict:
    """The fast suite under Windows Python on an export of tree (tools/windows_suite.py), strict, holding tree's
    Windows lock (held: the descriptor of it the caller locked) and the machine shared; recorded."""
    with windows_lock(repo, tree) if held is None else os.fdopen(held, "a") as lock:
        if held is None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        with shared_machine(repo, f"the Windows run of {tree[:12]}"):
            folder = export(repo, tree, work(repo) / "verify/trees" / f"{tree}.{os.getpid()}")
            out = runs(repo, tree) / "windows"
            out.mkdir(parents=True, exist_ok=True)
            try:
                with (out / "windows_suite.log").open("w", encoding="utf-8") as log:
                    # The checkout's runner, which finds the main checkout's Windows environments; the tree's files.
                    code = subprocess.run([sys.executable, repo / "tools/windows_suite.py", "--tree", folder, "--out",
                                           out, *arguments], cwd=folder, env=environment(repo, False, True),
                                          stdout=log, stderr=subprocess.STDOUT).returncode
                stopped(code, "the Windows run")
                found = outcome(out / "junit.xml", code)
            finally:
                shutil.rmtree(folder, ignore_errors=True)
        found["summary"] += f", log {out / 'pytest.log'}"
        record(repo, tree, {"check": "windows", "verdict": found["verdict"], "summary": found["summary"],
                            "judge": os.environ[JUDGED], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return found


def windows(repo: Path, tree: str, rerun: bool, wait: bool) -> str:
    """The Windows verdict for tree, or start the run in the background and say so."""
    lock = windows_lock(repo, tree)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        state = "running"
    else:
        if not rerun and (found := recorded(repo, tree, "windows")):
            lock.close()
            return f"{found['verdict']} (recorded {found['at']}): {found['summary']}"
        # The run inherits the locked descriptor, so the lock passes to it with no moment free between.
        with (runs(repo, tree) / "windows-background.log").open("w", encoding="utf-8") as out:
            subprocess.Popen([sys.executable, __file__, "_windows", tree, str(lock.fileno())], cwd=repo,
                             start_new_session=True, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                             pass_fds=(lock.fileno(),))
        state = "started"
    lock.close()
    if not wait:
        return (f"{state} in the background; its verdict lands in the notes in about 2 minutes, and main needs it "
                f"(tools/verify.py shows it, --wait waits for it)")
    with windows_lock(repo, tree) as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
    found = recorded(repo, tree, "windows")
    return f"{found['verdict']}: {found['summary']}" if found else "the run ended without a verdict (see its log)"


def windows_here(repo: Path, tree: str, rerun: bool) -> dict:
    """The Windows verdict for tree, run in this process (--commit) unless it is recorded; a run of it already going
    (the default mode's background one) is waited for, and its verdict taken."""
    lock = os.open(runs(repo, tree) / "windows.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    if not rerun and (found := recorded(repo, tree, "windows")):
        os.close(lock)
        return found | {"reused": True}
    return windows_run(repo, tree, [], lock)


# Reference builds


def venv_folder(repo: Path, tree: str) -> Path:
    """Where tree's Python environment is: work/verify/venvs/KEY/, where KEY hashes the files it is made from."""
    listing = git(repo, "ls-tree", "-r", tree, "--", "uv.lock", "pyproject.toml", "native")
    return work(repo) / "verify/venvs" / hashlib.sha256(listing.encode()).hexdigest()[:16]


def venv(repo: Path, tree: str, folder: Path) -> list[str]:
    """A Python environment with tree's locked packages and its native codecs, in venv_folder; made in KEY.partial
    and renamed into place, under KEY.lock, so a crash leaves nothing taken for finished and two runs never make it at
    once. Callers hold the machine, exclusively or shared."""
    made = venv_folder(repo, tree)
    made.parent.mkdir(parents=True, exist_ok=True)
    with made.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (made / "bin/python").exists():
            return [str(made / "bin/python")]
        partial = made.with_suffix(".partial")
        shutil.rmtree(partial, ignore_errors=True)
        env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(partial),
               "CARGO_TARGET_DIR": str(work(repo) / "verify/cargo-target")}
        subprocess.run(["uv", "sync", "--frozen", "--extra", "native", "--no-install-project", "-q"], cwd=folder,
                       env=env, check=True)
        partial.rename(made)
    return [str(made / "bin/python")]


def side_environment(repo: Path, folder: Path) -> dict:
    """The environment the step keys and the builds of the export in folder run in: the caller's, but no WOZMOD_*
    name other than the machine's inputs."""
    inputs = harness_inputs(repo) | {"WOZMOD_HARNESS_CONFIG": str(work(repo) / "harness.toml")}
    env = {name: value for name, value in os.environ.items() if not name.startswith("WOZMOD_")} | inputs
    return env | exported_path(folder)


def step_keys(python: list[str], folder: Path, env: dict) -> dict[str, str]:
    run = subprocess.run([*python, "-c", KEYS], cwd=folder, env=env, capture_output=True, encoding="utf-8")
    if run.returncode:
        raise SystemExit(f"verify: the step keys of {folder} failed:\n{run.stderr[-2000:]}")
    return json.loads(run.stdout)


def build(python: list[str], folder: Path, step: str, key: str, tree: str, env: dict, builds: Path) -> tuple[Path, str]:
    """The build of step at key (made in KEY.partial, renamed into place; a failed one is kept as KEY.failed), under
    KEY.lock, so a run that needs the same key while another builds it waits for that build and reuses it."""
    made = builds / step / key
    made.parent.mkdir(parents=True, exist_ok=True)
    with made.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _build(python, folder, step, key, tree, env, made)


def _build(python: list[str], folder: Path, step: str, key: str, tree: str, env: dict, made: Path) -> tuple[Path, str]:
    if (made / "build.json").is_file():
        return made, "reused"
    partial, failed = made.with_suffix(".partial"), made.with_suffix(".failed")
    for stale in (partial, failed):
        shutil.rmtree(stale, ignore_errors=True)
    partial.mkdir(parents=True)
    started = time.monotonic()
    if (folder / "src/wozmod/install/steps.py").is_file():
        command = [*python, "-c", BUILD, step, partial]
    else:  # a tree from before the step runner and the read gate (main up to 8f20076): its own one-step driver
        command = [*python, "tests/step_run.py", step, partial, "0"]
    with (partial / "driver.log").open("w", encoding="utf-8") as log:
        code = subprocess.run(command, cwd=folder, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
    if (partial / "art-staging").is_dir():
        (partial / "art-staging").rename(partial / "staging")
    if code:
        partial.rename(failed)
        tail = (failed / "driver.log").read_text(encoding="utf-8", errors="replace")[-1500:]
        raise SystemExit(f"verify: the {step} build of tree {tree[:12]} failed (exit {code}), in {failed}:\n{tail}")
    seconds = round(time.monotonic() - started)
    (partial / "build.json").write_text(json.dumps({"step": step, "key": key, "tree": tree, "seconds": seconds,
                                                    "gate": (partial / "trace.jsonl").is_file()}, indent=1) + "\n",
                                        encoding="utf-8")
    partial.rename(made)
    return made, f"built in {seconds} s"


def first_difference(a: Path, b: Path) -> int | None:
    with a.open("rb") as left, b.open("rb") as right:
        at = 0
        while True:
            x, y = left.read(1 << 20), right.read(1 << 20)
            if x != y:
                return at + next((i for i, (p, q) in enumerate(zip(x, y, strict=False)) if p != q), min(len(x), len(y)))
            if not x:
                return None
            at += len(x)


def diff(new: Path, old: Path) -> str:
    """new's out/ against old's, file by file: a summary line, then each file added, removed or changed."""
    files = lambda root: {p.relative_to(root).as_posix(): p for p in (root / "out").rglob("*") if p.is_file()}
    a, b = files(old), files(new)
    lines, same = [], 0
    for name in sorted(a.keys() | b.keys()):
        if name not in b:
            lines.append(f"removed {name} ({a[name].stat().st_size} bytes)")
        elif name not in a:
            lines.append(f"added   {name} ({b[name].stat().st_size} bytes)")
        elif (at := first_difference(a[name], b[name])) is None:
            same += 1
        else:
            lines.append(f"changed {name}: {a[name].stat().st_size} -> {b[name].stat().st_size} bytes, first "
                         f"difference at 0x{at:x}")
    kinds = {kind: sum(line.startswith(kind) for line in lines) for kind in ("changed", "added", "removed")}
    head = (f"{new} against {old}: {same} files the same, " + ", ".join(f"{n} {k}" for k, n in kinds.items()))
    return "\n".join([head, *lines]) + "\n"


def trace_summary(made: Path) -> str:
    if not (made / "trace.jsonl").is_file():
        return "no trace (built before the read gate)"
    read = [json.loads(line) for line in (made / "trace.jsonl").read_text(encoding="utf-8").splitlines() if line]
    kinds: dict[str, int] = {}
    for each in read:
        kinds[each["kind"]] = kinds.get(each["kind"], 0) + 1
    unread = json.loads((made / "unread.json").read_text(encoding="utf-8"))
    return (f"read {', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))}; declared but unread: "
            f"{', '.join(f'{len(v)} {k}' for k, v in unread.items() if v) or 'none'}")


def references(repo: Path, tree: str, base: str, steps: list[str], tests: list[str] = (),
               extra: list[str] = ()) -> list[dict]:
    """ref: each step's build of tree and of the merge base, the diff, then the readers and tests (which read the
    builds too), each with pytest's arguments extra, holding the machine as a build. Returns their run."""
    with machine_lock(repo).holding("build", f"the reference builds of {tree[:12]} ({' '.join(steps)})"):
        return _references(repo, tree, base, steps, tests, extra)


def _references(repo: Path, tree: str, base: str, steps: list[str], tests: list[str], extra: list[str]) -> list[dict]:
    builds = work(repo) / "verify/builds"
    base_tree = git(repo, "rev-parse", f"{base}^{{tree}}")
    inputs = harness_inputs(repo) | {"WOZMOD_HARNESS_CONFIG": str(work(repo) / "harness.toml")}
    sides, made = {}, {}
    try:
        for side in dict.fromkeys([tree, base_tree]):
            folder = export(repo, side, work(repo) / "verify/trees" / f"{side}.{os.getpid()}")  # another run may build it
            python = venv(repo, side, folder)
            env = side_environment(repo, folder)
            sides[side] = (folder, python, env)
            keys = step_keys(python, folder, env)
            for step in steps:
                made[side, step], how = build(python, folder, step, keys[step], side, env, builds)
                print(f"ref {step} of {'this tree' if side == tree else 'the merge base'} ({side[:12]}): key "
                      f"{keys[step][:12]}, {how}: {made[side, step]}", flush=True)
        for step in steps:
            if base_tree == tree:
                text = "this is the merge base's tree: nothing to diff\n"
            else:
                text = diff(made[tree, step], made[base_tree, step])
            (made[tree, step] / "diff.txt").write_text(text, encoding="utf-8")
            print(f"ref {step}: {trace_summary(made[tree, step])}\nref {step}: {text.splitlines()[0]}", flush=True)
            record(repo, tree, {"check": f"ref {step}", "verdict": "pass", "summary": str(made[tree, step]),
                                "judge": os.environ[JUDGED], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        readers = [node for step in steps for node in checklist(repo).get("readers", {}).get(step, [])]
        if not readers and not tests:
            return []
        folder, python, env = sides[tree]
        env = environment(repo, True) | {"PYTHONPATH": env["PYTHONPATH"]} | inputs
        env |= {f"WOZMOD_REFERENCE_{step.upper()}": str(made[tree, step]) for step in steps}
        found = pytest(folder, runs(repo, tree) / "readers", [*readers, *tests, *extra], env, python)
        expected = [name for name in collected(folder, env, python)
                    if any(within(node, name) for node in (*readers, *tests))]
        problems = narrowed(expected, found)
        verdict = "fail" if problems or found["verdict"] == "fail" else "pass"
        print(f"readers of {', '.join(steps)}: {verdict}: {found['summary']}", *problems, sep="\n  ", flush=True)
        record(repo, tree, {"check": f"readers {' '.join(steps)}", "verdict": verdict, "summary": found["summary"],
                            "judge": os.environ[JUDGED], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return [found | {"verdict": verdict, "problems": problems}]
    finally:
        for folder, _, _ in sides.values():
            shutil.rmtree(folder, ignore_errors=True)


# Checklist rows


def split(row: dict) -> tuple[list[str], list[str]]:
    """A row's run: the steps of its `ref STEP` items, and its pytest items."""
    steps = [word for item in row["run"] if item.startswith("ref ") for word in item.split()[1:]]
    return steps, [item for item in row["run"] if not item.startswith("ref ")]


def checks(rows: list[dict]) -> list[dict]:
    """What --triggered runs for rows, in their order: each row without `ref STEP` items as a pytest run of its own,
    and the rows with them as one references run, where the first of them stands, of every step they name, with their
    tests. Each check: the rows it stands for, its steps (none for a pytest run), its tests."""
    shared = [row for row in rows if split(row)[0]]
    found = []
    for row in rows:
        if row not in shared:
            found.append({"rows": [row["name"]], "steps": [], "tests": split(row)[1]})
        elif row is shared[0]:
            steps = {step for each in shared for step in split(each)[0]}
            found.append({"rows": [each["name"] for each in shared], "steps": [step for step in STEPS if step in steps],
                          "tests": list(dict.fromkeys(test for each in shared for test in split(each)[1]))})
    return found


def run_rows(repo: Path, tree: str, base: str, rows: list[dict], rerun: bool, exact: bool = False) -> bool:
    """Run checks(rows), recording each row's verdict: the rows of pytest items in the checkout, recorded only when
    it still holds tree, or (exact) on an export of tree."""
    ok = True
    for check in checks(rows):
        if not rerun and all(found := [recorded(repo, tree, f"row {name}") for name in check["rows"]]):
            for name, entry in zip(check["rows"], found, strict=True):
                print(f"{name}: {entry['verdict']} (recorded {entry['at']}): {entry['summary']}")
                ok &= entry["verdict"] == "pass"
            continue
        if check["steps"]:  # the rows' tests run with the readers, against the reference builds
            done = references(repo, tree, base, check["steps"], check["tests"])
        else:
            out = runs(repo, tree) / "rows" / re.sub(r"\W+", "-", check["rows"][0])
            done = [on_export(repo, tree, out, check["tests"], True, f"row {check['rows'][0]} of {tree[:12]}")
                    if exact else pytest(repo, out, check["tests"], environment(repo, True), LIVE)]
        verdict = "pass" if all(found["verdict"] == "pass" for found in done) else "fail"
        summary = "; ".join(found["summary"] for found in done)
        if not exact and (now := tree_id(repo)) != tree:
            print(f"{', '.join(check['rows'])}: {verdict}: {summary}; not recorded: the checkout changed to tree "
                  f"{now[:12]} during the run", flush=True)
            ok = False
            continue
        for name in check["rows"]:
            print(f"{name}: {verdict}: {summary}", flush=True)
            record(repo, tree, {"check": f"row {name}", "verdict": verdict, "summary": summary,
                                "judge": os.environ[JUDGED], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        ok &= verdict == "pass"
    return ok


# Release


def release(repo: Path, tree: str, base: str, extra: list[str]) -> bool:
    """Everything, strict, each run held to the tests a full collection finds; stops at the first red run."""
    env = environment(repo, True)
    every = collected(repo, env, LIVE)
    # the readers, and the tests of the rows that run with the reference builds (they read them too)
    bound = list(dict.fromkeys(test for row in checklist(repo).get("row", []) if split(row)[0] for test in split(row)[1]))
    readers = [node for nodes in checklist(repo).get("readers", {}).values() for node in nodes] + bound
    reads = lambda name: any(within(node, name) for node in readers)
    e2e = lambda name: within(E2E, name)

    def judged(label: str, found: dict, expected: list[str], allowed: tuple[str, ...] = ELSEWHERE) -> bool:
        problems = narrowed(expected, found, allowed)
        verdict = "fail" if problems or found["verdict"] == "fail" else "pass"
        print(f"release, {label}: {verdict}: {found['summary']}", *problems[:40], sep="\n  ", flush=True)
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more")
        return verdict == "pass"

    out = runs(repo, tree) / "release"
    slow = pytest(repo, out / "linux", [f"--ignore={E2E}", *(f"--deselect={n}" for n in readers), *extra], env, LIVE)
    if not judged("the slow suite on Linux", slow, [n for n in every if not reads(n) and not e2e(n)]):
        return False
    for found in references(repo, tree, base, list(STEPS), bound, extra):
        if found["verdict"] != "pass":
            return False
    with machine_lock(repo).holding("measure", f"the timed install of release on {tree[:12]}"):
        alone = pytest(repo, out / "e2e", ["-n0", E2E, *extra], env, LIVE)
    if not judged("the timed install, alone", alone, [n for n in every if e2e(n)]):
        return False
    # The Windows run is the fast suite: its slow tests skip, saying which switch they need.
    return judged("Windows", windows_run(repo, tree, extra), every, (*ELSEWHERE, "set WOZMOD_"))


# The gate on main


def owed(repo: Path, base: str | None, tree: str) -> list[str]:
    """The checks main needs passing on tree, as the judge rules them for the change from base (the merge base with
    main): linux, windows, and `row NAME` for each checklist row the diff triggers, every row when the change edits
    the judge. The checklist is base's, or the tree's when base has none (or there is no base)."""
    if base and git(repo, "rev-parse", "-q", "--verify", f"{base}:{JUDGE[0]}", check=False):
        text = git(repo, "show", f"{base}:{JUDGE[1]}", check=False)
        edits = git(repo, "diff", "--name-only", base, tree, "--", *JUDGE).splitlines()
    else:
        text, edits = git(repo, "show", f"{tree}:{JUDGE[1]}", check=False), list(JUDGE)
    rows = tomllib.loads(text).get("row", []) if text else []
    if not edits:
        rows = triggered(rows, git(repo, "diff", "--name-only", base, tree).splitlines())
    return ["linux", "windows", *(f"row {row['name']}" for row in rows)]


def gate(repo: Path, updates: list[str]) -> list[str]:
    """Why a ref transaction must not move main, from its lines as git's reference-transaction hook gets them in the
    prepared state (`OLD NEW REF`, symbolic refs resolved): main may move to a commit whose tree has a passing verdict
    of every check it owes (owed(): Linux, Windows, and the checklist rows its diff from the merge base with main
    triggers), or that a remote-tracking ref holds (now, or in this same transaction), and may keep its tree or be
    deleted. Empty when it may."""
    moves = [line.split() for line in updates if line.strip()]
    deleted = lambda new: new.strip("0") == ""
    pending = [new for _, new, ref in moves if ref.startswith("refs/remotes/") and not deleted(new)]
    problems = []
    for old, new, ref in moves:
        if ref != f"refs/heads/{MAIN}" or deleted(new):
            continue
        tree = git(repo, "rev-parse", f"{new}^{{tree}}")
        if tree == git(repo, "rev-parse", "-q", "--verify", f"refs/heads/{MAIN}^{{tree}}", check=False):
            continue  # a re-stamp or an amended message: main's content stays what it was
        base = None if deleted(old) else git(repo, "merge-base", old, new, check=False) or None
        found = {check: recorded(repo, tree, check) for check in owed(repo, base, tree)}
        if all(entry and entry["verdict"] == "pass" for entry in found.values()):
            continue
        if git(repo, "for-each-ref", "--count=1", "--contains", new, "--format=%(refname)", "refs/remotes/") or any(
                subprocess.run(["git", "merge-base", "--is-ancestor", new, tip], cwd=repo).returncode == 0
                for tip in pending):
            continue  # upstream's commit: a pull or a fast-forward to it
        label = lambda check: check.capitalize() if check in ("linux", "windows") else check
        state = [f"no {label(check)} verdict" if entry is None else
                 f"a red {label(check)} verdict (recorded {entry['at']}: {entry.get('summary', '')})"
                 for check, entry in found.items() if entry is None or entry["verdict"] != "pass"]
        red = any(entry and entry["verdict"] != "pass" for entry in found.values())
        problems.append(f"main may not move to {new[:12]}: its tree {tree} has {'; '.join(state)}.\n"
                        f"{'Fix the change, or for a flaky test rerun' if red else 'Verify it'}: "
                        f"tools/verify.py --commit {tree}{' --rerun' if red else ''}")
    return problems


# The background run of a commit


def background(repo: Path) -> str:
    """_background (tools/githooks/post-commit, in a linked worktree): HEAD, to verify as --commit does, detached,
    with this process's output in runs/TREE/background.log. A newer commit makes the checkout's run of an older tree
    obsolete, so this stops it first; a run of the same tree (after an amend or a re-stamp) goes on, and so does a
    later commit's, and this one exits. The checkout's run holds work/verify/checkouts/KEY.lock for its life and
    KEY.json names it; the lock of a run that crashed or was killed is free, so its stale record names no one."""
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]  # what git sets for its hooks (GIT_DIR, GIT_INDEX_FILE) would steer every test's git
    if os.getpgrp() != os.getpid():
        os.setsid()  # a group of its own: stopping it stops every process it started
    head, tree = git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "HEAD^{tree}")
    runs(repo, tree).mkdir(parents=True, exist_ok=True)
    with (runs(repo, tree) / "background.log").open("a", encoding="utf-8") as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
    print(f"--- {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}: commit {head[:12]} in {repo}", flush=True)
    folder = work(repo) / "verify/checkouts"
    folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(git(repo, "rev-parse", "--absolute-git-dir").encode()).hexdigest()[:16]
    running = os.open(folder / f"{key}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with (folder / f"{key}.start").open("a") as start:
        fcntl.flock(start, fcntl.LOCK_EX)  # one start at a time per checkout, so the newest commit's run stays
        if git(repo, "rev-parse", "HEAD^{tree}") != tree:
            print("HEAD has moved on since this commit; the later commit's run verifies it")
            sys.exit(0)
        try:
            fcntl.flock(running, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:  # a run is going: its record is the one under this lock
            before = json.loads((folder / f"{key}.json").read_text(encoding="utf-8"))
            if before["tree"] == tree:
                print(f"the run of this tree (pid {before['pid']}) goes on")
                sys.exit(0)
            print(f"stopping the run of the older tree {before['tree'][:12]} (pid {before['pid']})", flush=True)
            try:
                os.killpg(before["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass  # it ended just now
            fcntl.flock(running, fcntl.LOCK_EX)
        (folder / f"{key}.json").write_text(json.dumps({"pid": os.getpid(), "tree": tree, "commit": head}) + "\n",
                                            encoding="utf-8")
    os.set_inheritable(running, True)  # held by the judge this process becomes, until the run ends
    return head


# The command


def main(argv: list[str]) -> int:
    if argv[:1] == ["_gate"]:  # tools/githooks/reference-transaction, before any judge: the checkout's own gate
        problems = gate(Path.cwd(), sys.stdin.read().splitlines())
        print(*(f"reference-transaction: {problem}" for problem in problems), sep="\n", file=sys.stderr)
        return 1 if problems else 0
    repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    if argv[:1] == ["_background"]:  # tools/githooks/post-commit, before any judge: the checkout's own
        argv = ["--commit", background(repo)]
    delegate(repo, argv)
    if argv[:1] == ["_windows"]:  # the background half of the default mode
        found = windows_run(repo, argv[1], [], int(argv[2]))
        return 0 if found["verdict"] == "pass" else 1
    mode = argv[0] if argv[:1] and argv[0] in ("ref", "release") else None
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    if mode is None:
        for flag, about in (("--triggered", "also run the checklist rows the diff triggers (--commit always does)"),
                            ("--wait", "wait for the Windows verdict and exit on every verdict (--commit always does)"),
                            ("--rerun", "run again what is recorded"),
                            ("--plan", "print the tree, the judge and the triggered rows; run nothing")):
            parser.add_argument(flag, action="store_true", help=about)
        parser.add_argument("--commit", metavar="REV", help="verify the tree of REV (a commit or a tree) for main: every "
                            "check it needs, on exports, after any run of it already going")
        options = parser.parse_args(argv)
    exact = mode is None and bool(options.commit)
    base = merge_base(repo)
    if exact:
        tree = git(repo, "rev-parse", "--verify", f"{options.commit}^{{tree}}")
        print(f"tree {tree} (of {options.commit}, on an export); merge base with {MAIN} {base[:12]}")
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(128 + signal.SIGTERM))  # stopped: clean up the exports
    else:
        tree = tree_id(repo)
        head = git(repo, "rev-parse", "HEAD")
        clean = git(repo, "rev-parse", "HEAD^{tree}") == tree
        print(f"tree {tree} (HEAD {head[:12]}{'' if clean else ' with changes'}); merge base with {MAIN} {base[:12]}")
    judge = os.environ[JUDGED]
    print(f"judge: {'the tree' if judge == 'tree' else 'the merge base'}'s {', '.join(JUDGE)}")
    edits = judge_edits(repo, base, tree)
    if edits:
        why = ("the merge base has none, so the tree's own judges it" if judge == "tree" else
               "they run from the merge base, not from the tree")
        print(f"the tree changes the judge ({', '.join(edits)}): {why}; every checklist row counts as triggered")
    paths = git(repo, "diff", "--name-only", base, tree).splitlines()
    rows = checklist(repo).get("row", [])
    rows = rows if edits else triggered(rows, paths)
    if gone := removed_tests(repo, base, tree):
        print(f"tests removed since the merge base: {', '.join(gone)}")
    if mode == "ref":
        steps = argv[1:]
        if not steps or set(steps) - set(STEPS):
            parser.error(f"ref takes steps of {', '.join(STEPS)}")
        return 0 if all(found["verdict"] == "pass" for found in references(repo, tree, base, steps, [])) else 1
    if mode == "release":
        extra = argv[argv.index("--") + 1:] if "--" in argv else []
        verdict = "pass" if release(repo, tree, base, extra) else "fail"
        record(repo, tree, {"check": "release", "verdict": verdict, "judge": judge, "summary": " ".join(extra),
                            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        print(f"release: {verdict}")
        return 0 if verdict == "pass" else 1
    print("checklist rows the diff triggers" + (" (run with --triggered):" if not (options.triggered or exact) else ":")
          if rows else "checklist rows the diff triggers: none")
    for row in rows:
        print(f"  {row['name']}: {', '.join(row['run'])}")
    if options.plan:
        return 0
    if exact:
        tree_lock(repo, tree)
    found = linux(repo, tree, options.rerun, exact)
    print(f"Linux: {found['verdict']}{' (recorded ' + found['at'] + ')' if found.get('reused') else ''}: "
          f"{found['summary']}", flush=True)
    flaky = {r["verdict"] for r in records(repo, tree) if r["check"] == "linux"}
    if len(flaky) > 1:
        print("Linux: this tree has both a pass and a fail recorded: a flaky test, to root-cause or delete")
    if exact:  # everything main needs (gate), in this process
        won = windows_here(repo, tree, options.rerun)
        state = (f"{won['verdict']}{' (recorded ' + won['at'] + ')' if won.get('reused') else ''}: "
                 f"{won['summary']}")
    else:
        state = windows(repo, tree, options.rerun, options.wait)
    print(f"Windows: {state}", flush=True)
    # The default mode exits on the Linux verdict; main needs every one (gate), which --wait and --commit wait for.
    ok = found["verdict"] == "pass" and (not (options.wait or exact) or state.startswith("pass"))
    if options.triggered or exact:
        ok &= run_rows(repo, tree, base, rows, options.rerun, exact)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
