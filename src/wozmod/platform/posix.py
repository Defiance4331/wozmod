"""wozmod on Linux (and other POSIX systems, Proton's host included): the adapter that wozmod.platform picks there."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Iterable
from pathlib import Path

NICE = 10  # how much lower lower_priority() puts this process (the voice step, below the art step)


def match_folder_access(paths: Iterable[Path]) -> int:
    """Permissions do not travel with a move here, so a placed file has nothing to repair: 0 changed."""
    return 0


def run_hidden(command: list[str], **options) -> subprocess.CompletedProcess:
    """subprocess.run: a program started here opens no window of its own."""
    return subprocess.run(command, **options)


class ProcessTree(subprocess.Popen):
    """A started program that leads its own process group, which every program it starts joins, so that kill_tree()
    ends them together, even those whose parent has already ended."""

    def __init__(self, command: list[str], **options):
        super().__init__(command, process_group=0, **options)

    def kill_tree(self) -> None:
        """Kill the program and everything it started."""
        try:  # the group outlives its leader while its other members run
            os.killpg(self.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass  # nothing of it is left
        self.kill()

    def close(self) -> None:
        """Let go of what kill_tree() needs: here nothing."""


def memory() -> tuple[int, int] | None:
    """(total, available) physical memory in bytes, or None when the system does not say."""
    try:
        fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines())
        return int(fields["MemTotal"].split()[0]) * 1024, int(fields["MemAvailable"].split()[0]) * 1024
    except (OSError, KeyError, ValueError):
        return None


def console_ansi(stream) -> bool:
    """Whether the terminal stream understands ANSI cursor codes: a POSIX terminal does without a switch."""
    return True


def settings_folder() -> Path:
    """The folder that holds the player's settings for programs: XDG_CONFIG_HOME, else ~/.config."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def split_command(command) -> list[str]:
    """The program and its arguments, from the command a subprocess.Popen audit event names: a list here."""
    return list(command)


def executable(name: str) -> str:
    """The file name of the program called name."""
    return name


def shared_library(name: str, version: str) -> str:
    """The file name of version version of the shared library called name."""
    return f"lib{name}.so.{version}"


def release_name() -> str:
    """What a release's files and folders call this system."""
    return "linux"


def lower_priority() -> None:
    """Make this process, and what it starts from now on, give way to the other work on the machine: NICE steps
    nicer, and on Linux the batch policy, whose lack of wake-up preemption also saves work."""
    os.nice(NICE)
    try:
        os.sched_setscheduler(0, os.SCHED_BATCH, os.sched_param(0))
    except (AttributeError, OSError):
        pass  # not Linux, or the system does not allow it: the voices only take a little longer
