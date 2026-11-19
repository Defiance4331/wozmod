#!/usr/bin/env python3
"""Capture Warriors Orochi 2 Xbox 360 (USA), the layout reference, under the Xenia emulator: stage a run, launch it,
drive it to the first Shu battle and fire a strategy cut-in there.

    uv run python tools/xenia_wo2.py stage NAME        # work/xenia/runs/NAME: Xenia, portable mode, launch.sh
    uv run python tools/xenia_wo2.py launch NAME       # in a game slot; start it with Bash run_in_background
    uv run python tools/xenia_wo2.py battle NAME       # right after launch: waits for the intro; to Shu ch.1, Easy
    uv run python tools/xenia_wo2.py cutin NAME CLIP   # after battle: Guan Yu's Face Off cut-in, to evidence/CLIP.mkv
    uv run python tools/xenia_wo2.py peek NAME ADDR LEN | poke NAME ADDR HEX   # guest memory, big-endian

Drive the run by hand with `game_harness.py key|capture|video|stop --root work/xenia/runs/NAME`; `screen`, `peek` and
`poke` there are MOZ's and do not reach Xenia. Keys (launch.sh binds them): A Return, B b, Start x, the d-pad arrows;
the sticks w a s d, attack l, switch officer (RT) e. Xenia renders at 1280x720, the layout's own space. The ISO comes
from WOZMOD_WO2_X360_ISO in work/harness.toml ([tests]) or --iso; Xenia from the extracted AppImage, --xenia DIR
(default work/xenia/ext/squashfs-root). No profile and no save are needed. The run ends on `game_harness.py stop`, or
after --timeout seconds (the harness caps it at 3600): give the launching Bash call a longer timeout than that.

Known gaps: after the intro, battle presses keys on a clock, with no screen to wait on (Xenia has no harness
`screen`). Under load a skip can land in a loading screen and leave the run at the briefing map (run 008): press Up,
Return, then x (hold 0.3 s) by hand, then e once for Guan Yu. The battle can end by itself after a minute or several
(straight to the save prompt), likely a defeat of the low-health active officer; nothing here holds the officer's
life.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import game_harness as harness
from checkout import inputs, work

RUNS = work() / "xenia/runs"
HOST_BASE = 0x100000000  # Xenia maps guest address G at host G + 4 GiB
# The per-officer battle records, as MOZ's (stride 0x28, camp order: in Shu ch.1 slot 0 Liu Bei, 1 Sun Shang Xiang,
# 2 Guan Yu): KO count u32 at +0, officers defeated u16 at +4, the strategy's once-flag byte at +0xB (1 once it fired).
# Measured in the Xenia runs of 2026-11-14 (work/xenia/runs/002-strategy, 003-strategy).
MEMBERS, STRIDE, ONCE = 0x8257B3F0, 0x28, 0xB
GUAN_YU = 2  # Face Off: fires on the KO that reaches 300
# From the battle start to the nearest enemies: (stick key, seconds held), attacking after each (run 002-strategy).
WALK = [("w", 2), ("d", 2), ("d", 2), ("s", 2), ("s", 2), ("s", 2), ("d", 2), ("d", 1.5), ("d", 1.5), ("w", 1),
        ("w", 1.5)]
MOVIE_OPEN = b"DiscImageDevice::ResolvePath(\\data\\movie)"  # Xenia's log line as the intro movie opens
INTRO_SKIP = 8  # seconds into the intro before Start skips it, as run 005-wrap pressed it (inference: 48 s from launch)
LAUNCH = """#!/bin/bash
export SDL_VIDEODRIVER=x11
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
export LD_LIBRARY_PATH="$GAME_DIR/usr/lib${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"
cd "$GAME_DIR"
exec "$GAME_DIR/usr/bin/xenia_canary" \\
  --storage_root="$GAME_DIR" --gpu=vulkan --hid=keyboard \\
  --keyboard_mode=1 --keyboard_user_index=0 \\
  --keybind_a=0x0D --keybind_b=0x42 --keybind_start=X \\
  --keybind_dpad_up=0x26 --keybind_dpad_down=0x28 \\
  --keybind_dpad_left=0x25 --keybind_dpad_right=0x27 \\
  --fullscreen=true --draw_resolution_scale_x=1 --draw_resolution_scale_y=1 \\
  {iso}
