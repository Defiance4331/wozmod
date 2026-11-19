#!/usr/bin/env python3
"""Machine-wide locks in the main checkout's work/, shared by every checkout of the repo: the two game slots
(game_harness.py) and the heavy-run lock.

    python3 tools/machine_lock.py COMMAND [ARG...]

runs COMMAND with the machine to itself: it waits for every other heavy run and every running game to end first
(saying who holds the machine), holds the lock for COMMAND's life, and exits with COMMAND's status. tools/verify.py
takes it this way; run a dev install or an art build this way too. A benchmark goes through tools/bench.py, which
takes the lock around each measured run alone, so the runs waiting between them go in. A game launch (game_harness.py
run) and a parallel test run (tests/conftest.py) hold it shared, beside each other. Every run waits in one line,
first come, first served: a run goes in once every run that began waiting before it has. A heavy run beside
another, or beside a game, slows both, skews a benchmark's numbers and makes time limits (the install budget, Wine
boot timeouts) fail for no fault of the code.

Each lock is a flock on a file the kernel releases when its holder exits, so a crashed or killed run never leaves the
machine locked. The descriptor is not inherited, so a process COMMAND leaves behind (a wineserver) cannot hold it.
Each finished heavy run appends a row to work/machine-lock.log: start, minutes held, CPU minutes used, exit, command.
-h or --help prints this; any other option before COMMAND is refused, since no command starts with one.
"""

from __future__ import annotations

import fcntl
import json
import os
import resource
import signal
import subprocess
import sys
import time

from pathlib import Path

from checkout import work

HEAVY = ".machine.lock"  # a heavy run holds it exclusively; each running game holds it shared
# The line of runs waiting for the machine: one ticket file per run, named by when it began to wait, locked by its run.
# First come, first served, so neither a stream of games nor a benchmark's back-to-back runs can keep the others out.
LINE = ".machine.line"
HELD = "WOZMOD_MACHINE_LOCK"  # set for COMMAND, so a heavy run inside one runs without waiting for itself
# Two games may run at once on the machine, from any checkout: every run holds one of these, in the main checkout's
# work/, for its whole life, and writes its name and start into it so a waiting launch can say who holds
# what.
SLOTS = (".game-run.lock", ".game-run.1.lock")


