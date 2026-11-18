#!/usr/bin/env python3
"""Machine-wide locks in the main checkout's work/, shared by the checkout and all its worktrees: the two game slots
(game_harness.py) and the heavy-run lock.

    python3 tools/machine_lock.py COMMAND [ARG...]

runs COMMAND with the machine to itself: it waits for every other heavy run and every running game to end first
(saying who holds the machine), holds the lock for COMMAND's life, and exits with COMMAND's status. tools/verify.py
takes it this way; run a dev install, an art build or a benchmark this way too. A game launch (game_harness.py
run) and a test run (tests/conftest.py) wait in turn while a heavy run holds the machine or waits for it. A heavy run beside another, or beside a game,
slows both, skews a benchmark's numbers and makes time limits (the install budget, Wine boot timeouts) fail for no
fault of the code.

Each lock is a flock on a file the kernel releases when its holder exits, so a crashed or killed run never leaves the
machine locked. The descriptor is not inherited, so a process COMMAND leaves behind (a wineserver) cannot hold it.
Each finished heavy run appends a row to work/machine-lock.log: start, minutes held, CPU minutes used, exit, command.
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

from checkout import work

HEAVY = ".machine.lock"  # a heavy run holds it exclusively; each running game holds it shared
QUEUE = ".machine.queue"  # a heavy run holds it while it waits, so games started one after another cannot starve it
HELD = "WOZMOD_MACHINE_LOCK"  # set for COMMAND, so a heavy run inside one runs without waiting for itself
# Two games may run at once across the checkout and all its worktrees: every run holds one of these, in the main
# checkout's work/, for its whole life, and writes its name and start into it so a waiting launch can say who holds
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


def claim(holder: str, poll: float = 1.0) -> int:
    """For a heavy run: wait until no other heavy run and no game holds the machine, saying who does, then hold it
    exclusively. The descriptor stays open, so the lock lasts until the process ends."""
    busy = holders((HEAVY,)) or [f"the game {game}" for game in holders(SLOTS)]
    if busy:
        print(f"machine_lock: waiting for the machine, held by {', '.join(busy)}", file=sys.stderr, flush=True)
    queue, _ = take((QUEUE,), holder, poll)  # ahead of any game that has not started yet
    fd, _ = take((HEAVY,), holder, poll)  # once every running game has ended
    os.close(queue)
    return fd


def share(poll: float = 1.0, what: str = "the game") -> int | None:
    """For a game launch or a test run (what): wait until no heavy run holds or waits for the machine, then hold it
    shared, which other games and test runs may too, so no heavy run starts beside it. The descriptor (None inside a
    heavy run, which already has the machine) stays open, so the lock lasts until the process ends."""
    if os.environ.get(HELD):
        return None
    told = False
    while True:
        queue = os.open(work() / QUEUE, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        machine = os.open(work() / HEAVY, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(queue, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(machine, fcntl.LOCK_SH | fcntl.LOCK_NB)
            os.close(queue)
            return machine
        except BlockingIOError:
            os.close(queue)
            os.close(machine)
        if not told:
            busy = holders((HEAVY,)) or holders((QUEUE,)) or ["a heavy run"]
            print(f"machine_lock: {what} waits for the machine, held by {busy[0]}", file=sys.stderr, flush=True)
            told = True
        time.sleep(poll)


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


def main(command: list[str]) -> int:
    if not command:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if os.environ.get(HELD):
        return subprocess.run(command).returncode
    line = " ".join(command)
    holder = f"{line} (pid {os.getpid()}, in {os.getcwd()})"
    fd = claim(holder)
    start = time.time()
    child = subprocess.Popen(command, env={**os.environ, HELD: "1"})
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):  # stopping this stops COMMAND, then frees the machine
        signal.signal(sig, lambda number, _frame: child.send_signal(number))
    status = child.wait()
    used = resource.getrusage(resource.RUSAGE_CHILDREN)
    with (work() / "machine-lock.log").open("a", encoding="utf-8") as log:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(start))}\t{(time.time() - start) / 60:.1f}\t"
                  f"{(used.ru_utime + used.ru_stime) / 60:.1f}\t{status}\t{line}\n")
    os.close(fd)
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
