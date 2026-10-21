#!/usr/bin/env python3
"""Stage a run, launch the game, run a check spec, stop, and print the result: one command.

    uv run python tools/run_check.py --install work/fixups2/install [--dll FILE] [--save NAME]
        [--only SCREEN,...] [SPEC]

SPEC defaults to data/checks/layout.toml. Running this launches the game under the machine-wide lock:
get the user's permission for the current task first (AGENTS.md).
It also writes evidence/contact-sheet.html: each capture beside its rows, for review.
Exit 1 on any FAIL row, a run that did not end by our stop, or a game that never started.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import contact_sheet
import game_harness as harness
import prepare_run
import screen_checks

DEFAULT_SPEC = prepare_run.REPO / "data/checks/layout.toml"
START_WAIT = 300  # seconds from launch (slot wait included) until the game answers the test mailbox
STOP_WAIT = 60  # seconds for the supervisor to end after stop
RETRIES = 2  # fresh runs after one that ended before the game started


def launch(root: Path, pad: bool = False, size: str | None = None) -> subprocess.Popen:
    """The supervisor, in a game slot, in its own process group so it can be ended whole; ``pad``: routes press a
    virtual pad instead of the keyboard; ``size``: render at WxH instead of 1080p upscaled."""
    command = [sys.executable, str(Path(harness.__file__).resolve()), "run", "--root", str(root), "--slot",
               "--target", harness.TARGET, "--game", str(root / "game"), "--authorize-launch", "--timeout", "1800",
               *(["--pad", "--route-input", "pad"] if pad else []), *(["--size", size] if size else [])]
    with (root / "run.log").open("w") as log:
        return subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def wait_up(root: Path, proc: subprocess.Popen, seconds: float = START_WAIT) -> bool:
    """True once the game answers a screen request (a frame ran); False when the run ended first or the wait ran
    out. Raises at once on an error waiting cannot fix, such as a run path through a symlink."""
    deadline = time.monotonic() + seconds
    while proc.poll() is None and time.monotonic() < deadline:
        try:
            if harness.send(root, {"action": "screen"}, 30)["ok"]:
                return True
        except (harness.NotReady, TimeoutError, FileNotFoundError):  # still starting
            pass
        except ValueError:
            if harness.ended(root) is None:
                raise  # a bad run path or a refused request: waiting will not fix it
            return False
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
        size = getattr(args, "size", None)
        root = stage(run_name(args.name, attempt), args.install.resolve(strict=True), args.dll, args.save,
                     settings=args.setting + ([f"display.size={size}"] if size else []), replace=args.file)
        phases["stage"] += time.monotonic() - t
        t, proc = time.monotonic(), start(root, getattr(args, "pad", False), *([size] if size else []))
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
        record = harness.exit_info(root)
        # retry only a run that ended before the game started: no frame ever answered, the reason is unknown, and the
        # game left no exit status (it can write one after the harness recorded a compositor that ended first)
        if up or record is None or record["reason"] != "unknown" or (root / "evidence/game-exit.json").is_file():
            break
        print(f"run_check: {root.name} ended before the game started (see evidence/harness.log); "
              + ("retrying" if attempt < RETRIES else "giving up"), file=sys.stderr)
    reason = record["reason"] if record else "missing exit.json (see run.log)"
    phases["total"] = time.monotonic() - began
    print(f"run: {root.name}")
    print("phases: " + ", ".join(f"{k} {v:.1f} s" for k, v in phases.items()) + f"; starts {attempt + 1}")
    print(f"exit: {harness.describe_exit(record) if record else reason}")
    if (root / "evidence").is_dir():
        harness.write_json(root / "evidence/run-check.json", {"phases": phases, "reason": reason, "starts": attempt + 1})
    if (root / "evidence/check-report.json").is_file():
        print(f"sheet: {contact_sheet.write(root / 'evidence')}")
    return 0 if up and result == 0 and reason == "stopped" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", type=Path, nargs="?", default=DEFAULT_SPEC, help="check file (default layout.toml)")
    parser.add_argument("--install", type=Path, required=True, help="directory holding d3d9.dll and wozmod/")
    parser.add_argument("--dll", type=Path, help="use this d3d9.dll instead of the install's")
    parser.add_argument("--save", default="full-unlock", help="fixture under work/saves (default full-unlock)")
    parser.add_argument("--file", action="append", default=[], metavar="INSTALLED=PATH",
                        help="swap one installed file for a working build, such as wozmod/table.bin=PATH")
    parser.add_argument("--setting", action="append", default=[], metavar="SECTION.KEY=VALUE",
                        help="a mod setting saved before the run, such as display.fps=120")
    parser.add_argument("--pad", action="store_true", help="routes press a pad, so captures show the pad's prompts")
    parser.add_argument("--only", metavar="SCREEN,...", help="run only these screens of the spec")
    parser.add_argument("--size", metavar="WxH", help="render the game at this listed display size (saved as the "
                        "player's choice) instead of 1080p upscaled")
    parser.add_argument("--name", default="check", help="label in the run directory name")
    args = parser.parse_args(argv)
    try:
        return run_check(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"run_check: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
