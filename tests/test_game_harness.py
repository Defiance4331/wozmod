"""Exercise the control protocol without a game, compositor, or host input."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
from itertools import pairwise
import json
import os
import struct
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import checkout
import prepare_run
import pytest
from synthetic import pe_image


SPEC = importlib.util.spec_from_file_location(
    "game_harness", Path(__file__).resolve().parents[1] / "tools/game_harness.py",
)
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def _staged(game: Path) -> Path:
    """A run folder as prepare_run.py leaves it: one mod file and its record."""
    (game / "wozmod").mkdir(parents=True, exist_ok=True)
    (game / "wozmod/table.bin").write_bytes(b"mod")
    prepare_run.write_record(game.parent, {"wozmod/table.bin": hashlib.sha256(b"mod").hexdigest()}, {})
    return game


def test_control_round_trip(tmp_path, monkeypatch):
    """Bugs: the server does not answer through the control folder, runs a malformed or unknown request (a key name
    with a shell character, a path out of evidence/), leaves ready.json behind after stop, or does not log the stop.
    The key defaults and a taken capture name have their own tests."""
    (tmp_path / "control").mkdir()
    (tmp_path / "evidence").mkdir()
    (tmp_path / "launch.sh").write_text("exec sleep 60\n")
    monkeypatch.setenv("MUSOU_HARNESS_ROOT", str(tmp_path))
    monkeypatch.setenv("GAMESCOPE_WAYLAND_DISPLAY", "synthetic-test-only")
    monkeypatch.setattr(harness, "start_audio", lambda root: None)
    keys = []
    monkeypatch.setattr(harness, "tap_keys", lambda names, hold, gap, settle: keys.append((names, hold, gap, settle)))

    def capture(command, **kwargs):
        assert command[:2] == ["gamescopectl", "screenshot"]
        Path(command[2]).write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\0IEND\xaeB\x60\x82")

    monkeypatch.setattr(harness.subprocess, "run", capture)
    results = []
    thread = threading.Thread(target=lambda: results.append(harness.serve(tmp_path)))
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "control/ready.json").exists():
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.01)
        assert harness.send(tmp_path, {"action": "status"}, 5)["ok"]
        assert harness.send(tmp_path, {"action": "key", "key": "Return", "hold": 0.08}, 5)["ok"]
        assert harness.send(tmp_path, {
            "action": "keys", "keys": ["Down", "Down", "Return"], "hold": 0.08, "gap": 0.3,
        }, 5)["ok"]
        for bad in ({"keys": []}, {"keys": ["Down", "Down;id"]}, {"keys": "Down"}, {"gap": 0.01},
                    {"keys": ["Down"] * (harness.MAX_KEYS + 1)}):
            request = {"action": "keys", "keys": ["Down"], "hold": 0.08, "gap": 0.3} | bad
            assert not harness.send(tmp_path, request, 5)["ok"]
        assert len(keys) == 2
        assert harness.send(tmp_path, {"action": "capture", "name": "menu"}, 5)["ok"]
        assert not harness.send(tmp_path, {"action": "capture", "name": "../escape"}, 5)["ok"]
        assert not harness.send(tmp_path, {
            "action": "key", "key": "Return", "hold": float("nan"),
        }, 5)["ok"]
        assert not harness.send(tmp_path, {"action": "shell"}, 5)["ok"]
    finally:
        if thread.is_alive():
            harness.send(tmp_path, {"action": "stop"}, 5)
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert results == [0]
    assert not (tmp_path / "control/ready.json").exists()
    records = [
        json.loads(line)
        for line in (tmp_path / "evidence/actions.jsonl").read_text().splitlines()
    ]
    assert records[-1]["request"]["action"] == "stop"
    assert harness.exit_record(tmp_path, 139, False)["reason"] == "stopped"


def _slot_runtime(tmp_path: Path, monkeypatch) -> Path:
    """A main checkout in tmp_path with the first game slot's Steam runtime, so a launch never reads the machine's."""
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    steamrt = tmp_path / "work/runtime-tests/_template/steamrt"
    steamrt.mkdir(parents=True)
    (steamrt / "run").write_text("")
    return steamrt


def test_launch_boundaries_and_isolation(tmp_path, monkeypatch):
    steamrt = _slot_runtime(tmp_path, monkeypatch)
    root = tmp_path / "run"
    root.mkdir()
    game = _staged(root / "game")
    (root / "launch.sh").write_text("exit 0\n")
    args = argparse.Namespace(authorize_launch=False, game=game, target=harness.TARGET, timeout=30, pad=False)
    with pytest.raises(ValueError, match="permission"):
        harness.run(root, args)
    args.authorize_launch = True
    args.game = tmp_path
    with pytest.raises(ValueError, match="disposable"):
        harness.run(root, args)
    args.game = game
    (root / "prefix").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        harness.run(root, args)
    (root / "prefix").unlink()
    (game / "wozmod/table.bin").write_bytes(b"mud")  # a build changed since staging is never launched
    with pytest.raises(ValueError, match="changed since"):
        harness.run(root, args)
    (game / "wozmod/table.bin").write_bytes(b"mod")
    calls = []

    def launch(command, **kwargs):
        calls.append(command)
        assert kwargs["start_new_session"]
        return SimpleNamespace(wait=lambda: 0, poll=lambda: 0, returncode=0)

    monkeypatch.setattr(harness.subprocess, "Popen", launch)
    assert harness.run(root, args) == 0
    command = calls[0]
    for flag in ("--unshare-net", "--unshare-pid", "--unshare-ipc", "--die-with-parent"):
        assert flag in command
    assert command[command.index("--backend") + 1] == "headless"
    binds = [command[i + 1:i + 3] for i, item in enumerate(command) if item == "--bind"]
    assert binds == [[str(root), str(root)], [str(root / "tmp"), "/tmp"],
                     [str(root / "runtime"), f"/run/user/{harness.os.getuid()}"], [str(steamrt), str(steamrt)]]
    assert "PROTON_USE_XALIA" in command
    assert json.loads((root / "evidence/session.json").read_text())["game"] == str(game)
    with pytest.raises(FileExistsError):
        harness.run(root, args)


def test_size_renders_and_outputs_unscaled(tmp_path, monkeypatch):
    """Bug: --size is dropped (the run renders 1080p) or still upscales with FSR, so an ultrawide capture is wrong."""
    _slot_runtime(tmp_path, monkeypatch)
    root = tmp_path / "work/runtime-tests/wide"
    game = _staged(root / "game")
    (root / "launch.sh").write_text("exit 0\n")
    calls = []
    monkeypatch.setattr(harness.subprocess, "Popen", lambda command, **kwargs: calls.append(command)
                        or SimpleNamespace(wait=lambda: 0, poll=lambda: 0, returncode=0))
    assert harness.main(["run", "--root", str(root), "--game", str(game), "--authorize-launch",
                         "--target", harness.TARGET, "--size", "3440x1440"]) == 0
    command = calls[0]
    for flag, value in (("-W", "3440"), ("-H", "1440"), ("-w", "3440"), ("-h", "1440")):
        assert command[command.index(flag) + 1] == value
    assert "-F" not in command
    session = json.loads((root / "evidence/session.json").read_text())
    assert session["render_size"] == session["output_size"] == [3440, 1440] and not session["fsr"]


def test_bare_run_name_addresses_the_run_under_work_runtime_tests(tmp_path, monkeypatch):
    """Bug: a bare --root name resolves against the shell's directory, so a command sent from the repo root misses the
    run (or reaches another directory of that name)."""
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    (tmp_path / "work/runtime-tests/r1").mkdir(parents=True)
    (tmp_path / "r1").mkdir()
    sent = []
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: sent.append(root) or {"ok": True})
    monkeypatch.chdir(tmp_path)
    assert harness.main(["status", "--root", "r1"]) == 0
    assert sent == [(tmp_path / "work/runtime-tests/r1").resolve()]


def test_xenia_launch_keeps_native_frame_and_save_inside_its_storage(tmp_path, monkeypatch):
    """Bug: the reference emulator inherits MOZ scaling/save edits or accepts another run store."""
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    monkeypatch.setenv("MUSOU_HARNESS_HZ", "120")
    root = tmp_path / "work/xenia/runs/reference"
    game = root / "game"
    game.mkdir(parents=True)
    (root / "launch.sh").write_text("exit 0\n")
    calls = []

    def launch(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(wait=lambda: 0, poll=lambda: 0, returncode=0)

    monkeypatch.setattr(harness.subprocess, "Popen", launch)
    arguments = ["run", "--root", str(root), "--game", str(game), "--authorize-launch"]
    assert harness.main(arguments + ["--target", harness.XENIA]) == 0
    command = calls[0]
    for flag, value in (("-W", "1280"), ("-H", "720"), ("-w", "1280"), ("-h", "720"), ("-r", "60")):
        assert command[command.index(flag) + 1] == value
    assert "-F" not in command
    for flag in ("--unshare-net", "--unshare-pid", "--unshare-ipc", "--die-with-parent"):
        assert flag in command
    session = json.loads((root / "evidence/session.json").read_text())
    assert session["render_size"] == session["output_size"] == [1280, 720]
    assert not session["fsr"]
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: {"ok": True})
    assert harness.main(["status", "--root", str(root)]) == 0
    assert harness.main(arguments + ["--target", harness.TARGET]) == 1
    outside = tmp_path / "work/runtime-tests/reference"
    outside.mkdir(parents=True)
    assert harness.main(["run", "--root", str(outside), "--game", str(game),
                         "--authorize-launch", "--target", harness.XENIA]) == 1
    assert harness.main(["status", "--root", str(tmp_path / "work/xenia/runs")]) == 1
    assert len(calls) == 1


def test_timeout_does_not_overwrite_pending_input(tmp_path):
    (tmp_path / "control").mkdir()
    harness.write_json(tmp_path / "control/ready.json", {})
    with pytest.raises(TimeoutError, match="outcome is unknown"):
        harness.send(tmp_path, {"action": "status"}, 0.01)
    pending = (tmp_path / "control/request.json").read_bytes()
    with pytest.raises(ValueError, match="pending"):
        harness.send(tmp_path, {"action": "stop"}, 0.01)
    assert (tmp_path / "control/request.json").read_bytes() == pending
    for value in ("nan", "inf", "0", "3601"):
        with pytest.raises(argparse.ArgumentTypeError):
            harness.bounded(value)


def test_reject_host_input_and_capture_symlink(tmp_path, monkeypatch):
    monkeypatch.delenv("GAMESCOPE_WAYLAND_DISPLAY", raising=False)
    with pytest.raises(ValueError, match="isolated"):
        harness.tap_keys(["Return"], 0.08, 1.0)
    (tmp_path / "evidence").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        harness.perform(tmp_path, {"action": "capture", "name": "escape"})


def test_click_rejects_points_outside_the_run_frame_before_delivery(tmp_path, monkeypatch):
    """Bug: a click outside the native frame reaches XTest, or input uses the host display."""
    monkeypatch.delenv("GAMESCOPE_WAYLAND_DISPLAY", raising=False)
    with pytest.raises(ValueError, match="isolated"):
        harness.click_point(100, 200)
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    root = tmp_path / "work/xenia/runs/click-test"
    (root / "evidence").mkdir(parents=True)
    harness.write_json(root / "evidence/session.json", {"render_size": [1280, 720]})
    clicked = []
    monkeypatch.setattr(harness, "click_point", lambda x, y: clicked.append((x, y)))
    response = harness.perform(root, {"action": "click", "x": 1279, "y": 719})
    assert response == {"action": "click", "x": 1279, "y": 719, "button": "left"}
    for x, y in ((-1, 0), (0, -1), (1280, 0), (0, 720), (1.5, 200), (True, 200)):
        with pytest.raises(ValueError, match="inside 1280x720"):
            harness.perform(root, {"action": "click", "x": x, "y": y})
    assert clicked == [(1279, 719)]
    sent = []
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: sent.append(request) or {"ok": True})
    assert harness.main(["click", "--root", str(root), "100", "200"]) == 0
    assert sent == [{"action": "click", "x": 100, "y": 200}]


def test_key_command_keeps_one_key_request_and_times_sequences(tmp_path, monkeypatch):
    root = tmp_path / "work/runtime-tests/run"
    root.mkdir(parents=True)
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    sent = []
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: sent.append((request, timeout)) or {"ok": True})
    assert harness.main(["key", "--root", str(root), "Return"]) == 0
    assert harness.main(["key", "--root", str(root), "--gap", "0.5", "Down", "Down"]) == 0
    assert sent[0] == ({"action": "key", "key": "Return", "hold": 0.08}, 30)
    assert sent[1] == ({"action": "keys", "keys": ["Down", "Down"], "hold": 0.08, "gap": 0.5}, 30 + 2 * 0.58)


def test_pad_request_reaches_supervisor_and_reports_its_failure(tmp_path):
    """Bugs: a mistyped input goes to the pad unchecked, or a failed press is reported as done."""
    (tmp_path / "control").mkdir()
    with pytest.raises(ValueError, match="pad inputs"):
        harness.perform(tmp_path, {"action": "pad", "names": ["A+Q"], "hold": 0.08, "gap": 0.05})
    assert not (tmp_path / "control/pad.json").exists()

    def supervisor(error):
        request = tmp_path / "control/pad.json"
        while not request.exists():
            time.sleep(0.01)
        taken.append(json.loads(request.read_text())["names"])
        harness.write_json(tmp_path / "control/pad-done.json", {"error": error} if error else {})
        request.unlink()

    taken = []
    for error in (None, "device gone"):
        thread = threading.Thread(target=supervisor, args=(error,))
        thread.start()
        request = {"action": "pad", "names": ["A", "LB+R-UP"], "hold": 0.08, "gap": 0.05}
        if error:
            with pytest.raises(ValueError, match=error):
                harness.perform(tmp_path, request)
        else:
            harness.perform(tmp_path, request)
        thread.join()
    assert taken == [["A", "LB+R-UP"]] * 2


def test_memory_actions_peek_poke_and_find_across_chunks(tmp_path, monkeypatch):
    import ctypes
    import mmap
    import os

    monkeypatch.setattr(harness, "game_pid", os.getpid)  # this test process stands in for the game
    monkeypatch.setattr(harness, "ADDRESS_LIMIT", 1 << 64)
    monkeypatch.setattr(harness, "FIND_CHUNK", mmap.PAGESIZE)
    pages = mmap.mmap(-1, 2 * mmap.PAGESIZE)  # page-aligned, so a find chunk ends mid-pattern
    base = ctypes.addressof(ctypes.c_char.from_buffer(pages))
    pattern, at = os.urandom(24), mmap.PAGESIZE - 8
    pages[at:at + 24] = pattern
    assert f"{base + at:#x}" in harness.perform(tmp_path, {"action": "find", "hex": pattern.hex()})["addresses"]
    peeked = harness.perform(tmp_path, {"action": "peek", "address": base + at, "length": 24})
    assert peeked["hex"] == pattern.hex()
    poked = harness.perform(tmp_path, {"action": "poke", "address": base + at, "hex": "deadbeef"})
    assert poked["before"] == pattern[:4].hex() and pages[at:at + 4] == bytes.fromhex("deadbeef")
    for request in (
        {"action": "peek", "address": -1, "length": 4},
        {"action": "peek", "address": base, "length": harness.MAX_PEEK + 1},
        {"action": "poke", "address": base, "hex": "abc"},
        {"action": "poke", "address": base, "hex": "00" * (harness.MAX_POKE + 1)},
        {"action": "find", "hex": ""},
    ):
        with pytest.raises(ValueError):
            harness.perform(tmp_path, request)
    monkeypatch.setattr(harness, "ADDRESS_LIMIT", 1 << 32)
    with pytest.raises(ValueError, match="below"):
        harness.perform(tmp_path, {"action": "peek", "address": (1 << 32) - 2, "length": 4})


def test_troops_near_counts_used_units_by_their_actors_x_and_z_around_the_player(tmp_path):
    """Bug: a wrong unit stride, the player or a free unit counted, height counted as distance, or the last unit
    dropped: the spawn row would pass or fail on the wrong number."""
    import os
    import struct

    actors = 0x100000
    me = (5000.0, 0.0, 5000.0)
    units = {0: me, 1: (5900.0, 300.0, 4100.0), 2: (6100.0, 0.0, 5000.0), 3: me, 4: (4000.0, 0.0, 6000.0),
             5: (5000.0, 9000.0, 5000.0), 254: (5500.0, 0.0, 5500.0)}
    free = {3}  # stands on the player, but its slot is free
    memory = tmp_path / "memory"
    with memory.open("wb") as f:
        for i, at in units.items():
            f.seek(harness.UNITS + i * harness.UNIT_SIZE + 0x14)
            f.write(struct.pack("<I", actors + i * 0x100))
            f.seek(harness.UNITS + i * harness.UNIT_SIZE + 0xBC)
            f.write(struct.pack("<I", 0 if i in free else 1))
            f.seek(actors + i * 0x100 + harness.ACTOR_AT)
            f.write(struct.pack("<3f", *at))
        f.truncate(harness.UNITS + harness.UNIT_COUNT * harness.UNIT_SIZE)
    fd = os.open(memory, os.O_RDONLY)
    try:
        assert harness.troops_near(fd, 1000) == 4  # units 1, 4 (on the edge), 5 and 254
    finally:
        os.close(fd)


def test_test_mailbox_screen_wait_and_say(tmp_path, monkeypatch):
    import ctypes
    import mmap
    import os
    import struct

    monkeypatch.setattr(harness, "game_pid", os.getpid)
    image = mmap.mmap(-1, 0x1000 + harness.TEST_SIZE + 0x20)  # the DLL's header page, then its .data section
    base = ctypes.addressof(ctypes.c_char.from_buffer(image))
    monkeypatch.setattr(harness, "DLL_BASE", base)
    image[:0x1000] = pe_image(0x1000, (b".data", harness.TEST_SIZE + 0x20, 0x1000, 0, 0))
    block = 0x1010
    image[block:block + 24] = struct.pack("<16s2I", harness.TEST_MAGIC, harness.TEST_VERSION, harness.TEST_SIZE)

    def put(at: int, *records: tuple[struct.Struct, tuple]) -> None:
        for layout, values in records:
            layout.pack_into(image, at, *values)
            at += layout.size

    text, quad = harness.TEST_TEXT, harness.TEST_QUAD
    put(block + harness.TEST_HEADER.size - 4, (struct.Struct("<I"), (2,)),  # two windows set
        (text, (6, 0x4F4D1F, 2, 5, 0, "Rowan!".encode())), (text, (7, 0x1, 1, 40, 0, b"Save?")))
    put(block + harness.TEST_SNAPSHOT, (struct.Struct("<3I"), (41, 2, 4)),
        (text, (1, 0x53C225, 100, 200, 300, b"Defeat \x84\x40da\nNobunaga")),
        (text, (4, 0x1, 0, 0, 0, b"caption")))
    at = block + harness.TEST_SNAPSHOT + 12 + harness.TEST_TEXTS * text.size
    put(at, *[(quad, (21, 0x581A22, 7, 4074 << 16 | 3, 10, 20, 168, 28))] * 2,
        (quad, (21, 0x1, 8, 0, 0, 0, 1, 1)), (quad, (22, 0x2, 9, 4079 << 16 | 12, 5, 6, 7, 8)))
    received, stop = [], threading.Event()

    def game() -> None:  # the pump hook: run each command, count frames
        while not stop.is_set():
            command, done, op, arg = struct.unpack_from("<4I", image, block + 28)
            struct.pack_into("<I", image, block + 24, 50)
            if command != done:
                if op == 2:
                    received.append((arg, image[block + 48:block + 48 + harness.TEST_INPUT].rstrip(b"\0")))
                if op in (3, 4, 5, 6, 8):
                    received.append((op, arg))
                    busy = op == 4 and received.count((4, arg)) == 1  # a script is playing at first
                    refused = {(3, 8): 3, (8, 2): 7}.get((op, arg), 4 if busy else 0)  # scene 2's cast is absent
                    struct.pack_into("<I", image, block + 44, refused)
                struct.pack_into("<I", image, block + 32, command)
            time.sleep(0.005)

    thread = threading.Thread(target=game)
    thread.start()
    try:
        seen = harness.perform(tmp_path, {"action": "screen"})
        assert seen["frame"] == 41 and seen["texts"][0] == {
            "x": 100, "y": 200, "width": 300, "caller": "0x53c225", "text": "Defeat Ōda\nNobunaga"}
        assert seen["texts"][1]["hook"] == "caption" and seen["unnamed_quads"] == 1
        assert seen["boxes"] == [{"x": 0, "y": 0, "width": 1, "height": 1, "caller": "0x1", "count": 1}]
        assert seen["textures"][0] == {"texture": "4074.3", "x": 10, "y": 20, "width": 168, "height": 28,
                                       "caller": "0x581a22", "count": 2}
        assert [w["set"] for w in seen["windows"]] == ["window 2", "message box"]
        wait = {"action": "wait", "seconds": 0.3}
        assert harness.perform(tmp_path, wait | {"text": "Ōda Nobunaga"})["matches"][0]["y"] == 200
        assert harness.perform(tmp_path, wait | {"texture": "4079.12"})["matches"][0]["x"] == 5
        with pytest.raises(TimeoutError):  # set before the wait began
            harness.perform(tmp_path, wait | {"text": "Rowan"})
        with pytest.raises(ValueError):
            harness.perform(tmp_path, wait | {"text": "a", "texture": "4079.12"})
        assert harness.perform(tmp_path, {"action": "say", "text": "Ōda"}) == {"window": 1}
        harness.perform(tmp_path, {"action": "notice", "text": "\u6575"})
        assert harness.perform(tmp_path, {"action": "win"}) == {"queued": "win"}
        with pytest.raises(RuntimeError, match="not in a battle"):
            harness.perform(tmp_path, {"action": "lose"})
        assert harness.perform(tmp_path, {"action": "script", "number": 12}) == {"queued": 12}
        assert harness.perform(tmp_path, {"action": "scene", "number": 1}) == {"scene": 1}  # op 8, never a script
        with pytest.raises(RuntimeError, match="not on the field"):  # not "no such sub-scene"
            harness.perform(tmp_path, {"action": "scene", "number": 2})
        harness.perform(tmp_path, {"action": "unlock"})
        assert received == [(1, b"\x84\x40da"), (2, "\u6575".encode("cp932")), (3, 7), (3, 8), (4, 12), (4, 12), (8, 1),
                            (8, 2), (5, 0)]
    finally:
        stop.set()
        thread.join()
    struct.pack_into("<I", image, block + 20, harness.TEST_SIZE + 4)
    with pytest.raises(ValueError, match="does not match"):
        harness.perform(tmp_path, {"action": "screen"})
    image[block:block + 16] = bytes(16)
    with pytest.raises(ValueError, match="--test"):
        harness.perform(tmp_path, {"action": "screen"})


def test_stage_scripts_name_the_dialog_and_notice_records(tmp_path, monkeypatch):
    import ctypes
    import mmap
    import os
    import struct

    monkeypatch.setattr(harness, "game_pid", os.getpid)
    # The game's pointers are 32-bit: map the fake game memory below 4 GiB (Linux x86-64 MAP_32BIT).
    memory = mmap.mmap(-1, 0x400, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS | 0x40)
    base = ctypes.addressof(ctypes.c_char.from_buffer(memory))
    globals_ = {"STAGE": base, "STAGE_DATA": base + 4, "SCRIPT_TABLE": base + 8, "SCRIPT_EVENTS": base + 16}
    for name, address in globals_.items():
        monkeypatch.setattr(harness, name, address)
    with pytest.raises(RuntimeError, match="not in a battle"):
        harness.perform(tmp_path, {"action": "scripts"})
    scripts, events = base + 0x40, base + 0x20
    struct.pack_into("<5I", memory, 0, 1, 0xBEEF, scripts, 2, events)  # stage 1, loaded, two scripts
    struct.pack_into("<2I", memory, 0x4C, 0x20, 0x40)  # script offsets
    struct.pack_into("<2I", memory, 0x20, scripts + 0x100, scripts + 0x140)  # their events
    memory[0x60:0x66] = bytes([1, 2, 0, 0, 1, 2])  # fired, normal, queued, one condition, two events
    memory[0x80:0x86] = bytes([0, 7, 0, 1, 0, 1])  # victory, immediate
    struct.pack_into("<16H", memory, 0x140, 0x26, 59, 6, 1, 0, 0, 1, 0, 0x25, 42, 55, 0, 0, 0, 0, 0)
    struct.pack_into("<8H", memory, 0x180, 0x0A, 1, 0, 0, 0, 0, 0, 0)
    assert harness.perform(tmp_path, {"action": "scripts"}) == {"stage": 1, "scripts": [
        {"index": 0, "type": 2, "fired": True, "queued": True, "dialogs": [6], "notices": [42]},
        {"index": 1, "type": 7, "fired": False, "queued": False, "dialogs": [], "notices": []}]}


def test_tour_checks_screens_and_diff(tmp_path):
    from PIL import Image

    seen = {"texts": [
        {"x": 100, "y": 100, "width": 200, "text": "Larch", "caller": "0x1"},
        {"x": 250, "y": 110, "width": 100, "text": "Sound", "caller": "0x2"},  # overlaps Larch
        {"x": 1200, "y": 300, "width": 120, "text": "Too wide", "caller": "0x3"},
        {"x": 100, "y": 400, "width": 48, "text": "\u8a2d\u5b9a", "caller": "0x3"},
    ], "windows": [{"text": "\u672a\u7ffb\u8a33"}], "textures": [{"texture": "4085.6"}, {"texture": "4080.0"}]}
    checks = harness.tour_checks(seen)
    assert checks == {"cjk": ["\u672a\u7ffb\u8a33", "\u8a2d\u5b9a"], "overflow": ["Too wide"], "overlap": [["Larch", "Sound"]]}
    assert harness.shows(seen, {"texture": "4085.6"})
    assert harness.shows(seen, {"texture": "4085.5|4085.6"})  # the other boot's wheel art: the tour must not time out
    assert not harness.shows(seen, {"texture": "4085.5|4085.7"})
    assert not harness.shows(seen, {"texture": "4085.6", "without": "4080.0"})
    assert not harness.shows(seen, {"texture": "4085.6", "without": "4081.0|4080.0"})  # any listed texture rules it out
    assert harness.shows(seen, {"texture": "4085.6", "with": "4080.0"})  # a screen told apart by an extra texture
    assert not harness.shows(seen, {"texture": "4085.6", "with": "4081.0"})
    assert harness.shows(seen, {"caller": "0x2"}) and not harness.shows(seen, {"caller": "0x9"})
    assert harness.shows(seen, {"x": 250}) and not harness.shows(seen, {"x": 251})
    # a panel's texture stays drawn once opened; the screen is told from the course screen by a text it hides
    assert harness.shows(seen, {"texture": "4085.6", "without_caller": "0x9"})
    assert not harness.shows(seen, {"texture": "4085.6", "without_caller": "0x2"})

    runs = []
    for shade, texts in ((0, ["Larch", "Sound"]), (200, ["Larch", "Sounds"])):
        root = tmp_path / f"run{shade}"
        (root / "evidence").mkdir(parents=True)
        Image.new("L", (8, 8), shade).save(root / "evidence/tour-00-options.png")
        stop = {"name": "options", "png": "evidence/tour-00-options.png",
                "screen": {"texts": [{"text": t} for t in texts]}, "checks": {}}
        (root / "evidence/tour-report.json").write_text(json.dumps({"stops": [stop]}))
        runs.append(root)
    same, = harness.tour_diff(runs[0], runs[0])
    assert not same["changed"] and same["image_change"] == 0
    row, = harness.tour_diff(*runs)
    assert row["changed"] and row["gone"] == ["Sound"] and row["new"] == ["Sounds"]
    assert row["image_change"] == round(200 / 255, 4)


def test_renderer_review_refuses_rectangles_that_wrap_the_game_uv_fields():
    """A negative origin or oversized rectangle would wrap the game's unsigned quad UVs."""
    import pytest
    name, rect = harness.review_request("4080.7", [0, 0, 232, 32])
    assert name == (4080 << 16 | 7) and rect == [0, 0, 232, 32]
    assert harness.review_request("off", [0, 0, 512, 32]) == (0, [0, 0, 0, 0])
    for rect in ([-1, 0, 232, 32], [0, 0, 0, 32], [3900, 0, 232, 32], [0, 0, 512, 201]):
        with pytest.raises(ValueError, match="review rect"):
            harness.review_request("4080.7", rect)


