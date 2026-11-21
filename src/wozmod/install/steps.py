"""The installer's build steps (text, voices, art) and how they run.

STEPS is the one table of them: each step's function in wozmod.install. A function's parameters say which inputs it
takes, of Inputs and its two folders (Inputs.arguments, the one place a step's arguments are made); its manifest
(install/manifest.py) says everything else it may depend on.

run() builds steps side by side, each in a worker process of its own whose environment is fixed (environment()), so
what a step writes cannot depend on the player's time zone, locale, hash seed, working folder or stray settings, and
whose reads the read gate (install/gate.py) holds to the step's manifest. run_here() runs one step in this process:
tests (tools/stock_pieces.py runs the steps through run() in record mode).
"""

from __future__ import annotations

import importlib
import inspect
import os
import pickle
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, fields
from pathlib import Path

from wozmod import platform
from wozmod import progress as reporting
from wozmod.install import gate
from wozmod.progress import Cancelled, Progress


@dataclass(frozen=True)
class Step:
    function: str  # its function in wozmod.install
    label: str  # what the progress shows for it before its first event


STEPS = {
    "text": Step("_text_table", "building the text table"),
    "voices": Step("_voice_banks", "reading the voice banks"),
    "art": Step("_menu_art", "preparing the menu art"),
}



@dataclass(frozen=True)
class Inputs:
    """What the steps build from, the same for each step; each takes those its function names."""
    game: Path  # the stock game folder
    donors: Path  # the extracted discs, laid out as the installer lays them out (install.DonorsFolder)
    speech: Path  # PocketSphinx's US English model folder (voices.phones.MODEL)
    separator: Path  # the Dream voice separator's model folder (voices.separation.MODEL)
    lettering: Path  # the release lettering bundle, lettering.zip
    jobs: int  # workers a step may use (install.worker_count)

    @classmethod
    def environment(cls, jobs: int) -> Inputs:
        """A checkout's inputs: each path from WOZMOD_<NAME> (tests and tools/verify.py, from [tests] in
        work/harness.toml)."""
        return cls(**{field.name: Path(os.environ[f"WOZMOD_{field.name.upper()}"])
                      for field in fields(cls) if field.name != "jobs"}, jobs=jobs)

    def arguments(self, staging: Path, out: Path) -> dict:
        """A step's arguments: these inputs, its staging folder and out, the folder it writes into (laid out as the
        game folder)."""
        return {**{field.name: getattr(self, field.name) for field in fields(self)}, "staging": staging, "out": out}


GRACE = 10  # seconds the workers get to stop by themselves before they are killed
FIXED = {"TZ": "UTC", "LC_ALL": "C.UTF-8", "PYTHONHASHSEED": "0", "PYTHONUTF8": "1"}
# Passed on as they are: PATH finds ffmpeg where the release bundles none (resources.ffmpeg); Windows cannot start
# Python without SYSTEMROOT, and its programs look for the others.
PASSED = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT")
WORKER = "--step-worker"  # the release program's argument that makes it a worker (wozmod.__main__.program)
# How a source checkout starts a worker: with this Python's import path, so it runs the code this process runs.
_BOOT = "import sys; sys.path[:] = sys.argv[1:]; from wozmod.install.steps import worker; worker()"


def environment(settings: tuple[str, ...], folder: Path) -> dict[str, str]:
    """The whole environment of a step's worker: FIXED, PASSED and the step's settings (its manifest's environment)
    where this process has them, and folder as its temporary folder."""
    passed = {name: os.environ[name] for name in (*PASSED, *settings) if name in os.environ}
    return {**passed, **FIXED, "TEMP": str(folder), "TMP": str(folder), "TMPDIR": str(folder)}