"""


def stage(root: Path, xenia: Path, iso: Path) -> None:
    """The run directory: a reflinked copy of Xenia's usr/ (it writes caches beside itself), portable mode, launch.sh."""
    if not (xenia / "usr/bin/xenia_canary").is_file():
        raise ValueError(f"no usr/bin/xenia_canary in {xenia}: extract the AppImage there "
                         "(xenia_canary_linux.AppImage --appimage-extract)")
    iso = iso.resolve(strict=True)
    (root / "game").mkdir(parents=True)  # refuses a run that exists: runs are single-use
    subprocess.run(["cp", "-a", "--reflink=auto", str(xenia / "usr"), str(root / "game")], check=True)
    (root / "game/portable.txt").touch()
    (root / "launch.sh").write_text(LAUNCH.format(iso=shlex.quote(str(iso))), encoding="utf-8")
    (root / "launch.sh").chmod(0o755)


def ask(root: Path, request: dict) -> None:
    """One harness request; raises when the harness refuses it (a run that ended says so)."""
    reply = harness.send(root, request, 60)
    if not reply.get("ok"):
        raise RuntimeError(f"{request['action']}: {reply.get('error', reply)}")


def keys(root: Path, *names: str, hold: float = 0.08, gap: float = 0.3) -> None:
    ask(root, {"action": "keys", "keys": list(names), "hold": hold, "gap": gap})


def wait_intro(log: Path, seconds: float = 300) -> None:
    """Until Xenia's log shows the game opening its movie folder: the intro starts, where the key route begins. The
    blind route needs a known start: begun on the title screen instead, it ended in the options (run 007)."""
    deadline = time.monotonic() + seconds
    while not (log.is_file() and MOVIE_OPEN in log.read_bytes()):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"no intro after {seconds:.0f} s ({log}): if the log ends before 'ALSA initialized', "
                               "the emulator hung at boot (run 006 did once); stop the run and launch a fresh one")
        time.sleep(1)


def battle(root: Path) -> None:
    """From the intro movie to the start of Shu chapter 1 on Easy (about 2 minutes), as run 005-wrap went, then
    switch from Liu Bei, who starts in play, to Guan Yu (the switch is relative: press it once only)."""
    wait_intro(root / "game/usr/bin/xenia.log")
    time.sleep(INTRO_SKIP)
    for step in [("x x x", 0.3, 1, 3), ("Return", 0.08, 0.3, 2), ("Return", 0.08, 0.3, 3),  # main menu, Story, Shu
                 ("Down Down", 0.08, 0.5, 0), ("Return", 0.08, 0.3, 3), ("Return", 0.08, 0.3, 3),  # the camp: sortie
                 ("Left", 0.08, 0.3, 0), ("Return", 0.08, 0.3, 30),  # Easy; the briefing loads
                 ("b", 0.08, 0.3, 2), ("b", 0.08, 0.3, 20), ("b", 0.08, 0.3, 4), ("b", 0.08, 0.3, 12),  # skip scenes
                 ("Up", 0.08, 0.3, 0), ("Return", 0.08, 0.3, 20), ("x", 0.3, 0.3, 8),  # start; the opening plays
                 ("e", 0.3, 0.3, 1)]:  # Guan Yu
        names, hold, gap, after = step
        keys(root, *names.split(), hold=hold, gap=gap)
        time.sleep(after)


def xenia_pid(root: Path, procs: Path = Path("/proc")) -> int:
    """This run's emulator: the xenia_canary whose storage root is the run's game folder (two runs may be up)."""
    want = f"--storage_root={root / 'game'}".encode()
    for proc in procs.iterdir():
        try:
            if proc.name.isdigit() and want in (proc / "cmdline").read_bytes().split(b"\0"):
                return int(proc.name)
        except OSError:  # gone, or not ours
            continue
    raise ValueError(f"no xenia_canary running for {root}")


def peek(root: Path, address: int, length: int) -> bytes:
    with open(f"/proc/{xenia_pid(root)}/mem", "rb", buffering=0) as mem:
        return os.pread(mem.fileno(), length, HOST_BASE + address)