@pytest.mark.parametrize("script, reason", [("exit 0\n", "quit"), ("exit 5\n", "crash")])
def test_ended_run_tells_a_crash_from_a_quit(tmp_path, monkeypatch, script, reason):
    """Bug: a game crash and a quit both end the harness with 139, so a crash reads as a clean exit."""
    _slot_runtime(tmp_path, monkeypatch)
    root = tmp_path / "run"
    _staged(root / "game")
    (root / "launch.sh").write_text(script)
    monkeypatch.setenv("MUSOU_HARNESS_ROOT", str(root))
    monkeypatch.setenv("GAMESCOPE_WAYLAND_DISPLAY", "synthetic-test-only")
    monkeypatch.setattr(harness, "start_audio", lambda root: None)
    real = harness.subprocess.Popen

    def launch(command, **kwargs):
        if command[0] == "bash":  # the server's launcher, inside the compositor
            return real(command, **kwargs)
        harness.serve(root)  # the compositor's child runs the game to its end; gamescope then exits 139
        return SimpleNamespace(poll=lambda: 139, wait=lambda **kwargs: 139, returncode=139, pid=0)

    monkeypatch.setattr(harness.subprocess, "Popen", launch)
    args = argparse.Namespace(authorize_launch=True, game=root / "game", target=harness.TARGET, timeout=30, pad=False)
    assert harness.run(root, args) == 139
    record = harness.exit_info(root)
    assert record["reason"] == reason and record["game_returncode"] == int(script.split()[1])
    with pytest.raises(ValueError, match=rf"the run has ended: {reason} \(game exit status {script.split()[1]}\)"):
        harness.send(root, {"action": "status"}, 1)


