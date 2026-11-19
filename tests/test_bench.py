"""tools/bench.py: benchmark arms run alternately, each run holding the machine lock alone."""

from __future__ import annotations

import os
import signal
import threading
import time

import pytest

pytestmark = pytest.mark.linux_only("tools/bench.py takes the fcntl machine lock and runs its arms through sh")


def test_arms_alternate_and_a_waiting_game_goes_in_between_two_runs(monkeypatch, tmp_path, capsys):
    """Bugs: a bench holds the machine lock across its whole chain of runs, so a game that queued during the first
    run waits for every run; or the arms run in blocks (A, A, B, B), so drift in the machine lands on one arm."""
    import bench
    import checkout
    import machine_lock

    (tmp_path / "work").mkdir()
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    monkeypatch.delenv(machine_lock.HELD, raising=False)
    order = tmp_path / "order"

    def game() -> None:
        while not (tmp_path / "started").exists():
            time.sleep(0.01)
        held = machine_lock.share(0.01)  # queues while the first run holds the machine
        with order.open("a", encoding="utf-8") as log:
            log.write("game\n")
        os.close(held)

    threading.Thread(target=game, daemon=True).start()
    first = f"touch {tmp_path / 'started'}; sleep 0.5; echo A >> {order}"
    assert bench.main(["--runs", "2", f"A={first}", f"B=echo B >> {order}"]) == 0
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
