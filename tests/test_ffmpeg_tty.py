"""How the installer runs ffmpeg: never reading the terminal, and never stopped by a scratch folder it cannot delete."""

import errno
import os
import subprocess
import tempfile

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


def test_a_scratch_folder_windows_cannot_delete_yet_does_not_stop_the_conversion(tmp_path, monkeypatch):
    # Bug (issue 4): a virus scanner holds a converted clip, the folder delete fails with "directory is not empty",
    # and the whole install stops.
    def fake_run(command, **kwargs):
        for part in command:
            if str(part).endswith(".out"):
                open(part, "wb").write(b"x")
        return subprocess.CompletedProcess(command, 0)

    def held(path, *args, rmdir=os.rmdir, **kwargs):
        if "wozmod-ffmpeg-" in str(path):
            raise OSError(errno.ENOTEMPTY, "The directory is not empty", str(path))
        return rmdir(path, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os, "rmdir", held)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # the folders it cannot delete stay in tmp_path
    assert audio._ffmpeg_each([([], b"clip", [])] * 9) == [b"x"] * 9