def test_server_reports_its_end_and_never_exits_into_gamescope(tmp_path, monkeypatch):
    """Bug: the server exits after a start failure, so gamescope runs its exit path and segfaults."""
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    root = tmp_path / "work/runtime-tests/serve-test"
    (root / "control").mkdir(parents=True)

    def fail(root):
        raise harness.subprocess.CalledProcessError(1, ["pactl", "set-default-sink"])

    class Held(Exception):
        pass

    def sleep(seconds):
        raise Held

    monkeypatch.setattr(harness, "serve", fail)
    monkeypatch.setattr(harness.time, "sleep", sleep)
    with pytest.raises(Held):
        harness.main(["_serve", "--root", str(root)])
    assert json.loads((root / "control/serve-exit.json").read_text()) == {"returncode": 1}


def test_run_kills_the_sandbox_once_the_server_ends(tmp_path, monkeypatch):
    """Bug: the supervisor waits for gamescope to exit on its own, and gamescope segfaults on exit."""
    _slot_runtime(tmp_path, monkeypatch)
    root = tmp_path / "run"
    _staged(root / "game")
    (root / "launch.sh").write_text("exit 0\n")
    killed, polls = [], []

    def poll():
        polls.append(1)
        assert len(polls) < 500, "the supervisor waited for gamescope to exit"
        return -9 if killed else None

    def launch(command, **kwargs):
        harness.write_json(root / "evidence/game-exit.json", {"stopped": True, "time": 0})
        harness.write_json(root / "control/serve-exit.json", {"returncode": 0})
        return SimpleNamespace(pid=4242, returncode=None, poll=poll, wait=lambda **kwargs: -9)

    monkeypatch.setattr(harness.subprocess, "Popen", launch)
    monkeypatch.setattr(harness.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    args = argparse.Namespace(authorize_launch=True, game=root / "game", target=harness.TARGET, timeout=30, pad=False)
    assert harness.run(root, args) == 0
    assert killed == [(4242, harness.signal.SIGKILL)]
    assert harness.exit_info(root)["reason"] == "stopped"


def test_route_library_reaches_every_screen_and_back_to_the_menu():
    """Bug: a mistyped screen name in an edge leaves a screen unreachable or a planned path broken."""
    names = set(harness.ROUTE_SCREENS)
    assert {n for edge in harness.ROUTE_EDGES for n in edge[:2]} == names
    dead_ends = {"story-z-narration", "story-z-still", "story-z-still-2",  # story pages: no verified way out, so a check spec lists them last
                 "story-narration", "story-wei-pre-battle", "story-wei-battle", "story-victory-still",
                 "story-stage-camp", "story-stage-scenario", "story-pre-battle", "story-battle",
                 "story-team-info", "story-officer-page", "ability-acquisition"}
    for start in names:
        for target in names - {"boot"} | {"menu"}:
            try:
                path = harness.route_plan(start, target)
            except ValueError:
                assert start != "boot" and (target != "menu" or start in dead_ends), (start, target)
                continue
            nodes = [start] + [edge[1] for edge in path]
            assert all(a[1] == b[0] for a, b in pairwise(path)) and (not path or path[0][0] == start)
            assert nodes[-1].startswith("menu@") if target == "menu" else nodes[-1] == target
        if start != "boot":
            harness.route_plan("boot", start)
    # the tour's own keys: Up Up from STORY MODE, then Return, reach the options
    assert [edge[1] for edge in harness.route_plan("boot", "options")] == ["title", "menu@story", "menu@option",
                                                                          "options"]


def test_route_resumes_where_it_left_and_stops_after_outside_input(tmp_path, monkeypatch):
    """Bug: a key sent by hand, or a route that failed midway, leaves the recorded screen stale,
    and the next route sends its keys from the wrong screen."""
    (tmp_path / "evidence").mkdir()
    journal, sent, at = tmp_path / "evidence/actions.jsonl", [], ["boot"]
    moves = {}
    for source, destination, steps in harness.ROUTE_EDGES:  # an edge of bare keys, or of one step of keys
        if isinstance(steps[0], str):
            moves[(source, tuple(steps))] = destination
        elif len(steps) == 1 and isinstance(steps[0][0], list):
            moves[(source, tuple(steps[0][0]))] = destination
    moves[("boot", ("Escape",))] = "title"

    def game(root, request, timeout):  # a game that follows the library's own moves, journaled like serve
        sent.append(request)
        keys = request.get("keys") or [request.get("key")]
        at[0] = moves.get((at[0], tuple(keys)), at[0])
        with journal.open("a") as stream:
            stream.write(json.dumps({"request": request, "response": {"ok": True}}) + "\n")
        texture = (harness.ROUTE_SCREENS[at[0]] or {"texture": "none"})["texture"].split("|")[0]
        return {"ok": True, "texts": [], "windows": [], "textures": [{"texture": texture}]}

    real_sleep = harness.time.sleep
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: real_sleep(min(seconds, 0.01)))
    monkeypatch.setattr(harness, "send", game)
    assert harness.route(tmp_path, "options") == 0 and at[0] == "options"
    assert harness.route(tmp_path, "game-settings") == 0 and at[0] == "game-settings"
    assert json.loads((tmp_path / "evidence/route.json").read_text())["at"] == "game-settings"
    with journal.open("a") as stream:  # someone presses Down by hand
        stream.write(json.dumps({"request": {"action": "key", "key": "Down"}, "response": {"ok": True}}) + "\n")
    before = len(sent)
    assert harness.route(tmp_path, "options") == 1
    assert len(sent) == before  # no input from an unknown screen

    journal.unlink()  # a fresh run, where Return on the options no longer opens Game Settings
    (tmp_path / "evidence/route.json").unlink()
    at[0] = "boot"
    del moves[("options", ("Return",))]
    monkeypatch.setattr(harness, "TOUR_WAIT", 0.1)
    assert harness.route(tmp_path, "game-settings") == 1 and at[0] == "options"
    before = len(sent)
    assert harness.route(tmp_path, "options") == 1
    assert len(sent) == before