def poke(root: Path, address: int, data: bytes) -> None:
    with open(f"/proc/{xenia_pid(root)}/mem", "r+b", buffering=0) as mem:
        os.pwrite(mem.fileno(), data, HOST_BASE + address)


def cutin(root: Path, clip: str, seconds: int) -> bool:
    """With Guan Yu in play (battle leaves him there), set his KOs to 299 and clear his once-flag, record ``seconds`` of video and attack until the
    flag sets or the recording ends: along the start's path toward the enemy, then where the path ends. True once
    the cut-in fired."""
    member = MEMBERS + STRIDE * GUAN_YU
    poke(root, member, (299).to_bytes(4, "big"))
    poke(root, member + ONCE, b"\0")
    ask(root, {"action": "video", "name": clip, "seconds": seconds})
    deadline = time.monotonic() + seconds
    steps = iter(WALK)
    while time.monotonic() < deadline:
        if (step := next(steps, None)) is not None:
            keys(root, step[0], hold=step[1])
        keys(root, "l", "l", "l", hold=0.1)
        if peek(root, member + ONCE, 1) == b"\1":
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_subparsers(dest="action", required=True)
    named = argparse.ArgumentParser(add_help=False)
    named.add_argument("name", help="the run, under work/xenia/runs")
    staged = actions.add_parser("stage", parents=[named], help="make work/xenia/runs/NAME")
    staged.add_argument("--xenia", type=Path, default=work() / "xenia/ext/squashfs-root",
                        help="the extracted Xenia Canary AppImage (holds usr/bin/xenia_canary)")
    staged.add_argument("--iso", type=Path, help="the WO2 X360 USA disc image (default: WOZMOD_WO2_X360_ISO)")
    launched = actions.add_parser("launch", parents=[named], help="run it in a game slot until stop or --timeout")
    launched.add_argument("--timeout", type=int, default=3600, help="seconds (the harness caps it at 3600)")
    actions.add_parser("battle", parents=[named], help="from the intro to the start of Shu ch.1 on Easy")
    fired = actions.add_parser("cutin", parents=[named], help="fire Guan Yu's Face Off cut-in, recorded to evidence/CLIP.mkv")
    fired.add_argument("clip", help="the recording's name")
    fired.add_argument("--seconds", type=int, default=30, help="the recording's length")
    peeked = actions.add_parser("peek", parents=[named], help="print LEN guest bytes at ADDR as hex")
    peeked.add_argument("address", type=lambda v: int(v, 16), metavar="ADDR")
    peeked.add_argument("length", type=int, metavar="LEN")
    poked = actions.add_parser("poke", parents=[named], help="write HEX bytes at guest ADDR")
    poked.add_argument("address", type=lambda v: int(v, 16), metavar="ADDR")
    poked.add_argument("data", type=bytes.fromhex, metavar="HEX")
    args = parser.parse_args(argv)
    root = RUNS / args.name
    try:
        if args.action == "stage":
            iso = args.iso or inputs().get("WOZMOD_WO2_X360_ISO")
            if not iso:
                raise ValueError("no WO2 X360 disc: give --iso or set WOZMOD_WO2_X360_ISO in work/harness.toml")
            stage(root, args.xenia, Path(iso))
            print(f"staged {root}\nnext: uv run python tools/xenia_wo2.py launch {args.name}  (in the background)")
        elif args.action == "launch":
            return subprocess.call([sys.executable, str(Path(harness.__file__).resolve()), "run", "--root", str(root),
                                    "--slot", "--target", harness.XENIA, "--game", str(root / "game"),
                                    "--timeout", str(args.timeout)])
        elif args.action == "battle":
            battle(root)
        elif args.action == "cutin":
            fired_ok = cutin(root, args.clip, args.seconds)
            print(f"{'fired' if fired_ok else 'not fired'}: {root / 'evidence' / args.clip}.mkv")
            return 0 if fired_ok else 1
        elif args.action == "peek":
            print(peek(root, args.address, args.length).hex())
        else:
            poke(root, args.address, args.data)
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.CalledProcessError) as exc:
        print(f"xenia_wo2: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
