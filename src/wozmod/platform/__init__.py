"""Everything wozmod does differently on Windows than elsewhere, behind one interface: windows.py there, posix.py on
Linux (and Proton's host). The rest of wozmod calls these and never asks which system it runs on
(tests/test_platform_lint.py); tests/test_platform_contract.py runs the same cases against each adapter on its own
system (Linux, Windows Python under Wine, and a Windows 11 machine for what Wine cannot show).

- match_folder_access(paths): give placed files their folder's permissions; how many changed.
- run_hidden(command, **options): subprocess.run, with no console window on Windows.
- ProcessTree(command, **options): subprocess.Popen whose kill_tree() ends it and all it started; close() after.
- memory(): (total, available) bytes of RAM, or None.
- console_ansi(stream): whether a terminal stream takes ANSI cursor codes (switching them on where needed).
- settings_folder(): where the player's settings for programs live.
- split_command(command): the program and arguments a subprocess.Popen audit event names.
- executable(name): the file name of a program.
- lower_priority(): this process and what it starts give way to other work.
- shared_library(name, version): the file name of a shared library.
- release_name(): what a release's files and folders call the system.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

if sys.platform == "win32":
    from .windows import (ProcessTree, console_ansi, executable, lower_priority, match_folder_access,
                          memory, release_name, run_hidden, settings_folder, shared_library, split_command)
else:
    from .posix import (ProcessTree, console_ansi, executable, lower_priority, match_folder_access,
                        memory, release_name, run_hidden, settings_folder, shared_library, split_command)

__all__ = ["NAME", "ProcessTree", "console_ansi", "executable", "lower_priority", "match_folder_access",
           "memory", "release_name", "run_hidden", "settings_folder", "shared_library", "split_command", "staging_folder"]

NAME = sys.platform  # the system, as the install record and the step keys name it


def staging_folder(parent: Path, prefix: str) -> Path:
    """A new folder in parent, with parent's permissions, for files that will be moved into place.

    Not tempfile.mkdtemp: since Python 3.12.4 its folder on Windows admits only SYSTEM, Administrators and its owner,
    and cuts off what parent grants. Files moved out of it keep that, and the game then fails to start (0xc0000022,
    access denied) for every other account, including the player's own account after Windows is reinstalled.
    """
    path = Path(parent) / f"{prefix}{uuid.uuid4().hex[:12]}"
    path.mkdir()
    return path
