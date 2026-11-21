"""tools/machine_lock.py: across the main checkout and its worktrees, only a measuring run has the machine alone."""

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


def test_a_measuring_run_waits_for_another_worktrees_build_and_the_build_keeps_its_exit_status(linked_worktree, tmp_path):
    """Bugs: a run started from a linked worktree locks a file of its own and measures beside the main checkout's
    build; the wrapper reports a failed run as passed; a process the run leaves behind keeps the machine locked; or a
    run inside one waits for itself."""
    main, worktree = linked_worktree
    (main / "work").mkdir()
    for checkout in (main, worktree):
        (checkout / "tools").mkdir()
        for name in ("machine_lock.py", "checkout.py"):
            shutil.copy(TOOLS / name, checkout / "tools" / name)
    env = {key: value for key, value in os.environ.items() if key != "WOZMOD_MACHINE_LOCK"}
    first = subprocess.Popen([sys.executable, str(main / "tools/machine_lock.py"), "sh", "-c",
                              f"sleep 30 >/dev/null 2>&1 & echo $! > orphan; "
                              f"{sys.executable} {main / 'tools/machine_lock.py'} touch nested; "
                              "touch started; sleep 0.5; touch first-done; exit 3"], cwd=tmp_path, env=env)
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "started").exists():
            assert first.poll() is None and time.monotonic() < deadline, "the first run never started its work"
            time.sleep(0.02)
        second = subprocess.Popen([sys.executable, "-c", (  # a benchmark's run, from the worktree's copy
            f"import sys; sys.path.insert(0, {str(worktree / 'tools')!r}); import machine_lock; "
            "sys.exit(machine_lock.run(['sh', '-c', 'test -e first-done'], 'measure')[0])")], cwd=tmp_path, env=env)
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


def _machine(monkeypatch, tmp_path):
    """machine_lock, its work/ in tmp_path, outside any held run."""
    import checkout
    import machine_lock

    (tmp_path / "work").mkdir()
    monkeypatch.setattr(checkout, "REPO", tmp_path)
    monkeypatch.delenv(machine_lock.HELD, raising=False)
    return machine_lock


def test_builds_games_and_test_runs_share_the_machine_and_a_measuring_run_waits_for_them(monkeypatch, tmp_path):
    """Bugs: a build still has the machine alone, so a second build, a game or a test run waits behind an install
    (one 71 s test run waited 69 min); or a benchmark's run starts beside a running build or game and measures it."""
    lock = _machine(monkeypatch, tmp_path)
    held = [lock.hold("build", "an install", 0.01)]
    beside = {kind: _start(lambda kind=kind: held.append(lock.hold(kind, f"a {kind}", 0.01)))
              for kind in ("build", "game", "test")}
    assert {kind for kind, done in beside.items() if not done.wait(2)} == set()
    measured = []
    bench = _start(lambda: measured.append(lock.hold("measure", "a benchmark", 0.01)))
    for fd in held:
        assert not bench.wait(0.2)  # it waits while any of them is on the machine
        os.close(fd)
    assert bench.wait(2)
    games = []
    game = _start(lambda: games.append(lock.hold("game", "a game", 0.01)))
    assert not game.wait(0.3)  # and nothing goes in beside it
    os.close(measured[0])
    assert game.wait(2)
    os.close(games[0])


@pytest.mark.parametrize("later", ["game", "build"])
def test_runs_started_one_after_another_do_not_keep_a_waiting_measuring_run_out(monkeypatch, tmp_path, later):
    """Bug: a game check starts its next game, or a build its next build, while the last is still up, so the machine
    is never free and the benchmark waiting for it never starts."""
    lock = _machine(monkeypatch, tmp_path)
    first = lock.hold(later, "the first", 0.01)
    claimed = []
    run = _start(lambda: claimed.append(lock.hold("measure", "bench", 0.01)))
    time.sleep(0.2)  # the measuring run is waiting for the first
    after = []
    second = _start(lambda: after.append(lock.hold(later, "the next", 0.01)))
    assert not second.wait(0.3)  # the next queues behind the waiting measuring run
    os.close(first)
    assert run.wait(2) and not second.is_set()
    os.close(claimed[0])
    assert second.wait(2)
    os.close(after[0])


def test_a_run_that_began_waiting_first_goes_in_first(monkeypatch, tmp_path):
    """Bug: a benchmark that asks for the machine again right after each of its runs goes ahead of a game waiting
    since the run before, so the game waits for the whole sequence."""
    lock = _machine(monkeypatch, tmp_path)
    run_1 = lock.hold("measure", "bench r1", 0.01)
    games, benches = [], []
    game = _start(lambda: games.append(lock.hold("game", "a game", 0.01)))
    time.sleep(0.2)  # the game is in line
    bench = _start(lambda: benches.append(lock.hold("measure", "bench r2", 0.01)))
    time.sleep(0.2)  # the benchmark's next run is in line behind it
    os.close(run_1)
    assert game.wait(2) and not bench.wait(0.3)
    os.close(games[0])
    assert bench.wait(2)
    os.close(benches[0])


def test_a_run_that_died_waiting_does_not_hold_up_the_line(monkeypatch, tmp_path):
    """Bug: a run killed while it waited leaves its ticket, and every later run waits behind it forever."""
    lock = _machine(monkeypatch, tmp_path)
    line = tmp_path / "work" / lock.LINE
    line.mkdir()
    (line / f"{0:020d}-1-measure").write_text("a killed benchmark", encoding="utf-8")  # no process holds it
    got = []
    assert _start(lambda: got.append(lock.hold("build", "an install", 0.01))).wait(2)
    os.close(got[0])
    assert list(line.iterdir()) == []


