"""tools/machine_lock.py: one heavy run at a time across the main checkout and its worktrees."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

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
            os.kill(int((tmp_path / "orphan").read_text()), signal.SIGKILL)
