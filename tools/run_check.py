#!/usr/bin/env python3
"""Stage a run, launch the game, run a check spec, stop, and print the result: one command.

    uv run python tools/run_check.py --install work/install [--dll FILE] [--art DIR] [--save NAME]
        [--only SCREEN,...] [SPEC]

Without --dll it runs the install's d3d9.dll when that is a --test build, and otherwise builds this checkout's runtime
as one (about 4 s), since a release build answers no route or check.

With --to SCREEN [--stage N] it routes the game there instead and leaves it running for hand commands
(`game_harness.py ACTION --root RUN`); `game_harness.py stop --root RUN` ends it, or its 30-minute limit does.

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
import tempfile
import time
from datetime import datetime
from pathlib import Path

import contact_sheet
import game_harness as harness
import prepare_run
import screen_checks
from wozmod.runtime.build import build_dll

DEFAULT_SPEC = prepare_run.REPO / "data/checks/layout.toml"
START_WAIT = 300  # seconds from taking a game slot until the game answers the test mailbox
SLOT_WAIT = 1800  # seconds a launch waits for one of the two game slots
SLOT_REPORT = 30  # seconds between the waiting launch's reports of who holds the slots
IDLE = 900  # a game left up with --to stops after this long with no command
STOP_WAIT = 60  # seconds for the supervisor to end after stop
RETRIES = 2  # fresh runs after one that ended before the game started


def launch(root: Path, pad: str | None = None, size: str | None = None, video: bool = False,
           idle: float | None = None, unplugged: bool = False, route_keys: bool = False,
           spare: str | None = None) -> subprocess.Popen:
    """The supervisor, in a game slot, in its own process group so it can be ended whole; ``pad``: a virtual pad of
    that model (harness PAD_MODELS), which routes press unless ``route_keys``, plugged in after the start when
    ``unplugged`` (game_harness.py plug on); ``spare``: a second XInput identity, linked by reenumerate; ``size``:
    render at WxH instead of 1080p upscaled; ``video``: output 1080p without FSR, so the video command keeps the game
    at full speed."""
    # low CPU and disk priority, so the user's own work stays responsive
    command = ["nice", "-n", "10", "ionice", "-c2", "-n7",
               sys.executable, str(Path(harness.__file__).resolve()), "run", "--root", str(root), "--slot",
               "--target", harness.TARGET, "--game", str(root / "game"), "--authorize-launch", "--timeout", "1800",
               *(["--pad", pad, "--route-input", "keys" if route_keys else "pad"] if pad else []),
               *(["--spare", spare] if pad and spare else []),
               *(["--unplugged"] if pad and unplugged else []), *(["--size", size] if size else []),
               *(["--video"] if video else []), *(["--idle", str(idle)] if idle else [])]
    with (root / "run.log").open("w") as log:
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)


def wait_slot(root: Path, proc: subprocess.Popen, seconds: float = SLOT_WAIT, report: float = SLOT_REPORT) -> bool:
    """True once the run holds a game slot; meanwhile say every ``report`` seconds who holds them. False when the run
    ended first or the wait ran out."""
    deadline, reported = time.monotonic() + seconds, time.monotonic()
    while not (root / "slot.json").is_file():
        if proc.poll() is not None or time.monotonic() >= deadline:
            return False
        if time.monotonic() - reported >= report:
            print("run_check: waiting for a game slot, held by " + (", ".join(harness.slot_holders()) or "nobody now"),
                  file=sys.stderr)
            reported = time.monotonic()
        time.sleep(0.5)
    return True


def wait_up(root: Path, proc: subprocess.Popen, seconds: float = START_WAIT) -> bool:
    """True once the game answers a screen request (a frame ran); False when the run ended first or the wait ran
    out. The clock starts once the run holds a game slot. Raises at once on an error waiting cannot fix, such as a run
    path through a symlink."""
    if not wait_slot(root, proc):
        return False
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


def test_build(install: Path, into: Path) -> Path:
    """This checkout's runtime as a --test build, from the install's stock OROCHI_Z.exe. No --log: it writes on the game
    thread, which timing and video runs must not pay; only tour reads it, so pass a --log build with --dll for that."""
    return build_dll(install / "OROCHI_Z.exe", into / "d3d9.dll", test=True)


def is_test_build(dll: Path) -> bool:
    return harness.TEST_MAGIC in dll.read_bytes()  # its routes and checks talk to the test mailbox


def run_check(args: argparse.Namespace, stage=prepare_run.prepare, start=launch, build=test_build) -> int:
    if args.dll and not is_test_build(args.dll):
        print(f"run_check: {args.dll} is not a --test build; omit --dll to build this checkout's runtime as one",
              file=sys.stderr)
        return 1
    to, spec, only = getattr(args, "to", None), None, None
    if to:
        harness.route_plan("boot", to)  # an unknown screen fails before the minute of staging and boot
    else:
        spec = args.spec.resolve(strict=True)
        names = {sc["name"] for sc in screen_checks.load_spec(spec)}
        only = args.only.split(",") if args.only else None
        if only and set(only) - names:  # before the minute of staging and boot
            print(f"run_check: no screen {', '.join(sorted(set(only) - names))} in {spec.name}", file=sys.stderr)
            return 1
    prepare_run.WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=prepare_run.WORK, prefix=".test-dll-") as built:  # in work/: staging reflinks
        dll = args.dll
        if dll is None and not is_test_build(args.install / "d3d9.dll"):
            dll = build(args.install.resolve(strict=True), Path(built))
            print(f"run_check: {args.install / 'd3d9.dll'} is a release build; running this checkout's runtime as a "
                  "--test build", file=sys.stderr)
        return staged_run(args, dll, to, spec, only, stage, start)


def staged_run(args: argparse.Namespace, dll: Path | None, to: str | None, spec: Path | None, only: list[str] | None,
               stage, start) -> int:
    """Stage, boot, then check the spec or route to ``to``, retrying a run that ended before the game started."""
    phases: dict[str, float] = {"stage": 0.0, "boot": 0.0, "check": 0.0, "stop": 0.0}
    began, result, record = time.monotonic(), 1, None
    for attempt in range(RETRIES + 1):
        t = time.monotonic()
        size = getattr(args, "size", None)
        root = stage(run_name(args.name, attempt), args.install.resolve(strict=True), dll, args.save,
                     settings=args.setting + ([f"display.size={size}"] if size else []), replace=args.file,
                     art=args.art and args.art.resolve(strict=True))
        phases["stage"] += time.monotonic() - t
        t, proc = time.monotonic(), start(root, getattr(args, "pad", None), size=size,
                                         video=getattr(args, "video", False), idle=IDLE if to else None,
                                         unplugged=getattr(args, "unplugged", False),
                                         route_keys=getattr(args, "route_keys", False),
                                         spare=getattr(args, "spare", None))
        try:
            up = wait_up(root, proc)
            phases["boot"] += time.monotonic() - t
            if up:
                t = time.monotonic()
                try:
                    result = harness.route(root, to, args.stage, getattr(args, "movie", None),
                                           getattr(args, "officer", None)) if to else harness.check(root, spec, only)
                except (OSError, ValueError, RuntimeError) as exc:
                    print(f"run_check: check failed: {exc}", file=sys.stderr)
                phases["check"] += time.monotonic() - t
        finally:
            t = time.monotonic()
            if not (to and up and result == 0):  # a game routed with --to stays up for hand commands
                stop(root, proc)
            phases["stop"] += time.monotonic() - t
        record = harness.exit_info(root)
        # retry only a run that ended before the game started: no frame ever answered, the reason is unknown, and the
        # game left no exit status (it can write one after the harness recorded a compositor that ended first)
        if up or record is None or record["reason"] != "unknown" or (root / "evidence/game-exit.json").is_file():
            break
        print(f"run_check: {root.name} ended before the game started (see evidence/harness.log); "
              + ("retrying" if attempt < RETRIES else "giving up"), file=sys.stderr)
    if to and up and result == 0:
        print(f"run: {root.name}\nphases: stage {phases['stage']:.1f} s, boot {phases['boot']:.1f} s, "
              f"route {phases['check']:.1f} s\nup at {to}: game_harness.py ACTION --root {root.name}; stop ends it")
        return 0
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
    parser.add_argument("--dll", type=Path, help="use this --test build of d3d9.dll instead of "
                        "the install's (default: the install's when it is one, else this checkout's runtime built as one)")
    parser.add_argument("--save", default="full-unlock",
                        help="fixture under work/saves (default full-unlock), a save.dat path, or none for a fresh profile")
    parser.add_argument("--file", action="append", default=[], metavar="INSTALLED=PATH",
                        help="swap one installed file for a working build, such as wozmod/table.bin=PATH")
    parser.add_argument("--art", type=Path, metavar="DIR",
                        help="swap all of the install's menu art for a fresh art build's: DIR as `python -m "
                             "wozmod.art.build --output-dir DIR --hires` or `python -m wozmod.art compose` wrote it")
    parser.add_argument("--entries-dir", type=Path, metavar="DIR",
                        help="swap in each menu-art entry DIR holds (<ordinal>.bin), as `python -m wozmod.art.build "
                             "--entries N... --entries-dir DIR` writes them in seconds")
    parser.add_argument("--setting", action="append", default=[], metavar="SECTION.KEY=VALUE",
                        help="a mod setting saved before the run, such as display.fps=120")
    parser.add_argument("--pad", nargs="?", const="x360", choices=harness.PAD_MODELS,
                        help="give the game a virtual pad (default x360; steam, ds4, dualsense, ...), which routes "
                             "press, so captures show the pad's prompts")
    parser.add_argument("--unplugged", action="store_true",
                        help="with --pad: the pad is plugged in after the start (game_harness.py plug on)")
    parser.add_argument("--spare", choices=[model for model in harness.PAD_MODELS if model in harness.XINPUT_PADS],
                        help="with --pad: a second XInput identity; game_harness.py reenumerate links it instead")
    parser.add_argument("--route-keys", action="store_true",
                        help="with --pad: routes press the keyboard, for a pad the game may not bind")
    parser.add_argument("--only", metavar="SCREEN,...", help="run only these screens of the spec")
    parser.add_argument("--video", action="store_true", help="with --to: output 1080p without FSR, so "
                        "`game_harness.py video NAME --seconds N` records at full speed")
    parser.add_argument("--size", metavar="WxH", help="render the game at this listed display size (saved as the "
                        "player's choice) instead of 1080p upscaled")
    parser.add_argument("--name", default="check", help="label in the run directory name")
    parser.add_argument("--to", metavar="SCREEN", help="instead of a spec: route to this screen (game_harness.py "
                        "route) and leave the game running")
    parser.add_argument("--stage", type=int, help="with --to: the story chapter or Dream stage the route picks")
    parser.add_argument("--movie", type=int, help="with --to gallery-movie-play: the movie NNN to play")
    parser.add_argument("--officer", action="append", default=[], metavar="NAME",
                        help="with --to a Free Mode screen: an officer for the team (repeat, up to 3)")
    args = parser.parse_args(argv)
    if args.entries_dir:  # each rebuilt entry replaces the installed one, as --file would
        built = sorted(args.entries_dir.resolve(strict=True).glob("*.bin"))
        if not built:
            parser.error(f"--entries-dir {args.entries_dir} holds no <ordinal>.bin")
        args.file += [f"wozmod/entries/{entry.name}={entry}" for entry in built]
    try:
        return run_check(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"run_check: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