def take(names: tuple[str, ...], holder: str = "", poll: float = 1.0) -> tuple[int, int]:
    """Wait for any one of the lock files ``names`` (in work/) to be free and lock it: (descriptor, index), writing
    ``holder`` and the time into the file. The descriptor stays open, so the lock lasts until the process ends."""
    while True:
        for index, name in enumerate(names):
            fd = os.open(work() / name, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            os.ftruncate(fd, 0)
            os.pwrite(fd, json.dumps({"run": holder, "since": time.time()}).encode(), 0)
            return fd, index
        time.sleep(poll)


def _alive(ticket: Path) -> bool:
    """Whether the run that wrote ticket still waits: it holds the ticket locked while it does. A dead run's ticket is
    removed here, so a crashed run never blocks the line."""
    try:
        fd = os.open(ticket, os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    ticket.unlink(missing_ok=True)
    return False


def _wait_turn(alone: bool, holder: str, poll: float) -> int:
    """Wait in line for the machine, first come first served, then hold it: alone (a heavy run) or shared (a game or
    a test run, beside the shared runs next to it in line). A run goes in when no run that began waiting before it
    still waits (a shared run: no heavy one), and the runs on the machine allow it. Returns the held descriptor."""
    line = work() / LINE
    line.mkdir(exist_ok=True)
    name = f"{time.monotonic_ns():020d}-{os.getpid()}-{'heavy' if alone else 'shared'}"
    # Written under a hidden name, locked, then renamed: no one sees the ticket before its owner holds it.
    draft = line / f".{name}"
    ticket = os.open(draft, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    fcntl.flock(ticket, fcntl.LOCK_EX)
    os.write(ticket, holder.encode("utf-8"))
    os.rename(draft, line / name)
    told = False
    try:
        while True:
            ahead = [t for t in sorted(line.iterdir()) if not t.name.startswith(".") and t.name < name and _alive(t)]
            if not (ahead if alone else [t for t in ahead if t.name.endswith("-heavy")]):
                machine = os.open(work() / HEAVY, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
                try:
                    fcntl.flock(machine, (fcntl.LOCK_EX if alone else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(machine)
                else:
                    if alone:
                        os.ftruncate(machine, 0)
                        os.pwrite(machine, json.dumps({"run": holder, "since": time.time()}).encode(), 0)
                    return machine
            if not told:
                print(f"machine_lock: {holder} waits for the machine, held by {_busy(ahead)}", file=sys.stderr,
                      flush=True)
                told = True
            time.sleep(poll)
    finally:
        (line / name).unlink(missing_ok=True)
        os.close(ticket)


def _busy(ahead: list[Path]) -> str:
    """Who has the machine, for a run waiting behind the tickets ahead: the heavy run, else a game, else the first
    run ahead in line."""
    return (holders((HEAVY,)) or [f"the game {game}" for game in holders(SLOTS)]
            or [t.read_text(encoding="utf-8", errors="replace") for t in ahead[:1]] or ["a running game or test run"])[0]


def waits(pid: int) -> str:
    """What the process pid, waiting in the machine's line, waits for: who holds the machine and how many runs that
    go before it wait ahead (a shared run waits only for heavy ones). '' when pid is not in line."""
    line = work() / LINE
    tickets = [t for t in sorted(line.iterdir()) if not t.name.startswith(".") and _alive(t)] if line.is_dir() else []
    mine = next((t for t in tickets if t.name.split("-")[1] == str(pid)), None)
    if mine is None:
        return ""
    ahead = [t for t in tickets if t.name < mine.name and (mine.name.endswith("-heavy") or t.name.endswith("-heavy"))]
    return f"the machine, held by {_busy(ahead)}" + (f"; {len(ahead)} run(s) wait ahead" if ahead else "")


def claim(holder: str, poll: float = 1.0) -> int:
    """For a heavy run: wait in line, saying who has the machine, then hold it alone. The descriptor stays open, so
    the lock lasts until the process ends."""
    return _wait_turn(True, holder, poll)


def share(poll: float = 1.0, what: str = "the game") -> int | None:
    """For a game launch or a test run (what): wait in line, then hold the machine shared, beside other games and
    test runs, so no heavy run starts beside it. The descriptor (None inside a heavy run, which already has the
    machine) stays open, so the lock lasts until the process ends."""
    if os.environ.get(HELD):
        return None
    return _wait_turn(False, f"{what} (pid {os.getpid()})", poll)


def holders(names: tuple[str, ...]) -> list[str]:
    """Who holds each busy lock of ``names``, and for how long: `run-name (12 min)`."""
    held = []
    for name in names:
        path = work() / name
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
                continue  # free
            except BlockingIOError:
                pass
            try:
                info = json.loads(handle.read() or b"{}")
            except ValueError:
                info = {}
        age = f"{(time.time() - info['since']) / 60:.0f} min" if "since" in info else "age unknown"
        held.append(f"{info.get('run') or 'a launch without a name'} ({age})")
    return held


def run(command: list[str]) -> tuple[int, float]:
    """Run command with the machine to itself: wait in line, hold the lock for its life, log its row, then free the
    machine. Inside a heavy run it runs at once. Returns its exit status (negative: the signal that ended it) and the
    seconds it ran, not counting the wait."""
    if os.environ.get(HELD):
        start = time.monotonic()
        return subprocess.run(command).returncode, time.monotonic() - start
    line = " ".join(command)
    fd = claim(f"{line} (pid {os.getpid()}, in {os.getcwd()})")
    try:
        before = resource.getrusage(resource.RUSAGE_CHILDREN)  # cumulative: a second run in this process subtracts it
        start, began = time.time(), time.monotonic()
        child = subprocess.Popen(command, env={**os.environ, HELD: "1"})
        # stopping this stops COMMAND, then frees the machine; after it the old handlers come back, so a stop between
        # two of a bench's runs stops the bench
        kept = {sig: signal.signal(sig, lambda number, _frame: child.send_signal(number))
                for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
        try:
            status = child.wait()
        finally:
            for sig, handler in kept.items():
                signal.signal(sig, handler)
        seconds = time.monotonic() - began
        used = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = used.ru_utime + used.ru_stime - before.ru_utime - before.ru_stime
        with (work() / "machine-lock.log").open("a", encoding="utf-8") as log:
            log.write(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(start))}\t{seconds / 60:.1f}\t"
                      f"{cpu / 60:.1f}\t{status}\t{line}\n")
    finally:
        os.close(fd)
    return status, seconds


def main(command: list[str]) -> int:
    if command[:1] in (["-h"], ["--help"]):
        print(__doc__.strip())
        return 0
    if not command or command[0].startswith("-"):  # an option, never a command: it would wait in line, then not start
        print(f"machine_lock: {command[0]} is not an option; give the command to run" if command else __doc__.strip(),
              file=sys.stderr)
        return 2
    status, _ = run(command)
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