def _manifests(mode: str, log: Path | None) -> dict:
    """Per step, its manifest. A release that cannot make them (it would rebuild every step for want of keys too)
    runs its steps unchecked, as before the gate, and says so in the log; our own builds stop."""
    from wozmod.install import manifest

    try:
        return manifest.manifests()
    except manifest.StepKeyError as error:
        if mode != gate.OBSERVE:
            raise
        line = f"{gate.LINE}no step manifests ({error}); the steps run unchecked\n"
        if log:
            with open(log, "a", encoding="utf-8") as stream:
                stream.write(line)
        else:
            sys.stderr.write(line)
        return {}


def _function(step: str):
    return getattr(importlib.import_module("wozmod.install"), STEPS[step].function)


def _taken(function, arguments: dict) -> dict:
    """The arguments function takes, by its parameter names."""
    return {name: arguments[name] for name in inspect.signature(function).parameters}


def run_here(step: str, mode: str | None = None, trace: Path | None = None, **arguments) -> None:
    """Run a step in this process with the arguments it takes (Inputs.arguments);
    with a mode, inside the read gate (trace: where it writes each item read). This process's environment is not fixed."""
    function = _function(step)
    taken = _taken(function, arguments)
    if mode is None:
        function(**taken)
        return
    from wozmod.install import manifest

    with gate.watch(step, manifest.manifest(step), mode, taken, trace):
        function(**taken)


class _RemoteTraceback(Exception):
    def __init__(self, text: str):
        self.text = text

    def __str__(self) -> str:
        return f'\n"""\n{self.text}"""'


class _Worker:
    """One step building in its own process. Its events and its end go to inbox as (worker, kind, value)."""

    def __init__(self, step: str, arguments: dict, inbox: queue.Queue, log: Path | None, manifest, mode: str,
                 trace: Path | None):
        self.step, self.outcome = step, None
        function = _function(step)
        # It works in its own folder: a path given relative to this process's working folder must not move.
        taken = {name: value.absolute() if isinstance(value, Path) else value
                 for name, value in _taken(function, arguments).items()}
        trace = trace and trace.absolute()
        # Before the start: a step that cannot be sent starts nothing.
        message = pickle.dumps((step, function, taken, log, manifest, mode, trace))
        command = [sys.executable, WORKER] if getattr(sys, "frozen", False) else [
            sys.executable, "-c", _BOOT, *(os.path.abspath(p) for p in sys.path)]
        from wozmod.install.manifest import ENVIRONMENT

        settings = manifest.environment if manifest else ENVIRONMENT[step]
        self.folder = Path(tempfile.mkdtemp(prefix=f"wozmod-{step}-"))  # its working and temporary folder
        try:
            self.process = platform.ProcessTree(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                cwd=self.folder, env=environment(settings, self.folder))
        except BaseException:
            shutil.rmtree(self.folder, ignore_errors=True)
            raise
        try:
            self.process.stdin.write(message)
            self.process.stdin.flush()
        except OSError:
            pass  # it ended before it read them: its end says how
        self.reader = threading.Thread(target=self._read, args=(inbox,), daemon=True)
        self.reader.start()

    def _read(self, inbox: queue.Queue) -> None:
        try:
            while True:
                kind, value = pickle.load(self.process.stdout)
                if kind == "event":
                    inbox.put((self, kind, value))
                else:
                    self.outcome = (kind, value)
        except Exception:  # the end of what it sends (EOFError), or a message cut short by a crash
            pass
        self.process.wait()
        inbox.put((self, "ended", None))

    def result(self) -> None:
        """Return once the step finished; raise what it raised."""
        kind, value = self.outcome or ("crashed", None)
        if kind == "failed":
            error, text = value
            raise error from _RemoteTraceback(text)
        if kind != "done":
            raise RuntimeError(f"the {self.step} step's worker ended without finishing "
                               f"(exit code {self.process.returncode})")

    def stop(self) -> None:
        """Ask it to stop: the step raises Cancelled at its next progress event."""
        try:
            self.process.stdin.close()
        except OSError:
            pass

    def close(self) -> None:
        self.stop()
        self.reader.join(5)
        self.process.stdout.close()
        self.process.close()
        shutil.rmtree(self.folder, ignore_errors=True)


