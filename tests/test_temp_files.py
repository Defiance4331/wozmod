"""Where the tests write their files."""

from __future__ import annotations

import tempfile
from pathlib import Path

WORK = Path(__file__).resolve().parents[1] / "work" / "test-tmp"


def test_test_files_go_to_the_checkouts_work_folder_on_disk(tmp_path):
    # Bug: tmp_path and the temporary files of the code under test go to /tmp, often a tmpfs: six slow tests at a time
    # each building several GB there filled RAM and the kernel killed the run (verify.sh --full, 2026-10-29).
    assert tmp_path.resolve().is_relative_to(WORK.resolve())
    assert Path(tempfile.gettempdir()).resolve().is_relative_to(WORK.resolve())
