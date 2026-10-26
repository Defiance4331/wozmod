"""Install progress: the overall percent and time left, events from worker processes, the terminal view, Cancel."""

from __future__ import annotations

import io
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pytest

import wozmod.install as installer
from wozmod import progress as reporting
from wozmod.progress import Cancelled, Progress, TerminalView


class Screen(io.StringIO):
    encoding = "utf-8"


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def _done(progress: Progress, *keys: str) -> None:
    for key in keys:
        progress.begin(key)
        progress.finish(key)


def test_percent_counts_text_voices_and_art_as_one_block_not_one_after_another():
    # Bug: the three stages' typical times are added up, so the bar passes 100 % too soon or ends under it.
    progress = Progress()
    _done(progress, "check", "discs")
    before = reporting.CHECK + reporting.DISCS
    assert progress.percent() == pytest.approx(before / reporting.TOTAL)
    for key in ("text", "voices", "art"):
        progress.apply((key, "working", 0.5, None))
    # the slowest stage stands for the whole block: half of each stage is half of the block, not the sum of halves
    assert progress.percent() == pytest.approx((before + reporting.BLOCK * 0.5) / reporting.TOTAL)
    _done(progress, "text", "voices", "art", "build", "place")
    assert progress.percent() == pytest.approx(1.0)


def test_time_left_follows_the_rate_so_far(monkeypatch):
    # Bug: the estimate stays at the typical time however slow or fast this computer is.
    clock = Clock()
    progress = Progress(clock=clock)
    _done(progress, "check", "discs")
    for key in ("text", "voices", "art"):
        progress.apply((key, "working", 0.5, None))
    clock.now = 100.0
    done = progress.percent()
    assert progress.seconds_left() == pytest.approx(100.0 * (1 - done) / done)


def _report_until_seen(stage: str, seen: Path) -> None:
    # The stage stays open until the parent shows its event: a finished stage drops later events by design.
    reporting.report(stage, "converting voice clips", 0.5, (2, 4))
    deadline = time.monotonic() + 10
    while not seen.exists() and time.monotonic() < deadline:
        time.sleep(0.01)


def _until_cancelled(stage: str) -> None:
    while True:
        reporting.report(stage, "working", 0.1)
        time.sleep(0.02)


def _pool(worker, stages, *args):
    context = get_context("spawn")
    events, stop = context.Queue(), context.Event()
    pool = ProcessPoolExecutor(len(stages), mp_context=context, initializer=installer._worker_start,
                               initargs=(events, stop, None))
    return pool, {pool.submit(worker, stage, *args): stage for stage in stages}, events, stop


def test_events_from_worker_processes_reach_the_progress(tmp_path):
    # Bug: the worker's events stay in the worker (no queue attached), so the voices line never moves.
    seen = tmp_path / "seen"

    def show(progress):
        if progress.stages["voices"].text == "converting voice clips, 2 of 4":
            seen.touch()

    progress = Progress(show)
    pool, futures, events, stop = _pool(_report_until_seen, ["voices", "art"], seen)
    with pool:
        installer._run_stages(pool, futures, events, stop, progress)
    assert seen.exists()
    assert progress.stages["art"].finished is not None


def test_cancel_stops_the_worker_processes_and_raises(monkeypatch):
    # Bug: Cancel sets a flag the workers never see, so the install keeps building until it ends.
    progress = Progress()
    pool, futures, events, stop = _pool(_until_cancelled, ["voices", "art"])
    processes = []
    with pool:
        started = time.monotonic()
        threading.Timer(0.5, progress.cancel.set).start()
        try:
            installer._run_stages(pool, futures, events, stop, progress)
        except Cancelled:
            pass
        else:
            pytest.fail("cancel did not raise")
        processes = list(pool._processes.values())
        installer._stop_workers(pool, futures, stop)
    assert time.monotonic() - started < 8  # the grace period before a kill is 10
    for process in processes:
        process.join(10)
    assert all(not process.is_alive() for process in processes)


def test_terminal_plain_output_has_one_line_per_step_and_one_per_ten_percent():
    # Bug: a log file or pipe gets a line per update (thousands), or loses a step.
    out = Screen()
    view = TerminalView(out, ansi=False)
    progress = Progress(view)
    _done(progress, "check", "discs")
    for step in range(200):
        for key in ("text", "voices", "art"):
            progress.apply((key, "working", step / 199, None))
    _done(progress, "text", "voices", "art", "build", "place")
    lines = out.getvalue().splitlines()
    assert [line for line in lines if line.startswith("✓")] == [
        "✓ Checked your files", "✓ Read the three discs", "✓ Built the text, voices and art",
        "✓ Placed the files"]
    first = int((reporting.CHECK + reporting.DISCS) * 100 / reporting.TOTAL) // 10 * 10
    assert [line.split("%")[0] for line in lines if "% done" in line] == [str(n) for n in range(first, 101, 10)]


