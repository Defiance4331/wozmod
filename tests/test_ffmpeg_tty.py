"""ffmpeg must not read the terminal: a background process group that does is stopped, and the install hangs."""

import subprocess

from wozmod.formats import audio


def test_ffmpeg_is_started_without_the_terminal_as_its_input(tmp_path, monkeypatch):
    # Bug: ffmpeg inherits stdin, so under a terminal (worker in its own process group) it is stopped and the install hangs.
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs)
        for part in command:
            if str(part).endswith(".out"):
                open(part, "wb").write(b"x")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert audio._ffmpeg_each([([], b"clip", [])]) == [b"x"]
    assert seen["stdin"] is subprocess.DEVNULL