def test_run_root_is_the_main_checkouts_work_from_any_worktree(tmp_path, monkeypatch, linked_worktree):
    """Bugs: a harness started from a linked worktree refuses the runs prepare_run.py staged in the main checkout's
    work/, or accepts runs under the worktree's own work/ or another checkout's."""
    main, worktree = linked_worktree
    monkeypatch.setattr(checkout, "REPO", worktree)
    sent = []
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: sent.append(root) or {"ok": True})
    mine, xenia = main / "work/runtime-tests/run", main / "work/xenia/runs/run"
    refused = [main / "work/runtime-tests", worktree / "work/runtime-tests/run", tmp_path / "old/work/runtime-tests/run"]
    for root in [mine, xenia, *refused]:
        root.mkdir(parents=True, exist_ok=True)
    assert [harness.main(["status", "--root", str(root)]) for root in (mine, xenia, *refused)] == [0, 0, 1, 1, 1]
    assert sent == [mine.resolve(), xenia.resolve()]


def fake_game(monkeypatch, quad_visible):
    """A test mailbox in fake game memory whose pump runs commands and calls quad_visible(t) each tick,
    which sets whether the texture 4079.12 is drawn. Returns (stop, thread, frame counter)."""
    import ctypes
    import mmap
    import os
    import struct

    monkeypatch.setattr(harness, "game_pid", os.getpid)
    image = mmap.mmap(-1, 0x1000 + harness.TEST_SIZE + 0x20)
    base = ctypes.addressof(ctypes.c_char.from_buffer(image))
    monkeypatch.setattr(harness, "DLL_BASE", base)
    image[:0x1000] = pe_image(0x1000, (b".data", harness.TEST_SIZE + 0x20, 0x1000, 0, 0))
    block = 0x1010
    image[block:block + 24] = struct.pack("<16s2I", harness.TEST_MAGIC, harness.TEST_VERSION, harness.TEST_SIZE)
    quads = block + harness.TEST_SNAPSHOT + 12 + harness.TEST_TEXTS * harness.TEST_TEXT.size
    harness.TEST_QUAD.pack_into(image, quads, 21, 0x1, 7, 4079 << 16 | 12, 5, 6, 7, 8)
    stop, frames, start = threading.Event(), [0], time.monotonic()

    def pump() -> None:
        while not stop.is_set():
            frames[0] += 1
            struct.pack_into("<I", image, block + 24, frames[0])
            struct.pack_into("<3I", image, block + harness.TEST_SNAPSHOT, frames[0], 0,
                             int(quad_visible(time.monotonic() - start)))
            command, done = struct.unpack_from("<2I", image, block + 28)
            if command != done:
                struct.pack_into("<I", image, block + 32, command)
            time.sleep(0.005)

    thread = threading.Thread(target=pump)
    thread.start()
    return stop, thread, frames


def test_wait_capture_takes_the_screenshot_while_the_match_is_still_drawn(tmp_path, monkeypatch):
    """Bug: capturing after the wait returns, or after a fixed settle, misses a banner that is up for
    half a second."""
    (tmp_path / "evidence").mkdir()
    stop, thread, _ = fake_game(monkeypatch, lambda t: 0.15 <= t < 0.65)
    shots = []

    def screenshot(command, **kwargs):
        shots.append(time.monotonic())
        drawn = harness.perform(tmp_path, {"action": "screen"})["textures"]  # what Gamescope would see
        assert [q["texture"] for q in drawn] == ["4079.12"]
        Path(command[2]).write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\0IEND\xaeB\x60\x82")

    monkeypatch.setattr(harness.subprocess, "run", screenshot)
    try:
        started = time.monotonic()
        found = harness.perform(tmp_path, {"action": "wait", "texture": "4079.12", "seconds": 3,
                                           "capture": "banner"})
    finally:
        stop.set()
        thread.join()
    assert found["path"] == str(tmp_path / "evidence/banner.png") and (tmp_path / "evidence/banner.png").is_file()
    assert len(shots) == 1 and shots[0] - started < 0.45  # soon after the match, not at the timeout
    assert found["frames_to_capture"] >= 0 and found["matches"][0]["x"] == 5


