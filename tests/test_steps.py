"""The step runner (install/steps.py): each step builds in a worker process with a fixed environment; its events reach
the progress, its prints the install log, its errors the installer, and Cancel ends it with all it started.

The fakes below stand in for the installer's step functions. A worker imports them from this module by name, as it
imports the real ones from wozmod.install.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import fake_program
import pytest

import wozmod.install as installer
from wozmod import progress as reporting
from wozmod.install import InstallError, manifest, steps
from wozmod.progress import Cancelled, Progress


def _fake(monkeypatch, function, *names: str) -> None:
    for name in names:
        monkeypatch.setattr(installer, steps.STEPS[name].function, function)


def _write_environment(out: Path) -> None:
    out.mkdir(parents=True)
    (out / "seen.json").write_text(json.dumps({"environment": dict(os.environ), "folder": os.getcwd()}),
                                   encoding="utf-8")


def test_a_worker_sees_only_the_fixed_environment_in_a_folder_of_its_own(tmp_path, monkeypatch):
    """Bug: the worker inherits this process's environment or working folder, so a player's time zone, locale, hash
    seed or a stray WOZMOD_ setting changes what a step writes while its step key stays the same."""
    for name, value in (("TZ", "Pacific/Kiritimati"), ("LANG", "ja_JP.SJIS"), ("PYTHONHASHSEED", "4242"),
                        ("WOZMOD_X", "stray"), ("WOZMOD_NATIVE", "1")):  # the text step declares WOZMOD_NATIVE
        monkeypatch.setenv(name, value)
    _fake(monkeypatch, _write_environment, "text")
    steps.run({"text": {"out": tmp_path / "out"}}, Progress(), None)
    seen = json.loads((tmp_path / "out/seen.json").read_text(encoding="utf-8"))
    environment, folder = seen["environment"], seen["folder"]
    temporary = {environment.pop(name) for name in ("TEMP", "TMP", "TMPDIR")}
    passed = {name: os.environ[name] for name in steps.PASSED if name in os.environ}
    expected = {**passed, "TZ": "UTC", "LC_ALL": "C.UTF-8", "PYTHONHASHSEED": "0", "PYTHONUTF8": "1",
                "WOZMOD_NATIVE": "1"}
    if sys.platform == "win32":  # Windows and Wine add names of their own to every new process: check the planted ones
        assert {name: environment.get(name) for name in expected} == expected
        assert {"LANG", "WOZMOD_X"}.isdisjoint(environment)
    else:
        assert environment == expected
    assert {os.path.realpath(p) for p in temporary} == {folder} and folder != os.path.realpath(os.getcwd())



def test_a_relative_path_means_the_same_folder_in_the_worker(tmp_path, monkeypatch):
    # Bug: the worker, in a folder of its own, takes a path the player gave relative to theirs (wozmod install
    # --game .) from its own folder, so the step reads no game and writes where the install never looks.
    monkeypatch.chdir(tmp_path)
    _fake(monkeypatch, _write_environment, "text")
    steps.run({"text": {"out": Path("relative/out")}}, Progress(), None)
    assert (tmp_path / "relative/out/seen.json").is_file()

def _report_until_seen(out: Path) -> None:
    # The stage stays open until the parent shows its event: a finished stage drops later events by design.
    reporting.report(out.name, "converting voice clips", 0.5, (2, 4))
    deadline = time.monotonic() + 10
    while not (out.parent / "seen").exists() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_events_from_the_workers_reach_the_progress(tmp_path, monkeypatch):
    # Bug: the worker's events stay in the worker, so the voices line never moves.
    def show(progress):
        if progress.stages["voices"].text == "converting voice clips, 2 of 4":
            (tmp_path / "seen").touch()

    _fake(monkeypatch, _report_until_seen, "voices", "art")
    progress = Progress(show)
    steps.run({name: {"out": tmp_path / name} for name in ("voices", "art")}, progress, None)
    assert (tmp_path / "seen").exists()
    assert progress.stages["art"].finished is not None


def _fail(out: Path) -> None:
    raise InstallError("Menu art: entry 4079 is not on the disc")


def test_a_steps_error_reaches_the_installer_as_it_was_raised(tmp_path, monkeypatch):
    # Bug: the error is lost on the way back, so the player gets an unexpected-error traceback (or a finished
    # install) instead of the step's message.
    _fake(monkeypatch, _fail, "art")
    with pytest.raises(InstallError, match="^Menu art: entry 4079 is not on the disc$"):
        steps.run({"art": {"out": tmp_path}}, Progress(), None)


def _until_cancelled(out: Path) -> None:
    while True:
        reporting.report(out.name, "working", 0.1)
        time.sleep(0.02)


def test_cancel_stops_the_workers_and_raises(tmp_path, monkeypatch):
    # Bug: Cancel sets a flag the workers never see, so the install keeps building until the kill.
    # A grace period far longer than two workers take to start (9 s seen under Wine on a busy machine) separates the
    # two endings: stopped by the workers themselves, or by the kill once the grace period is over.
    monkeypatch.setattr(steps, "GRACE", 60)
    _fake(monkeypatch, _until_cancelled, "voices", "art")
    progress = Progress()
    started = time.monotonic()
    threading.Timer(0.5, progress.cancel.set).start()
    with pytest.raises(Cancelled):
        steps.run({name: {"out": tmp_path / name} for name in ("voices", "art")}, progress, None)
    assert time.monotonic() - started < steps.GRACE


HEARTBEAT = ("import sys, time\nfrom pathlib import Path\nfor beat in range(1200):\n"
             "    Path(sys.argv[1]).write_text(str(beat), encoding='utf-8')\n    time.sleep(0.05)\n")


def _start_a_child_and_hang(out: Path) -> None:
    out.mkdir(parents=True)
    child = subprocess.Popen([sys.executable, "-c", HEARTBEAT, str(out / "heartbeat")])
    while not (out / "heartbeat").exists():  # running, so a child that survives the kill keeps counting
        time.sleep(0.01)
    reporting.report(out.name, "working", 0.1, (child.pid, 0))  # tells the parent the worker is busy
    time.sleep(60)  # never looks at the cancel flag again


@pytest.mark.slow_first
def test_a_worker_that_does_not_stop_is_killed_together_with_what_it_started(tmp_path, monkeypatch):
    # Bug: only the worker is killed, so its ffmpeg (or its own pool) keeps running after Cancel.
    monkeypatch.setattr(steps, "GRACE", 1)
    _fake(monkeypatch, _start_a_child_and_hang, "voices")
    found = manifest.manifests()  # the fake starts a Python where the voices start ffmpeg
    python = Path(sys.executable).name.removesuffix(".exe")
    monkeypatch.setattr(manifest, "manifests", lambda root=None: {
        **found, "voices": dataclasses.replace(found["voices"], starts=(python,))})
    # Cancel once the worker has reported its child: a timer could fire before a slow worker starts one.
    progress = Progress(lambda progress: progress.stages["voices"].text and progress.cancel.set())
    with pytest.raises(Cancelled):
        steps.run({"voices": {"out": tmp_path / "voices"}}, progress, None)
    heartbeat = tmp_path / "voices/heartbeat"
    time.sleep(0.5)
    stopped_at = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.5)  # ten beats, were the child still running
    assert heartbeat.read_text(encoding="utf-8") == stopped_at


def _chatter(out: Path) -> None:
    print("entry 4117: 1 textures, 2017488 -> 1850400 bytes", flush=True)
    os.write(1, b"written below Python\n")


def test_what_a_step_prints_is_added_to_the_log_not_shown(tmp_path, monkeypatch, capfd):
    # Bug: the build's prints ("entry 4117: ...") land in the terminal view or the window, or the worker starts the
    # log over and drops the install's own first lines.
    _fake(monkeypatch, _chatter, "art")
    log = tmp_path / "install.log"
    log.write_text("art: built\n", encoding="utf-8")
    steps.run({"art": {"out": tmp_path}}, Progress(), log)
    assert capfd.readouterr() == ("", "")
    assert log.read_text(encoding="utf-8").splitlines() == [
        "art: built", "entry 4117: 1 textures, 2017488 -> 1850400 bytes", "written below Python"]


def test_the_release_program_starts_a_worker(tmp_path, monkeypatch):
    # Bug: the packaged program (PyInstaller sets sys.frozen) treats --step-worker as a command line to parse, so
    # every install from a release zip stops at the build.
    program = fake_program.program(tmp_path / "wozmod", f"import sys\nsys.path[:0] = {[os.path.abspath(p) for p in sys.path]!r}\n"
                                   "from wozmod.__main__ import program\nraise SystemExit(program())\n")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(program))
    _fake(monkeypatch, _write_environment, "text")
    steps.run({"text": {"out": tmp_path / "out"}}, Progress(), None)
    assert (tmp_path / "out/seen.json").is_file()


def _crash(out: Path) -> None:
    (out / "died").write_text(repr(time.time()), encoding="utf-8")  # when it died, for the parent to time from
    os._exit(9)  # as when the system kills the worker for memory


def test_a_worker_that_dies_is_reported_at_once(tmp_path, monkeypatch):
    # Bug: the parent waits for an end message from a worker that has already died, so the install hangs. Timed from
    # the death, not the start: starting a worker under Wine on a busy machine took 14 s alone.
    _fake(monkeypatch, _crash, "art")
    with pytest.raises(RuntimeError, match="art step's worker ended without finishing"):
        steps.run({"art": {"out": tmp_path}}, Progress(), None)
    assert time.time() - float((tmp_path / "died").read_text(encoding="utf-8")) < 10
