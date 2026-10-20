"""Start the programs the installer runs: ffmpeg, the decryption programs and taskkill."""

from __future__ import annotations

import os
import subprocess

# subprocess.DETACHED_PROCESS, which Python defines only on Windows. Unlike CREATE_NO_WINDOW it starts no hidden
# console host, which outlives the program with the program's folder as its own, so that folder cannot be removed.
DETACHED_PROCESS = 0x00000008


def run(command: list[str], **options) -> subprocess.CompletedProcess:
    """subprocess.run, but on Windows without a console, so without a console window.

    The setup window is a windowed program, so Windows gives each console program it starts a console of its own.
    Every caller redirects stdin, stdout and stderr, so the program never needs one.
    """
    if os.name == "nt":
        options["creationflags"] = options.get("creationflags", 0) | DETACHED_PROCESS
    return subprocess.run(command, **options)
