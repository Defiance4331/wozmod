"""tools/xenia_wo2.py without the emulator: a fake Xenia and a fake /proc."""

from __future__ import annotations

import os
import subprocess

import pytest

pytestmark = pytest.mark.linux_only("the Xenia runs use the Linux game harness, bash and /proc")


def test_launch_sh_hands_xenia_the_disc_path_whole(tmp_path):
    """Bugs: a disc path with a space or a quote reaches Xenia split or cut; the template's shell braces come out
    mangled, so GAME_DIR's libraries are not on the path."""
    import xenia_wo2

    xenia = tmp_path / "xenia"
    (xenia / "usr/bin").mkdir(parents=True)
    fake = xenia / "usr/bin/xenia_canary"  # prints what it was given, one argument a line
    fake.write_text('#!/bin/bash\nprintf "%s\\n" "$LD_LIBRARY_PATH" "$@"\n', encoding="utf-8")
    fake.chmod(0o755)
    iso = tmp_path / "donor discs/it's wo2.iso"
    iso.parent.mkdir()
    iso.write_bytes(b"")
    root = tmp_path / "runs/001"
    xenia_wo2.stage(root, xenia, iso)
    game = root / "game"
    out = subprocess.run([root / "launch.sh"], env={**os.environ, "GAME_DIR": str(game), "LD_LIBRARY_PATH": "/x"},
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout.splitlines()
    assert out[0] == f"{game}/usr/lib:/x"
    assert out[-1] == str(iso) and out[1] == f"--storage_root={game}"
    assert (game / "portable.txt").is_file()


def test_peek_and_poke_find_this_runs_emulator_among_two(tmp_path):
    """Bug: memory access takes the first xenia_canary on the machine, so a poke lands in another agent's run."""
    import xenia_wo2

    procs = tmp_path / "proc"
    for pid, run in ((100, "runs/other"), (200, "runs/mine"), (300, "runs/mine-2")):
        (procs / str(pid)).mkdir(parents=True)
        (procs / str(pid) / "cmdline").write_bytes(
            b"\0".join([b"/x/usr/bin/xenia_canary", f"--storage_root={tmp_path / run / 'game'}".encode(), b"a.iso"]))
    (procs / "self").mkdir()
    assert xenia_wo2.xenia_pid(tmp_path / "runs/mine", procs) == 200
    with pytest.raises(ValueError):
        xenia_wo2.xenia_pid(tmp_path / "runs/none", procs)
