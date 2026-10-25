#!/usr/bin/env python3
"""Headless game control for the wozmod checks."""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import math
import os
import re
import resource
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from checkout import work
from prepare_run import local_path, verify_copy
from wozmod.formats import pe
from wozmod.runtime.english_runtime import DLL_BASE, decode_english, encode_english
from wozmod.runtime.layout import DEFINES


TARGET = "musou-orochi-z-pc-jp"
XENIA = "warriors-orochi-2-x360-usa"  # official X360 menu references, using a disposable emulator
MAX_KEYS = 100  # keys in one sequence
GAME_PROCESS = "OROCHI_Z.exe"  # /proc/<pid>/comm of the game under Proton
ADDRESS_LIMIT = 1 << 32  # the game is 32-bit
MAX_PEEK, MAX_POKE, MAX_PATTERN, MAX_HITS = 65536, 4096, 256, 64
FIND_CHUNK = 1 << 24  # bytes read at a time by find
# save.dat holds the game's state from 0x7FB650 at offset 8, so the resolution index (0x80152D, into
# the table at 0x6FDE2C) sits at 0x5EE5. At 0x25F24 is the file's byte sum, taken with that 20-byte field zeroed.
# The mailbox of a --test runtime build; english_runtime.c struct test_block (TEST_INPUT: its input[1024]).
TEST_MAGIC, TEST_VERSION = b"ENRT-TEST-BLOCK\0", DEFINES["TEST_VERSION"]
TEST_TEXTS, TEST_QUADS, TEST_SETS, TEST_INPUT = DEFINES["TEST_TEXTS"], DEFINES["TEST_QUADS"], DEFINES["TEST_SETS"], 1024
TEST_TEXT = struct.Struct(f"<2I3i{DEFINES['TEST_CHARS']}s")  # hook, caller, x, y, width, text
TEST_QUAD = struct.Struct("<4I4i")  # hook, caller, handle, name, x, y, width, height
TEST_HEADER = struct.Struct(f"<16s8I{TEST_INPUT}sI")  # magic ... result, input, sets
TEST_SNAPSHOT = TEST_HEADER.size + TEST_SETS * TEST_TEXT.size
TEST_RELOADS = TEST_SNAPSHOT + 12 + TEST_TEXTS * TEST_TEXT.size + TEST_QUADS * TEST_QUAD.size  # tables loaded
TEST_SIZE = TEST_RELOADS + 4
RELOAD_SECONDS = 5  # the runtime looks at table.bin about once a second
TEST_COMMAND_SECONDS = 5  # the game runs a command at its next frame
# Battle state the stock exe keeps at fixed addresses; see english_runtime.c.
STAGE, STAGE_DATA, SCRIPT_TABLE, SCRIPT_EVENTS = 0x801AA0, 0x9C8FD4, 0x9C919C, 0x9C91B0
# the game's units (stride 0xD0, used bit +0xBC & 1, actor pointer +0x14) and the actor's x, y (up), z at +0x40;
# unit 0 is the player (work/crowdperf/re-spawn.md)
UNITS, UNIT_SIZE, UNIT_COUNT, ACTOR_AT = 0x7DDC30, 0xD0, 255, 0x40
CHARACTERS, CHARACTER_SIZE, RIDING = 0x9DE420, 0x2200, 0x1944  # the players' characters; +0x1944: the horse ridden
TEAM_ACTIVE = 0x801B34  # player 0's active team member, 0-2
SPAWN_MAX = 220  # the general unit pool
SPAWN_INNER = 600  # the default nearest ring, in world units: in view, well inside the 7500 far cull
SPAWN_SPACING = 120  # runtime/troop_ring.h TROOP_SPACING
SPAWN_SQUAD = 13  # the game spawns whole squads: a spawn may place up to 12 troops more than asked
# shortcut: measured, not derived: in its first frames the game pushed freshly placed soldiers up to about 400 units
# from their ring spots (run crowd-check1, 2026-10-19); find the push in the unit update if a stage scatters farther
SPAWN_SCATTER = 500
# The virtual pad (run --pad): a uinput Xbox 360 pad, the device Steam Input and xpad present to
# Wine. Evdev codes: buttons, then (axis, value) presses for the triggers, D-pad and sticks.
PAD_BUTTONS = {"A": 0x130, "B": 0x131, "X": 0x133, "Y": 0x134, "LB": 0x136, "RB": 0x137,
               "BACK": 0x13A, "START": 0x13B, "GUIDE": 0x13C, "LS": 0x13D, "RS": 0x13E}
STICK = 32767
PAD_HIDDEN = "/dev/harness-pad"  # the pad node while unplugged: no input scan looks here
PAD_PRESSES = {"LT": (2, 255), "RT": (5, 255), "UP": (0x11, -1), "DOWN": (0x11, 1), "LEFT": (0x10, -1),
               "RIGHT": (0x10, 1), "L-UP": (1, -STICK), "L-DOWN": (1, STICK), "L-LEFT": (0, -STICK),
               "L-RIGHT": (0, STICK), "R-UP": (4, -STICK), "R-DOWN": (4, STICK), "R-LEFT": (3, -STICK),
               "R-RIGHT": (3, STICK)}
PAD_AXES = {0: (-32768, STICK), 1: (-32768, STICK), 3: (-32768, STICK), 4: (-32768, STICK),
            2: (0, 255), 5: (0, 255), 0x10: (-1, 1), 0x11: (-1, 1)}
# xpad pads by USB id and kernel name: the Microsoft pad, and a third-party XInput pad Wine names differently
PAD_MODELS = {"x360": (0x045E, 0x028E, b"Microsoft X-Box 360 pad"),
              "8bitdo": (0x2DC8, 0x3106, b"8BitDo Ultimate Wireless / Pro 2 Wired Controller")}


def bounded(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 3600:
        raise argparse.ArgumentTypeError("use a finite value greater than 0 and at most 3600")
    return number


def render_size_arg(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{3,4})x(\d{3,4})", value)
    if not match or not (640 <= int(match[1]) <= 5120 and 480 <= int(match[2]) <= 2160):
        raise argparse.ArgumentTypeError("use WxH, at most 5120x2160")
    return int(match[1]), int(match[2])


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".next")
    temporary.write_text(json.dumps(value) + "\n")
    temporary.replace(path)


X_INPUT = None  # (libX11, libXtst, display) kept open by the server, so only its first key input primes


def x_input() -> tuple:
    """The isolated Xwayland display for key input, opened and primed on first use. Use only inside headless Gamescope."""
    global X_INPUT
    if X_INPUT:
        return X_INPUT
    if not os.environ.get("GAMESCOPE_WAYLAND_DISPLAY"):
        raise ValueError("key input requires the isolated Gamescope child")
    x11 = ctypes.CDLL("libX11.so.6")
    xtst = ctypes.CDLL("libXtst.so.6")
    display_type = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = display_type
    x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
    x11.XStringToKeysym.restype = ctypes.c_ulong
    x11.XKeysymToKeycode.argtypes = [display_type, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_uint
    x11.XFlush.argtypes = [display_type]
    x11.XCloseDisplay.argtypes = [display_type]
    xtst.XTestFakeKeyEvent.argtypes = [display_type, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    xtst.XTestFakeMotionEvent.argtypes = [
        display_type, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong,
    ]
    display = x11.XOpenDisplay(None)
    if not display:
        raise ValueError("cannot open the isolated DISPLAY")
    # Prime Xwayland's libei connection before the keyboard events.
    if not xtst.XTestFakeMotionEvent(display, 0, 640, 360, 0):
        x11.XCloseDisplay(display)
        raise RuntimeError("XTEST pointer priming failed")
    x11.XFlush(display)
    time.sleep(1)
    X_INPUT = (x11, xtst, display)
    return X_INPUT


def tap_keys(names: list[str], hold: float, gap: float, settle: float | None = None) -> None:
    """Tap keys in order, gap seconds apart; wait ``settle`` seconds (default ``gap``) after the last release.

    The display stays open between calls (x_input), so a call after the first needs no priming second."""
    global X_INPUT
    x11, xtst, display = x_input()
    keys = []
    for name in names:  # resolve every key before sending any
        key = x11.XKeysymToKeycode(display, x11.XStringToKeysym(name.encode("ascii")))
        if not key:
            raise ValueError(f"unknown X keysym: {name}")
        keys.append(key)
    for n, key in enumerate(keys):
        try:
            if not xtst.XTestFakeKeyEvent(display, key, 1, 0):
                x11.XCloseDisplay(display)
                X_INPUT = None  # reopen and prime on the next call
                raise RuntimeError("XTEST key press failed")
            x11.XFlush(display)
            time.sleep(hold)
        finally:
            xtst.XTestFakeKeyEvent(display, key, 0, 0)
            x11.XFlush(display)
        time.sleep(gap if n < len(keys) - 1 or settle is None else settle)


def click_point(x: int, y: int) -> None:
    """Click the left button on the isolated Xwayland display."""
    if not os.environ.get("GAMESCOPE_WAYLAND_DISPLAY"):
        raise ValueError("click input requires the isolated Gamescope child")
    x11 = ctypes.CDLL("libX11.so.6")
    xtst = ctypes.CDLL("libXtst.so.6")
    display_type = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = display_type
    x11.XFlush.argtypes = [display_type]
    x11.XCloseDisplay.argtypes = [display_type]
    xtst.XTestFakeMotionEvent.argtypes = [
        display_type, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong,
    ]
    xtst.XTestFakeButtonEvent.argtypes = [display_type, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    display = x11.XOpenDisplay(None)
    if not display:
        raise ValueError("cannot open the isolated DISPLAY")
    try:
        # Prime Xwayland's libei connection, then position the pointer for the click.
        if not xtst.XTestFakeMotionEvent(display, 0, 640, 360, 0):
            raise RuntimeError("XTEST pointer priming failed")
        x11.XFlush(display)
        time.sleep(1)
        if not xtst.XTestFakeMotionEvent(display, 0, x, y, 0):
            raise RuntimeError("XTEST pointer positioning failed")
        x11.XFlush(display)
        time.sleep(0.05)
        try:
            if not xtst.XTestFakeButtonEvent(display, 1, 1, 0):
                raise RuntimeError("XTEST left-button press failed")
            x11.XFlush(display)
            time.sleep(0.08)
        finally:
            xtst.XTestFakeButtonEvent(display, 1, 0, 0)
            x11.XFlush(display)
        time.sleep(1)
    finally:
        x11.XCloseDisplay(display)


class VirtualPad:
    """A uinput XInput pad (PAD_MODELS), created outside the sandbox; its event node is bound in."""

    def __init__(self, model: str) -> None:
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, 0x40045564, 1)  # UI_SET_EVBIT EV_KEY
            fcntl.ioctl(self.fd, 0x40045564, 3)  # UI_SET_EVBIT EV_ABS
            for code in PAD_BUTTONS.values():
                fcntl.ioctl(self.fd, 0x40045565, code)  # UI_SET_KEYBIT
            for axis, (low, high) in PAD_AXES.items():
                fcntl.ioctl(self.fd, 0x40045567, axis)  # UI_SET_ABSBIT
                flat = 128 if high == STICK else 0
                fcntl.ioctl(self.fd, 0x401C5504, struct.pack("<H2x6i", axis, 0, low, high, 16 if flat else 0, flat, 0))
            vendor, product, name = PAD_MODELS[model]
            fcntl.ioctl(self.fd, 0x405C5503, struct.pack("<4H80sI", 3, vendor, product, 0x110, name, 0))  # UI_DEV_SETUP
            fcntl.ioctl(self.fd, 0x5501)  # UI_DEV_CREATE
            sysname = fcntl.ioctl(self.fd, 0x80405500 | 44, bytes(64)).split(b"\0")[0].decode()  # UI_GET_SYSNAME
            deadline = time.monotonic() + 5
            while not (nodes := list(Path(f"/sys/devices/virtual/input/{sysname}").glob("event*"))):
                if time.monotonic() >= deadline:
                    raise RuntimeError("the virtual pad got no event node")
                time.sleep(0.05)
            self.node = f"/dev/input/{nodes[0].name}"
            while not os.access(self.node, os.R_OK):  # udev gives the seat user access (uaccess)
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"no access to {self.node}; udev did not tag it for this seat")
                time.sleep(0.05)
        except BaseException:
            os.close(self.fd)
            raise

    def send(self, events: list[tuple[int, int, int]]) -> None:
        now = time.time()
        stamp = struct.pack("<qq", int(now), int(now % 1 * 1e6))
        os.write(self.fd, b"".join(stamp + struct.pack("<HHi", *event) for event in events + [(0, 0, 0)]))

    def tap(self, names: list[str], hold: float, gap: float) -> None:
        """Press each name (parts joined by + go down together) for hold seconds, then release it."""
        for name in names:
            down, up = [], []
            for part in name.split("+"):
                if part in PAD_BUTTONS:
                    down.append((1, PAD_BUTTONS[part], 1)), up.append((1, PAD_BUTTONS[part], 0))
                else:
                    axis, value = PAD_PRESSES[part]
                    down.append((3, axis, value)), up.append((3, axis, 0))
            try:
                self.send(down)
                time.sleep(hold)
            finally:
                self.send(up)
            time.sleep(gap)

    def close(self) -> None:
        fcntl.ioctl(self.fd, 0x5502)  # UI_DEV_DESTROY
        os.close(self.fd)


def pad_names(names: object) -> list[str]:
    if not isinstance(names, list) or not 0 < len(names) <= MAX_KEYS or not all(
        isinstance(name, str) and all(part in PAD_BUTTONS or part in PAD_PRESSES for part in name.split("+"))
        for name in names
    ):
        raise ValueError(f"use 1 to {MAX_KEYS} pad inputs from {', '.join([*PAD_BUTTONS, *PAD_PRESSES])}, "
                         "joined by + to press together")
    return names


def game_pid() -> int:
    """The game's pid in this PID namespace: the server only sees its own run's processes."""
    pids = [p.parent.name for p in Path("/proc").glob("[0-9]*/comm")
            if p.read_text(errors="replace").strip() == GAME_PROCESS]
    if len(pids) != 1:
        raise ValueError(f"expected one {GAME_PROCESS} process, found {len(pids)}")
    return int(pids[0])


def hex_bytes(value: object, limit: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2}){1,%d}" % limit, value):
        raise ValueError(f"use 1 to {limit} bytes as plain hex, such as 00000000")
    return bytes.fromhex(value)


def game_range(address: object, length: int) -> int:
    if not isinstance(address, int) or not 0 <= address <= ADDRESS_LIMIT - length:
        raise ValueError(f"address range must lie below {ADDRESS_LIMIT:#x}")
    return address


def find_bytes(pid: int, pattern: bytes) -> list[int]:
    """Addresses of pattern in the game's writable memory, up to MAX_HITS."""
    hits, chunk = [], FIND_CHUNK
    with open(f"/proc/{pid}/mem", "rb", buffering=0) as memory:
        for line in Path(f"/proc/{pid}/maps").read_text().splitlines():
            span, permissions = line.split()[:2]
            start, end = (int(x, 16) for x in span.split("-"))
            if not permissions.startswith("rw") or end > ADDRESS_LIMIT:
                continue
            for offset in range(start, end, chunk):  # chunks overlap by len(pattern) - 1
                try:
                    data = os.pread(memory.fileno(), min(chunk + len(pattern) - 1, end - offset), offset)
                except OSError:  # guard or vanished pages
                    break
                at = data.find(pattern)
                while at != -1 and at < chunk:
                    hits.append(offset + at)
                    if len(hits) == MAX_HITS:
                        return hits
                    at = data.find(pattern, at + 1)
    return hits


# The runtime counts its frames in the mailbox (test_block.frame, +24). The supervisor logs the count once a second to
# evidence/fps.jsonl, so a capture, a recording and the run's end can say whether the game kept its frame rate: under
# load a frame-locked game runs slow, and timing read from such a run is the machine's, not the mod's.
FRAME_COUNT = 24
SLOW = 0.95  # of the target frame rate: below it a second counts as slow


def target_fps(game: Path) -> int:
    """The frame rate the mod's display.fps setting asks for (the runtime's default 60 when unset)."""
    ini = game / "wozmod/settings.ini"
    text = ini.read_text(errors="replace") if ini.is_file() else ""
    section = re.search(r"(?ims)^\[display\](.*?)(?=^\[|\Z)", text)
    return 120 if section and re.search(r"(?im)^fps\s*=\s*120\s*$", section.group(1)) else 60


def run_target(root: Path) -> int:
    """The run's target frame rate, as its launch recorded it (60 for a run without one)."""
    session = root / "evidence/session.json"
    return json.loads(session.read_text()).get("target_fps", 60) if session.is_file() else 60


def fps_seconds(root: Path, start: float = 0, end: float = float("inf")) -> list[float]:
    """Frames per second for each logged second from start to end (wall-clock times)."""
    path = root / "evidence/fps.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
    return [(b["frames"] - a["frames"]) / (b["time"] - a["time"]) for a, b in zip(rows, rows[1:], strict=False)
            if start <= a["time"] and b["time"] <= end and b["time"] > a["time"] and b["frames"] >= a["frames"]]


