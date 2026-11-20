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
    assert list(audio._ffmpeg_each([([], b"clip", [])], 1)) == [b"x"]
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
    assert list(audio._ffmpeg_each([([], b"clip", [])] * 9, 9)) == [b"x"] * 9


def test_each_job_input_is_made_only_as_its_batch_starts(monkeypatch):
    # Bug: the jobs (each a clip's bytes) are all made before the first ffmpeg runs, so the voices step holds every
    # input beside every output: the donor clips alone were about 0.7 GB of its peak in 2026-11.
    made, seen = [0], []

    def fake_run(command, **kwargs):
        seen.append(made[0])
        for part in command:
            if str(part).endswith(".out"):
                open(part, "wb").write(b"x")
        return subprocess.CompletedProcess(command, 0)

    def jobs():
        for _ in range(10 * audio.FFMPEG_BATCH):
            made[0] += 1
            yield [], b"clip", []

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert list(audio._ffmpeg_each(jobs(), 10 * audio.FFMPEG_BATCH, 2)) == [b"x"] * 10 * audio.FFMPEG_BATCH
    assert seen[0] <= 3 * audio.FFMPEG_BATCH  # the two processes' batches and the one made while they run
