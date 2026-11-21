#!/usr/bin/env python3
"""Benchmark commands against each other, each measured run holding the machine alone.

    python3 tools/bench.py [--runs N] NAME=COMMAND NAME=COMMAND [NAME=COMMAND...]

runs the arms in turn (A, B, A, B...) N times each (default 1). Each run is a measuring run (tools/machine_lock.py):
it waits for every run on the machine and holds it alone, only while it runs, so the builds, games and test runs that
queued meanwhile go in between two runs, and alternating spreads any drift in the machine over every arm. COMMAND is
a shell command line. Prints, per arm, the wall time of its passed run (of several: their median and range) and how
many failed; exits 1 when any run failed. One run gives the rough number the owner asks for (AGENTS.md); give --runs N
only when the owner asks for more runs. One run cannot tell apart two arms closer than the machine's run-to-run noise
(Windows installs have varied 801 to 863 s, about 7%), so do not report a smaller gap as a difference.
"""

from __future__ import annotations

import argparse
import statistics
import sys

import machine_lock


def arm(text: str) -> tuple[str, str]:
    name, sep, command = text.partition("=")
    if not sep or not name or not command:
        raise argparse.ArgumentTypeError(f"{text!r} is not NAME=COMMAND")
    return name, command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=1, metavar="N", help="runs per arm (default 1)")
    parser.add_argument("arms", nargs="+", type=arm, metavar="NAME=COMMAND")
    args = parser.parse_args(argv)
    if len(args.arms) < 2 or len({name for name, _ in args.arms}) < len(args.arms):
        parser.error("give two or more arms with different names")
    times: dict[str, list[float]] = {name: [] for name, _ in args.arms}
    failed = dict.fromkeys(times, 0)
    for round_ in range(1, args.runs + 1):
        for name, command in args.arms:
            status, seconds = machine_lock.run(["sh", "-c", command], "measure")
            print(f"bench: {name} run {round_}: {seconds:.2f} s, exit {status}", file=sys.stderr, flush=True)
            if status:
                failed[name] += 1
            else:
                times[name].append(seconds)
    for name, passed in times.items():
        if len(passed) > 1:
            spread = f"median {statistics.median(passed):.2f} s, range {min(passed):.2f}-{max(passed):.2f} s"
        else:
            spread = f"{passed[0]:.2f} s" if passed else "no passed run"
        print(f"{name}: {spread}, {failed[name]} of {args.runs} failed")
    return 1 if any(failed.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