def fps_summary(root: Path, target: int, start: float = 0, end: float = float("inf")) -> dict | None:
    """The median and lowest frame rate over a stretch's drawing seconds, its seconds below SLOW of the target, and its
    seconds with no frame (loading screens, which draw nothing); None unlogged."""
    seconds = fps_seconds(root, start, end)
    rates = sorted(rate for rate in seconds if rate > 0)
    if not seconds:
        return None
    return {"target": target, "median": round(rates[len(rates) // 2], 1) if rates else 0.0,
            "lowest": round(rates[0], 1) if rates else 0.0, "slow_seconds": sum(rate < SLOW * target for rate in rates),
            "loading_seconds": len(seconds) - len(rates), "seconds": len(seconds)}


def recordings_slow(root: Path, target: int) -> list[str]:
    """Recordings (record, video) whose stretch had a slow second: their timing is the machine's."""
    path = root / "evidence/actions.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
    slow = []
    for row in rows:
        request = row["request"]
        if request.get("action") in ("record", "video") and row["response"].get("ok"):
            summary = fps_summary(root, target, row["time"], row["time"] + float(request.get("seconds", 0)))
            if summary and summary["slow_seconds"]:
                slow.append(request["name"])
    return slow


def log_frames(root: Path, stop: "threading.Event") -> None:
    """The supervisor's sampler: the runtime's frame count once a second, until stop is set."""
    block = None
    with (root / "evidence/fps.jsonl").open("a") as log:
        while not stop.wait(1.0):
            try:
                with open(f"/proc/{game_pid()}/mem", "rb", buffering=0) as memory:
                    block = block or test_block(memory.fileno())
                    frames, = struct.unpack("<I", os.pread(memory.fileno(), 4, block + FRAME_COUNT))
            except (OSError, ValueError, RuntimeError):
                continue  # still loading, or the game is gone
            log.write(json.dumps({"time": time.time(), "frames": frames}) + "\n")
            log.flush()


def test_block(memory: int) -> int:
    """Address of a --test runtime's mailbox: its magic in the d3d9.dll image at its fixed base."""
    try:
        header = os.pread(memory, 4096, DLL_BASE)
    except OSError:
        header = b""
    if header[:2] != b"MZ":
        raise ValueError("no English runtime d3d9.dll at its base; is WINEDLLOVERRIDES=d3d9=n,b set?")
    for section in pe.sections(header):  # loaded, so each section spans its vsize at its rva
        at = os.pread(memory, section["vsize"], DLL_BASE + section["rva"]).find(TEST_MAGIC)
        if at >= 0:
            block = DLL_BASE + section["rva"] + at
            layout = struct.unpack("<2I", os.pread(memory, 8, block + 16))
            if layout != (TEST_VERSION, TEST_SIZE):
                raise ValueError(f"test mailbox version/size {layout} does not match this harness")
            return block
    raise ValueError("the game has no test mailbox; build the runtime with --test")


# What an op's own results mean, where they differ from the shared ones in test_command.
OP_REFUSALS = {
    10: {5: "no player in that slot", 6: "already riding, or held by or linked to another character",
         7: "this stage or this spot allows no horse; move and retry",
         8: "the game's horse call brought no horse (no free horse unit, or it refused here)",
         9: "the mount did not take"},
    11: {5: "no such team member, or that one is already active", 6: "that officer is down"},
}
KEEPS = {"life": 1, "clock": 2, "no-defeat": 4}  # the runtime's KEEP_ bits
SWITCH_SECONDS = 5  # the game's switch gate refuses for 20 ticks after a switch, and mid-attack


def switch(memory: int, block: int, member: int) -> dict:
    """Switch player 0 to team member 0-2 (test op 11), resending until the game's switch gate lets it through."""
    def active() -> int:
        return struct.unpack("<I", os.pread(memory, 4, TEAM_ACTIVE))[0]

    was, deadline = active(), time.monotonic() + SWITCH_SECONDS
    while active() != member:
        if time.monotonic() >= deadline:
            raise RuntimeError(f"the game kept refusing the switch for {SWITCH_SECONDS} s (mid-attack, or a scene); retry")
        test_command(memory, block, 11, member, busy=SWITCH_SECONDS)
        settle = time.monotonic() + 0.5
        while active() != member and time.monotonic() < settle:
            time.sleep(0.02)
    return {"officer": member, "was": was}


def player_at(memory: int) -> tuple[float, float, float]:
    actor, = struct.unpack("<I", os.pread(memory, 4, UNITS + 0x14))
    if not actor:
        raise RuntimeError("the player has no unit; is a battle loaded?")
    return struct.unpack("<3f", os.pread(memory, 12, actor + ACTOR_AT))


def teleport(memory: int, block: int, x: float, z: float, facing: float, relative: bool) -> dict:
    """Put player 0, with its horse, at world x, z (or that far from where it stands) facing a heading in radians
    (test op 12), and read back where the game set it down (y from the ground)."""
    if relative:
        here = player_at(memory)
        x, z = here[0] + x, here[2] + z
    test_command(memory, block, 12, 0, struct.pack("<3f", x, z, facing), busy=10)
    return {"at": [round(v, 1) for v in struct.unpack("<3f", os.pread(memory, 12, block + 48))]}


def keep(memory: int, block: int, names: list[str]) -> dict:
    """Hold battle state every frame until the battle ends (test op 13): the player's life, the clock, no defeat."""
    unknown = set(names) - set(KEEPS)
    if unknown:
        raise ValueError(f"keep takes {', '.join(KEEPS)} (none: stop keeping), not {', '.join(sorted(unknown))}")
    test_command(memory, block, 13, sum(KEEPS[n] for n in set(names)))
    return {"keep": sorted(set(names))}


def mount(memory: int, block: int, slot: int) -> dict:
    """Put player slot on its horse (test op 10), and read back the horse it rides."""
    if slot not in (0, 1):
        raise ValueError("mount takes player slot 0 or 1")
    test_command(memory, block, 10, slot, busy=10)
    horse, = struct.unpack("<I", os.pread(memory, 4, CHARACTERS + CHARACTER_SIZE * slot + RIDING))
    return {"mounted": slot, "horse": hex(horse)}


def test_command(memory: int, block: int, op: int, arg: int = 0, data: bytes = b"", busy: float = 0) -> None:
    """Run one mailbox op on the game thread, at the game's next frame, retrying result 4 for busy seconds."""
    if len(data) >= TEST_INPUT:
        raise ValueError(f"text must be under {TEST_INPUT} bytes in CP932")
    busy_until = time.monotonic() + busy
    while True:
        command, done = struct.unpack("<2I", os.pread(memory, 8, block + 28))
        if command != done:
            raise RuntimeError("the game has not run the previous test command yet; is it loading?")
        os.pwrite(memory, struct.pack("<3I", op, arg, 0) + data.ljust(TEST_INPUT, b"\0"), block + 36)
        command = (command + 1) & 0xFFFFFFFF
        os.pwrite(memory, struct.pack("<I", command), block + 28)
        deadline = time.monotonic() + TEST_COMMAND_SECONDS
        while struct.unpack("<I", os.pread(memory, 4, block + 32))[0] != command:
            if time.monotonic() >= deadline:
                raise TimeoutError("the game ran no frame for the test command; it runs once the game pumps again")
            time.sleep(0.01)
        result = struct.unpack("<I", os.pread(memory, 4, block + 44))[0]
        if result != 4 or time.monotonic() >= busy_until:
            break
        time.sleep(0.2)
    if result:
        raise RuntimeError(f"the game refused test op {op}: " + (OP_REFUSALS.get(op, {}).get(result) or {
            1: "use window 1 or 2", 2: "unknown op, rebuild the runtime with this harness",
            3: "not in a battle, or the battle is already ending",
            4: "a script or scene is playing, or the event queue is full; retry",
            5: "no such sub-scene in this stage's event scene, or the game refused it" if op == 8 else
               "no such queued script in this stage; run scripts",
            6: "review texture or rectangle is unavailable; check the test-only review.bin",
            7: "an officer that sub-scene casts is not on the field (no model loaded); the stage would not start it now",
        }.get(result, f"result {result}")))


def troops_near(memory: int, radius: float) -> int:
    """Units in use, the player aside, whose actor stands within radius of the player's (x and z, the larger)."""
    table = os.pread(memory, UNIT_SIZE * UNIT_COUNT, UNITS)
    at = []
    for i in range(UNIT_COUNT):
        flags, = struct.unpack_from("<I", table, i * UNIT_SIZE + 0xBC)
        actor, = struct.unpack_from("<I", table, i * UNIT_SIZE + 0x14)
        at.append(struct.unpack("<3f", os.pread(memory, 12, actor + ACTOR_AT)) if flags & 1 and actor else None)
    me = at[0]
    if me is None:
        raise RuntimeError("the player has no unit; is a battle loaded?")
    return sum(1 for p in at[1:] if p and max(abs(p[0] - me[0]), abs(p[2] - me[2])) <= radius)


def ring_reach(count: int, inner: int) -> int:
    """How far out (x or z, the larger) the runtime's square rings (troop_ring.h) place count troops, plus a spacing
    for float rounding."""
    half = inner
    while count > (ring := int(8 * half / SPAWN_SPACING)):
        count, half = count - ring, half + SPAWN_SPACING
    return half + SPAWN_SPACING


def spawn(memory: int, block: int, count: int, inner: int) -> dict:
    """Spawn enemy squads around the player (test op 9), and count the troops near the player before and after."""
    if not 0 < count <= SPAWN_MAX or not 0 < inner < 0x10000:
        raise ValueError(f"spawn takes 1 to {SPAWN_MAX} troops and a nearest ring from 1 to 65535 units")
    reach = ring_reach(count + SPAWN_SQUAD - 1, inner) + SPAWN_SCATTER
    before = troops_near(memory, reach)
    test_command(memory, block, 9, count | inner << 16, busy=10)
    spawned, despawned = struct.unpack("<2I", os.pread(memory, 8, block + 48))
    return {"asked": count, "spawned": spawned, "despawned": despawned, "near_before": before,
            "near_after": troops_near(memory, reach), "within": reach}


def stage_scripts(memory: int) -> dict:
    """The loaded stage's event scripts and the dialog and notice records each shows."""
    def u32s(address: int, count: int = 1) -> tuple:
        return struct.unpack(f"<{count}I", os.pread(memory, 4 * count, address))

    if not u32s(STAGE_DATA)[0]:
        raise RuntimeError("not in a battle")
    (stage,), (base, count), (events,) = u32s(STAGE), u32s(SCRIPT_TABLE, 2), u32s(SCRIPT_EVENTS)
    scripts = []
    for i, (offset, at) in enumerate(zip(u32s(base + 12, count), u32s(events, count), strict=True)):
        flags, kind, setup, immediate, _, n = os.pread(memory, 6, base + offset)
        # event: opcode, operands; 0x26 spoken dialog (speaker, record), 0x25 battle notice (record, ...)
        rows = list(struct.iter_unpack("<8H", os.pread(memory, 16 * n, at)))
        scripts.append({"index": i, "type": kind, "fired": bool(flags & 1), "queued": not setup and not immediate,
                        "dialogs": [r[2] for r in rows if r[0] == 0x26],
                        "notices": [r[1] for r in rows if r[0] == 0x25]})
    return {"stage": stage, "scripts": scripts}


def english(raw: bytes) -> str:
    return decode_english(raw.split(b"\0")[0], "replace")


def screen(memory: int, block: int) -> dict:
    """Text and named textures the last frame drew, and the latest window texts set."""
    test_command(memory, block, 1)
    data = os.pread(memory, TEST_SIZE, block)
    now, sets = struct.unpack_from("<I", data, 24)[0], struct.unpack_from("<I", data, TEST_HEADER.size - 4)[0]
    frame, texts, quads = struct.unpack_from("<3I", data, TEST_SNAPSHOT)
    windows = []
    for n in range(max(0, sets - TEST_SETS), sets):
        hook, caller, which, when, _, raw = TEST_TEXT.unpack_from(data, TEST_HEADER.size + n % TEST_SETS * TEST_TEXT.size)
        windows.append({"set": f"window {which}" if hook == 6 else "message box", "frame": when,
                        "caller": f"{caller:#x}", "text": english(raw)})
    drawn = []
    for n in range(min(texts, TEST_TEXTS)):
        hook, caller, x, y, width, raw = TEST_TEXT.unpack_from(data, TEST_SNAPSHOT + 12 + n * TEST_TEXT.size)
        drawn.append({"x": x, "y": y, "width": width, "caller": f"{caller:#x}", "text": english(raw),
                      **({"hook": "caption"} if hook == 4 else {})})
    named, unnamed = {}, {}
    for n in range(min(quads, TEST_QUADS)):
        at = TEST_SNAPSHOT + 12 + TEST_TEXTS * TEST_TEXT.size + n * TEST_QUAD.size
        _, caller, _, name, x, y, width, height = TEST_QUAD.unpack_from(data, at)
        found = named if name else unnamed  # unnamed: plain boxes such as a cursor or a plate drawn without a texture
        key = (f"{name >> 16}.{name & 0xFFFF}" if name else None, x, y, width, height, f"{caller:#x}")
        found[key] = found.get(key, 0) + 1
    textures = [dict(zip(("texture", "x", "y", "width", "height", "caller"), key, strict=True), count=count)
                for key, count in named.items()]
    boxes = [dict(zip(("x", "y", "width", "height", "caller"), key[1:], strict=True), count=count) for key, count in unnamed.items()]
    return {"frame": frame, "frames_run": now, "texts": drawn, "textures": textures,
            "unnamed_quads": sum(unnamed.values()), "boxes": boxes,
            "dropped": {"texts": max(0, texts - TEST_TEXTS), "quads": max(0, quads - TEST_QUADS)},
            "windows": windows}


def wait_for(memory: int, block: int, text: str | None, texture: str | None, seconds: float,
             on_match=None) -> dict:
    """Poll frames until text is drawn or set in a window after the wait began, or texture is drawn.

    on_match runs straight after the matching poll, before the answer is built; the poll gap
    shrinks to 20 ms when it is set, so a capture lands within a few frames of the match."""
    deadline, start = time.monotonic() + seconds, None
    while True:
        seen = screen(memory, block)
        start = seen["frames_run"] if start is None else start
        if text is not None:
            recent = [w for w in seen["windows"] if w["frame"] >= start]
            hits = [t for t in seen["texts"] + recent if text in t["text"].replace("\n", " ")]
        else:
            hits = [q for q in seen["textures"] if q["texture"] == texture]
        if hits:
            found = {"frame": seen["frame"], "matches": hits}
            if on_match:
                found.update(on_match())
                found["frames_to_capture"] = screen(memory, block)["frames_run"] - seen["frames_run"]
            return found
        if time.monotonic() >= deadline:
            raise TimeoutError(f"not drawn within {seconds:g} s; run screen to see what is")
        time.sleep(0.02 if on_match else 0.1)


def review_request(texture: str, rect: list[int]) -> tuple[int, list[int]]:
    """Validate a renderer review before it reaches the game mailbox."""
    if texture == "off":
        return 0, [0, 0, 0, 0]
    if not isinstance(texture, str) or not re.fullmatch(r"\d+\.\d+", texture):
        raise ValueError("review texture is key.member, or off")
    entry, item = map(int, texture.split("."))
    if not 0 < entry < 65536 or not 0 <= item < 65536:
        raise ValueError("review entry and item must fit 16 bits")
    if len(rect) != 4 or any(not isinstance(v, int) or v < 0 for v in rect):
        raise ValueError("review rect is four nonnegative integers: X Y W H")
    x, y, w, h = rect
    if not 0 < w <= 1110 or not 0 < h <= 200 or x + w > 4095 or y + h > 4095:
        raise ValueError("review rect must fit the UV cap, width 1110, and height 200")
    return entry << 16 | item, rect


def capture_name(root: Path, name: object) -> Path:
    """The PNG path for a new capture; refuses an unsafe stem or an existing file."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
        raise ValueError("capture name must be a short filename stem, without slashes")
    destination = local_path(root, f"evidence/{name}.png")
    if destination.exists():
        raise ValueError(f"capture already exists: {destination}")
    return destination


def capture_png(root: Path, name: object) -> Path:
    destination = capture_name(root, name)
    subprocess.run(["gamescopectl", "screenshot", str(destination)], check=True, timeout=10)
    deadline = time.monotonic() + 10
    while not destination.exists() or not destination.read_bytes().endswith(
        b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
    ):
        if time.monotonic() >= deadline:
            raise TimeoutError("Gamescope did not produce the screenshot")
        time.sleep(0.1)
    return destination


def perform(root: Path, request: dict) -> dict:
    action = request["action"]
    if action == "scripts":
        with open(f"/proc/{game_pid()}/mem", "rb", buffering=0) as memory:
            return stage_scripts(memory.fileno())
    if action in ("screen", "wait", "say", "notice", "win", "lose", "script", "scene", "unlock", "review", "spawn",
                  "mount", "switch", "teleport", "keep"):
        with open(f"/proc/{game_pid()}/mem", "r+b", buffering=0) as memory:
            block = test_block(memory.fileno())
            if action == "screen":
                return screen(memory.fileno(), block)
            if action == "review":
                name, rect = review_request(request["texture"], request["rect"])
                test_command(memory.fileno(), block, 7, name, struct.pack("<4I", *rect))
                return {"review": request["texture"], "rect": rect, "reference": "top", "vector": "bottom"}
            if action in ("script", "scene"):
                number = request["number"]
                if not isinstance(number, int) or not 0 <= number <= 0xFF:
                    raise ValueError(f"{action} takes a number from 0 to 255")
                # waits out a script or scene already playing; a scene is the stage's own sub-scene (0: its opening)
                test_command(memory.fileno(), block, 4 if action == "script" else 8, number, busy=10)
                return {"queued": number} if action == "script" else {"scene": number}
            if action == "spawn":
                return spawn(memory.fileno(), block, request["count"], request.get("inner", SPAWN_INNER))
            if action == "mount":
                return mount(memory.fileno(), block, request.get("slot", 0))
            if action == "switch":
                return switch(memory.fileno(), block, request["member"])
            if action == "teleport":
                return teleport(memory.fileno(), block, request["x"], request["z"], request.get("facing", 0.0),
                                request.get("relative", False))
            if action == "keep":
                return keep(memory.fileno(), block, request["keep"])
            if action == "unlock":
                test_command(memory.fileno(), block, 5)
                return {"officers": "level 99, four weapons each"}
            if action == "wait":
                text, texture, seconds = request.get("text"), request.get("texture"), request["seconds"]
                if (text is None) == (texture is None) or not isinstance(text or texture, str):
                    raise ValueError("wait needs one of text or texture")
                if texture is not None and not re.fullmatch(r"\d+\.\d+", texture):
                    raise ValueError("texture is key.member, such as 4079.12")
                if not isinstance(seconds, (int, float)) or not 0 < seconds <= 600:
                    raise ValueError("wait seconds must be greater than 0 and at most 600")
                shot = request.get("capture")
                if shot is not None:
                    capture_name(root, shot)  # refuse a bad or taken name before waiting
                return wait_for(memory.fileno(), block, text, texture, seconds,
                                (lambda: {"path": str(capture_png(root, shot))}) if shot is not None else None)
            if action in ("win", "lose"):
                # The stage's own victory (type 7) or defeat (type 8) script; the result screen follows.
                test_command(memory.fileno(), block, 3, 7 if action == "win" else 8)
                return {"queued": action}
            if not isinstance(request["text"], str) or not request["text"]:
                raise ValueError("say and notice need text")
            # Window 1 is spoken dialog, 2 the battle notice; the runtime translates and wraps as the game would.
            test_command(memory.fileno(), block, 2, 1 if action == "say" else 2, encode_english(request["text"]))
            return {"window": 1 if action == "say" else 2}
    if action == "reload":
        # Replace table.bin whole, as an editor saving it would, and wait for the runtime to load it.
        source = Path(request["table"])
        if not source.is_file():
            raise ValueError(f"no table at {source}")
        with open(f"/proc/{game_pid()}/mem", "rb", buffering=0) as memory:
            block = test_block(memory.fileno())
            before = struct.unpack("<I", os.pread(memory.fileno(), 4, block + TEST_RELOADS))[0]
            target = root / "game/wozmod/table.bin"
            partial = target.with_name("table.bin.partial")
            shutil.copyfile(source, partial)
            os.replace(partial, target)
            start = time.monotonic()
            while (now := struct.unpack("<I", os.pread(memory.fileno(), 4, block + TEST_RELOADS))[0]) == before:
                if time.monotonic() - start > RELOAD_SECONDS:
                    raise TimeoutError(f"table.bin not reloaded within {RELOAD_SECONDS} s; see wozmod/runtime.log")
                time.sleep(0.05)
            return {"reloads": now, "seconds": round(time.monotonic() - start, 2)}
    if action == "peek":
        length = request["length"]
        if not isinstance(length, int) or not 0 < length <= MAX_PEEK:
            raise ValueError(f"peek length must be 1 to {MAX_PEEK} bytes")
        address = game_range(request["address"], length)
        with open(f"/proc/{game_pid()}/mem", "rb", buffering=0) as memory:
            return {"address": f"{address:#x}", "hex": os.pread(memory.fileno(), length, address).hex()}
    if action == "poke":
        data = hex_bytes(request["hex"], MAX_POKE)
        address = game_range(request["address"], len(data))
        with open(f"/proc/{game_pid()}/mem", "r+b", buffering=0) as memory:
            before = os.pread(memory.fileno(), len(data), address)
            if os.pwrite(memory.fileno(), data, address) != len(data):
                raise OSError("short write to game memory")
        # before is journaled in actions.jsonl: poke it back to undo
        return {"address": f"{address:#x}", "before": before.hex(), "after": data.hex()}
    if action == "plug":  # inside the sandbox: the pad's event node appears or goes, as a real hotplug
        node = json.loads((root / "evidence/session.json").read_text())["pad"]
        if not node:
            raise ValueError("this run has no pad; start it with run --pad")
        if request["on"]:
            Path(node).symlink_to(PAD_HIDDEN)
        else:
            Path(node).unlink()
        return {"plugged": request["on"]}
    if action == "find":
        return {"addresses": [f"{a:#x}" for a in find_bytes(game_pid(), hex_bytes(request["hex"], MAX_PATTERN))]}
    if action == "click":
        size = json.loads(local_path(root, "evidence/session.json").read_text())["render_size"]
        if not isinstance(size, list) or len(size) != 2 or any(type(value) is not int for value in size):
            raise ValueError("run has no valid render size for click input")
        width, height = size
        if not 0 < width <= 5120 or not 0 < height <= 2160:
            raise ValueError("click render size must fit 5120x2160")
        x, y = request["x"], request["y"]
        if type(x) is not int or type(y) is not int or not 0 <= x < width or not 0 <= y < height:
            raise ValueError(f"click coordinates must be integers inside {width}x{height}")
        click_point(x, y)
        return {"action": "click", "x": x, "y": y, "button": "left"}
    if action in ("key", "keys"):
        # "key" keeps the original one-tap request and 1-second settle; "settle" overrides the wait after the last key.
        names = [request["key"]] if action == "key" else request["keys"]
        hold = float(request["hold"])
        gap = float(request["gap"]) if action == "keys" else 1.0
        settle = float(request["settle"]) if "settle" in request else gap
        if not isinstance(names, list) or not 0 < len(names) <= MAX_KEYS or not all(
            isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_]+", name) for name in names
        ):
            raise ValueError(f"use 1 to {MAX_KEYS} X keysyms, such as Return or Down")
        if not math.isfinite(hold) or not 0 < hold <= 2:
            raise ValueError("key hold must be greater than 0 and at most 2 seconds")
        if not math.isfinite(gap) or not 0.05 <= gap <= 2:
            raise ValueError("key gap must be from 0.05 to 2 seconds")
        if not math.isfinite(settle) or not 0 <= settle <= 10:
            raise ValueError("key settle must be from 0 to 10 seconds")
        tap_keys(names, hold, gap, settle)
    elif action == "pad":
        # The supervisor outside the sandbox owns the pad (gamescope closes inherited descriptors).
        control = root / "control"
        write_json(control / "pad.json", dict(request, names=pad_names(request["names"])))
        deadline = time.monotonic() + 10 + len(request["names"]) * (request["hold"] + request["gap"])
        while (control / "pad.json").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("the supervisor did not take the pad input")
            time.sleep(0.02)
        done = json.loads((control / "pad-done.json").read_text())
        if done.get("error"):
            raise ValueError(done["error"])
        time.sleep(request["gap"])
    elif action == "capture":
        path, now = capture_png(root, request["name"]), time.time()
        recent = fps_summary(root, run_target(root), now - 3, now)
        return {"path": str(path), **({"fps": recent["median"], "slow": bool(recent["slow_seconds"])} if recent else {})}
    elif action == "record":
        # The game plays into the private null sink; its monitor is what a player would hear.
        name, seconds = request["name"], request["seconds"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
            raise ValueError("record name must be a short filename stem, without slashes")
        if not isinstance(seconds, (int, float)) or not 0 < seconds <= 600:
            raise ValueError("record seconds must be greater than 0 and at most 600")
        destination = local_path(root, f"evidence/{name}.wav")
        if destination.exists():
            raise ValueError(f"recording already exists: {destination}")
        subprocess.Popen(["timeout", str(seconds), "parecord", "--device=musou_harness.monitor",
                          "--file-format=wav", str(destination)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"path": str(destination), "seconds": seconds}
    elif action == "video":
        # Gamescope's PipeWire screen stream and the private sink's monitor, muxed by one pipeline so they stay in step.
        # NVENC keeps encoding off the CPU the game needs; start the run with --video (1080p output) for full speed.
        # Pin BGRx: offered NV12, Gamescope converts each frame on its compositor thread and the game drops to 30 fps.
        name, seconds = request["name"], request["seconds"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
            raise ValueError("video name must be a short filename stem, without slashes")
        if not isinstance(seconds, (int, float)) or not 0 < seconds <= 600:
            raise ValueError("video seconds must be greater than 0 and at most 600")
        destination = local_path(root, f"evidence/{name}.mkv")
        if destination.exists():
            raise ValueError(f"video already exists: {destination}")
        with local_path(root, f"evidence/{name}.log").open("w") as log:
            subprocess.Popen(
                ["timeout", "-s", "INT", str(seconds), "gst-launch-1.0", "-e",
                 "pipewiresrc", "target-object=gamescope", "do-timestamp=true", "!", "video/x-raw,format=BGRx", "!",
                 "queue", "!", "nvh264enc", "bitrate=12000", "!", "h264parse", "!", "queue", "!", "mux.",
                 "pulsesrc", "device=musou_harness.monitor", "do-timestamp=true", "!", "audioconvert", "!",
                 "opusenc", "bitrate=192000", "!", "queue", "!", "mux.",
                 "matroskamux", "name=mux", "!", "filesink", f"location={destination}"],
                stdout=log, stderr=subprocess.STDOUT)
        return {"path": str(destination), "seconds": seconds}
    elif action not in ("status", "stop"):
        raise ValueError(f"unknown action: {action}")
    return {"action": action}


def start_audio(root: Path) -> None:
    """Keep Proton's audio backend available without host playback."""
    runtime = os.environ["XDG_RUNTIME_DIR"]
    os.environ["PULSE_SERVER"] = f"unix:{runtime}/pulse/native"
    for name in ("pipewire", "pipewire-pulse", "wireplumber"):
        with (root / f"evidence/{name}.log").open("w") as log:
            subprocess.Popen([name], stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 10
    while not Path(runtime, "pulse/native").is_socket():
        if time.monotonic() >= deadline:
            raise TimeoutError("private PulseAudio socket did not become ready")
        time.sleep(0.1)
    subprocess.run(
        ["pactl", "load-module", "module-null-sink", "sink_name=musou_harness"],
        check=True, timeout=10, stdout=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10
    while subprocess.run(["pactl", "set-default-sink", "musou_harness"], timeout=10).returncode:
        if time.monotonic() > deadline:  # on a loaded machine the new sink can take seconds to be usable
            raise RuntimeError("pactl could not make musou_harness the default sink")
        time.sleep(0.5)


def serve(root: Path) -> int:
    """Internal entry point, invoked only within the isolated compositor."""
    if (
        not os.environ.get("GAMESCOPE_WAYLAND_DISPLAY")
        or os.environ.get("MUSOU_HARNESS_ROOT") != str(root)
    ):
        raise ValueError("serve requires the harness Gamescope environment")
    control = root / "control"
    session_path = root / "evidence/session.json"
    session = json.loads(session_path.read_text()) if session_path.is_file() else {}
    idle, target = session.get("idle_seconds"), session.get("target_fps", 60)
    sampling = threading.Event()
    threading.Thread(target=log_frames, args=(root, sampling), daemon=True).start()
    with (root / "evidence/launcher.log").open("w") as log:
        process = subprocess.Popen(
            ["bash", str(root / "launch.sh")], cwd=root,
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            write_json(control / "ready.json", {"display": os.environ.get("DISPLAY")})
            busy_until = time.monotonic()  # the last request's end, or a recording's
            while process.poll() is None:
                path = control / "request.json"
                if not path.exists():
                    if idle and time.monotonic() - busy_until > idle:  # a game left up by an agent that moved on
                        write_json(root / "evidence/game-exit.json", {"stopped": True, "idle": idle, "time": time.time()})
                        return 0
                    time.sleep(0.02)
                    continue
                request = json.loads(path.read_text())
                response = {"id": request.get("id"), "ok": False}
                try:
                    response.update(perform(root, request), ok=True)
                except (
                    OSError, ValueError, KeyError, TypeError,
                    RuntimeError, subprocess.SubprocessError,
                ) as exc:
                    response["error"] = str(exc)
                busy_until = time.monotonic() + (float(request.get("seconds", 0)) if request.get("action") in
                                                 ("record", "video") else 0)
                if request.get("action") == "stop":  # how the game kept its frame rate, for every run's last word
                    response.update(fps=fps_summary(root, target), slow_recordings=recordings_slow(root, target))
                with (root / "evidence/actions.jsonl").open("a") as journal:
                    journal.write(json.dumps({
                        "time": time.time(), "request": request,
                        "response": response,
                    }) + "\n")
                write_json(control / "response.json", response)
                path.unlink()
                if request.get("action") == "stop" and response["ok"]:
                    write_json(root / "evidence/game-exit.json", {"stopped": True, "time": time.time()})
                    return 0
            # The launcher execs Proton, so this is the game's own exit status: the one record that
            # tells a crash from a quit, since both end the harness with the same code.
            write_json(root / "evidence/game-exit.json", {"returncode": process.returncode, "time": time.time()})
            return process.returncode
        finally:
            sampling.set()
            (control / "ready.json").unlink(missing_ok=True)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            # The outer PID namespace removes all remaining Proton/audio children.


def last_actions(root: Path, count: int = 3) -> list[str]:
    """The run's last journaled requests, as `action args -> ok|error`."""
    journal = root / "evidence/actions.jsonl"
    lines = journal.read_text().splitlines()[-count:] if journal.is_file() else []
    out = []
    for line in lines:
        entry = json.loads(line)
        request, response = entry["request"], entry["response"]
        detail = request.get("keys") or request.get("key") or request.get("name") or request.get("number") or ""
        out.append(f"{request['action']} {detail}".strip() + f" -> {'ok' if response['ok'] else response.get('error')}")
    return out


def hold_for_supervisor(root: Path, code: int) -> None:
    """Report the server's end and wait to be killed. gamescope 3.16 on NVIDIA segfaults in its own
    exit path once its child exits (the CVulkanDevice destructor, or the pipeline-compile thread on
    an early exit), so the compositor's child never exits: the supervisor SIGKILLs the sandbox."""
    write_json(root / "control/serve-exit.json", {"returncode": code})
    while True:
        time.sleep(1)


def exit_record(root: Path, returncode: int | None, interrupted: bool) -> dict:
    """Why a run ended. The harness exits with the server's code after both a crash and a quit; the game's own exit
    status (evidence/game-exit.json, written inside the sandbox) separates them."""
    path = root / "evidence/game-exit.json"
    game = json.loads(path.read_text()) if path.is_file() else None
    if game and game.get("idle"):
        reason = "idle"  # no command for the idle limit (run --idle): left up by an agent that moved on
    elif game and game.get("stopped"):
        reason = "stopped"  # the stop command
    elif game:
        reason = "quit" if game["returncode"] == 0 else "crash"
    elif interrupted:
        reason = "interrupted"  # the supervisor was stopped while the game ran
    elif returncode in (124, 137):
        reason = "timeout"  # run --timeout elapsed
    else:
        reason = "unknown"  # the compositor or sandbox ended before the game did; see harness.log
    record = {"returncode": returncode, "reason": reason, "last_actions": last_actions(root)}
    if game and "returncode" in game:
        record["game_returncode"] = game["returncode"]
    if summary := fps_summary(root, run_target(root)):
        record["fps"] = summary
    return record


def exit_info(root: Path) -> dict | None:
    """The exit_record the supervisor wrote as it ended, or None before then."""
    path = root / "evidence/exit.json"
    return json.loads(path.read_text()) if path.is_file() else None


def describe_exit(record: dict) -> str:
    """An exit record's reason, with the game's own exit status when it has one."""
    code = f" (game exit status {record['game_returncode']})" if "game_returncode" in record else ""
    fps = record.get("fps")
    rate = (f"; {fps['median']} fps median, lowest {fps['lowest']}, {fps['slow_seconds']} of {fps['seconds']} s slow "
            f"(target {fps['target']}; {fps.get('loading_seconds', 0)} s loading)" if fps else "")
    return f"{record.get('reason', 'unknown')}{code}{rate}"


def ended(root: Path) -> str | None:
    """A one-line account of a finished run, or None while it has not ended."""
    record = exit_info(root)
    if record is None and (root / "evidence/game-exit.json").is_file():  # the game is gone; the compositor is closing
        record = exit_record(root, None, False)
    if record is None:
        return None
    last = "; ".join(record.get("last_actions", [])) or "none"
    return f"the run has ended: {describe_exit(record)}; last actions: {last}"


# Two games may run at once across the checkout and all its worktrees: every run holds one of these, in the main
# checkout's work/, for its whole life, and writes its name and start into it so a waiting launch can say who holds
# what.
SLOTS = (".game-run.lock", ".game-run.1.lock")
# Each slot's own Steam runtime in the main checkout's template, so two games never share one. A run binds its slot's
# writable: pressure-vessel hard-links the runtime into its var/, which through an overlay copied 16k files into every
# run. Make one with cp -a --reflink=always _template/steamrt _template/steamrt-1.
RUNTIMES = ("steamrt", "steamrt-1")


def take_slot(holder: str = "", poll: float = 1.0) -> tuple[int, int]:
    """Wait for a free game slot and lock it: (descriptor, slot index), writing ``holder`` (the run) and the time into
    the lock file. The descriptor stays open, so the lock lasts until the process ends."""
    while True:
        for index, slot in enumerate(SLOTS):
            fd = os.open(work() / slot, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            os.ftruncate(fd, 0)
            os.pwrite(fd, json.dumps({"run": holder, "since": time.time()}).encode(), 0)
            return fd, index
        time.sleep(poll)


def slot_holders() -> list[str]:
    """Who holds each busy game slot, and for how long: `run-name (12 min)`."""
    held = []
    for slot in SLOTS:
        path = work() / slot
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


def run(root: Path, args: argparse.Namespace) -> int:
    if not args.authorize_launch:
        raise ValueError("launch needs current-task permission and --authorize-launch")
    game = args.game.resolve(strict=True)
    if not game.is_dir() or not game.is_relative_to(root):
        raise ValueError("--game must be a disposable directory inside --root")
    launcher = local_path(root, "launch.sh")
    if not launcher.is_file():
        raise ValueError("prepare and review a run-local launch.sh first")
    lock, slot = take_slot(root.name)  # every launch, so no game runs outside the two-game limit
    try:
        write_json(root / "slot.json", {"slot": slot, "time": time.time()})  # run_check: the boot clock starts here
        return launch_in_slot(root, args, game, launcher, slot)
    finally:
        os.close(lock)


def launch_in_slot(root: Path, args: argparse.Namespace, game: Path, launcher: Path, slot: int) -> int:
    steamrt = (work() / "runtime-tests/_template" / RUNTIMES[slot]).resolve()
    if args.target != XENIA:  # a MOZ run is staged by prepare_run.py; the reference emulator's are not
        verify_copy(root, game)
        if not (steamrt / "run").is_file():
            raise ValueError(f"no Steam runtime for game slot {slot}: cp -a --reflink=always "
                             f"{steamrt.parent / RUNTIMES[0]} {steamrt}")
    for name in ("tmp", "runtime", "home", "prefix"):
        local_path(root, name).mkdir(mode=0o700, exist_ok=True)
    # Run directories are single-use: never replay stale input after a restart.
    local_path(root, "control").mkdir(mode=0o700)
    local_path(root, "evidence").mkdir(mode=0o700)
    size = getattr(args, "size", None)
    render_size = (1280, 720) if args.target == XENIA else size or (1920, 1080)
    # Gamescope copies each output frame into the screen stream on its own thread; at 3840x2160 that halves the
    # game's frame rate, and the frame-locked game then runs slow against its real-time voices. --video outputs 1080p.
    # Another --size renders and outputs at that size, unscaled.
    fsr = args.target != XENIA and not getattr(args, "video", False) and not size
    output_size = (3840, 2160) if fsr else render_size
    identity = {"target": args.target, "game": str(game), "root": str(root),
                "launch_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
                "timeout_seconds": args.timeout, "render_size": render_size,
                "output_size": output_size, "fsr": fsr, "target_fps": target_fps(game),
                "idle_seconds": getattr(args, "idle", None)}
    pad = VirtualPad(args.pad) if args.pad else None
    identity["pad"] = pad.node if pad else None
    identity["route_input"] = getattr(args, "route_input", "keys")
    if identity["route_input"] == "pad" and not pad:
        raise ValueError("--route-input pad needs --pad")
    write_json(root / "evidence/session.json", identity)
    runtime = f"/run/user/{os.getuid()}"
    command = [
        "timeout", "--kill-after=10", str(args.timeout), "bwrap",
        "--die-with-parent", "--new-session", "--unshare-pid",
        "--unshare-net", "--unshare-ipc", "--ro-bind", "/", "/",
        "--bind", str(root), str(root), "--bind", str(root / "tmp"), "/tmp",
        "--bind", str(root / "runtime"), runtime,
        *(["--bind", str(steamrt), str(steamrt)] if steamrt.is_dir() else []),
        "--dev", "/dev", "--proc", "/proc",
    ]
    fixtures = root / "fixtures.json"
    for part, lower in (json.loads(fixtures.read_text()).get("overlays", {}) if fixtures.is_file() else {}).items():
        # the template under the run's own folder, which keeps what the run writes (prepare_run.py)
        upper, scratch = local_path(root, part), local_path(root, f"overlay-work/{part}")
        scratch.mkdir(parents=True)
        command += ["--overlay-src", lower, "--overlay", str(upper), str(scratch), str(upper)]
    for device in (
        "/dev/dri", "/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-modeset",
        "/dev/nvidia-uvm", "/dev/nvidia-uvm-tools",
    ):
        if Path(device).exists():
            command += ["--dev-bind", device, device]
    if pad:  # only the virtual pad: host input devices stay outside; plug links or unlinks its node
        command += ["--dev-bind", pad.node, PAD_HIDDEN, "--dir", "/dev/input"]
        if not args.unplugged:
            command += ["--symlink", PAD_HIDDEN, pad.node]
    for variable in (
        "DISPLAY", "WAYLAND_DISPLAY", "GAMESCOPE_WAYLAND_DISPLAY", "XAUTHORITY",
        "DBUS_SESSION_BUS_ADDRESS", "PULSE_SERVER", "PIPEWIRE_REMOTE",
    ):
        command += ["--unsetenv", variable]
    environment = {
        "MUSOU_HARNESS_ROOT": str(root), "GAME_DIR": str(game),
        "XDG_RUNTIME_DIR": runtime, "PULSE_SERVER": "unix:/nonexistent",
        "HOME": str(root / "home"), "XDG_CACHE_HOME": str(root / "home/cache"),
        "XDG_CONFIG_HOME": str(root / "home/config"), "XDG_STATE_HOME": str(root / "home/state"),
        "STEAM_COMPAT_DATA_PATH": str(root / "prefix"), "SLR4": str(steamrt / "run"),
        "DXVK_LOG_PATH": str(root / "evidence"), "DXVK_STATE_CACHE_PATH": str(root / "home/cache"),
        "PROTON_USE_XALIA": "0",
    }
    for key, value in environment.items():
        command += ["--setenv", key, value]
    hz = "60" if args.target == XENIA else os.environ.get("MUSOU_HARNESS_HZ", "60")
    if hz not in ("60", "120"):
        raise ValueError("MUSOU_HARNESS_HZ must be 60 or 120")
    # The private audio server starts first: gamescope connects to PipeWire once, at start, for its screen stream.
    command += ["dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()), "_audio", "--root", str(root),
                "gamescope", "--backend", "headless",
                "-W", str(output_size[0]), "-H", str(output_size[1]),
                "-w", str(render_size[0]), "-h", str(render_size[1])]
    if fsr:
        command += ["-F", "fsr"]
    command += ["-r", hz, "--",
                sys.executable, str(Path(__file__).resolve()), "_serve", "--root", str(root)]

    def interrupted(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    served = root / "control/serve-exit.json"
    served.unlink(missing_ok=True)
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    code = None
    try:
        with (root / "evidence/harness.log").open("w") as log:
            process = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,  # no core dumps: a gamescope dump can stall the desktop
                preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)),
            )
            try:
                while process.poll() is None and not served.exists():
                    request = root / "control/pad.json"
                    if pad and request.exists():
                        taken = json.loads(request.read_text())
                        done = {"id": taken.get("id")}
                        try:
                            pad.tap(pad_names(taken["names"]), float(taken["hold"]), float(taken["gap"]))
                        except (OSError, ValueError, KeyError) as exc:
                            done["error"] = str(exc)
                        write_json(root / "control/pad-done.json", done)
                        request.unlink()
                    time.sleep(0.02)
                if served.exists():  # SIGKILL ends gamescope before its crashing exit path runs
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    code = json.loads(served.read_text())["returncode"]
                    return code
                return process.returncode
            finally:
                interrupted = process.poll() is None
                if interrupted:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                write_json(root / "evidence/exit.json", exit_record(root, process.returncode if code is None else code, interrupted))
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        (root / "control/ready.json").unlink(missing_ok=True)
        if pad:
            pad.close()


class NotReady(ValueError):
    """The run is still starting or busy: the same request may succeed later."""


def send(root: Path, request: dict, timeout: float) -> dict:
    control = local_path(root, "control")
    deadline = time.monotonic() + timeout
    # ponytail: one command at a time per run; add a queue only for real concurrent scenarios.
    with (control / "client.lock").open("a") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("another agent holds this run's control lock") from None
                time.sleep(0.1)
        if not (control / "ready.json").exists():
            if reason := ended(root):
                raise ValueError(reason)
            raise NotReady("harness is not ready; read evidence/harness.log and launcher.log")
        if (control / "request.json").exists():
            raise NotReady("an earlier command is pending; do not replay it")
        request = dict(request, id=uuid.uuid4().hex)
        write_json(control / "request.json", request)
        while time.monotonic() < deadline:
            response_path = control / "response.json"
            if response_path.exists():
                response = json.loads(response_path.read_text())
                if response.get("id") == request["id"] and not (control / "request.json").exists():
                    return response
            time.sleep(0.02)
        raise TimeoutError("no acknowledgement; command outcome is unknown, do not repeat input")


# The tour: a fixed route from a fresh boot on the full-unlock save. Each step sends keys (or one
# request), waits for the screen that must follow, and records a stop there if it names one. The
# screens are named by package texture (optionally without another), a stock-code text draw caller
# or, for text the runtime draws itself (its addresses move with every build), a text x; never
# by English, so the route runs on any build.
# The main menu's wheel art is 4085.6 on one boot and 4085.5 (with a character panel, 4085.158) on another.
MENU = "4085.6|4085.5"
TOUR = [
    (["Escape"], {"texture": "4085.0"}, "title"),  # repeated until the title: skips the logo and movie
    (["Return"], {"texture": MENU}, None),  # main menu, cursor on STORY MODE
    (["Up", "Up", "Return"], {"texture": "4079.171"}, "options"),
    (["Return"], {"texture": "4069.14"}, "game-settings"),
    (["Escape", "Escape"], {"texture": MENU}, None),  # cursor on OPTION
    (["Down", "Down", "Return"], {"texture": "4082.0"}, "story-select"),
    (["Return"], {"texture": "4079.170"}, "camp"),  # Shu
    (["Escape"], {"texture": "4082.0"}, None),
    (["Escape"], {"texture": MENU}, None),  # cursor on STORY MODE
    (["Down", "Down", "Return"], {"texture": "4094.5"}, "dream-select"),
    (["Escape"], {"texture": MENU}, None),  # cursor on DREAM MODE
    (["Down", "Down", "Down", "Return"], {"texture": "4079.169"}, "gallery"),
    (["Escape"], {"texture": MENU}, None),  # cursor on GALLERY
    (["Up", "Up", "Up", "Return"], {"texture": "4094.5"}, None),
    (["Return"], None, None),  # the first scenario, stage 40
    (["Left"], None, None),  # difficulty wraps, and the save leaves it on CHAOS: three Lefts reach EASY
    (["Left"], None, None),
    (["Left"], None, None),
    (["Return"], None, None),
    (["Return"], {"texture": "4079.170"}, None),  # recommended team, then the camp
    (["Up", "Return"], {"caller": "0x548f52"}, "briefing"),  # NEXT; stock caller of the briefing text
    (["Escape"], {"texture": "4079.168"}, "pre-battle"),
    (["Up", "Return"], {"texture": "4079.148"}, "first-dialog"),  # FIGHT; the opening dialog is up by then
    ([], {"caller": "0x550f0b"}, "first-notice"),
    (["Escape"], {"texture": "4079.223"}, "pause"),
    (["Escape"], {"texture": "4079.148"}, None),
    ({"action": "win"}, {"texture": "4079.52", "without": "4080.0"}, "results"),
    (["Return"], {"caller": "0x54e76f"}, "save-prompt"),  # message box, cursor on NO
    (["Return"], None, "save-cancelled"),
    (["Return"], {"texture": MENU}, None),
]
# The stage sweep, per stage: its Dream Mode briefing, opening dialog and victory, from the main menu
# with the cursor on DREAM MODE, where a Dream battle leaves it. Return every 3 s pages the victory's still
# events.
SWEEP_START = TOUR[:2] + [(["Down", "Down"], None, None)]


def stage_route(stage: int, dialog: float = 25) -> list:
    return [
        (["Return"], {"texture": "4094.5"}, None),
        ({"stage": stage}, None, None),  # the cursor to its scenario
        *[([key], None, None) for key in ("Return", "Left", "Left", "Left", "Return")],
        (["Return"], {"texture": "4079.170"}, None),
        (["Up", "Return"], {"caller": "0x548f52"}, f"{stage:03d}-briefing"),
        (["Escape"], {"texture": "4079.168"}, None),
        (["Up", "Return"], {"texture": "4079.148"}, None),
        ([], {"seconds": dialog}, f"{stage:03d}-dialog"),  # the opening dialog plays itself
        ({"action": "win"}, None, None),
        (["Return"], {"texture": MENU, "every": 3}, f"{stage:03d}-victory"),
    ]


TOUR_WAIT, TOUR_SETTLE, TOUR_GAP = 90, 2.0, 0.5  # seconds: per step, before a stop's record, between keys
ROUTE_POLL = 0.1  # seconds between screen polls while a route waits for a screen
ROUTE_GAP = 0.6  # seconds a route waits after keys whose destination screen was showing already
# A screen is drawn before it takes input: the main menu's fingerprint is its wheel art, which shows at once, and its
# items slide in for over half a second after (Down Down Down on a fresh menu moved the cursor four); a submenu slides in
# too, and a battle or the results screen ignore input for seconds. So a route waits after the screen that ends an edge shows
# (an edge's own `slow` fingerprint overrides this); keys between its steps are still gated only by the poll.
ARRIVE_GAP = 0.6  # seconds after an edge's destination first shows
ROUTE_KEY_GAP = 0.3  # seconds between the keys of one route step
MENU_SETTLE = 1.2  # ... a main-menu cursor position
SCREEN_WIDTH, LINE_HEIGHT = 1280, 24  # the game's layout space and font cell
CJK = re.compile("[\u3000-\u30ff\u3400-\u9fff\uff00-\uffef]")
IMAGE_CHANGE = 0.03  # mean absolute grey difference, 0 to 1, that marks a stop's image as changed


def tour_checks(seen: dict) -> dict:
    """The design's Tier 2 checks on one screen: Japanese that missed the table, text past the
    screen edge, and text rectangles that intersect."""
    # ponytail: overflow is checked against the screen edge; per-site box widths come with layout.json.
    texts = seen["texts"]
    boxes = [(t["x"], t["y"], t["x"] + t["width"], t["y"] + LINE_HEIGHT * (t["text"].count("\n") + 1))
             for t in texts]
    return {
        "cjk": sorted({t["text"] for t in texts + seen["windows"] if CJK.search(t["text"])}),
        "overflow": [t["text"] for t, (x, _, right, _) in zip(texts, boxes, strict=True) if x < 0 or right > SCREEN_WIDTH],
        "overlap": [[a["text"], b["text"]] for i, (a, p) in enumerate(zip(texts, boxes, strict=True))
                    for b, q in zip(texts[i + 1:], boxes[i + 1:], strict=True)
                    if a["text"] != b["text"] and p[0] < q[2] and q[0] < p[2] and p[1] < q[3] and q[1] < p[3]],
    }


def shows(seen: dict, until: dict) -> bool:
    if "any" in until:  # either fingerprint
        return any(shows(seen, one) for one in until["any"])
    drawn = {q["texture"] for q in seen["textures"]}
    if "box_caller" in until:  # a plain box, such as a banner plate piece; "|" between alternatives
        return any(b["caller"] in until["box_caller"].split("|") for b in seen.get("boxes", []))
    if "caller" in until:  # `lines`: a text of that many lines; `texts`: at least that many texts of the caller at once
        drawn_by = [t for t in seen["texts"] if t["caller"] == until["caller"]
                    and ("lines" not in until or t["text"].count("\n") + 1 == until["lines"])]
        return len(drawn_by) >= until.get("texts", 1)
    if "x" in until:
        return any(t["x"] == until["x"] for t in seen["texts"])
    return (not drawn.isdisjoint(until["texture"].split("|")) and drawn.isdisjoint(until.get("without", "").split("|"))
            and (not until.get("with") or until["with"] in drawn)
            and (not until.get("quad_caller") or any(q["caller"] == until["quad_caller"] for q in seen["textures"]))
            and not any(t["caller"] == until.get("without_caller") for t in seen["texts"]))


def routed_screens(seen: dict) -> list[str]:
    """The routed screens whose fingerprint a frame shows (the menu's items as one); none: no route reaches it yet."""
    names = [name.split("@")[0] for name, mark in ROUTE_SCREENS.items() if mark and shows(seen, mark)]
    return list(dict.fromkeys(names))


NO_ROUTE = ("no route reaches this screen: if you drove here by hand, add its route before you hand back "
            "(wozmod-game-harness skill, Routes)")


def ask(root: Path, request: dict, timeout: float = 30) -> dict:
    response = send(root, request, timeout)
    if not response["ok"]:
        raise RuntimeError(f"{request['action']}: {response.get('error')}")
    return response


# The pad buttons a route sends for its keys in a `run --route-input pad` run: A confirms, B goes back, Y is the
# page key Tab (the prompts say so; runs fid-pad, v-pad). In a battle B attacks: Escape there is START (PAUSE_SOURCES).
PAD_FOR_KEY = {"Return": "A", "Escape": "B", "Tab": "Y", "Up": "UP", "Down": "DOWN", "Left": "LEFT", "Right": "RIGHT"}
PAUSE_SOURCES = {"battle", "story-battle", "story-wei-battle"}


def route_input(root: Path) -> str:
    try:
        return json.loads((root / "evidence/session.json").read_text()).get("route_input", "keys")
    except (OSError, ValueError):
        return "keys"


def drive_step(root: Path, keys: list | dict, until: dict | None, every: float | None, label: str,
               quick: bool = False, before: dict | None = None) -> dict | None:
    """Send keys (or one request), then wait until the screen shows ``until``, resending every
    ``every`` seconds; without ``until``, settle instead. ``quick`` (routes): a step with a screen to
    wait for sends its keys with no settle after the last one and polls fast, since the poll gates the next step.
    That holds only if the screen did not show ``until`` already: ``before`` is the last screen seen (None: unknown),
    and when it shows ``until`` the poll gates nothing, so the keys settle ROUTE_GAP seconds instead (a menu cursor
    still moving drops the next input). An ``until`` with ``gap`` S waits S more seconds once its screen shows: the
    screen is drawn before it takes input (a battle loads for seconds after its first frame). Returns the screen that matched, None after a step with nothing to wait for."""
    deadline, sent = time.monotonic() + TOUR_WAIT * (2 if until and "every" in until else 1), None
    pad = route_input(root) == "pad"
    every = every or (until or {}).get("every")
    gated = quick and until is not None and "seconds" not in until
    key_gap = ROUTE_KEY_GAP if quick else TOUR_GAP

    def drawn() -> dict | None:
        try:
            return seen if shows(seen := ask(root, {"action": "screen"}), until) else None
        except RuntimeError:  # loading: no frame ran the command
            return None

    def arrived(seen: dict) -> dict:
        if gated and until.get("gap"):
            time.sleep(until["gap"])
        return seen

    if every and until and before is not None and "seconds" not in until and shows(before, until):
        return arrived(before)  # a resend loop whose screen is up already: its key would land on that screen
    while True:
        if sent is None or (every and time.monotonic() - sent > every):
            if sent is not None and until is not None and "seconds" not in until and (seen := drawn()):
                return arrived(seen)  # the screen came up since the last poll: a resent key would land on it
            if isinstance(keys, dict):
                ask(root, keys)
            elif keys and pad:  # a player on a pad: the runtime shows the pad's prompts while it is the last input
                if not set(keys) <= set(PAD_FOR_KEY) | {"START"}:
                    raise ValueError(f"{label}: no pad button for {sorted(set(keys) - set(PAD_FOR_KEY))}")
                ask(root, {"action": "pad", "names": [PAD_FOR_KEY.get(k, k) for k in keys], "hold": 0.08, "gap": key_gap},
                    30 + len(keys) * key_gap)
                if gated and (before is None or shows(before, until)):
                    time.sleep(ROUTE_GAP)
            elif keys:
                extra = {"settle": ROUTE_GAP if before is None or shows(before, until) else 0} if gated else {}
                ask(root, {"action": "key", "key": keys[0], "hold": 0.08, **extra} if len(keys) == 1 else
                    {"action": "keys", "keys": keys, "hold": 0.08, "gap": key_gap, **extra},
                    30 + len(keys) * key_gap)
            sent = time.monotonic()
        if until is None or "seconds" in until:
            time.sleep(until["seconds"] if until else 0 if quick else TOUR_SETTLE)  # a blind route step keeps the key's 1 s settle
            return None
        if seen := drawn():
            return arrived(seen)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{label}: {until} not drawn in {TOUR_WAIT} s")
        time.sleep(ROUTE_POLL if gated else 0.5)


# The route library: the screens the tour has verified, and the moves between them, as a graph.
# `route <screen>` drives the shortest path from where the last route left the game. A screen is
# named as in TOUR; the main menu is one screen per cursor position, since its keys depend on it.
# The battle moves assume the full-unlock save (its difficulty sits on CHAOS; three Lefts reach EASY).
ROUTE_SCREENS = {
    "boot": None,  # a fresh run, before any input
    "title": {"texture": "4085.0"},
    **{f"menu@{item}": {"texture": MENU} for item in ("story", "option", "free", "dream", "vs", "survival", "gallery")},
    "options": {"texture": "4079.171"},
    "game-settings": {"texture": "4069.14", "without": "4079.149"},  # the controller page draws 4069.14 too
    "options-controller": {"texture": "4069.14", "with": "4079.149"},
    "options-controller-buttons": {"caller": "0x5983cb"},  # Button Settings: each action's name (its key 0x598418)
    "options-graphics": {"caller": "0x56cb92"},  # the resolution row's size label
    "options-advanced": {"texture": "4069.14|4069.15", "quad_caller": "0x56bff8"},  # the preset row's plates
    "story-select": {"texture": "4082.0|4082.4"},  # two columns: 4082.0 with the cursor in the left one (Shu), 4082.4 in the right (Z)
    "story-camp": {"texture": "4060.39"},  # Shu; the camp menu plate, which its submenus do not draw
    "camp-officers": {"caller": "0x54335f"},
    "camp-weapons": {"texture": "4060.36"},
    "camp-weapon-panel": {"caller": "0x59538a"},
    "camp-customize": {"texture": "4060.14", "without": "4060.39"},
    "customize-upgrade-skills": {"caller": "0x533469"},
    # both officer lists draw the same textures and texts; the header plate 4060.15 is drawn by a different stock
    # function in each (the names' x moves with the build: 263 and 284 on one, 258 and 258 on the other)
    "customize-fusion-officers": {"texture": "4060.15", "quad_caller": "0x52d841"},
    "customize-weapon-upgrade": {"texture": "4060.15", "quad_caller": "0x52967d"},
    "story-z-camp": {"texture": "4060.39"},
    "story-z-scenario": {"texture": "4077.30"},  # the strip is 4077.(30 + stage index)
    "story-z-scenario-3": {"texture": "4077.33"},  # three armies
    "story-z-narration": {"caller": "0x55f31c"},
    # the Z still event after the narration, by its page text (its plates are unnamed quads); -2 is one page on
    "story-z-still": {"caller": "0x59a785"},
    "story-z-still-2": {"caller": "0x59a785"},
    # the Orochi and Wei tabs: camps look like Shu's; strips 4077.62 (with the Orochi chapter tag 4079.158) and 4077.145
    "story-orochi-camp": {"texture": "4060.39"},
    "story-orochi-scenario": {"texture": "4077.62", "with": "4079.158"},
    "story-wei-camp": {"texture": "4060.39"},
    "story-wei-scenario": {"texture": "4077.145"},
    "story-narration": {"caller": "0x561111"},  # the main story's (Z's is 0x55f31c); its panel is the stretched one
    # the Wei story battle: the same screens as the Dream ones, but `win` here leads to the victory still, not the results
    "story-wei-pre-battle": {"texture": "4079.168"},
    "story-wei-battle": {"texture": "4079.148"},
    "story-victory-still": {"caller": "0x59c7bd"},  # the main-path still: X360 still plate with a name plate
    # a gallery movie's captions (caller 0x56a116, one draw per line) come and go; a two-line one is two draws at once
    "gallery-movie-play": {"caller": "0x56a116", "transient": True},
    "gallery-movie-two-line": {"caller": "0x56a116", "texts": 2, "transient": True},
    "gallery-treasure-guide": {"caller": "0x552f3d"},
    "camp-treasure": {"texture": "4060.43"},  # the acquired-treasure grid on the upgrade skills page
    "customize-weapon-pick": {"texture": "4060.37", "without": "4060.13"},  # the weapon list before the attribute list
    "customize-weapon-attributes": {"caller": "0x5956fa"},  # the attribute (upgrade skill) list; its text box
    "customize-fusion-weapons": {"texture": "4060.6"},  # the weapon list of the fusion; several weapons only after `unlock`
    "dream-select": {"texture": "4094.5"},
    "gallery": {"texture": "4079.169", "without": "4064.7|4064.9|4064.17|4079.105|4079.17"},  # sub-pages draw one of these
    "gallery-original-background": {"texture": "4064.7"},
    "gallery-backgrounds": {"texture": "4064.17"},
    "gallery-weapons": {"texture": "4064.9"},
    "gallery-movies": {"caller": "0x556821"},
    "vs-course": {"caller": "0x5a6774"},  # the course blurb, hidden while the match-settings panel is open
    "vs-team-select": {"texture": "4098.11", "without_caller": "0x5a6774"},  # 4098.11 stays drawn after Escape
    "survival-members": {"caller": "0x54335f"},
    "free-select": {"texture": "4061.18"},  # Free Mode's character select; its officer name is caller 0x543382
    "battle-camp": {"texture": "4079.170"},
    "briefing": {"caller": "0x548f52"},  # stock draw caller of the briefing text (its x moves with the build)
    "pre-battle": {"texture": "4079.168"},
    "pre-battle-weapons": {"caller": "0x5950ec"},
    "pre-battle-weapon-panel": {"caller": "0x59538a"},
    "pre-battle-officers": {"caller": "0x536742"},
    "battle": {"texture": "4079.148"},  # the opening dialog is up by then
    # the pre-battle menu draws its frame and 4079.44; the frame is 4079.223 on stage 1 and 4079.168 on stage 40
    "pause": {"texture": "4079.223|4079.168", "without": "4079.44"},
    "battle-history": {"texture": "4080.38"},  # the entries (caller 0x539f9f) are empty at the start of a battle
    "results": {"texture": "4079.52", "without": "4080.0"},
    "save-prompt": {"caller": "0x54e76f"},  # message box, cursor on NO
    # any story chapter's camp, chapter screen (one of the strips 4077.30 to 4077.166), pre-battle and battle
    "story-stage-camp": {"texture": "4060.39"},
    "story-stage-scenario": {"texture": "|".join(f"4077.{30 + m}" for m in range(137))},
    "story-pre-battle": {"texture": "4079.168"},
    "story-battle": {"texture": "4079.148"},
    "story-team-info": {"texture": "4080.7"},  # TEAM INFO's officer list on the story pre-battle menu
    "story-officer-page": {"caller": "0x59538a"},  # an officer's page, cursor on ABILITY ACQUISITION
    # the chapter's text pages, --stage picking the chapter: narration (WO1 4073 in chapters 72-131, WO2 4071 in 0-39),
    # the WO2 talk scene after it (4103), and the WO1 camp talk after a won battle (4105)
    # Free Mode (run 2026-10-25_1426-free1): the team's camp, the story list (4077.171 only there), a story's
    # chapters (the armies' names, as on a story chapter), then the pre-battle map and the battle the story uses
    "free-camp": {"texture": "4060.39"},
    "free-stories": {"texture": "4077.171"},
    "free-chapter": {"caller": "0x588ac8"},
    "free-pre-battle": {"texture": "4079.168"},
    "free-battle": {"texture": "4079.148"},
    "story-stage-narration": {"any": [{"caller": "0x561111"}, {"caller": "0x55f31c"}]},
    "story-stage-talk": {"caller": "0x59a785"},
    "story-victory-talk": {"any": [{"caller": "0x59c7bd"}, {"caller": "0x59a785"}]},
    "ability-acquisition": {"texture": "4080.28"},  # the officer's next ability and its conditions
    # VS: character select (three picks a side, the player picks COM's too), its confirmation, the battle (stage 68,
    # TAG TEAM) and its menu (RETRY / CHANGE CHARACTER / MAIN MENU), which Escape opens mid-match (run vs-a)
    "vs-character-select": {"texture": "4099.1"},
    "vs-confirm": {"texture": "4099.20", "without": "4099.1"},
    "vs-battle": {"texture": "4081.8"},
    "vs-pause": {"texture": "4081.40", "without": "4079.44"},
    # in-engine events with spoken lines: a caption (caller 0x49dace) is up for seconds at a time
    "opening-event": {"caller": "0x49dace", "transient": True},  # after FIGHT, on stages that have one (40)
    "victory-event": {"caller": "0x49dace", "transient": True},  # after `win`, on stages that have one (133)
}


def slow(screen: str, gap: float = 1.0) -> dict:
    """The screen's fingerprint plus a wait after it shows: it is drawn some time before it takes input."""
    return {**ROUTE_SCREENS[screen], "gap": gap}


# A route picks its stage as a player does, on the select screen: no stage is written into the game.
DREAM_STAGES = [*range(40, 68), *range(132, 144)]  # the Dream grid in cursor order, 10 a row (confirm at 0x5486EF)
DREAM_STATE = 0xC275E4  # pointer to the Dream select state; its +0x64 is the cursor index
# The four stories of the story select's left column (0x6D5710): chapter slots 0-7 on the top row, then 16-22 on
# the row below, where slot 16 + b sits under slot 1 + b. The right column is Z sets 0-3 (stages 8j to 8j + 7) and
# Orochi (32-39), one row each.
STORIES = [(107, 121, 90, 96, 82, 79, 108, 130, 76, 117, 88, 98, 127, 72, 125),
           (119, 97, 85, 95, 112, 99, 126, 128, 92, 77, 123, 104, 74, 73, 100),
           (93, 86, 110, 109, 118, 91, 116, 129, 80, 105, 103, 106, 122, 75, 87),
           (81, 124, 115, 89, 84, 94, 102, 131, 114, 83, 78, 120, 111, 113, 101)]
DREAM_STEP, STORY_STEP, CHAPTER_STEP = {"stage": "dream"}, {"stage": "story"}, {"stage": "chapter"}  # filled by route
MOVIE_STEP = {"pick": "movie"}  # filled by route: the cursor moves to --movie
NARRATION_STEP = {"pick": "narration"}  # filled by route: wait for the chapter's narration, as its stage needs
# The Gallery movie list (work/handbacks/route-cursors.md): its state at [0xC2E774] keeps the page (0-2) and the cell
# (row * 5 + column) of the cursor; pages of 15, 15 and 6 cells; the table maps a list index to the movie id played.
GALLERY_STATE, MOVIE_PAGE, MOVIE_CELL, MOVIE_IDS = 0xC2E774, 0x1A90, 0x1A8C, 0x713688
MOVIE_PAGES = (15, 15, 6)
DEFAULT_MOVIE = 102  # index 3, where the list's first route went
OFFICER_STEP = {"pick": "officers"}  # filled by route: the team --officer names
# Free Mode's character select (work/handbacks/route-cursors.md, read in run 2026-10-25_1426-free1): its state at
# [0xC275C0] keeps 8 groups of up to 15 cells holding officer ids (0x60 locked, -1 empty), the cursor's group and each
# group's remembered row, the hovered officer, and the three team slots each Return fills.
FREE_STATE, FREE_HOVER, FREE_GROUP, FREE_ROWS, FREE_COUNTS, FREE_GRID = 0xC275C0, 0x3354, 0x35E4, 0x35E8, 0x35C0, 0x33E0
FREE_NAME = "0x543382"  # the hovered officer's name
FREE_STAGE_STEP = {"pick": "free stage"}  # filled by route: the story and chapter of --stage
# Free Mode's stage select: Up and Down pick a story, Return opens its chapters; there the cursor index is at
# [[0xC54AF0] + 0xB8] and the stage under it at [[0xC54AF0] + 0xFC + 4 * index] (0x58BF11 stores it on Return).
STAGE_SELECT, STAGE_CURSOR, STAGE_LIST = 0xC54AF0, 0xB8, 0xFC
FREE_STORIES = 10  # more than the select offers, so a full turn of Down comes back round
# On the story list the chapter cursor reads 23, inside a story 0-22 (it moves the frame Return lands; run free15):
# the help bar is no sign (two-row stories show the Up/Down icon too), nor are army names (Story Z previews them)
STORY_LIST_CURSOR = 23


def title_index(stage: int) -> int:
    """The stage's title index m, shared by every stage-name texture: the chapter strip is 4077.(30 + m)."""
    return stage if stage < 68 else stage - 4 if stage <= 128 else 124 if stage <= 131 else stage - 7


def wrapped(delta: int, size: int, ahead: str, back: str) -> list[str]:
    """The fewest keys that move a cursor ``delta`` places on a wrapping row or column of ``size``."""
    delta %= size
    return [ahead] * delta if delta <= size // 2 else [back] * (size - delta)


def dream_keys(stage: int, cursor: int) -> list[str]:
    if stage not in DREAM_STAGES:
        raise ValueError(f"stage {stage} is not a Dream scenario; Dream stages are 40-67 and 132-143")
    target = DREAM_STAGES.index(stage)
    return (wrapped(target % 10 - cursor % 10, 10, "Right", "Left")
            + wrapped(target // 10 - cursor // 10, 4, "Down", "Up"))


def story_keys(stage: int) -> tuple[list[str], list[str]]:
    """The story select keys from its first row (Shu), then the chapter keys from the first chapter."""
    if stage < 32:
        return ["Right"] + ["Down"] * (stage // 8), ["Right"] * (stage % 8)
    if stage < 40:
        return ["Right", "Up"], ["Right"] * (stage - 32)
    for row, chapters in enumerate(STORIES):
        if stage in chapters:
            slot = chapters.index(stage)
            return ["Down"] * row, ["Right"] * slot if slot < 8 else ["Right"] * (slot - 7) + ["Down"]
    raise ValueError(f"stage {stage} is not a story chapter; story stages are 0-39 and 72-131")


def strip(stage: int) -> dict:
    return {"texture": f"4077.{30 + title_index(stage)}"}
# Space skips a battle's opening and closing scenes (about 70 s and 25 s in chapter 107); it plays no battle action and
# does not turn a talk page (run 2026-10-25_1512-skipkeys; Escape and Return skip neither)
SKIP = ["space"]
UNLOCK_STEP = {"action": "unlock"}  # every officer gets four weapons: the rest of the run is no longer the plain save
# (from, to, steps): each step is (keys or one request, screen to wait for); a bare key list is one
# step that waits for the destination screen. A move up a level that leaves a submenu cursor on the
# item it opened ends with the keys that put the cursor back, so the move down works again.
# An edge enters this table only after it worked in a run; each group names its run (2026-10-11_
# runs, since removed from work/runtime-tests/). A back edge that sends several keys sends a key that a
# menu still sliding in drops, so one that has to leave the cursor on an item sends each key as its
# own step. A fingerprint that is only drawn while a screen slides in (not steady) breaks the next route.
# Drop an edge that fails twice.
ROUTE_EDGES = [
    # the tour, 0202-tour; 0207-B
    ("boot", "title", [(["Escape"], {**ROUTE_SCREENS["title"], "every": 4})]),  # skips the logos and movie
    ("title", "menu@story", ["Return"]),
    ("menu@story", "menu@option", ["Up", "Up"]),
    ("menu@option", "menu@story", ["Down", "Down"]),
    ("menu@story", "menu@dream", ["Down", "Down"]),
    ("menu@dream", "menu@story", ["Up", "Up"]),
    ("menu@dream", "menu@gallery", ["Down", "Down", "Down"]),
    ("menu@gallery", "menu@dream", ["Up", "Up", "Up"]),
    ("menu@option", "options", ["Return"]),
    ("options", "game-settings", ["Return"]),
    ("game-settings", "menu@option", ["Escape", "Escape"]),
    ("menu@story", "story-select", ["Return"]),
    ("story-select", "story-camp", ["Return"]),
    ("story-camp", "story-select", ["Escape"]),
    ("story-select", "menu@story", ["Escape"]),
    ("menu@dream", "dream-select", ["Return"]),
    ("dream-select", "menu@dream", ["Escape"]),
    ("menu@gallery", "gallery", ["Return"]),
    ("gallery", "menu@gallery", ["Escape"]),
    ("dream-select", "battle-camp", [(DREAM_STEP, None), *[([key], None) for key in ("Return", "Left", "Left", "Left",
                                                                                 "Return")],
                                     (["Return"], slow("battle-camp", 2.0))]),  # first scenario, EASY, team; the story camp's plates fade out for seconds after its fingerprint shows
    ("battle-camp", "briefing", ["Up", "Return"]),  # NEXT
    ("briefing", "pre-battle", ["Escape"]),
    # FIGHT; the battle's fingerprint shows at its first frame, and the first Escape in the next seconds is lost
    ("pre-battle", "battle", [(["Up", "Return"], None), (SKIP, {**slow("battle", 3.0), "every": 3})]),
    ("battle", "pause", ["Escape"]),
    ("pause", "battle", ["Escape"]),
    # VS: a row refuses a character it already has; Down changes the force, so the third pick is another force's
    ("vs-team-select", "vs-character-select", ["Return"]),
    ("vs-character-select", "vs-confirm", [*[([key], None) for key in ("Return", "Right", "Return", "Down", "Return",
                                                                      "Return", "Right", "Return", "Right")],
                                           (["Return"], ROUTE_SCREENS["vs-confirm"])]),
    ("vs-confirm", "vs-battle", [(["Return"], slow("vs-battle", 3.0))]),
    ("vs-battle", "vs-pause", ["Escape"]),
    # MAIN MENU is the last item, and the cursor starts on the first: Up wraps to it (Down Down hit CHANGE CHARACTER
    # in the menu Escape opens mid-match, run fid-vsback)
    ("vs-pause", "menu@vs", [(["Up"], None), (["Return"], slow("save-prompt")),
                             (["Left"], None), (["Return"], slow("menu@vs", 3.0))]),
    # the events play themselves: the battle follows the opening one, and Escape skips the victory one (run fid-win)
    ("pre-battle", "opening-event", [(["Up", "Return"], {**ROUTE_SCREENS["opening-event"], "gap": 0})]),
    ("opening-event", "battle", [([], slow("battle", 3.0))]),
    ("battle", "victory-event", [({"action": "win"}, {**ROUTE_SCREENS["victory-event"], "gap": 0})]),
    ("victory-event", "results", [(["Escape"], {**slow("results", 2.0), "every": 3})]),
    # MAIN MENU ends the battle after a prompt whose cursor starts on NO; it names the title screen but leads to the
    # main menu, cursor on DREAM MODE (run fid-exit)
    ("pre-battle", "menu@dream", [*[([key], None) for key in ("Up", "Up")], (["Return"], slow("save-prompt")),
                                  (["Left"], None), (["Return"], ROUTE_SCREENS["menu@dream"])]),
    # from a battle a second prompt, cursor on NO again, warns that the battle is lost unless it was saved (fid-exit2)
    ("pause", "menu@dream", [*[([key], None) for key in ("Up", "Up")], (["Return"], slow("save-prompt")),
                             (["Left"], None), (["Return"], {"seconds": 1.0}),
                             (["Left"], None), (["Return"], ROUTE_SCREENS["menu@dream"])]),
    # the results screen takes no input while it counts up; a stage's victory event (caption caller 0x49dace) comes
    # first and waits, and Escape skips it as a player can (run fid-win, stage 133)
    ("battle", "results", [({"action": "win"}, {"any": [ROUTE_SCREENS["results"], {"caller": "0x49dace"}]}),
                           (["Escape"], {**slow("results", 2.0), "every": 3})]),
    ("results", "save-prompt", ["Return"]),
    ("save-prompt", "menu@dream", [(["Return"], None), (["Return"], ROUTE_SCREENS["menu@dream"])]),
    # 1107-B: VS and Survival menus, the options pages
    ("menu@story", "menu@vs", ["Down", "Down", "Down"]),
    ("menu@vs", "menu@story", ["Up", "Up", "Up"]),
    ("menu@vs", "menu@survival", ["Down"]),
    ("menu@survival", "menu@vs", ["Up"]),
    ("menu@vs", "vs-course", ["Return"]),
    ("vs-course", "menu@vs", ["Escape"]),
    ("vs-course", "vs-team-select", ["Return"]),
    ("vs-team-select", "vs-course", ["Escape"]),
    # Free Mode, the item above DREAM MODE (runs sanzang-fix, 2026-10-24_1558-sanzang-route)
    ("menu@dream", "menu@free", ["Up"]),
    ("menu@free", "menu@dream", ["Down"]),
    ("menu@free", "free-select", ["Return"]),
    ("free-select", "menu@free", ["Escape"]),
    ("menu@survival", "survival-members", ["Return"]),
    ("survival-members", "menu@survival", ["Escape"]),
    ("options", "options-controller", ["Down", "Return", "Return"]),  # also 0207-B
    ("options-controller", "options", [(["Escape"], None), (["Escape"], None), (["Up"], None)]),  # a sub list sits between; Up undoes the Down
    # 2026-10-25_1641-controls: Button Settings, the fourth row; `screen` there lists every binding as text
    ("options-controller", "options-controller-buttons", ["Down", "Down", "Down", "Return"]),
    ("options-controller-buttons", "options-controller", [(["Escape"], ROUTE_SCREENS["options-controller"]),
                                                          (["Up"], None), (["Up"], None), (["Up"], None)]),
    ("options", "options-graphics", ["Down", "Down", "Down", "Return"]),
    ("options-graphics", "options", [(["Escape"], None), (["Up", "Up", "Up"], None)]),
    # from the first row; in a window (the harness) the cursor skips the greyed BRIGHTNESS row
    ("options-graphics", "options-advanced", ["Down", "Down", "Down", "Return"]),
    ("options-advanced", "options-graphics", [(["Escape"], None)]),
    # 0211-B2: the gallery pages
    ("gallery", "gallery-backgrounds", ["Return", "Return"]),
    ("gallery-backgrounds", "gallery", ["Escape", "Escape"]),
    ("gallery", "gallery-original-background", ["Down", "Return"]),
    ("gallery-original-background", "gallery", ["Escape", "Up"]),
    ("gallery", "gallery-weapons", ["Down", "Down", "Return"]),
    ("gallery-weapons", "gallery", ["Escape", "Up", "Up"]),
    ("gallery", "gallery-movies", ["Down", "Down", "Down", "Return"]),
    ("gallery-movies", "gallery", ["Escape", "Up", "Up", "Up"]),
    # 1107-C, 1107-C2, 1107-C4: the Shu camp and its pages
    ("story-select", "story-z-camp", ["Right", "Return"]),
    ("story-z-camp", "story-select", [(["Escape"], None), (["Left"], {"texture": "4082.0"})]),  # left column
    ("story-camp", "camp-officers", ["Return"]),
    ("camp-officers", "story-camp", ["Escape"]),
    ("story-camp", "camp-weapons", ["Down", "Return"]),
    ("camp-weapons", "story-camp", ["Escape", "Up"]),
    ("camp-weapons", "camp-weapon-panel", ["Return"]),
    ("camp-weapon-panel", "camp-weapons", ["Escape"]),
    ("story-camp", "camp-customize", ["Down", "Down", "Down", "Down", "Return"]),
    ("camp-customize", "story-camp", ["Escape", "Down", "Down"]),
    ("camp-customize", "customize-upgrade-skills", ["Return"]),
    ("customize-upgrade-skills", "camp-customize", ["Escape"]),
    ("camp-customize", "customize-fusion-officers", ["Down", "Return"]),
    ("customize-fusion-officers", "camp-customize", ["Escape", "Up"]),
    ("camp-customize", "customize-weapon-upgrade", ["Down", "Down", "Return"]),
    ("customize-weapon-upgrade", "camp-customize", ["Escape", "Up", "Up"]),
    # 1107-C2, 1107-C4: the Z camp and its scenario select (Z narration is a dead end)
    ("story-z-camp", "story-z-scenario", ["Up", "Return"]),  # NEXT
    ("story-z-scenario", "story-z-camp", ["Escape", "Down"]),
    ("story-z-scenario", "story-z-scenario-3", ["Right", "Right", "Right"]),
    ("story-z-scenario-3", "story-z-scenario", ["Left", "Left", "Left"]),
    ("story-z-scenario", "story-z-narration", [(["Right"], None), (["Return"], None),
                                              (["Left", "Left", "Left", "Return"], ROUTE_SCREENS["story-z-narration"])]),
    # fixups3 runs (see work/fixups3/HANDOFF.md): the Z still pages after the narration, also dead ends
    ("story-z-narration", "story-z-still", [(["Return"], {**ROUTE_SCREENS["story-z-still"], "every": 2})]),
    ("story-z-still", "story-z-still-2", [(["Return"], {"seconds": 3}), ([], ROUTE_SCREENS["story-z-still-2"])]),
    # run zz-ex8, zz-routes-*: the Orochi and Wei tabs; the Wei narration, its battle and the victory still (all no way back)
    ("story-select", "story-orochi-camp", ["Right", "Up", "Return"]),
    ("story-orochi-camp", "story-select", [(["Escape"], None), (["Left"], {"texture": "4082.0"})]),
    ("story-orochi-camp", "story-orochi-scenario", ["Up", "Return"]),  # NEXT
    ("story-orochi-scenario", "story-orochi-camp", ["Escape", "Down"]),
    ("story-select", "story-wei-camp", ["Down", "Return"]),
    ("story-wei-camp", "story-select", [(["Escape"], None), (["Up"], {"texture": "4082.0"})]),
    ("story-wei-camp", "story-wei-scenario", ["Up", "Return"]),
    ("story-wei-scenario", "story-wei-camp", ["Escape", "Down"]),
    # difficulty page, EASY, then the opening movies: Escape skips one each, so it is resent until the narration shows
    ("story-wei-scenario", "story-narration", [(["Return"], None), (["Left", "Left", "Left", "Return"], None),
                                               (["Escape"], {**ROUTE_SCREENS["story-narration"], "every": 3})]),
    ("story-narration", "story-wei-pre-battle", ["Escape"]),  # End
    ("story-wei-pre-battle", "story-wei-battle", [(["Up", "Return"], None), (SKIP, {**slow("story-wei-battle", 3.0), "every": 3})]),
    ("story-wei-battle", "story-victory-still", [({"action": "win"}, None), (SKIP, {**ROUTE_SCREENS["story-victory-still"], "every": 3})]),
    # the gallery's treasure guide, and movies by position (Left or Right x19 reaches the first or last of the 19)
    ("gallery", "gallery-treasure-guide", ["Down", "Down", "Down", "Down", "Return"]),
    ("gallery-treasure-guide", "gallery", ["Escape", "Up", "Up", "Up", "Up"]),
    ("gallery-movies", "gallery-movie-play", [(MOVIE_STEP, None),
                                              (["Return"], {**ROUTE_SCREENS["gallery-movie-play"], "gap": 0})]),
    ("gallery-movies", "gallery-movie-two-line", [(["Right"] * 19 + ["Left"] * 3 + ["Return"],
                                                  {**ROUTE_SCREENS["gallery-movie-two-line"], "gap": 0})]),
    ("gallery-movie-play", "gallery-movies", ["Escape"]),
    ("gallery-movie-two-line", "gallery-movies", ["Escape"]),
    # customize: the weapon upgrade's weapon and attribute lists; the fusion's weapons (`unlock` gives four each); the treasure grid
    ("customize-weapon-upgrade", "customize-weapon-pick", ["Return"]),
    ("customize-weapon-pick", "customize-weapon-upgrade", ["Escape"]),
    ("customize-weapon-pick", "customize-weapon-attributes", ["Return"]),
    ("customize-weapon-attributes", "customize-weapon-pick", ["Escape"]),
    ("customize-fusion-officers", "customize-fusion-weapons", [(UNLOCK_STEP, None), (["Return"], ROUTE_SCREENS["customize-fusion-weapons"])]),
    ("customize-fusion-weapons", "customize-fusion-officers", ["Escape"]),
    ("customize-upgrade-skills", "camp-treasure", ["Tab"]),
    ("camp-treasure", "customize-upgrade-skills", ["Escape"]),
    # 1107-D, 1107-D2, 1107-D3: the pre-battle pages and the pause pages
    ("pre-battle", "pre-battle-weapons", ["Return"]),
    ("pre-battle-weapons", "pre-battle", ["Escape"]),
    ("pre-battle-weapons", "pre-battle-weapon-panel", ["Return"]),
    ("pre-battle-weapon-panel", "pre-battle-weapons", ["Escape"]),
    ("pre-battle", "pre-battle-officers", ["Down", "Down", "Down", "Down", "Return"]),
    ("pre-battle-officers", "pre-battle", ["Escape", "Down", "Down", "Down"]),  # 7 items: 4 + 3 wraps to the top
    ("pause", "battle-history", ["Down", "Down", "Down", "Return"]),
    ("battle-history", "pause", ["Escape", "Up", "Up", "Up"]),
    # any story chapter, picked on its chapter screen (no way back: a run plays one story chapter)
    ("story-select", "story-stage-camp", [(STORY_STEP, None), (["Return"], ROUTE_SCREENS["story-stage-camp"])]),
    # NEXT, then the chapter's strip; keys sent while the chapter screen slides in are lost (run v-s76)
    ("story-stage-camp", "story-stage-scenario", [(["Up", "Return"], slow("story-stage-scenario", ARRIVE_GAP)),
                                                  (CHAPTER_STEP, None)]),
    # difficulty page, EASY, then any opening movie, narration or still page until the pre-battle screen
    ("story-stage-scenario", "story-pre-battle", [(["Return"], None), (["Left", "Left", "Left", "Return"], None),
                                                  (["Escape"], {**ROUTE_SCREENS["story-pre-battle"], "every": 3})]),
    ("story-pre-battle", "story-battle", [(["Up", "Return"], None), (SKIP, {**slow("story-battle", 3.0), "every": 3})]),
    # Free Mode: three officers (each Return fills the next team slot), NEXT, the story and chapter, Return twice (the
    # second takes the difficulty the cursor starts on), then the battle (run 2026-10-25_1426-free1)
    ("free-select", "free-camp", [(OFFICER_STEP, None), ([], slow("free-camp", ARRIVE_GAP))]),
    ("free-camp", "free-stories", [(["Up", "Return"], slow("free-stories", ARRIVE_GAP))]),
    ("free-stories", "free-chapter", [(FREE_STAGE_STEP, None)]),
    ("free-chapter", "free-pre-battle", [(["Return"], None), (["Return"], slow("free-pre-battle", ARRIVE_GAP))]),
    ("free-pre-battle", "free-battle", [(["Up", "Return"], None), (SKIP, {**slow("free-battle", 3.0), "every": 3})]),
    # a chapter's pages: Escape skips any opening movie until the narration shows; Return (Next) pages through any
    # narration to the talk. Chapters differ: Z chapter 0 has a talk and no narration (run 2026-10-25_1413-ztalk)
    ("story-stage-scenario", "story-stage-narration", [(["Return"], None), (["Left", "Left", "Left", "Return"], None),
                                                       (NARRATION_STEP, None)]),
    ("story-stage-scenario", "story-stage-talk", [(["Return"], None), (["Left", "Left", "Left", "Return"], None),
                                                  (["Return"], {**ROUTE_SCREENS["story-stage-talk"], "every": 2})]),
    ("story-stage-narration", "story-stage-talk", [(["Return"], {**ROUTE_SCREENS["story-stage-talk"], "every": 2})]),
    ("story-stage-narration", "story-pre-battle", [(["Escape"], {**ROUTE_SCREENS["story-pre-battle"], "every": 3})]),
    ("story-stage-talk", "story-pre-battle", [(["Escape"], {**ROUTE_SCREENS["story-pre-battle"], "every": 3})]),
    ("story-battle", "story-victory-talk", [({"action": "win"}, None), (SKIP, {**ROUTE_SCREENS["story-victory-talk"], "every": 3})]),
    # 2026-10-22_2103-route-ability: Ability Acquisition of the first officer (the save picks which ability shows:
    # work/saves/ability-next-N); TEAM INFO is the third item, so the way back moves the cursor up to the top
    ("story-pre-battle", "story-team-info", ["Down", "Down", "Return"]),
    ("story-team-info", "story-officer-page", ["Return"]),
    ("story-officer-page", "ability-acquisition", ["Return"]),
    ("ability-acquisition", "story-officer-page", ["Escape"]),
    ("story-officer-page", "story-team-info", ["Escape"]),
    ("story-team-info", "story-pre-battle", [(["Escape"], ROUTE_SCREENS["story-pre-battle"]), (["Up"], None),
                                             (["Up"], None)]),
]
NAVIGATION = {"key", "keys", "pad", "click", "win", "lose"}  # actions that can move the game off a screen


def route_plan(start: str, target: str) -> list[tuple]:
    """The fewest edges from start to target; target "menu" is any main-menu cursor position."""
    goal = (lambda node: node.startswith("menu@")) if target == "menu" else (lambda node: node == target)
    if target != "menu" and target not in ROUTE_SCREENS:
        raise ValueError(f"no screen {target!r} in the route library")
    paths, frontier = {start: []}, [start]
    while frontier:
        node = frontier.pop(0)
        if goal(node):
            return paths[node]
        for edge in ROUTE_EDGES:
            if edge[0] == node and edge[1] not in paths:
                paths[edge[1]] = paths[node] + [edge]
                frontier.append(edge[1])
    raise ValueError(f"no route from {start} to {target}")


def route_position(root: Path) -> str | None:
    """The screen the last route left the game on: "boot" for a run with no input yet, None once
    input was sent after it (by hand, or by a route that then failed)."""
    journal = root / "evidence/actions.jsonl"
    lines = journal.read_text().splitlines() if journal.is_file() else []
    state_path = root / "evidence/route.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {"at": "boot", "journal": 0}
    if any(json.loads(line)["request"]["action"] in NAVIGATION for line in lines[state["journal"]:]):
        return None
    return state["at"]


def dream_cursor(root: Path) -> int:
    state = int.from_bytes(bytes.fromhex(ask(root, {"action": "peek", "address": DREAM_STATE, "length": 4})["hex"]), "little")
    return int.from_bytes(bytes.fromhex(ask(root, {"action": "peek", "address": state + 0x64, "length": 4})["hex"]), "little")


def peek_u32(root: Path, address: int) -> int:
    return int.from_bytes(bytes.fromhex(ask(root, {"action": "peek", "address": address, "length": 4})["hex"]), "little")


def movie_id(movie: int) -> int:
    """The game's id for movieNNN.wmv: NNN below 36, 100-109 from 36, 200 and up from 46."""
    if 0 <= movie < 36:
        return movie
    if 100 <= movie < 110:
        return 36 + movie - 100
    if movie >= 200:
        return 46 + movie - 200
    raise ValueError(f"movie{movie:03d} is no movie the game names")


def movie_key(page: int, cell: int, target: int) -> str:
    """One key that moves the Gallery cursor toward list index ``target``: Right and Left cross pages from the last and
    first column; Up and Down wrap over the page's rows; a column a row lacks is left first (the last page's second
    row has only column 0)."""
    target_page, target_cell = divmod(target, 15)
    if target_page != page:
        return "Right" if target_page > page else "Left"
    (row, col), (target_row, target_col) = divmod(cell, 5), divmod(target_cell, 5)
    rows = -(-MOVIE_PAGES[page] // 5)
    if row != target_row and target_row * 5 + col < MOVIE_PAGES[page]:
        return "Down" if (target_row - row) % rows <= rows // 2 else "Up"
    if col != target_col:
        return "Right" if target_col > col else "Left"
    return "Down" if (target_row - row) % rows <= rows // 2 else "Up"


def pick_movie(root: Path, movie: int, label: str, before: dict | None = None) -> None:
    """Move the Gallery movie cursor onto movieNNN a key at a time, reading the game's own cursor after each."""
    ids = struct.unpack(f"<{sum(MOVIE_PAGES)}I", bytes.fromhex(
        ask(root, {"action": "peek", "address": MOVIE_IDS, "length": 4 * sum(MOVIE_PAGES)})["hex"]))
    if movie_id(movie) not in ids:
        raise ValueError(f"movie{movie:03d} is not in the Gallery's movie list")
    target, state = ids.index(movie_id(movie)), peek_u32(root, GALLERY_STATE)
    for _ in range(2 * sum(MOVIE_PAGES)):
        page, cell = peek_u32(root, state + MOVIE_PAGE), peek_u32(root, state + MOVIE_CELL)
        if page * 15 + cell == target:
            return
        before = drive_step(root, [movie_key(page, cell, target)], None, None, label, quick=True, before=before)
    raise RuntimeError(f"the Gallery cursor did not reach movie{movie:03d} (list index {target})")


def free_select(root: Path) -> dict:
    """Free Mode's character select as the game holds it: grid (group -> officer ids), cursor and hovered officer."""
    state = peek_u32(root, FREE_STATE)

    def u32s(offset: int, count: int) -> tuple[int, ...]:
        raw = ask(root, {"action": "peek", "address": state + offset, "length": 4 * count})["hex"]
        return struct.unpack(f"<{count}I", bytes.fromhex(raw))

    counts, cells = u32s(FREE_COUNTS, 8), u32s(FREE_GRID, 8 * 15)
    group = u32s(FREE_GROUP, 1)[0]
    return {"grid": [list(cells[g * 15:g * 15 + counts[g]]) for g in range(8)], "group": group,
            "row": u32s(FREE_ROWS + 4 * group, 1)[0], "hover": u32s(FREE_HOVER, 1)[0]}


def officer_key(seen: dict, officer: int) -> str | None:
    """One key toward ``officer`` on the character select: Up and Down step the group (wrapping over 8), Left and Right
    the row (wrapping over the group); None when it is hovered."""
    if seen["hover"] == officer:
        return None
    group = next(g for g, ids in enumerate(seen["grid"]) if officer in ids)
    if group != seen["group"]:
        return "Down" if (group - seen["group"]) % 8 <= 4 else "Up"
    size, row = len(seen["grid"][group]), seen["grid"][group].index(officer)
    return "Right" if (row - seen["row"]) % size <= size // 2 else "Left"


def hover_officer(root: Path, officer: int, label: str) -> None:
    for _ in range(24):
        seen = free_select(root)
        if (key := officer_key(seen, officer)) is None:
            return
        drive_step(root, [key], None, None, label, quick=True)
    raise RuntimeError(f"the character select cursor did not reach officer {officer}")


def officer_by_name(root: Path, name: str, label: str) -> int:
    """The id of the officer the select shows as ``name``, found by hovering each until its name is drawn."""
    for officer in [o for ids in free_select(root)["grid"] for o in ids if o < 0x60]:
        hover_officer(root, officer, label)
        texts = ask(root, {"action": "screen"})["texts"]
        if any(t["caller"] == FREE_NAME and t["text"].casefold() == name.casefold() for t in texts):
            return officer
    raise ValueError(f"no officer named {name!r} on the character select")


def pick_officers(root: Path, names: list[str], label: str) -> None:
    """Fill Free Mode's three team slots: the named officers (a name, or an id), then the first others unlocked."""
    if len(names) > 3:
        raise ValueError("a Free Mode team has three officers")
    team = [int(n) if n.isdigit() else officer_by_name(root, n, label) for n in names]
    if len(set(team)) < len(team):
        raise ValueError("pick three different officers")
    team += [o for ids in free_select(root)["grid"] for o in ids if o < 0x60 and o not in team][:3 - len(team)]
    for officer in team:
        hover_officer(root, officer, label)
        drive_step(root, ["Return"], None, None, label, quick=True)


def stage_under_cursor(root: Path) -> tuple[int, int]:
    state = peek_u32(root, STAGE_SELECT)
    index = peek_u32(root, state + STAGE_CURSOR)
    return index, peek_u32(root, state + STAGE_LIST + 4 * index)


def pick_free_stage(root: Path, stage: int | None, label: str) -> None:
    """Open the Free Mode chapter of ``stage`` (None: the story and chapter the select starts on). Right and Left on the
    story list swap the main stories and the Story Z ones, Down steps through a column's stories; a story's chapters
    run Right along the top row, then Down to the second row's end and Left (run 2026-10-25_1446-free4)."""
    def press(key: str) -> None:
        drive_step(root, [key], None, None, label, quick=True)

    def moved(key: str) -> bool:  # press, and give the game a moment to move the cursor
        index, deadline = stage_under_cursor(root)[0], time.monotonic() + 1.5
        press(key)
        while stage_under_cursor(root)[0] == index:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
        return True

    def walk(key: str) -> bool:  # along a row until the stage is under the cursor, or the row ends
        while stage_under_cursor(root)[1] != stage:
            if not moved(key):
                return False
        return True

    def level(chapters: bool, key: str) -> None:  # press until the cursor is in that list (a key sent while the
        # list slides in is lost); the story list parks the chapter cursor at STORY_LIST_CURSOR
        press(key)
        deadline, again = time.monotonic() + 15, time.monotonic() + 3
        while (stage_under_cursor(root)[0] != STORY_LIST_CURSOR) != chapters:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"{key} did not open the Free Mode {'chapters' if chapters else 'stories'}")
            if time.monotonic() >= again:
                press(key)
                again = time.monotonic() + 3
            time.sleep(0.1)
        time.sleep(0.5)  # the list takes input a moment after its cursor is set

    level(True, "Return")
    if stage is None:
        return
    for _ in range(FREE_STORIES):
        if (stage_under_cursor(root)[1] < 40) != (stage < 40):  # the other column
            level(False, "Escape")
            press("Right")
            level(True, "Return")
            continue
        if walk("Right"):
            return
        if moved("Down") and walk("Left"):
            return
        level(False, "Escape")
        press("Down")
        level(True, "Return")
    raise ValueError(f"stage {stage} is in no Free Mode story")


def pick_dream(root: Path, stage: int, label: str, before: dict | None = None) -> None:
    """Move the Dream select cursor to the stage's scenario, from wherever it is, and check the game's own cursor."""
    drive_step(root, dream_keys(stage, dream_cursor(root)), None, None, label, quick=True, before=before)
    if (cursor := dream_cursor(root)) != DREAM_STAGES.index(stage):
        raise RuntimeError(f"the Dream cursor is on scenario {cursor}, not {DREAM_STAGES.index(stage)} (stage {stage})")


def route(root: Path, target: str, stage: int | None = None, movie: int | None = None,
          officers: list[str] | None = None) -> int:
    """Drive the game to ``target`` on a running --test build; print the screens passed."""
    state_path = local_path(root, "evidence/route.json")

    def settle(at: str) -> None:
        journal = root / "evidence/actions.jsonl"
        count = len(journal.read_text().splitlines()) if journal.is_file() else 0
        write_json(state_path, {"at": at, "journal": count})

    start = route_position(root)
    try:
        if start is None:
            raise ValueError("position unknown: input was sent outside a route, or a route failed; "
                             "start a fresh run")
        if start == "boot":  # the title can be up already (a `wait` for it): its Escape would leave it for the menu
            try:
                start = "title" if shows(ask(root, {"action": "screen"}), ROUTE_SCREENS["title"]) else start
            except RuntimeError:  # still loading
                pass
        plan = route_plan(start, target)
        steps = [step[0] for edge in plan if not isinstance(edge[2][0], str) for step in edge[2]]
        if DREAM_STEP in steps:
            stage = DREAM_STAGES[0] if stage is None else stage
            dream_keys(stage, 0)  # refuses a stage that is no Dream scenario before any key is sent
        if STORY_STEP in steps:
            if stage is None:
                raise ValueError(f"route {target} needs --stage, the story chapter to play")
            story_keys(stage)
        seen = None
        if start != "boot":
            seen = ask(root, {"action": "screen"})
            if not ROUTE_SCREENS[start].get("transient") and not shows(seen, ROUTE_SCREENS[start]):  # a caption is gone again
                raise ValueError(f"the screen no longer shows {start}; start a fresh run")
        passed = [start]
        for source, destination, steps in plan:
            moves = [(steps, ROUTE_SCREENS[destination])] if isinstance(steps[0], str) else steps
            for n, (keys, until) in enumerate(moves):
                if keys is DREAM_STEP:
                    pick_dream(root, stage, f"{source} -> {destination}", seen)
                    continue
                if keys is OFFICER_STEP:
                    pick_officers(root, officers or [], f"{source} -> {destination}")
                    continue
                if keys is FREE_STAGE_STEP:
                    pick_free_stage(root, stage, f"{source} -> {destination}")
                    continue
                if keys is NARRATION_STEP:
                    # a main-story chapter may open with a movie, which Escape skips; a Story Z chapter fades its
                    # narration in for about 4 s, and Escape in that time cancels it (0x55FF49; handbacks/wo2-narration.md)
                    narration = ROUTE_SCREENS["story-stage-narration"]
                    seen = drive_step(root, [], narration, None, f"{source} -> {destination}", quick=True, before=seen) \
                        if stage < 40 else drive_step(root, ["Escape"], {**narration, "every": 3}, None,
                                                      f"{source} -> {destination}", quick=True, before=seen)
                    continue
                if keys is MOVIE_STEP:
                    pick_movie(root, DEFAULT_MOVIE if movie is None else movie, f"{source} -> {destination}", seen)
                    continue
                if source in PAUSE_SOURCES and isinstance(keys, list) and route_input(root) == "pad":
                    keys = ["START" if k == "Escape" else k for k in keys]
                if keys is STORY_STEP:
                    keys = story_keys(stage)[0]
                elif keys is CHAPTER_STEP:
                    keys, until = story_keys(stage)[1], strip(stage)
                if n == len(moves) - 1 and until is not None and "seconds" not in until:  # arriving: the screen is up before it takes input
                    until = {"gap": MENU_SETTLE if destination.startswith("menu@") else ARRIVE_GAP, **until}
                seen = drive_step(root, keys, until, None, f"{source} -> {destination}", quick=True, before=seen)
            passed.append(destination)
            settle(destination)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"game-harness: route to {target}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"at": passed[-1], "passed": passed}))
    return 0


def stage_chain() -> set[str]:
    """The screens from the Dream scenario's team page to the save prompt, where the stage picked
    at the scenario select is already loaded: those reachable from battle-camp without the title or a menu."""
    seen, todo = {"battle-camp"}, ["battle-camp"]
    while todo:
        node = todo.pop()
        for source, destination, _ in ROUTE_EDGES:
            if source == node and destination != "title" and not destination.startswith("menu@") and destination not in seen:
                seen.add(destination)
                todo.append(destination)
    return seen


def check_legs(position: str, loaded: int | None, name: str, battle: int | None, script: int | None) -> tuple:
    """The route screens a check screen goes through, from ``position`` with stage ``loaded`` in the
    battle chain: (legs, edge count, end position, loaded stage after, whether it unlocks the officers' weapons,
    which changes every weapon screen after it). A script fires in the battle, so a
    screen with one goes there first; a screen needing another stage than the loaded one goes back through the
    Dream scenario select first. Raises ValueError when a leg has no route."""
    need = DREAM_STAGES[0] if battle is None else battle
    legs = ["dream-select"] if position in stage_chain() and loaded != need else []
    legs += ["battle"] if script is not None and name != "battle" else []
    legs.append(name)
    cost, unlocks = 0, False
    for leg in legs:
        plan = route_plan(position, leg)
        cost += len(plan)
        unlocks |= any(step[0] is UNLOCK_STEP for edge in plan if not isinstance(edge[2][0], str) for step in edge[2])
        if any(step[0] is DREAM_STEP for edge in plan if not isinstance(edge[2][0], str) for step in edge[2]):
            loaded = need
        position = plan[-1][1] if plan else position
    return legs, cost, position, loaded, unlocks


def order_screens(screens: list[dict], position: str | None) -> list[dict]:
    """Greedy visit order for a check: the screen with the fewest route edges from where the last one
    ended, a screen that ends where no route leads back to the menu (story-z-narration) after all others, a screen
    whose route unlocks the officers' weapons (the fusion weapon list) after all that leave the save as it is,
    spec order breaking ties. Screens with no route from here keep spec order at the end."""
    left, order, loaded = list(enumerate(screens)), [], None
    while position is not None and left:
        options = []
        for index, sc in left:
            try:
                legs, cost, end, after, unlocks = check_legs(position, loaded, sc["route"], sc.get("battle"), sc.get("script"))
            except ValueError:
                continue
            try:
                route_plan(end, "menu")
                dead_end = False
            except ValueError:
                dead_end = True
            options.append((dead_end, unlocks, cost, index, end, after))
        if not options:
            break
        _, _, _, index, position, loaded = min(options)
        order.append(screens[index])
        left = [(i, sc) for i, sc in left if i != index]
    return order + [sc for _, sc in left]


class CheckDriver:
    """Reaches check screens only by route, so every screen in a spec is one the route table knows."""

    def __init__(self, root: Path) -> None:
        self.root, self.count, self.loaded = root, 0, None
        # test shortcuts that put the game where a player gets only by playing (win, unlock, a fired script):
        # the report names them for each screen, as its capture is real but not a typical one
        self.unlocked, self.used, self.name, self.shortcuts = False, [], None, {}

    def route(self, name: str, fixture: str | None = None, battle: int | None = None,
              script: int | None = None) -> None:
        if fixture:
            seeded = self.root / "fixtures.json"
            if not seeded.is_file() or fixture not in seeded.read_text():
                raise ValueError(f"needs save fixture {fixture!r}; stage the run with --save {fixture}")
        stage = battle
        position = route_position(self.root)
        used = set()
        if position is None:  # route() refuses to send anything, and the screen is reported unreachable
            legs, after = [name], self.loaded
        else:
            legs, _, _, after, _ = check_legs(position, self.loaded, name, battle, script)
            at = position
            for leg in legs:
                plan = route_plan(at, leg)
                used |= {step[0]["action"] for edge in plan if not isinstance(edge[2][0], str) for step in edge[2]
                         if isinstance(step[0], dict) and step[0].get("action") in ("win", "lose", "unlock")}
                at = plan[-1][1] if plan else at
        self.unlocked |= "unlock" in used
        self.name = name
        self.used = sorted(used - {"unlock"} | ({"unlock"} if self.unlocked else set())
                           | ({f"script {script}"} if script is not None else set()))
        for leg in legs:
            if route(self.root, leg, stage):
                raise RuntimeError(f"route to {leg} failed")
            if script is not None and leg == "battle":  # the script fires in the battle, before the route goes on
                self.fire(script)
        self.loaded = after

    def notice(self, text: str) -> None:
        ask(self.root, {"action": "notice", "text": text})

    def spawn(self, count: int) -> dict:
        """Enemy troops around the player, in the battle the route reached; the rest of the run keeps them."""
        self.used = sorted(set(self.used) | {f"spawn {count}"})
        self.shortcuts[self.name] = self.used
        return ask(self.root, {"action": "spawn", "count": count})

    def keys(self, names: list[str]) -> None:
        """Keys within a screen (a cursor); the route position holds while the screen still shows."""
        at = route_position(self.root)
        for name in names:
            ask(self.root, {"action": "key", "key": name, "hold": 0.08})
            time.sleep(ROUTE_GAP)
        if at in ROUTE_SCREENS and shows(ask(self.root, {"action": "screen"}), ROUTE_SCREENS[at]):
            journal = self.root / "evidence/actions.jsonl"
            write_json(local_path(self.root, "evidence/route.json"),
                       {"at": at, "journal": len(journal.read_text().splitlines()) if journal.is_file() else 0})

    def fire(self, script: int, seconds: float = 60) -> None:
        """Send a battle script, retrying while the opening dialog still plays and the game refuses it."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                ask(self.root, {"action": "script", "number": script})
                return
            except RuntimeError as exc:
                if "retry" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(1)

    def wait(self, until: dict) -> bool:
        deadline, steady = time.monotonic() + until.get("timeout", 10), []
        while time.monotonic() < deadline:
            seen = ask(self.root, {"action": "screen"})
            if not shows(seen, until):
                steady = []
            elif "box_caller" not in until:
                return True
            else:  # a banner slides in: take it once its boxes held still for three polls
                steady = (steady + [[b for b in seen["boxes"] if b["caller"] in until["box_caller"].split("|")]])[-3:]
                if len(steady) == 3 and steady[0] == steady[1] == steady[2]:
                    return True
            time.sleep(0.2)
        return False

    def screen(self) -> dict:
        return ask(self.root, {"action": "screen"})

    def capture(self) -> Path:
        self.count += 1
        name = f"check-{self.count:03d}"
        ask(self.root, {"action": "capture", "name": name})
        if self.used:
            self.shortcuts[self.name] = self.used
        return self.root / f"evidence/{name}.png"


def check(root: Path, spec: Path, only: list[str] | None = None, learn: Path | None = None) -> int:
    """Run a check spec (data/checks/*.toml) on a running --test build; write evidence/check-report.json.
    ``only`` names the screens to run. They are visited in the order order_screens picks, which is printed.
    On the stock game, ``learn`` merges each screen's layout relations into that structure file under the
    screen's name, for the `stock_layout` assertions."""
    import layout_rules
    import screen_checks

    screens = screen_checks.load_spec(spec)
    if only:
        unknown = sorted(set(only) - {sc["name"] for sc in screens})
        if unknown:
            raise ValueError(f"no screen {', '.join(unknown)} in {spec.name}")
        screens = [sc for sc in screens if sc["name"] in only]
    screens = order_screens(screens, route_position(root))
    order = [sc["name"] for sc in screens]
    print("order: " + ", ".join(order))
    seen: dict = {}
    driver = CheckDriver(root)
    rows = screen_checks.run_spec(screens, driver, seen)
    if learn:
        structure = json.loads(learn.read_text()) if learn.is_file() else {}
        structure.update({name: layout_rules.learn(data, img) for name, (data, img) in seen.items()})
        learn.write_text(json.dumps(structure, indent=1, sort_keys=True) + "\n")
        print(f"learned {', '.join(seen)} into {learn}")
    write_json(local_path(root, "evidence/check-report.json"),
               {"spec": str(spec), "order": order, "shortcuts": driver.shortcuts, "rows": [vars(row) for row in rows]})
    print(screen_checks.format_table(rows))
    for screen_name, used in driver.shortcuts.items():
        print(f"shortcut: {screen_name} was reached with {', '.join(used)}, so its capture is not a typical one")
    return 0 if all(row.ok for row in rows) else 1


def tour(root: Path, stages: range | None = None, dialog: float = 25) -> int:
    """Drive TOUR, or the stage sweep over ``stages``, on a running --test --log build; write
    evidence/tour-report.json and a PNG per stop, and copy the log into the corpus."""
    game = Path(json.loads(local_path(root, "evidence/session.json").read_text())["game"])
    log, logged = game / "enrt_log.txt", 0
    report = {"build": local_path(root, "game.sha256").read_text().split("\n"), "stops": [], "failed": None}
    try:
        route = TOUR if stages is None else SWEEP_START + [s for n in stages for s in stage_route(n, dialog)]
        for n in stages or ():
            dream_keys(n, 0)  # the sweep plays Dream scenarios; it refuses a story stage before any key
        for step, (keys, until, name) in enumerate(route):
            if isinstance(keys, dict) and "stage" in keys:
                pick_dream(root, keys["stage"], f"step {step} (stage {keys['stage']})")
                continue
            drive_step(root, keys, until, 4 if step == 0 else None, f"step {step} ({name or keys})")
            if name:
                time.sleep(TOUR_SETTLE)  # menus slide in
                seen = {k: v for k, v in ask(root, {"action": "screen"}).items() if k not in ("id", "ok")}
                png = f"tour-{len(report['stops']):02d}-{name}"
                ask(root, {"action": "capture", "name": png})
                lines = []
                if log.is_file():
                    with log.open("rb") as stream:
                        stream.seek(logged)
                        data = stream.read()
                    data = data[:data.rfind(b"\n") + 1]  # whole lines only
                    logged += len(data)
                    lines = data.decode("ascii", "replace").splitlines()
                report["stops"].append({"name": name, "png": f"evidence/{png}.png", "screen": seen,
                                        "log": lines, "checks": tour_checks(seen)})
    except (OSError, ValueError, RuntimeError) as exc:
        report["failed"] = str(exc)
    report["summary"] = {check: sum(len(s["checks"][check]) for s in report["stops"])
                         for check in ("cjk", "overflow", "overlap")}
    write_json(local_path(root, "evidence/tour-report.json"), report)
    if log.is_file():
        corpus = work() / "enrt-log-corpus"
        corpus.mkdir(exist_ok=True)
        shutil.copyfile(log, corpus / f"{root.name}.txt")
    print(json.dumps({"stops": len(report["stops"]), "failed": report["failed"], **report["summary"]}))
    return 1 if report["failed"] else 0


def tour_diff(one: Path, two: Path) -> list[dict]:
    """Per stop of base run one's tour: the drawn texts gone and new in run two's, and the image change."""
    from PIL import Image, ImageChops, ImageStat

    a, b = (json.loads(local_path(root, "evidence/tour-report.json").read_text()) for root in (one, two))
    theirs = {stop["name"]: stop for stop in b["stops"]}
    rows = []
    for stop in a["stops"]:
        other = theirs.get(stop["name"])
        if other is None:
            rows.append({"stop": stop["name"], "changed": True, "missing": True})
            continue
        old, new = ({t["text"] for t in s["screen"]["texts"]} for s in (stop, other))
        with Image.open(one / stop["png"]) as p, Image.open(two / other["png"]) as q:
            grey = ImageChops.difference(p.convert("L"), q.convert("L").resize(p.size))  # a base taken at another size
            change = ImageStat.Stat(grey).mean[0] / 255
        rows.append({"stop": stop["name"], "changed": bool(old ^ new) or change > IMAGE_CHANGE,
                     "gone": sorted(old - new), "new": sorted(new - old), "image_change": round(change, 4),
                     "checks": other["checks"]})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("run", "status", "key", "click", "pad", "plug", "capture", "record", "video", "peek", "poke",
                   "find", "screen", "wait", "say", "notice", "win", "lose", "scripts", "script", "scene", "spawn", "mount", "switch",
                   "teleport", "keep", "unlock",
                   "reload", "review", "tour", "route", "check", "stop", "_audio", "_serve"):
        command = commands.add_parser(action)
        command.add_argument("--root", required=True, type=Path,
                             help="the run directory, or a bare run name under work/runtime-tests")
        command.add_argument("--timeout", type=bounded, default=1800 if action == "run" else 30)
        if action == "run":
            command.add_argument("--target", required=True, choices=[TARGET, XENIA])
            command.add_argument("--game", type=Path, required=True)
            command.add_argument("--authorize-launch", action="store_true")
            command.add_argument("--pad", nargs="?", const="x360", choices=PAD_MODELS,
                                 help="give the game a virtual XInput pad (default x360), driven by the pad command")
            command.add_argument("--unplugged", action="store_true", help="the pad starts unplugged; see plug")
            command.add_argument("--video", action="store_true",
                                 help="output 1080p without FSR, so the video command keeps the game at full speed")
            command.add_argument("--size", type=render_size_arg, metavar="WxH",
                                 help="render and output at this size without FSR (default: 1920x1080 upscaled)")
            command.add_argument("--route-input", choices=["keys", "pad"], default="keys",
                                 help="what routes press: the keyboard, or (with --pad) the pad, for the pad's prompts")
            command.add_argument("--slot", action="store_true",
                                 help="kept for old callers: every run waits for one of the two game slots")
            command.add_argument("--idle", type=bounded, metavar="SECONDS",
                                 help="stop the game after this long with no command (run_check --to passes 900)")
        if action == "plug":
            command.add_argument("state", choices=["on", "off"])
        if action == "key":
            command.add_argument("keys", nargs="+", metavar="key")
        if action == "click":
            command.add_argument("x", type=int)
            command.add_argument("y", type=int)
        if action == "pad":
            command.add_argument("names", nargs="+", metavar="input",
                                 help="A B X Y LB RB LT RT BACK START LS RS, UP DOWN LEFT RIGHT (D-pad), "
                                      "L-UP ... R-RIGHT (sticks); join with + to press together")
        if action in ("key", "pad"):
            command.add_argument("--hold", type=bounded, default=0.08)
            command.add_argument("--gap", type=bounded, default=0.3,
                                 help="seconds between keys of a sequence (one key settles 1 s)")
        if action == "capture":
            command.add_argument("name")
        if action in ("record", "video"):
            command.add_argument("name")
            command.add_argument("--seconds", type=bounded, default=60,
                                 help="record the game's audio (video: picture and audio) this long, in the background")
        if action == "_audio":
            command.add_argument("compositor", nargs=argparse.REMAINDER)
        if action in ("peek", "poke"):
            command.add_argument("address", type=lambda value: int(value, 0), help="such as 0xC2AE00")
        if action == "peek":
            command.add_argument("length", type=int, nargs="?", default=16)
        if action in ("poke", "find"):
            command.add_argument("hex", help="bytes in memory order (little-endian), such as 01000000")
        if action == "wait":
            wanted = command.add_mutually_exclusive_group(required=True)
            wanted.add_argument("--text", help="drawn text containing this (line breaks match spaces)")
            wanted.add_argument("--texture", help="drawn package texture key.member, such as 4079.12")
            command.add_argument("--capture", metavar="NAME",
                                 help="capture evidence/NAME.png on the frame that matches, as the capture command does")
        if action in ("say", "notice"):
            command.add_argument("text", help="English or stock Japanese; the runtime translates and wraps it")
        if action == "script":
            command.add_argument("number", type=int, help="script index from scripts")
        if action == "scene":
            command.add_argument("number", type=int, help="sub-scene of the current stage's event scene (0: its opening)")
        if action == "switch":
            command.add_argument("member", type=int, choices=[0, 1, 2], help="the team member to play, 0-2")
        if action == "teleport":
            command.add_argument("x", type=float)
            command.add_argument("z", type=float)
            command.add_argument("--facing", type=float, default=0.0, help="heading in radians")
            command.add_argument("--relative", action="store_true", help="x and z are offsets from the player")
        if action == "keep":
            command.add_argument("keep", nargs="*", choices=list(KEEPS), metavar="WHAT",
                                 help=f"any of {', '.join(KEEPS)}, held until the battle ends; none stops keeping")
        if action == "mount":
            command.add_argument("--slot", type=int, choices=[0, 1], default=0, help="the player (0, or 1 in 2P)")
        if action == "spawn":
            command.add_argument("count", type=int, help=f"enemy troops to put around the player (whole squads; at most {SPAWN_MAX})")
            command.add_argument("--inner", type=int, default=SPAWN_INNER, help="the nearest ring, in world units")
        if action == "review":
            command.add_argument("texture", help="key.member, or off; needs the test-only review.bin")
            command.add_argument("--rect", type=int, nargs=4, default=[0, 0, 512, 32], metavar=("X", "Y", "W", "H"))
        if action == "reload":
            command.add_argument("table", type=Path, help="a table.bin from build_moz_english_runtime.py --table-only")
        if action == "route":
            command.add_argument("screen", choices=sorted(set(ROUTE_SCREENS) - {"boot"} | {"menu"}),
                                 help="drive there from where the last route left the game")
            command.add_argument("--stage", type=int, help="the stage a battle route picks on the scenario or chapter "
                                 f"select, as a player does (Dream default {DREAM_STAGES[0]}; a story chapter needs it)")
            command.add_argument("--movie", type=int, help="gallery-movie-play: the movie NNN (movieNNN.wmv) to play "
                                 f"(default {DEFAULT_MOVIE})")
            command.add_argument("--officer", action="append", default=[], metavar="NAME",
                                 help="free-camp onward: an officer for the Free Mode team, by name as the select shows "
                                      "it or by id (repeat, up to 3; the rest are filled in)")
        if action == "check":
            command.add_argument("spec", type=Path, help="a check file such as data/checks/menus.toml")
            command.add_argument("--only", type=lambda value: value.split(","), metavar="SCREEN,...",
                                 help="run only these screens of the spec")
            command.add_argument("--learn", type=Path, metavar="STRUCTURE",
                                 help="on the stock game: save each screen's layout relations into this file")
        if action == "tour":
            command.add_argument("--stages", type=lambda value: range(int(value.split("-")[0]),
                                                                       int(value.split("-")[-1]) + 1),
                                 metavar="A-B", help="sweep these stages (0-143) instead of touring")
            command.add_argument("--dialog-seconds", type=bounded, default=25,
                                 help="seconds of opening dialog before the win; shorter where the player falls first")
            command.add_argument("--diff", type=Path, metavar="RUN",
                                 help="compare this run's tour with a base run's, such as a golden, instead of touring")
    args = parser.parse_args(argv)
    try:
        # a bare run name is a MOZ run under work/runtime-tests
        root = (args.root if len(args.root.parts) > 1 else work() / "runtime-tests" / args.root).resolve(strict=True)
        storage = (work() / "runtime-tests").resolve()
        xenia_storage = (work() / "xenia/runs").resolve()
        permitted = (storage, xenia_storage)
        if args.action == "run":
            permitted = (xenia_storage,) if args.target == XENIA else (storage,)
        if not any(root != base and root.is_relative_to(base) for base in permitted):
            locations = " or ".join(str(base) for base in permitted)
            raise ValueError(f"--root must be a run directory inside {locations}")
        if args.action == "run":
            return run(root, args)
        if args.action == "_audio":  # inside the sandbox: the audio server, then the compositor in its place
            if os.environ.get("MUSOU_HARNESS_ROOT") != str(root):
                raise ValueError("_audio requires the harness sandbox")
            start_audio(root)
            os.execvp(args.compositor[0], args.compositor)
        if args.action == "_serve":
            try:
                code = serve(root)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                print(f"game-harness: {exc}", file=sys.stderr)
                code = 1
            hold_for_supervisor(root, code)
        if args.action == "tour" and args.diff:
            rows = tour_diff(args.diff.resolve(strict=True), root)
            print(json.dumps(rows, indent=1))
            return 1 if any(row["changed"] for row in rows) else 0
        if args.action == "tour":
            return tour(root, args.stages, args.dialog_seconds)
        if args.action == "route":
            return route(root, args.screen, args.stage, args.movie, args.officer)
        if args.action == "check":
            return check(root, args.spec.resolve(strict=True), args.only, args.learn)
        request = {"action": args.action}
        timeout = args.timeout
        if args.action == "key" and len(args.keys) == 1:
            request.update(key=args.keys[0], hold=args.hold)
        elif args.action == "key":
            request.update(action="keys", keys=args.keys, hold=args.hold, gap=args.gap)
            timeout += len(args.keys) * (args.hold + args.gap)  # the acknowledgement follows the last key
        if args.action == "click":
            request.update(x=args.x, y=args.y)
        if args.action == "pad":
            request.update(names=args.names, hold=args.hold, gap=args.gap)
            timeout += len(args.names) * (args.hold + args.gap)
        if args.action == "review":
            review_request(args.texture, args.rect)
            request.update(texture=args.texture, rect=args.rect)
        if args.action == "capture":
            request["name"] = args.name
        if args.action == "plug":
            request["on"] = args.state == "on"
        if args.action in ("record", "video"):
            request.update(name=args.name, seconds=args.seconds)
        if args.action in ("peek", "poke"):
            request["address"] = args.address
        if args.action == "peek":
            request["length"] = args.length
        if args.action in ("poke", "find"):
            request["hex"] = args.hex
        if args.action == "wait":
            request.update(text=args.text, texture=args.texture, seconds=args.timeout)
            timeout += 2 * TEST_COMMAND_SECONDS  # the answer follows the wait
            if args.capture:
                request["capture"] = args.capture
                timeout += 10  # the screenshot follows the match
        if args.action in ("say", "notice"):
            request["text"] = args.text
        if args.action in ("script", "scene"):
            request["number"] = args.number
        if args.action == "spawn":
            request.update(count=args.count, inner=args.inner)
        if args.action == "mount":
            request["slot"] = args.slot
        if args.action == "switch":
            request["member"] = args.member
            timeout += SWITCH_SECONDS
        if args.action == "teleport":
            request.update(x=args.x, z=args.z, facing=args.facing, relative=args.relative)
        if args.action == "keep":
            request["keep"] = args.keep
        if args.action == "reload":
            request["table"] = str(args.table.resolve(strict=True))
            timeout += RELOAD_SECONDS
        response = send(root, request, timeout)
        if args.action == "screen" and response["ok"]:
            response = {"routes": routed_screens(response), **response}
            if not response["routes"]:
                print(f"game-harness: {NO_ROUTE}", file=sys.stderr)
        print(json.dumps(response))
        return 0 if response["ok"] else 1
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"game-harness: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
