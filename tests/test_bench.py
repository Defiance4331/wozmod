"""tools/bench.py: benchmark arms run alternately, each run holding the machine alone."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux_only("tools/bench.py takes the fcntl machine lock and runs its arms through sh")


def test_arms_alternate_and_a_waiting_game_goes_in_between_two_runs(monkeypatch, tmp_path, capsys):
    """Bugs: a bench holds the machine lock across its whole chain of runs, so a game that queued during the first
    run waits for every run; or a bench's run holds the machine shared, so the game goes in beside it and is measured;
    or the arms run in blocks (A, A, B, B), so drift in the machine lands on one arm."""
    import bench
    import checkout
    import machine_lock

    (tmp_path / "work").mkdir()
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    monkeypatch.delenv(machine_lock.HELD, raising=False)
    order = tmp_path / "order"

    game = subprocess.Popen([sys.executable, "-c", (  # a game launch: its own process, in line for the machine
        "import os, sys, time; from pathlib import Path; "
        f"sys.path.insert(0, {str(Path(machine_lock.__file__).parent)!r}); import machine_lock; "
        f"work = Path({str(tmp_path / 'work')!r}); machine_lock.work = lambda: work; "
        f"started = Path({str(tmp_path / 'started')!r})\n"
        "while not started.exists(): time.sleep(0.01)\n"
        "held = machine_lock.hold('game', 'a game', 0.01)  # queues while the first run holds the machine\n"
        f"open({str(order)!r}, 'a').write('game\\n')")],
        env={key: value for key, value in os.environ.items() if key != machine_lock.HELD})
    first = f"touch {tmp_path / 'started'}; sleep 0.5; echo A >> {order}"
    assert bench.main(["--runs", "2", f"A={first}", f"B=echo B >> {order}"]) == 0
    assert game.wait(5) == 0
    assert order.read_text(encoding="utf-8").split() == ["A", "game", "B", "A", "B"]
    assert "A: median" in capsys.readouterr().out


def test_a_stop_between_two_runs_stops_the_bench(monkeypatch, tmp_path):
    """Bug: each run leaves its stop handler behind, so a bench stopped between runs (TaskStop, Ctrl-C) passes the
    stop to the run that already ended and goes on to the next one."""
    import checkout
    import machine_lock

    (tmp_path / "work").mkdir()
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    monkeypatch.delenv(machine_lock.HELD, raising=False)
    stops = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    before = {sig: signal.getsignal(sig) for sig in stops}
    assert machine_lock.run(["true"])[0] == 0
    assert {sig: signal.getsignal(sig) for sig in stops} == before
