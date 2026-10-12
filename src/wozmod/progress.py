"""Install progress: stage events from the build code, the overall percent, the time left, and the terminal view.

The build code calls `report`. In the parent process that reaches the `Progress` directly; in a worker
process the install attaches a queue that the parent drains. Both front ends read the same `Progress`.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from dataclasses import dataclass

# Typical seconds of a full install (measured with the packaged program). Text, voices and art run at the
# same time: that block lasts as long as its slowest stage, and each stage counts by its own share of it.
CHECK, DISCS, PLACE = 1, 12, 2  # the input checks now run during the disc reads
PARALLEL = {"text": 12, "voices": 100, "art": 55}
BLOCK = max(PARALLEL.values())
TOTAL = CHECK + DISCS + BLOCK + PLACE
NAMES = {"text": "Text", "voices": "Voices", "art": "Art"}
STEPS = {"check": ("Checking your files", "Checked your files"),
         "discs": ("Reading the three discs", "Read the three discs"),
         "build": ("Building the English files", "Built the text, voices and art"),
         "place": ("Placing the files", "Placed the files")}


DONE = "Done"


class Cancelled(Exception):
    """The player cancelled the install."""


# What `report` talks to in this process: a function that takes one event, and a cancel flag.
_sink = None
_cancel = None
_last: dict[str, float] = {}


def attach(sink, cancel) -> None:
    global _sink, _cancel
    _sink, _cancel = sink, cancel
    _last.clear()


def report(stage: str, label: str, fraction: float, counts: tuple[int, int] | None = None) -> None:
    """Say what a stage is doing: fraction of the stage done (0 to 1), and optionally (done, total) to show.

    Raises Cancelled when the player cancelled, so a long loop that reports stops by itself.
    """
    if _cancel is not None and _cancel.is_set():
        raise Cancelled()
    if _sink is None:
        return
    now = time.monotonic()
    if fraction < 1 and now - _last.get(stage, -1.0) < 0.1:  # at most ten events a second per stage
        return
    _last[stage] = now
    _sink((stage, label, min(max(fraction, 0.0), 1.0), counts))


@dataclass
class Stage:
    fraction: float = 0.0
    text: str = ""
    running: bool = False
    finished: float | None = None


class Progress:
    """The state of one install. `listener(progress)` is called after every change."""

    def __init__(self, listener=None, cancel: threading.Event | None = None, clock=time.monotonic):
        self.listener = listener
        self.cancel = cancel or threading.Event()
        self.clock = clock
        self.started = clock()
        self.stages = {key: Stage() for key in (*STEPS, *PARALLEL)}
        self.done: list[str] = []  # the finished steps, in the order they finished
        self.finished_at: float | None = None
        self.lock = threading.Lock()

    def checkpoint(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    def _changed(self) -> None:
        if self.listener:
            self.listener(self)

    def begin(self, key: str, text: str | None = None) -> None:
        with self.lock:
            stage = self.stages[key]
            stage.running, stage.text = True, text or (STEPS[key][0] if key in STEPS else "")
        self._changed()

    def finish(self, key: str) -> None:
        with self.lock:
            stage = self.stages[key]
            stage.running, stage.fraction, stage.finished = False, 1.0, self.clock()
            if key in STEPS:
                self.done.append(STEPS[key][1])
        self._changed()

    def end(self) -> None:
        self.finished_at = self.clock()
        self._changed()

    def apply(self, event) -> None:
        """Take one `report` event (from this process, or from a worker through the queue)."""
        key, label, fraction, counts = event
        with self.lock:
            stage = self.stages[key]
            if stage.finished is None:
                stage.running, stage.fraction = True, fraction
                stage.text = f"{label}, {counts[0]} of {counts[1]}" if counts else label
        self._changed()

    # --- what the front ends show

    def percent(self) -> float:
        s = self.stages
        shares = sum(PARALLEL.values())
        block = sum(PARALLEL[k] * s[k].fraction for k in PARALLEL) / shares
        return (CHECK * s["check"].fraction + DISCS * s["discs"].fraction
                + BLOCK * block + PLACE * s["place"].fraction) / TOTAL

    def elapsed(self) -> float:
        return (self.finished_at or self.clock()) - self.started

    def seconds_left(self) -> float | None:
        """From the progress so far; the typical time until there is enough progress to go by."""
        done = self.percent()
        if done >= 1:
            return 0.0
        if done < 0.05:
            return (1 - done) * TOTAL
        return self.elapsed() * (1 - done) / done

    def rows(self) -> list[tuple[str, float, str]]:
        """Like `lines`, but the three stages stay listed until the end (for the window's details)."""
        with self.lock:
            if self.stages["build"].running or self.stages["build"].finished:
                return [(NAMES[k], self.stages[k].fraction, DONE if self.stages[k].finished else self.stages[k].text)
                        for k in PARALLEL]
        return self.lines()

    def lines(self) -> list[tuple[str, float, str]]:
        """(name, fraction, what it is doing) for each running stage, as the progress view lists them."""
        with self.lock:
            building = self.stages["build"].finished is None
            running = [(k, s) for k, s in self.stages.items() if building and k in PARALLEL and (s.running or s.finished)]
            single = [(STEPS[k][0], s) for k, s in self.stages.items() if k in STEPS and s.running]
            if running:
                return [(NAMES[k], s.fraction, DONE if s.finished else s.text) for k, s in running]
            return [(name, s.fraction, s.text) for name, s in single]


def minutes_left(seconds: float | None) -> str:
    """The window's wording: "About 2 minutes left"."""
    if not seconds:
        return ""
    if seconds < 45:
        return "Less than a minute left"
    minutes = round(seconds / 60)
    return f"About {minutes} minute{'s' if minutes != 1 else ''} left"


def duration(seconds: float) -> str:
    """"2 min 6 s", or "42 s"."""
    seconds = round(seconds)
    return f"{seconds // 60} min {seconds % 60} s" if seconds >= 60 else f"{seconds} s"


def _left(seconds: float | None) -> str:
    return "" if not seconds else f"about {duration(round(seconds / 5) * 5 if seconds >= 20 else seconds)} left"


# --- the terminal view

BAR = 20


def _bar(fraction: float) -> str:
    filled = round(fraction * BAR)
    return "█" * filled + "░" * (BAR - filled)


def enable_ansi(stream) -> bool:
    """True when the stream is a terminal that understands ANSI cursor codes (Windows 10 consoles need a switch)."""
    if not stream.isatty() or os.environ.get("TERM") == "dumb":
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel = ctypes.windll.kernel32
            handle = kernel.GetStdHandle(-11 if stream is sys.stdout else -12)
            mode = ctypes.c_uint32()
            return bool(kernel.GetConsoleMode(handle, ctypes.byref(mode))
                        and kernel.SetConsoleMode(handle, mode.value | 0x0004))
        except (AttributeError, OSError):
            return False
    return True


class TerminalView:
    """Draws a Progress as a terminal view that redraws in place, or as plain lines when not a terminal."""

    def __init__(self, stream=None, ansi: bool | None = None):
        self.out = stream or sys.stdout
        self.ansi = enable_ansi(self.out) if ansi is None else ansi
        self.printed = 0  # finished steps already written
        self.live = 0  # lines of the redrawing block
        self.milestone = 0  # plain mode: last 10 % written
        try:  # a console code page that lacks the tick and the bar gets plain letters
            "\u2713\u2588\u2591".encode(getattr(self.out, "encoding", None) or "ascii")
            self.tick, self.full, self.empty = "\u2713", "\u2588", "\u2591"
        except (UnicodeEncodeError, LookupError):
            self.tick, self.full, self.empty = "OK", "#", "-"

    def _write(self, text: str) -> None:
        self.out.write(text)
        self.out.flush()

    def __call__(self, progress: Progress) -> None:
        (self._redraw if self.ansi else self._plain)(progress)

    def _bar(self, fraction: float) -> str:
        filled = round(fraction * BAR)
        return self.full * filled + self.empty * (BAR - filled)

    def _redraw(self, progress: Progress) -> None:
        frame = [f"  {name:<8}[{self._bar(fraction)}] {fraction:>4.0%}  {text}"
                 for name, fraction, text in progress.lines()]
        frame.append(f"  {'Overall':<8}[{self._bar(progress.percent())}] {progress.percent():>4.0%}  "
                     f"{_left(progress.seconds_left())}")
        text = "\x1b[?25l" + (f"\x1b[{self.live}A" if self.live else "")
        for line in progress.done[self.printed:]:
            text += f"\r\x1b[2K✓ {line}\n"
        self.printed = len(progress.done)
        text += "".join(f"\r\x1b[2K{line}\n" for line in frame) + "\x1b[J\x1b[?25h"
        self.live = len(frame)
        self._write(text)

    def _plain(self, progress: Progress) -> None:
        lines = [f"{self.tick} {line}" for line in progress.done[self.printed:]]
        self.printed = len(progress.done)
        tenth = int(progress.percent() * 10) * 10
        if tenth > self.milestone:
            self.milestone = tenth
            left = _left(progress.seconds_left())
            lines.append(f"{tenth}% done" + (f", {left}" if left else ""))
        if lines:
            self._write("".join(line + "\n" for line in lines))

    def finish(self, progress: Progress, message: str) -> None:
        """Replace the live block with the closing line."""
        if self.ansi and self.live:
            self._write(f"\x1b[{self.live}A\r\x1b[J")
            self.live = 0
        self._write(f"✓ {message}\n")

    def stop(self) -> None:
        """Leave the terminal tidy after a failure or a cancel: the live block stays, on its own lines."""
        self.live = 0