def _stop(workers: list[_Worker]) -> None:
    """Ask the workers to stop, give them GRACE seconds, then kill what is left with all it started."""
    for each in workers:
        each.stop()
    deadline = time.monotonic() + GRACE
    for each in workers:
        try:
            each.process.wait(max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for each in workers:
        each.process.kill_tree()
    for each in workers:
        try:
            each.process.wait(5)
        except subprocess.TimeoutExpired:
            pass


def run(work: dict[str, dict], progress: Progress, log: Path | None, mode: str | None = None,
        trace: Path | None = None) -> None:
    """Build each step in work (step -> the arguments, of which it takes those it names) side by side, each in its own
    worker, and return once all have finished; progress gets their events, and each step's finish.

    Each worker holds its step to its manifest in mode (gate.default_mode() when None; trace: where it writes
    each item read). A step's error stops the others and is raised here. On progress.cancel the workers are asked to stop, and
    Cancelled is raised within GRACE seconds; a worker still running GRACE seconds later is killed with all it
    started. What the steps print goes to log (appended), or where this process prints when there is none.
    """
    mode = mode or gate.default_mode()
    manifests = _manifests(mode, log)
    inbox: queue.Queue = queue.Queue()
    workers: list[_Worker] = []
    try:
        for step, arguments in work.items():
            workers.append(_Worker(step, arguments, inbox, log, manifests.get(step), mode, trace))
        pending, cancelled_at = set(workers), None
        while pending:
            try:
                worker, kind, value = inbox.get(timeout=0.2)
            except queue.Empty:
                pass
            else:
                if kind == "event":
                    progress.apply(value)
                else:
                    pending.discard(worker)
                    worker.result()  # a step that failed or was cancelled raises here
                    progress.finish(worker.step)
            if progress.cancel.is_set():
                for each in workers:
                    each.stop()
                cancelled_at = cancelled_at or time.monotonic()
                if time.monotonic() - cancelled_at > GRACE:
                    raise Cancelled()
    except BaseException:
        _stop(workers)
        raise
    finally:
        for each in workers:
            each.close()


def worker() -> None:
    """A worker's main: build the step the parent sends on stdin; send its progress events and its end on stdout.

    The parent closing stdin asks it to stop. What the step and the programs it starts print goes to the log. The
    read gate holds the step to its manifest (none: a release that has none runs it unchecked).
    """
    commands, replies = os.dup(0), os.dup(1)  # kept from what the step starts (both are non-inheritable)
    with open(commands, "rb", closefd=False) as received:
        step, function, arguments, log_path, manifest, mode, trace = pickle.load(received)
    nothing = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nothing, 0)
    os.close(nothing)
    if log_path:
        log = open(log_path, "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = log
        for descriptor in (1, 2):  # also what a child process or native code writes
            os.dup2(log.fileno(), descriptor)
    else:
        os.dup2(2, 1)
    lock = threading.Lock()

    def send(message) -> None:
        data = memoryview(pickle.dumps(message))
        with lock:
            while data:
                data = data[os.write(replies, data):]

    def watch() -> None:
        while os.read(commands, 4096):
            pass
        stop.set()

    stop = threading.Event()
    threading.Thread(target=watch, daemon=True).start()
    reporting.attach(lambda event: send(("event", event)), stop)
    try:
        if manifest is None:
            function(**arguments)
        else:
            with gate.watch(step, manifest, mode, arguments, trace):
                function(**arguments)
    except BaseException as error:
        text = traceback.format_exc()
        try:
            pickle.loads(pickle.dumps(error))
        except Exception:
            error = RuntimeError(f"{type(error).__name__}: {error}")
        send(("failed", (error, text)))
        raise SystemExit(1) from None
    send(("done", None))