def test_wait_capture_refuses_a_taken_name_at_once_and_skips_the_shot_on_timeout(tmp_path, monkeypatch):
    """Bugs: a matched wait that then fails on a taken name wastes the transient it waited for, and a
    timed-out wait captures a screen that never matched."""
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence/taken.png").write_bytes(b"")
    stop, thread, _ = fake_game(monkeypatch, lambda t: 0)
    shots = []
    monkeypatch.setattr(harness.subprocess, "run", lambda *a, **k: shots.append(a))
    wait = {"action": "wait", "texture": "4079.12", "seconds": 0.3}
    try:
        started = time.monotonic()
        with pytest.raises(ValueError, match="already exists"):
            harness.perform(tmp_path, wait | {"seconds": 30, "capture": "taken"})
        assert time.monotonic() - started < 1
        with pytest.raises(ValueError, match="filename stem"):
            harness.perform(tmp_path, wait | {"capture": "../x"})
        with pytest.raises(TimeoutError):
            harness.perform(tmp_path, wait | {"capture": "never"})
    finally:
        stop.set()
        thread.join()
    assert shots == [] and not (tmp_path / "evidence/never.png").exists()


def test_wait_command_sends_capture_and_allows_time_for_the_screenshot(tmp_path, monkeypatch):
    """Bug: the --capture flag is dropped before the request, or the client gives up before the shot."""
    root = tmp_path / "work/runtime-tests/run"
    root.mkdir(parents=True)
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    sent = []
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: sent.append((request, timeout)) or {"ok": True})
    assert harness.main(["wait", "--root", str(root), "--text", "x", "--timeout", "60"]) == 0
    assert harness.main(["wait", "--root", str(root), "--text", "x", "--timeout", "60", "--capture", "b"]) == 0
    plain, shot = sent
    assert "capture" not in plain[0] and shot[0]["capture"] == "b" and shot[1] > plain[1]


def test_check_exits_one_and_reports_a_failed_row(tmp_path, monkeypatch):
    """Bug: check exits 0 or drops a failed assertion, so a regression run reads as green."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    (tmp_path / "evidence").mkdir()
    spec = tmp_path / "spec.toml"
    spec.write_text('[[screen]]\nname = "two-lines"\nroute = "menu"\n'
                    '[[screen.assert]]\nkind = "line_count"\nmax = 1\n')
    drawn = {"texts": [{"text": "a\nb", "x": 10, "y": 10, "width": 40, "caller": "0x1"}],
             "textures": [], "windows": []}
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(harness, "route", lambda root, name, stage: 0)
    monkeypatch.setattr(harness, "ask", lambda root, request: drawn)
    assert harness.check(tmp_path, spec) == 1
    report = json.loads((tmp_path / "evidence/check-report.json").read_text())
    assert [(row["screen"], row["assertion"], row["ok"]) for row in report["rows"]] == [
        ("two-lines", "line_count", False)]


def test_check_driver_fires_a_script_in_the_battle_before_routing_on(tmp_path, monkeypatch):
    """Bug: the script is sent after the route to the screen, so a screen that fills from the script
    (the battle history) is checked empty."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    steps = []
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(harness, "route", lambda root, name, stage: steps.append(name) or 0)
    monkeypatch.setattr(harness, "ask", lambda root, request: steps.append(request["action"] + str(request["number"])))
    driver = harness.CheckDriver(tmp_path)
    driver.route("battle-history", script=10)
    driver.route("battle", script=7)
    assert steps == ["battle", "script10", "battle-history", "battle", "script7"]


def test_check_driver_retries_a_script_the_game_refuses_while_the_opening_dialog_plays(tmp_path, monkeypatch):
    """Bug: the one refusal while the battle's opening dialog plays ends the screen's row as unreachable."""
    monkeypatch.setattr(harness, "route", lambda root, name, stage: 0)
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    replies = iter([RuntimeError("script: the game refused test op 4: a script is playing; retry"),
                    RuntimeError("script: the game refused test op 4: a script is playing; retry"), {"ok": True}])

    def ask(root, request):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(harness, "ask", ask)
    harness.CheckDriver(tmp_path).route("battle-history", script=10)
    assert next(replies, "all used") == "all used"
    monkeypatch.setattr(harness, "ask", lambda root, request: (_ for _ in ()).throw(RuntimeError("script: not in a battle")))
    with pytest.raises(RuntimeError, match="not in a battle"):
        harness.CheckDriver(tmp_path).route("battle-history", script=10)


