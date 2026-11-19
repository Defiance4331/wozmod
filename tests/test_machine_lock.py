"""tools/machine_lock.py: one heavy run at a time across the main checkout and its worktrees."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux_only("tools/machine_lock.py locks with fcntl and runs its commands through sh")
TOOLS = Path(__file__).resolve().parents[1] / "tools"


def test_a_heavy_run_waits_for_another_worktrees_and_keeps_its_exit_status(linked_worktree, tmp_path):
    """Bugs: a heavy run started from a linked worktree locks a file of its own and runs beside the main checkout's;
    the wrapper reports a failed run as passed; a process the run leaves behind keeps the machine locked; or a heavy
    run inside one waits for itself."""
    main, worktree = linked_worktree
    (main / "work").mkdir()
    for checkout in (main, worktree):
        (checkout / "tools").mkdir()
        for name in ("machine_lock.py", "checkout.py"):
            shutil.copy(TOOLS / name, checkout / "tools" / name)
    env = {key: value for key, value in os.environ.items() if key != "WOZMOD_MACHINE_LOCK"}
    lock = lambda checkout, script: subprocess.Popen(  # noqa: E731
        [sys.executable, str(checkout / "tools/machine_lock.py"), "sh", "-c", script], cwd=tmp_path, env=env)
    first = lock(main, f"sleep 30 >/dev/null 2>&1 & echo $! > orphan; "
                       f"{sys.executable} {main / 'tools/machine_lock.py'} touch nested; "
                       "touch started; sleep 0.5; touch first-done; exit 3")
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "started").exists():
            assert first.poll() is None and time.monotonic() < deadline, "the first run never started its work"
            time.sleep(0.02)
        second = lock(worktree, "test -e first-done")
        assert first.wait(10) == 3
        assert second.wait(10) == 0  # within 10 s, though the orphan sleeps 30 s holding the run's descriptors
        assert (tmp_path / "nested").exists()
    finally:
        first.kill()
        if (tmp_path / "orphan").exists():
            os.kill(int((tmp_path / "orphan").read_text(encoding="utf-8")), signal.SIGKILL)


def _start(target) -> threading.Event:
    done = threading.Event()
    threading.Thread(target=lambda: target() or done.set(), daemon=True).start()
    return done


def test_a_game_and_a_heavy_run_wait_for_each_other(monkeypatch, tmp_path):
    """Bugs: a game launches while a benchmark holds the machine, so the game takes CPU the benchmark measures; or a
    heavy run starts while a game is up; or two games no longer run at once."""
    import checkout
    import machine_lock

    (tmp_path / "work").mkdir()
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    monkeypatch.delenv(machine_lock.HELD, raising=False)
    heavy, _ = machine_lock.take((machine_lock.HEAVY,), "bench", 0.01)
    games = []
    game = _start(lambda: games.append(machine_lock.share(0.01)))
    assert not game.wait(0.3)  # the game waits for the benchmark
    os.close(heavy)
    assert game.wait(2)
    assert _start(lambda: games.append(machine_lock.share(0.01))).wait(2)  # a second game beside the first
    after = []
    run = _start(lambda: after.append(machine_lock.claim("install", 0.01)))
    assert not run.wait(0.3)  # the heavy run waits for both games
    os.close(games[0])
    assert not run.wait(0.3)
    os.close(games[1])
    assert run.wait(2)
    os.close(after[0])


def test_games_started_one_after_another_do_not_keep_a_waiting_heavy_run_out(monkeypatch, tmp_path):
    """Bug: a game check starts its next game while the last is still up, so the machine is never free of games
    and the benchmark waiting for it never starts."""
    import checkout
    import machine_lock

    (tmp_path / "work").mkdir()
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    monkeypatch.delenv(machine_lock.HELD, raising=False)
    first = machine_lock.share(0.01)
    claimed = []
    run = _start(lambda: claimed.append(machine_lock.claim("bench", 0.01)))
    time.sleep(0.2)  # the heavy run is waiting for the first game
    later = []
    game = _start(lambda: later.append(machine_lock.share(0.01)))
    assert not game.wait(0.3)  # the next game queues behind the waiting heavy run
    os.close(first)
    assert run.wait(2) and not game.is_set()
    os.close(claimed[0])
    assert game.wait(2)
    os.close(later[0])


def test_a_run_that_began_waiting_first_goes_in_first(monkeypatch, tmp_path):
    """Bug: a benchmark that asks for the machine again right after each of its runs goes ahead of a test run
    waiting since the run before, so the test run (or a commit's checks) waits for the whole sequence."""
    import checkout
    import machine_lock

    (tmp_path / "work").mkdir()
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    monkeypatch.delenv(machine_lock.HELD, raising=False)
    run_1 = machine_lock.claim("bench r1", 0.01)
    tests, benches = [], []
    test = _start(lambda: tests.append(machine_lock.share(0.01, "a test run")))
    time.sleep(0.2)  # the test run is in line
    bench = _start(lambda: benches.append(machine_lock.claim("bench r2", 0.01)))
    time.sleep(0.2)  # the benchmark's next run is in line behind it
    os.close(run_1)
    assert test.wait(2) and not bench.wait(0.3)
    os.close(tests[0])
    assert bench.wait(2)
    os.close(benches[0])


def test_a_run_that_died_waiting_does_not_hold_up_the_line(monkeypatch, tmp_path):
    """Bug: a run killed while it waited leaves its ticket, and every later run waits behind it forever."""
    import checkout
    import machine_lock

    (tmp_path / "work").mkdir()
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    monkeypatch.delenv(machine_lock.HELD, raising=False)
    line = tmp_path / "work" / machine_lock.LINE
    line.mkdir()
    (line / f"{0:020d}-1-heavy").write_text("a killed benchmark", encoding="utf-8")  # no process holds it
    got = []
    assert _start(lambda: got.append(machine_lock.claim("install", 0.01))).wait(2)
    os.close(got[0])
    assert list(line.iterdir()) == []
