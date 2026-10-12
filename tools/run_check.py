#!/usr/bin/env python3
"""Stage a run, launch the game, run a check spec, stop, and print the result: one command.

    uv run python tools/run_check.py --install work/fixups2/install [--dll FILE] [--save NAME]
        [--only SCREEN,...] [SPEC]

SPEC defaults to data/checks/layout.toml. Running this launches the game under the machine-wide lock:
get the user's permission for the current task first (AGENTS.md).
Exit 1 on any FAIL row, a run that did not end by our stop, or a game that never started.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import game_harness as harness
import prepare_run
import screen_checks

LOCK = harness.REPO / "work/.game-run.lock"
DEFAULT_SPEC = harness.REPO / "data/checks/layout.toml"
START_WAIT = 300  # seconds from launch (lock wait included) until the game answers the test mailbox
STOP_WAIT = 60  # seconds for the supervisor to end after stop
RETRIES = 2  # fresh runs after one that ended before the game started


def launch(root: Path) -> subprocess.Popen:
    """The supervisor, under the lock, in its own process group so it can be ended whole."""
    command = ["flock", str(LOCK), sys.executable, str(Path(harness.__file__).resolve()), "run", "--root", str(root),
               "--target", harness.TARGET, "--game", str(root / "game"), "--authorize-launch", "--timeout", "1800"]
    with (root / "run.log").open("w") as log:
        return subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def wait_up(root: Path, proc: subprocess.Popen, seconds: float = START_WAIT) -> bool:
    """True once the game answers a screen request (a frame ran); False when the supervisor ended first
    or the wait ran out."""
    deadline = time.monotonic() + seconds
    while proc.poll() is None and time.monotonic() < deadline:
        try:
            if harness.send(root, {"action": "screen"}, 30)["ok"]:
                return True
        except (ValueError, TimeoutError, OSError):  # not ready yet, or the run ended
            pass
        time.sleep(0.5)
    return False


def stop(root: Path, proc: subprocess.Popen) -> None:
    """End the game with the stop command and wait for the supervisor; end its group if it will not go."""
    if proc.poll() is None:
        try:
            harness.send(root, {"action": "stop"}, 30)
        except (ValueError, TimeoutError, OSError):
            pass
        try:
            proc.wait(STOP_WAIT)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()


def exit_reason(root: Path) -> dict | None:
    path = root / "evidence/exit.json"
    return json.loads(path.read_text()) if path.is_file() else None


def run_name(label: str, attempt: int) -> str:
    base = datetime.now().strftime("%Y-%m-%d_%H%M-") + label + (f"-r{attempt}" if attempt else "")
    name, n = base, 1
    while (prepare_run.WORK / "runtime-tests" / name).exists():
        n += 1
        name = f"{base}-{n}"
    return name


def run_check(args: argparse.Namespace, stage=prepare_run.prepare, start=launch) -> int:
    spec = args.spec.resolve(strict=True)
    names = {sc["name"] for sc in screen_checks.load_spec(spec)}
    only = args.only.split(",") if args.only else None
    if only and set(only) - names:  # before the minute of staging and boot
        print(f"run_check: no screen {', '.join(sorted(set(only) - names))} in {spec.name}", file=sys.stderr)
        return 1
    phases: dict[str, float] = {"stage": 0.0, "boot": 0.0, "check": 0.0, "stop": 0.0}
    began, result, record = time.monotonic(), 1, None
    for attempt in range(RETRIES + 1):
        t = time.monotonic()
        root = stage(run_name(args.name, attempt), args.install.resolve(strict=True), args.dll, args.save)
        phases["stage"] += time.monotonic() - t
        t, proc = time.monotonic(), start(root)
        try:
            up = wait_up(root, proc)
            phases["boot"] += time.monotonic() - t
            if up:
                t = time.monotonic()
                try:
                    result = harness.check(root, spec, only)
                except (OSError, ValueError, RuntimeError) as exc:
                    print(f"run_check: check failed: {exc}", file=sys.stderr)
                phases["check"] += time.monotonic() - t
        finally:
            t = time.monotonic()
            stop(root, proc)
            phases["stop"] += time.monotonic() - t
        record = exit_reason(root)
        # retry only a run that ended before the game started: reason unknown, and no frame ever answered
        if up or record is None or record["reason"] != "unknown" or (root / "evidence/game-exit.json").is_file():
            break
        print(f"run_check: {root.name} ended before the game started (see evidence/harness.log); "
              + ("retrying" if attempt < RETRIES else "giving up"), file=sys.stderr)
    reason = record["reason"] if record else "missing exit.json (see run.log)"
    phases["total"] = time.monotonic() - began
    print(f"run: {root.name}")
    print("phases: " + ", ".join(f"{k} {v:.1f} s" for k, v in phases.items()) + f"; starts {attempt + 1}")
    print(f"exit: {reason}" + (f" (game exit status {record['game_returncode']})" if record and "game_returncode" in record else ""))
    if (root / "evidence").is_dir():
        harness.write_json(root / "evidence/run-check.json", {"phases": phases, "reason": reason, "starts": attempt + 1})
    return 0 if up and result == 0 and reason == "stopped" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", type=Path, nargs="?", default=DEFAULT_SPEC, help="check file (default layout.toml)")
    parser.add_argument("--install", type=Path, required=True, help="directory holding d3d9.dll and wozmod/")
    parser.add_argument("--dll", type=Path, help="use this d3d9.dll instead of the install's")
    parser.add_argument("--save", default="full-unlock", help="fixture under work/saves (default full-unlock)")
    parser.add_argument("--only", metavar="SCREEN,...", help="run only these screens of the spec")
    parser.add_argument("--name", default="check", help="label in the run directory name")
    args = parser.parse_args(argv)
    try:
        return run_check(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"run_check: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