def test_check_driver_reads_a_banner_once_its_plate_holds_still(tmp_path, monkeypatch):
    """Bug: the banner is read while it still slides in, so its text is at a passing x."""
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(harness.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    plate = lambda x: {"x": x, "y": 273, "width": 28, "height": 35, "caller": "0x550211", "count": 1}
    frames = iter([[], [], [plate(1100)], [plate(1000)], [plate(986)], [plate(986)], [plate(986)]])
    monkeypatch.setattr(harness, "ask", lambda root, request: {"texts": [], "textures": [], "boxes": next(frames)})
    assert harness.CheckDriver(tmp_path).wait({"box_caller": "0x5501dd|0x550211", "timeout": 600})
    assert next(frames, "all read") == "all read"


def test_check_driver_keys_keep_the_route_position_only_while_its_screen_shows(tmp_path, monkeypatch):
    """Bugs: a cursor key on a screen loses the route position, so the next screen in the check is unreachable;
    or a key that left the screen keeps it, so the next route starts from the wrong place."""
    (tmp_path / "evidence").mkdir()
    harness.write_json(tmp_path / "evidence/route.json", {"at": "gallery-backgrounds", "journal": 0})
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    for drawn, after in (("4064.17", "gallery-backgrounds"), ("4079.169", None)):
        def ask(root, request):
            if request["action"] == "screen":
                return {"texts": [], "textures": [{"texture": drawn, "caller": "0x1"}], "boxes": []}
            with (root / "evidence/actions.jsonl").open("a") as journal:
                journal.write(json.dumps({"request": request}) + "\n")
            return {}

        monkeypatch.setattr(harness, "ask", ask)
        harness.CheckDriver(tmp_path).keys(["Right"])
        assert harness.route_position(tmp_path) == after


def test_every_committed_check_names_a_route_screen():
    """Bug: a route name misspelled in a check file fails only at run time, as an unreachable screen."""
    import tomllib

    for spec in (Path(__file__).resolve().parents[1] / "data/checks").glob("*.toml"):
        for screen in tomllib.loads(spec.read_text())["screen"]:
            assert screen["route"] in harness.ROUTE_SCREENS or screen["route"] == "menu", (spec.name, screen["name"])


def test_route_fingerprints_tell_the_screens_apart_that_share_textures():
    """Bug: story-select known by the texture of one cursor column times the route out when the cursor sits
    in the other; the match-settings panel's textures stay drawn after Escape, so only a hidden text ends it."""
    def drawn(*textures, callers=()):
        return {"textures": [{"texture": t} for t in textures], "texts": [{"caller": c, "x": 0} for c in callers]}

    screens = harness.ROUTE_SCREENS
    assert harness.shows(drawn("4082.0"), screens["story-select"]) and harness.shows(drawn("4082.4"), screens["story-select"])
    panel, course = drawn("4098.11"), drawn("4098.11", callers=["0x5a6774"])
    assert harness.shows(panel, screens["vs-team-select"]) and not harness.shows(course, screens["vs-team-select"])
    assert harness.shows(course, screens["vs-course"]) and not harness.shows(panel, screens["vs-course"])


def test_screen_names_the_routed_screen_it_shows_or_says_none_reaches_it(tmp_path, monkeypatch, capsys):
    """Bugs: a frame of a routed screen is reported as unrouted (agents add a duplicate route), or a screen reached by
    hand reads as routed, so nobody adds the route the next agent needs."""
    (tmp_path / "work/runtime-tests/r1").mkdir(parents=True)
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    frames = {"title": [{"texture": "4085.0"}], "unrouted": [{"texture": "9999.0"}]}
    for frame, textures in frames.items():
        monkeypatch.setattr(harness, "send", lambda root, request, timeout: {"ok": True, "texts": [],
                                                                             "textures": textures})
        assert harness.main(["screen", "--root", "r1"]) == 0
        out = capsys.readouterr()
        assert json.loads(out.out)["routes"] == ([] if frame == "unrouted" else ["title"])
        assert (harness.NO_ROUTE in out.err) == (frame == "unrouted")


@pytest.mark.parametrize("result, reason", [(6, "already riding"), (7, "allows no horse"), (3, "not in a battle")])
def test_mount_refusal_gives_the_ops_own_reason_and_the_shared_ones(tmp_path, result, reason):
    """Bugs: an op's own refusal text is lost behind the shared one (result 6 read as a review-texture error), or a
    shared result such as 3 crashes because the op has no text of its own for it."""
    block, memory = 0x100, tmp_path / "mem"
    memory.write_bytes(bytes(block + 48 + harness.TEST_INPUT))
    fd = os.open(memory, os.O_RDWR)

    def game():  # runs the command as the game thread would: result, then done = command
        while not (command := struct.unpack("<I", os.pread(fd, 4, block + 28))[0]):
            time.sleep(0.005)
        os.pwrite(fd, struct.pack("<I", result), block + 44)
        os.pwrite(fd, struct.pack("<I", command), block + 32)

    threading.Thread(target=game, daemon=True).start()
    with pytest.raises(RuntimeError, match=reason):
        harness.mount(fd, block, 0)
    os.close(fd)


def test_relative_teleport_offsets_x_and_z_from_where_the_player_stands(tmp_path):
    """Bug: the offset lands on the height (y) instead of z, or is sent as an absolute spot, so the player is set down
    far from where the agent asked."""
    block, actor = 0x100, 0x400
    memory = tmp_path / "mem"
    memory.write_bytes(bytes(harness.UNITS + 0x20))
    fd = os.open(memory, os.O_RDWR)
    os.pwrite(fd, struct.pack("<I", actor), harness.UNITS + 0x14)
    os.pwrite(fd, struct.pack("<3f", 1000.0, -50.0, 2000.0), actor + harness.ACTOR_AT)
    sent = []

    def game():  # the game thread: take the spot asked for, answer done
        while not (command := struct.unpack("<I", os.pread(fd, 4, block + 28))[0]):
            time.sleep(0.005)
        sent.append(struct.unpack("<3f", os.pread(fd, 12, block + 48)))
        os.pwrite(fd, struct.pack("<I", command), block + 32)

    threading.Thread(target=game, daemon=True).start()
    harness.teleport(fd, block, 300.0, -400.0, 1.5, relative=True)
    os.close(fd)
    assert sent == [(1300.0, 1600.0, 1.5)]


def test_scripts_reads_the_stage_at_the_real_addresses(tmp_path):
    """Bug: a later module constant rebinds the stage address (once a route dict), so every live scripts call fails."""
    memory = tmp_path / "mem"
    with memory.open("wb") as out:
        for address, value in ((harness.STAGE_DATA, 1), (harness.STAGE, 42), (harness.SCRIPT_TABLE, 0x1000),
                               (harness.SCRIPT_TABLE + 4, 0), (harness.SCRIPT_EVENTS, 0x2000)):
            out.seek(address)
            out.write(value.to_bytes(4, "little"))
    with memory.open("rb") as handle:
        assert harness.stage_scripts(handle.fileno()) == {"stage": 42, "scripts": []}


def test_tap_keys_waits_the_settle_after_the_last_key_and_the_gap_only_between_keys(monkeypatch):
    """Bugs: the last key of a sequence still waits the gap (or a route's settle=0 is ignored), so every
    step of a route pays a second it already spends polling for the next screen; every call primes the
    display again and spends a second before its first key."""
    class Lib:
        def __getattr__(self, name):
            def call(*args):
                return 1
            return call

    sleeps = []
    monkeypatch.setenv("GAMESCOPE_WAYLAND_DISPLAY", "synthetic-test-only")
    monkeypatch.setattr(harness, "X_INPUT", None)
    monkeypatch.setattr(harness.ctypes, "CDLL", lambda name: Lib())
    monkeypatch.setattr(harness.time, "sleep", sleeps.append)
    harness.tap_keys(["Down", "Return"], 0.08, 0.3, 0)
    assert sleeps == [1, 0.08, 0.3, 0.08, 0]  # the first call primes the display for a second
    sleeps.clear()
    harness.tap_keys(["Return"], 0.08, 1.0)  # hand use: the settle defaults to the gap; no priming again
    assert sleeps == [0.08, 1.0]


def test_server_passes_settle_to_the_keys_and_refuses_one_out_of_range(tmp_path, monkeypatch):
    """Bug: the settle field is dropped (a route's keys settle 1 s again) or unchecked (a typo sleeps for an hour)."""
    sent = []
    monkeypatch.setattr(harness, "tap_keys", lambda names, hold, gap, settle: sent.append((names, gap, settle)))
    perform = lambda **request: harness.perform(tmp_path, request)
    perform(action="key", key="Return", hold=0.08, settle=0)
    perform(action="keys", keys=["Up", "Up"], hold=0.08, gap=0.5, settle=0)
    perform(action="key", key="Return", hold=0.08)
    assert sent == [(["Return"], 1.0, 0.0), (["Up", "Up"], 0.5, 0.0), (["Return"], 1.0, 1.0)]
    with pytest.raises(ValueError, match="settle"):
        perform(action="key", key="Return", hold=0.08, settle=3600)


def screen_after(polls, texture="X"):
    """A game that shows ``texture`` from its ``polls``-th screen request on."""
    count = iter(range(1, 1000))
    return lambda root, request, timeout=30: {"ok": True, "texts": [], "windows": [], "textures": (
        [{"texture": texture}] if request["action"] == "screen" and next(count) >= polls else [])}


def test_route_step_with_a_screen_to_wait_for_sends_no_settle_and_waits_for_the_screen(tmp_path, monkeypatch):
    """Bugs: a route key settles a second the poll for the next screen already covers; or the step returns
    before the destination is drawn and the next key goes to a screen still sliding in."""
    sleeps, requests = [], []
    game = screen_after(4)
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: requests.append(request) or game(root, request, timeout))
    monkeypatch.setattr(harness.time, "sleep", sleeps.append)
    elsewhere = {"texts": [], "textures": [{"texture": "Y"}]}
    shown = harness.drive_step(tmp_path, ["Return"], {"texture": "X"}, None, "a -> b", quick=True, before=elsewhere)
    assert shown["textures"] == [{"texture": "X"}]  # the matching screen, for the next step's check
    assert requests[0] == {"action": "key", "key": "Return", "hold": 0.08, "settle": 0}
    assert [r["action"] for r in requests] == ["key"] + ["screen"] * 4  # polled until drawn
    assert sleeps == [harness.ROUTE_POLL] * 3  # polling fast, no settle sleep
    requests.clear()
    harness.drive_step(tmp_path, ["Down", "Down"], {"texture": "X"}, None, "a -> b", quick=True, before=elsewhere)
    assert requests[0]["settle"] == 0 and requests[0]["gap"] == harness.ROUTE_KEY_GAP  # keys still apart from each other


def test_route_step_whose_screen_was_showing_already_settles_because_the_poll_gates_nothing(tmp_path, monkeypatch):
    """Bug: a main-menu cursor move (same fingerprint before and after) passes its poll at once, and the next key
    follows 0.2 s later while the cursor still moves, so the menu drops it (Return after Down Down Down opened
    the wrong item)."""
    requests = []
    game = screen_after(1)
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: requests.append(request) or game(root, request, timeout))
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    showing = {"texts": [], "textures": [{"texture": "X"}]}
    harness.drive_step(tmp_path, ["Down"], {"texture": "X"}, None, "menu -> menu", quick=True, before=showing)
    harness.drive_step(tmp_path, ["Down"], {"texture": "X"}, None, "after a blind step", quick=True, before=None)
    assert [r["settle"] for r in requests if r["action"] == "key"] == [harness.ROUTE_GAP] * 2


def test_blind_route_step_and_the_tour_keep_their_settle(tmp_path, monkeypatch):
    """Bug: a step with no screen to wait for loses its settle and the next key hits a menu mid-transition;
    or the tour (not a route) changes timing."""
    sleeps, requests = [], []
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: requests.append(request) or {"ok": True})
    monkeypatch.setattr(harness.time, "sleep", sleeps.append)
    harness.drive_step(tmp_path, ["Left"], None, None, "blind", quick=True)
    assert "settle" not in requests[0] and sleeps == [0]  # the server's own 1 s settle after a single key stays
    harness.drive_step(tmp_path, ["Left"], None, None, "blind tour step")
    assert sleeps[-1] == harness.TOUR_SETTLE
    requests.clear()
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: requests.append(request) or screen_after(1)(root, request))
    harness.drive_step(tmp_path, ["Return"], {"texture": "X"}, None, "tour step")
    assert "settle" not in requests[0]


def test_check_visits_the_nearest_screen_next_and_a_dead_end_last():
    """Bugs: screens visited in spec order send a route back to the main menu between neighbours; the narration
    (no way out) is visited before screens that still need the menu."""
    pick = lambda start, names: [sc["name"] for sc in harness.order_screens([{"name": n, "route": n} for n in names], start)]
    # from the options: game-settings and options-controller are one or three edges away, vs-course is 5 edges
    assert pick("options", ["vs-course", "game-settings", "options-controller"]) == [
        "game-settings", "options-controller", "vs-course"]
    assert pick("menu@story", ["story-z-narration", "vs-course", "game-settings"])[-1] == "story-z-narration"
    # the narration is as near as the camp here and first in spec order, yet it has no way out
    assert pick("story-z-scenario", ["story-z-narration", "story-z-camp"]) == ["story-z-camp", "story-z-narration"]
    # the dead ends lead on from each other, so one boot checks all three, whatever order the spec lists them in
    assert pick("story-z-scenario", ["story-z-still-2", "story-z-still", "story-z-narration"]) == [
        "story-z-narration", "story-z-still", "story-z-still-2"]
    assert pick(None, ["vs-course", "game-settings"]) == ["vs-course", "game-settings"]  # position unknown: spec order
    assert pick("options", ["vs-course", "no-such-screen", "game-settings"]) == ["game-settings", "vs-course", "no-such-screen"]


def test_check_orders_a_screen_that_needs_a_stage_through_the_scenario_select():
    """Bug: a screen needing stage 5 is checked on the stage an earlier screen loaded, because the route
    from inside the battle never passes the scenario select that picks the stage."""
    legs = lambda at, loaded, battle: harness.check_legs(at, loaded, "briefing", battle, None)
    assert legs("pause", 40, 45)[0][0] == "dream-select"
    assert legs("pause", 45, 45)[0] == ["briefing"] and legs("pause", 40, None)[0] == ["briefing"]  # the stage it has
    assert legs("menu@dream", None, 45)[3] == 45  # the route plays the stage the screen names
    assert legs("pause", 40, 45)[3] == 45
    screens = [{"name": "a", "route": "briefing", "battle": 45}, {"name": "b", "route": "pre-battle"},
               {"name": "c", "route": "pre-battle", "battle": 45}]
    # a loads stage 45; c is next to it on that stage, b (stage 40) goes back to the scenario select, so it comes last
    assert [sc["name"] for sc in harness.order_screens(screens, "menu@dream")] == ["a", "c", "b"]