def test_a_waiting_heavy_run_of_a_checkout_on_the_old_lock_is_not_passed_over(monkeypatch, tmp_path):
    """Bug: a checkout that still has the lock from before measuring runs (every heavy run alone) waits in line for
    its install, and the builds and games of updated checkouts go in beside one another without end, so it never
    finds the machine free."""
    import fcntl

    lock = _machine(monkeypatch, tmp_path)
    line = tmp_path / "work" / lock.LINE
    line.mkdir()
    ticket = line / f"{0:020d}-1-heavy"
    ticket.write_text("an install on the old lock", encoding="utf-8")
    with ticket.open("rb") as waiting:  # its run is alive and waiting: it holds its ticket
        fcntl.flock(waiting, fcntl.LOCK_EX)
        got = []
        build = _start(lambda: got.append(lock.hold("build", "a build", 0.01)))
        assert not build.wait(0.3)
        ticket.unlink()  # its run went in, and has since ended
    assert build.wait(2)
    os.close(got[0])


def test_a_measuring_run_inside_a_shared_one_is_refused_and_runs_inside_a_measuring_one_go_on(monkeypatch, tmp_path):
    """Bugs: a benchmark started inside a build (machine_lock.py tools/bench.py, a release inside a replay) waits for
    the machine its own build holds, for ever, or goes on beside the other runs and measures them; or a run inside a
    measuring run waits for the run it is part of."""
    lock = _machine(monkeypatch, tmp_path)
    monkeypatch.setenv(lock.HELD, "build")
    with pytest.raises(RuntimeError, match="needs the machine alone"):
        lock.hold("measure", "a benchmark", 0.01)
    monkeypatch.setenv(lock.HELD, "measure")
    assert [lock.hold(kind, "a run inside it", 0.01) for kind in ("measure", "build", "test")] == [None] * 3


def test_an_option_before_the_command_is_answered_at_once_and_never_waits_in_line(monkeypatch, tmp_path, capsys):
    """Bug: `machine_lock.py --help`, or an option the tool does not take, is read as the command to run, so it waits
    in the machine's line behind every measuring run and only then fails to start."""
    lock = _machine(monkeypatch, tmp_path)
    held = lock.hold("measure", "a benchmark", 0.01)  # whatever waits for the machine now waits until the test ends
    rows = [(["--help"], 0, "out", "tools/machine_lock.py COMMAND"), (["-h"], 0, "out", "tools/machine_lock.py COMMAND"),
            (["--runs", "5", "true"], 2, "err", "--runs")]
    wrong = []
    try:
        for argv, status, stream, said in rows:
            got = []
            answered = _start(lambda argv=argv: got.append(lock.main(argv))).wait(2)
            if not answered or got != [status] or said not in getattr(capsys.readouterr(), stream):
                wrong.append((argv, answered, got))
    finally:
        os.close(held)
    assert wrong == []


def test_a_test_run_goes_ahead_of_a_waiting_measuring_run_until_that_runs_turn_comes(monkeypatch, tmp_path):
    """Bugs: a short test run waits for every measuring run that began waiting before it (a 2-minute run behind a
    benchmark's back-to-back runs); or test runs always go first, so a stream of them, each arriving while the one
    before still runs, keeps a measuring run out for good."""
    lock = _machine(monkeypatch, tmp_path)
    order, first_done = [], threading.Event()

    def run(name, take, until=None):
        held = take()
        order.append(name)
        if until:
            until.wait(5)
        os.close(held)

    bench_1 = lock.hold("measure", "bench run 1", 0.01)
    bench_2 = _start(lambda: run("bench 2", lambda: lock.hold("measure", "bench run 2", 0.01)))
    time.sleep(0.2)  # bench run 2 waits for run 1
    test_1 = _start(lambda: run("test 1", lambda: lock.hold("test", "test run 1", 0.01), first_done))
    time.sleep(0.2)  # test run 1 arrives behind it, while bench run 1 still runs
    os.close(bench_1)
    deadline = time.monotonic() + 2
    while not order and time.monotonic() < deadline:
        time.sleep(0.01)
    test_2 = _start(lambda: run("test 2", lambda: lock.hold("test", "test run 2", 0.01)))
    time.sleep(0.3)  # test run 2 arrives while test run 1 runs: bench run 2's turn has come
    first_done.set()
    assert all(done.wait(2) for done in (test_1, bench_2, test_2))
    assert order == ["test 1", "bench 2", "test 2"]


def test_a_run_ahead_that_leaves_while_a_waiting_run_names_it_does_not_end_the_waiting_run(monkeypatch, tmp_path):
    """Bug: the run ahead in line goes in, or ends, between a waiting run's look at the line and its read of that
    run's ticket for the wait message, and the read's FileNotFoundError ends the waiting run."""
    lock = _machine(monkeypatch, tmp_path)
    game = lock.hold("game", "a game", 0.01)  # on the machine, shared, so the message names a run in line
    ahead = tmp_path / "work" / lock.LINE / f"{0:020d}-1-game"
    ahead.write_text("a game ahead", encoding="utf-8")
    alive = lock._alive

    def leaves_once_seen(ticket):  # the run ahead is in line when the waiting run looks, gone a moment later
        if ticket != ahead:
            return alive(ticket)
        ticket.unlink()
        return True

    monkeypatch.setattr(lock, "_alive", leaves_once_seen)
    got = []
    bench = _start(lambda: got.append(lock.hold("measure", "a benchmark", 0.01)))
    time.sleep(0.2)  # the benchmark has named who it waits for, and waits for the game
    os.close(game)
    assert bench.wait(2)
    os.close(got[0])