def test_terminal_redraw_moves_up_exactly_the_lines_it_drew():
    # Bug: an off-by-one in the cursor move leaves a stale bar line behind, or overwrites a finished step.
    out = Screen()
    view = TerminalView(out, ansi=True)
    progress = Progress(view)
    _done(progress, "check", "discs")
    progress.begin("build")
    for key in ("text", "voices", "art"):
        progress.begin(key)
    before = out.getvalue()
    progress.apply(("voices", "converting voice clips", 0.4, (2104, 4050)))
    drawn = out.getvalue()[len(before):]
    assert drawn.startswith("\x1b[?25l\x1b[4A")  # text, voices, art and the overall line
    assert "converting voice clips, 2104 of 4050" in drawn
    assert drawn.count("\n") == 4


@pytest.fixture
def game(tmp_path, monkeypatch):
    folder = tmp_path / "game"
    (folder / "data").mkdir(parents=True)
    (folder / "OROCHI_Z.exe").write_bytes(b"stock exe")
    (folder / "data/LINKDATA1.BIN").write_bytes(b"stock archive")
    monkeypatch.setattr(installer, "check_inputs", lambda game, inputs, log=None: [])

    def build(game, inputs, staging, progress, jobs, checked, cache=None):
        out = staging / "out"
        (out / "wozmod").mkdir(parents=True)
        for key in ("discs", "text", "voices", "art"):
            progress.begin(key)
            (staging / f"{key}.partial").write_bytes(b"half built")
            reporting.report(key, "working", 0.5)  # raises Cancelled once the player cancelled
            progress.finish(key)
        (out / "d3d9.dll").write_bytes(b"dll")
        (out / "wozmod/table.bin").write_bytes(b"table")
        return out

    monkeypatch.setattr(installer, "_build", build)
    return folder


def _tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


@pytest.mark.parametrize("stage", ["check", "discs", "text", "voices", "art", "place"])
def test_cancel_at_any_stage_leaves_the_game_folder_as_it_was(game, stage):
    # Bug: Cancel leaves the .wozmod-build-* folder, a half-placed install or an install.json behind;
    # or it cuts into the placing of the files, which must finish.
    before = _tree(game)

    def cancel_when(progress):
        if progress.stages[stage].running:
            progress.cancel.set()

    progress = Progress(cancel_when)
    if stage == "place":
        installer.install(game, {}, log=lambda _: None, progress=progress)
        assert (game / "wozmod/install.json").is_file() and (game / "d3d9.dll").read_bytes() == b"dll"
    else:
        with pytest.raises(Cancelled):
            installer.install(game, {}, log=lambda _: None, progress=progress)
        assert _tree(game) == before


def _start_a_child_and_hang(stage: str) -> None:
    import subprocess
    child = subprocess.Popen(["sleep", "60"])
    reporting.report(stage, "working", 0.1, (child.pid, 0))  # tells the parent which process to look for
    time.sleep(60)  # never looks at the cancel flag again


@pytest.mark.skipif(os.name == "nt", reason="uses sleep and process groups")
@pytest.mark.slow_first
def test_a_worker_that_does_not_stop_is_killed_together_with_what_it_started(monkeypatch):
    # Bug: only the worker is killed, so its ffmpeg (or its own pool) keeps running after Cancel.
    monkeypatch.setattr(installer, "GRACE", 1)
    # Cancel once the worker has reported its child: a timer could fire before a slow worker starts one.
    progress = Progress(lambda progress: progress.stages["voices"].text and progress.cancel.set())
    pool, futures, events, stop = _pool(_start_a_child_and_hang, ["voices"])
    with pool:
        with pytest.raises(Cancelled):
            installer._run_stages(pool, futures, events, stop, progress)
        child = progress.stages["voices"].text.split(" ")[-3].strip(",")
        installer._stop_workers(pool, futures, stop)
    pid = int(child)
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def _chatter(stage: str) -> None:
    print("entry 4117: 1 textures, 2017488 -> 1850400 bytes", flush=True)
    os.write(1, b"written below Python\n")


def test_what_the_build_prints_goes_to_the_log_not_to_the_terminal(tmp_path, capfd):
    # Bug: the build's progress prints ("entry 4117: ...") land in the terminal view or the window.
    context = get_context("spawn")
    events, stop, log = context.Queue(), context.Event(), tmp_path / "install.log"
    with ProcessPoolExecutor(1, mp_context=context, initializer=installer._worker_start,
                             initargs=(events, stop, log)) as pool:
        pool.submit(_chatter, "art").result()
    assert capfd.readouterr().out == ""
    assert log.read_text().splitlines() == ["entry 4117: 1 textures, 2017488 -> 1850400 bytes", "written below Python"]