def test_check_driver_goes_back_to_the_scenario_select_for_another_stage(tmp_path, monkeypatch):
    """Bug: the driver routes straight to the screen and plays the previous stage."""
    (tmp_path / "evidence").mkdir()
    harness.write_json(tmp_path / "evidence/route.json", {"at": "pause", "journal": 0})
    legs = []
    monkeypatch.setattr(harness, "route", lambda root, name, stage: legs.append((name, stage)) or 0)
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    driver = harness.CheckDriver(tmp_path)
    driver.loaded = 40
    driver.route("briefing", battle=45)
    assert legs == [("dream-select", 45), ("briefing", 45)] and driver.loaded == 45


def test_check_only_runs_the_named_screens_and_refuses_a_misspelt_one(tmp_path, monkeypatch):
    """Bugs: --only runs everything, or ignores a misspelt name and checks nothing yet exits 0; the order
    used is not recorded."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    (tmp_path / "evidence").mkdir()
    spec = tmp_path / "spec.toml"
    spec.write_text("".join(f'[[screen]]\nname = "{n}"\nroute = "menu"\n[[screen.assert]]\nkind = "no_cjk"\n'
                            for n in ("a", "b", "c")))
    visited = []
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(harness, "route", lambda root, name, stage: visited.append(name) or 0)
    monkeypatch.setattr(harness, "ask", lambda root, request: {"texts": [{"text": "x", "x": 0, "y": 0, "width": 1, "caller": "0x1"}],
                                                               "textures": [], "windows": []})
    assert harness.check(tmp_path, spec, ["c", "a"]) == 0
    report = json.loads((tmp_path / "evidence/check-report.json").read_text())
    assert report["order"] == ["a", "c"] and [r["screen"] for r in report["rows"]] == ["a", "c"]
    with pytest.raises(ValueError, match="no screen zz"):
        harness.check(tmp_path, spec, ["a", "zz"])


def test_check_learn_saves_each_screens_stock_relations_under_its_name_and_keeps_the_others(tmp_path, monkeypatch):
    """Bugs: a learn run on some screens drops the relations of screens it did not visit, or files one screen's
    relations under another's name, so every later check of that screen compares against the wrong layout."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    (tmp_path / "evidence").mkdir()
    spec = tmp_path / "spec.toml"
    spec.write_text("".join(f'[[screen]]\nname = "{n}"\nroute = "menu"\n[[screen.assert]]\nkind = "no_cjk"\n'
                            for n in ("a", "b")))
    shown = iter([{"x": 0, "y": 0, "width": 1, "height": 1}, {"x": 500, "y": 0, "width": 9, "height": 9}])
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(harness, "route", lambda root, name, stage: 0)
    monkeypatch.setattr(harness, "ask", lambda root, request: {
        "texts": [{"text": "x", "x": 0, "y": 0, "width": 1, "caller": "0x1"}], "textures": [], "windows": [],
        "boxes": [{**next(shown), "caller": "0x2", "count": 1}]})
    structure = tmp_path / "structure.json"
    structure.write_text(json.dumps({"z": [{"rule": "on_screen", "el": "box@0x9#0"}]}))
    harness.check(tmp_path, spec, learn=structure)
    learned = json.loads(structure.read_text())
    place = {name: [r["rect"] for r in rels if r["rule"] == "place" and r["el"] == "box@0x2#0"]
             for name, rels in learned.items()}
    assert place == {"a": [[0, 0, 1, 1]], "b": [[500, 0, 509, 9]], "z": []}


def test_route_waits_for_the_main_menu_items_before_the_next_input(tmp_path, monkeypatch):
    """Bug: the menu's wheel art is its fingerprint and shows at once, so a route that goes on to Down Down Down
    sends them while the items still slide in and the game loses or misapplies them."""
    events = []
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: events.append(request["action"]) or
                        {"ok": True, "texts": [], "windows": [], "textures": [{"texture": "4085.6"}, {"texture": "4085.0"}]})
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    (tmp_path / "evidence").mkdir()
    harness.write_json(tmp_path / "evidence/route.json", {"at": "menu@story", "journal": 0})
    assert harness.route(tmp_path, "menu@vs") == 0
    assert events.index("keys") < events.index(("sleep", harness.MENU_SETTLE))  # the wait follows the keys that reach it


def test_route_step_with_a_gap_waits_after_its_screen_shows_not_after_its_keys(tmp_path, monkeypatch):
    """Bug: the wait for a slow screen (the battle shows its fingerprint at its first frame, then ignores input
    for seconds) runs inside the key press, ends before the screen exists, and the next Escape is lost."""
    events = []
    game = screen_after(3)
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: events.append(request["action"]) or game(root, request, timeout))
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    elsewhere = {"texts": [], "textures": []}
    harness.drive_step(tmp_path, ["Return"], {"texture": "X", "gap": 3.0}, None, "a -> b", quick=True, before=elsewhere)
    assert events[-1] == ("sleep", 3.0) and events.count("screen") == 3  # after the third poll showed it


def test_route_waits_a_second_after_the_screen_that_ends_an_edge_shows(tmp_path, monkeypatch):
    """Bug: a submenu's fingerprint shows while it slides in, and the next edge's first key (Down, Return) is lost
    (story camp -> weapons never opened)."""
    events = []
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: events.append(request["action"]) or
                        {"ok": True, "texts": [], "windows": [], "textures": [{"texture": "4085.6"}, {"texture": "4079.171"}]})
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    (tmp_path / "evidence").mkdir()
    harness.write_json(tmp_path / "evidence/route.json", {"at": "menu@option", "journal": 0})
    assert harness.route(tmp_path, "options") == 0
    assert events[-1] == ("sleep", harness.ARRIVE_GAP)  # after the poll that saw the options screen


def test_fingerprint_lines_tells_a_two_line_caption_from_a_one_line_one():
    """Bug: a caller-only fingerprint matches the single-line captions that fill most of a battle opening, so a
    route to the two-line caption stops at the first one."""
    caption = lambda text: {"textures": [], "texts": [{"caller": "0x49dace", "x": 0, "text": text}]}
    two = {"caller": "0x49dace", "lines": 2}
    assert harness.shows(caption("one\ntwo"), two) and not harness.shows(caption("one"), two)
    assert harness.shows(caption("one"), {"caller": "0x49dace"}) and not harness.shows(caption("a\nb\nc"), two)


def test_resent_key_is_not_sent_to_a_screen_that_came_up_since_the_last_poll(tmp_path, monkeypatch):
    """Bug: the Escape that skips a movie is resent on schedule even when the narration came up in the
    last 0.1 s, and the Escape then ends the narration."""
    clock, requests = [0.0], []
    monkeypatch.setattr(harness.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def send(root, request, timeout):
        requests.append(request["action"])
        if request["action"] != "screen":
            return {"ok": True}
        up = clock[0] >= 0.55  # drawn after the last poll before the resend is due (0.5 s), but before the resend itself
        return {"ok": True, "texts": [{"caller": "0x561111", "x": 0}] if up else [], "textures": []}

    monkeypatch.setattr(harness, "send", send)
    harness.drive_step(tmp_path, ["Escape"], {"caller": "0x561111", "every": 0.5}, None, "movie -> narration", quick=True)
    assert requests.count("key") == 1  # the poll before the resend found the narration


def test_fingerprint_texts_tells_a_two_line_movie_caption_from_a_single_line():
    """Bug: a movie's two-line caption is two texts of one caller (y 616 and 652 on one build, 600 and 636 on the
    other), so a caller-only fingerprint stops the route at the first single-line caption, and one by y fits one build."""
    drawn = lambda *ys: {"textures": [], "texts": [{"caller": "0x56a116", "x": 0, "y": y, "text": "t"} for y in ys]}
    two = harness.ROUTE_SCREENS["gallery-movie-two-line"]
    assert harness.shows(drawn(616, 652), two) and harness.shows(drawn(600, 636), two) and not harness.shows(drawn(652), two)
    assert harness.shows(drawn(652), harness.ROUTE_SCREENS["gallery-movie-play"])


def test_fingerprint_quad_caller_tells_two_screens_that_draw_the_same_textures_and_texts():
    """Bug: the weapon upgrade and the fusion officer lists look alike to a texture or text fingerprint, so the
    route for one is satisfied by the other (a fingerprint by the names' x was right on one build, wrong on the other)."""
    drawn = lambda caller: {"texts": [], "textures": [{"texture": "4060.15", "caller": caller}, {"texture": "4060.3", "caller": "0x53326a"}]}
    fusion, upgrade = harness.ROUTE_SCREENS["customize-fusion-officers"], harness.ROUTE_SCREENS["customize-weapon-upgrade"]
    assert harness.shows(drawn("0x52d841"), fusion) and not harness.shows(drawn("0x52d841"), upgrade)
    assert harness.shows(drawn("0x52967d"), upgrade) and not harness.shows(drawn("0x52967d"), fusion)


def test_check_visits_the_unlocking_screen_after_the_screens_that_need_the_plain_save():
    """Bug: the fusion weapon list (its route sends `unlock`: four weapons per officer) is visited before a weapon
    screen that is checked against the save's own weapons; and the story chain is visited out of order."""
    pick = lambda start, names: [sc["name"] for sc in harness.order_screens([{"name": n, "route": n} for n in names], start)]
    # equally near the fusion officers, and first in spec order, yet it unlocks
    assert pick("customize-fusion-officers", ["customize-fusion-weapons", "camp-customize"]) == [
        "camp-customize", "customize-fusion-weapons"]
    assert pick("story-wei-scenario", ["story-victory-still", "story-narration", "story-wei-battle", "story-wei-pre-battle"]) == [
        "story-narration", "story-wei-pre-battle", "story-wei-battle", "story-victory-still"]


def test_route_leaves_a_gallery_movie_between_two_captions_and_keeps_its_place_across_unlock(tmp_path, monkeypatch):
    """Bugs: a caption is on screen only for seconds, so the next route's start check finds the movie gone and refuses
    to leave it; or `unlock` (not a move) makes the route forget where the game is."""
    (tmp_path / "evidence").mkdir()
    harness.write_json(tmp_path / "evidence/route.json", {"at": "gallery-movie-play", "journal": 0})
    sent = []
    monkeypatch.setattr(harness, "send", lambda root, request, timeout: sent.append(request["action"]) or
                        {"ok": True, "texts": [{"caller": "0x556821", "x": 0}], "textures": [], "windows": []})
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    assert harness.route(tmp_path, "gallery-movies") == 0 and "key" in sent
    journal = tmp_path / "evidence/actions.jsonl"
    journal.write_text(json.dumps({"request": {"action": "unlock"}, "response": {"ok": True}}) + "\n")
    assert harness.route_position(tmp_path) == "gallery-movies"


def test_stage_keys_land_on_every_stage_by_the_games_own_cursor_rules():
    """Bugs: a Left where a Right belongs, a row and a column swapped, or a story stage put in the wrong slot, so a
    route confirms another stage than the one it names. The oracle replays the keys with the select screens' rules
    (Dream 0x548200: a row of 10 wraps, Up and Down step 10 over 40; chapter screen 0x58BC50: slot c sits over c + 15)."""
    def dream(cursor, keys):
        for key in keys:
            row, col = divmod(cursor, 10)
            col = (col + {"Right": 1, "Left": -1}.get(key, 0)) % 10
            row = (row + {"Down": 1, "Up": -1}.get(key, 0)) % 4
            cursor = row * 10 + col
        return cursor
    stages = [s for c in range(40) for s in [c + 40 if c < 28 else c + 104]]
    wrong = [(start, stage) for start in range(40) for stage in stages
             if stages[dream(start, harness.dream_keys(stage, start))] != stage]
    assert not wrong
    left = harness.STORIES
    for stage in [*range(40), *range(72, 132)]:
        rows, chapter = harness.story_keys(stage)
        column = 1 if "Right" in rows else 0
        row = (rows.count("Down") - rows.count("Up")) % (5 if column else 4)
        slot = chapter.count("Right") + 15 * chapter.count("Down")
        if column == 0:
            got = left[row][slot if slot < 8 else slot - 8]
        else:
            got = 32 + slot if row == 4 else 8 * row + slot
        assert got == stage, (stage, rows, chapter)
    with pytest.raises(ValueError):
        harness.dream_keys(1, 0)
    with pytest.raises(ValueError):
        harness.story_keys(45)


def test_staged_run_sees_the_template_prefix_through_an_overlay_on_its_own_folder(tmp_path, monkeypatch):
    """Bug: the overlay is missing, or mounted before the run's own bind hides it, so Proton finds an empty
    prefix (or writes into the shared template) and the run plays a different Wine setup than the template's."""
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    root = tmp_path / "work/runtime-tests/overlay"
    _staged(root / "game")
    (root / "launch.sh").write_text("exit 0\n")
    template = tmp_path / "work/runtime-tests/_template"
    (template / "steamrt").mkdir(parents=True)
    (template / "steamrt/run").write_text("")
    overlays = {"prefix": str(template / "prefix")}
    (root / "fixtures.json").write_text(json.dumps({**json.loads((root / "fixtures.json").read_text()), "overlays": overlays}))
    calls = []
    monkeypatch.setattr(harness.subprocess, "Popen", lambda command, **kwargs: calls.append(command) or
                        SimpleNamespace(wait=lambda: 0, poll=lambda: 0, returncode=0))
    monkeypatch.setattr(harness, "start_audio", lambda root: None)
    harness.main(["run", "--root", str(root), "--game", str(root / "game"), "--authorize-launch", "--target", harness.TARGET])
    command = next(c for c in calls if "bwrap" in c)
    after = command.index("--bind") + 3  # the run's own bind comes first
    for part, lower in overlays.items():
        at = command.index(lower) - 1
        assert at > after and command[at:at + 6] == ["--overlay-src", lower, "--overlay", str(root / part),
                                                      str(root / "overlay-work" / part), str(root / part)]


def test_check_report_names_the_shortcuts_behind_each_capture(tmp_path, monkeypatch):
    """Bugs: a results screen reached by `win` (seconds of battle, no KOs), or any screen after `unlock`, is reported
    as plain play, so its capture passes for a typical one; or a later screen keeps a one-off shortcut's label."""
    (tmp_path / "evidence").mkdir()
    monkeypatch.setattr(harness, "route", lambda root, name, stage: 0)
    monkeypatch.setattr(harness, "ask", lambda root, request: {"ok": True})
    driver = harness.CheckDriver(tmp_path)

    def visit(at, name):
        harness.write_json(tmp_path / "evidence/route.json", {"at": at, "journal": 0})
        driver.route(name)
        driver.capture()

    visit("pause", "save-prompt")  # through `win` and the results
    visit("customize-fusion-officers", "customize-fusion-weapons")  # sends `unlock`
    visit("menu@story", "options")
    assert driver.shortcuts == {"save-prompt": ["win"], "customize-fusion-weapons": ["unlock"], "options": ["unlock"]}


def test_two_runs_hold_the_two_game_slots_and_a_third_waits_across_worktrees(monkeypatch, linked_worktree):
    """Bugs: two runs lock the same slot file (one waits for nothing, or both slots go to one run), a third run
    starts while two games are up, or a linked worktree locks slots of its own and starts a third game."""
    main, worktree = linked_worktree
    (main / "work").mkdir()
    monkeypatch.setattr(checkout, "REPO", main)
    first, _ = harness.take_slot(0.01)
    monkeypatch.setattr(checkout, "REPO", worktree)
    second, _ = harness.take_slot(0.01)
    third, got = threading.Event(), []
    thread = threading.Thread(target=lambda: got.append(harness.take_slot(0.01)[0]) or third.set(), daemon=True)
    thread.start()
    assert not third.wait(0.3)
    os.close(first)
    assert third.wait(2)
    for fd in (second, *got):
        os.close(fd)


def test_two_games_at_once_each_run_their_own_writable_steam_runtime(tmp_path, monkeypatch):
    """Bug: both game slots launch the same Steam runtime, so two games share pressure-vessel's var/; or the runtime
    is left read-only (or bound as two mounts), so pressure-vessel cannot hard-link it into var/."""
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    template = tmp_path / "work/runtime-tests/_template"
    for runtime in harness.RUNTIMES:
        (template / runtime).mkdir(parents=True)
        (template / runtime / "run").write_text("")
    calls = []
    monkeypatch.setattr(harness.subprocess, "Popen", lambda command, **kwargs: calls.append(command) or
                        SimpleNamespace(wait=lambda: 0, poll=lambda: 0, returncode=0))
    monkeypatch.setattr(harness, "start_audio", lambda root: None)
    for name in ("first", "second"):  # a run keeps its slot until its process ends, so here the second gets slot 1
        root = tmp_path / "work/runtime-tests" / name
        _staged(root / "game")
        (root / "launch.sh").write_text("exit 0\n")
        harness.main(["run", "--root", str(root), "--game", str(root / "game"), "--authorize-launch", "--slot",
                      "--target", harness.TARGET])
    launched = [c for c in calls if "bwrap" in c]
    used = [c[c.index("SLR4") + 1] for c in launched]
    assert used == [str(template / runtime / "run") for runtime in harness.RUNTIMES]
    for command, runtime in zip(launched, harness.RUNTIMES, strict=True):
        bind = command.index(str(template / runtime)) - 1
        assert command[bind:bind + 3] == ["--bind", str(template / runtime), str(template / runtime)]


def test_a_resend_loop_sends_nothing_when_its_screen_is_up_already(monkeypatch):
    """Bug: after `win` on a stage with no victory event the results are up at once, and the Escape meant to skip the
    event lands on the results screen."""
    sent = []
    results = {"textures": [{"texture": "4079.52", "caller": "0x1"}], "texts": []}
    monkeypatch.setattr(harness, "ask", lambda root, request, *a: sent.append(request) or results)
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    until = {**harness.ROUTE_SCREENS["results"], "every": 3}
    assert harness.drive_step(Path("."), ["Escape"], until, None, "skip", quick=True, before=results) is results
    assert sent == []
    event = {"textures": [], "texts": [{"caller": "0x49dace", "text": "a", "x": 0, "y": 0}]}
    assert harness.shows(event, {"any": [harness.ROUTE_SCREENS["results"], {"caller": "0x49dace"}]})


def test_a_pad_run_routes_with_pad_buttons(tmp_path, monkeypatch):
    """Bugs: a `--route-input pad` run still presses the keyboard, so every capture shows the keyboard prompts; or
    Return becomes B (back) and the route leaves the screen it meant to enter; or a key with no button is dropped."""
    (tmp_path / "evidence").mkdir()
    harness.write_json(tmp_path / "evidence/session.json", {"route_input": "pad"})
    sent = []
    monkeypatch.setattr(harness, "ask", lambda root, request, *a: sent.append(request) or {"textures": [], "texts": []})
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    harness.drive_step(tmp_path, ["Down", "Return", "Escape"], None, None, "pad", quick=True)
    assert [(r["action"], r["names"]) for r in sent] == [("pad", ["DOWN", "A", "B"])]
    with pytest.raises(ValueError, match="F1"):
        harness.drive_step(tmp_path, ["F1"], None, None, "pad", quick=True)


def test_video_pins_the_screen_stream_to_bgrx(tmp_path, monkeypatch):
    """Bug: left to negotiate, NVENC takes Gamescope's NV12, whose per-frame conversion halves the game to 30 fps."""
    (tmp_path / "evidence").mkdir()
    calls = []
    monkeypatch.setattr(harness.subprocess, "Popen", lambda command, **kwargs: calls.append(command))
    harness.perform(tmp_path, {"action": "video", "name": "clip", "seconds": 5})
    command = calls[0]
    link = command.index("!", command.index("pipewiresrc"))
    assert command[link + 1] == "video/x-raw,format=BGRx"
