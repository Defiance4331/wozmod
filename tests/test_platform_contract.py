"""wozmod.platform's contract: the same cases against the adapter of the system they run on (posix.py on Linux,
windows.py under Windows Python, in Wine and on Windows itself). Cases marked real_windows check what Wine does not
emulate (access lists) and run on a Windows machine only."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from wozmod import platform

WINDOWS = sys.platform == "win32"
SOURCE = Path(__file__).resolve().parents[1] / "src"
# A program that beats (adds a byte to a file) every 50 ms, for 30 s at most, so that a test can see it run or end.
HEARTBEAT = ("import sys, time\nfor _ in range(600):\n    with open(sys.argv[1], 'ab') as beat:\n"
             "        beat.write(b'.')\n    time.sleep(0.05)\n")
# Starts the heartbeat and ends at once: the heartbeat's parent is gone before anything is killed.
MIDDLE = "import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]])\n"
# Starts MIDDLE, waits for it to end, says so, and runs on until killed.
WORKER = ("import pathlib, subprocess, sys, time\nsubprocess.run([sys.executable, '-c', sys.argv[1], sys.argv[2], "
          "sys.argv[3]])\npathlib.Path(sys.argv[4]).touch()\ntime.sleep(60)\n")


def _beats(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _until(condition, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def test_kill_tree_ends_a_grandchild_whose_parent_has_already_ended(tmp_path):
    # Bug: only the program is killed, or what it started through a parent that has already ended escapes
    # (taskkill /T walks parent links, which end with the parent), so an ffmpeg or a pool worker keeps running and
    # writing after Cancel.
    beat, middle_ended = tmp_path / "beat", tmp_path / "middle-ended"
    tree = platform.ProcessTree([sys.executable, "-c", WORKER, MIDDLE, HEARTBEAT, str(beat), str(middle_ended)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    try:
        _until(lambda: middle_ended.exists() and _beats(beat) > 0)
        before = _beats(beat)
        _until(lambda: _beats(beat) > before)  # the orphan runs
        tree.kill_tree()
        tree.wait(10)
        time.sleep(0.5)  # a beat written while the kill landed
        ended = _beats(beat)
        time.sleep(0.5)
        assert _beats(beat) == ended
    finally:
        tree.close()


def test_kill_tree_after_the_whole_tree_has_ended_does_nothing():
    # Bug: killing a tree that is already gone raises (no such process), so a step's own error is replaced by that
    # one while the installer stops the other steps.
    tree = platform.ProcessTree([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
    try:
        assert tree.wait(30) == 0
        tree.kill_tree()
    finally:
        tree.close()


def test_a_program_started_by_either_runs_with_the_callers_options(tmp_path):
    # Bug: the adapter drops or overrides an option the caller gave (here the working folder, the input and the
    # encoding), as a Windows flag merge that replaces the options would.
    script = "import os, sys\nprint(os.getcwd(), sys.stdin.read())\nsys.exit(3)\n"
    run = platform.run_hidden([sys.executable, "-c", script], cwd=tmp_path, input="given", capture_output=True,
                              encoding="utf-8")
    tree = platform.ProcessTree([sys.executable, "-c", script], cwd=tmp_path, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, encoding="utf-8")
    try:
        started = tree.communicate("given", timeout=30)[0]
    finally:
        tree.close()
    assert (run.returncode, run.stdout, tree.returncode, started) == (3, f"{tmp_path} given\n", 3, run.stdout)


_popen_commands: list = []


def _keep_popen_command(event: str, args: tuple) -> None:
    if event == "subprocess.Popen" and _popen_commands == [None]:
        _popen_commands[0] = args[1]


def test_a_started_programs_command_splits_into_the_words_it_was_given():
    # Bug: the read gate takes the command a start's audit event names as a list of words. On Windows it is one
    # command line, so the gate names the line's last path as the program and checks none of its arguments.
    words = [sys.executable, "-c", "pass", "a b\\", 'say "hi"', "", "C:\\a folder\\", "plain"]
    sys.addaudithook(_keep_popen_command)  # stays for the process's life: it keeps only the start made below
    _popen_commands[:] = [None]
    try:
        subprocess.run(words, check=True)
    finally:
        command = _popen_commands.pop()
    assert platform.split_command(command) == words


CONSOLE = ("import ctypes, sys\nfrom ctypes import wintypes\nlist = (wintypes.DWORD * 8)()\n"
           "print(ctypes.WinDLL('kernel32').GetConsoleProcessList(list, 8))\n")


@pytest.mark.real_windows("Wine gives a console program no console of its own when its parent has none")
def test_programs_start_without_a_console_of_their_own():
    # Bug: the DETACHED_PROCESS flag is dropped (or a caller's creationflags replace it), so the setup window opens
    # a console window for each ffmpeg run, decryption program and build worker.
    plain = subprocess.run([sys.executable, "-c", CONSOLE], capture_output=True, encoding="utf-8", check=True)
    hidden = platform.run_hidden([sys.executable, "-c", CONSOLE], capture_output=True, encoding="utf-8", check=True,
                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    tree = platform.ProcessTree([sys.executable, "-c", CONSOLE], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                encoding="utf-8")
    try:
        started = tree.communicate(timeout=30)[0]
    finally:
        tree.close()
    assert int(plain.stdout) > 0  # without the adapter it has one: the check can see a console
    assert (hidden.stdout, started) == ("0\n", "0\n")


ANSI = ("import ctypes, sys\nfrom ctypes import wintypes\nfrom wozmod import platform\nfound = platform.console_ansi("
        "sys.stdout)\nmode = wintypes.DWORD()\nk = ctypes.WinDLL('kernel32')\nk.GetConsoleMode(k.GetStdHandle(-11), "
        "ctypes.byref(mode))\nopen(sys.argv[1], 'w', encoding='utf-8').write(f'{found} {mode.value & 4}')\n")


@pytest.mark.real_windows("Wine opens no console for CREATE_NEW_CONSOLE without a terminal; Linux needs no switch")
def test_ansi_codes_are_switched_on_in_a_windows_console(tmp_path):
    # Bug: the switch goes to the wrong handle or is not made, so the terminal install prints its cursor codes as
    # text instead of redrawing the progress in place.
    seen = tmp_path / "seen"
    subprocess.run([sys.executable, "-c", ANSI, str(seen)], creationflags=subprocess.CREATE_NEW_CONSOLE, check=True,
                   timeout=30, env={**os.environ, "PYTHONPATH": str(SOURCE)})
    assert seen.read_text(encoding="utf-8") == "True 4"


def test_memory_gives_the_total_and_what_is_free_of_it():
    # Bug: a field read from the wrong place (the load, a page count) or the two swapped, so the install picks a
    # worker count for memory the machine does not have.
    total, available = platform.memory()
    assert 1 << 30 < total < 1 << 44 and 0 < available <= total


def test_settings_live_where_earlier_releases_kept_them(monkeypatch, tmp_path):
    # Bug: the folder moves (on Windows from APPDATA to ~/.config, or the reverse elsewhere), so a player's
    # remembered disc paths are lost with the update.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert platform.settings_folder() == tmp_path / ("appdata" if WINDOWS else "xdg")
    monkeypatch.delenv("APPDATA")
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert platform.settings_folder() == Path.home() / ".config"


def test_a_programs_file_name_is_the_one_this_system_starts():
    # Bug: the release looks for the bundled ffmpeg under the other system's file name, does not find it, and the
    # install needs an ffmpeg on PATH.
    assert (Path(sys.executable).parent / platform.executable("python")).is_file()


PRIORITY = ("import os, subprocess, sys\nfrom wozmod import platform\nplatform.lower_priority()\nsubprocess.run("
            "[sys.executable, '-c', 'import os; print(os.nice(0), os.sched_getscheduler(0))'])\n")


@pytest.mark.linux_only("no Windows priority change until it is measured (decision D3)")
def test_programs_started_after_lower_priority_run_nicer_in_the_batch_policy():
    # Bug: the lower priority stays in the worker's thread, or is not set, so the voice step's ffmpeg runs competes
    # with the art step's long serial stages and the install takes longer.
    seen = subprocess.run([sys.executable, "-c", PRIORITY], capture_output=True, encoding="utf-8", check=True,
                          env={**os.environ, "PYTHONPATH": str(SOURCE)}).stdout.split()
    assert seen == [str(min(os.nice(0) + 10, 19)), str(os.SCHED_BATCH)]  # Linux stops at 19: a run started nicer


def test_files_that_already_have_their_folders_permissions_are_left_alone(tmp_path):
    # Bug: every placed file is reset and counted (or a missing one raises), so each install logs a repair it did
    # not need and asks for administrator rights it does not need.
    (tmp_path / "placed").write_bytes(b"x")
    assert platform.match_folder_access([tmp_path / "placed", tmp_path / "missing", tmp_path]) == 0


# Access lists, read by Windows itself (not through the adapter).

def _access(path: Path) -> tuple[bool, str]:
    """Whether path's access list is cut off from its folder's (P), and its entries, in SDDL. Not the AI flag: a list
    that Windows has recomputed from the folder's says so, a file made in the folder does not, and both grant alike."""
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    descriptor, text = ctypes.c_void_p(), wintypes.LPWSTR()
    error = advapi32.GetNamedSecurityInfoW(str(path), 1, 4, None, None, None, None, ctypes.byref(descriptor))
    if error:
        raise ctypes.WinError(error)
    if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, ctypes.byref(text), None):
        raise ctypes.WinError(ctypes.get_last_error())
    flags, entries = text.value.removeprefix("D:").split("(", 1)
    return "P" in flags, "(" + entries


def _set_access(path: Path, sddl: str) -> None:

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    if not advapi32.SetFileSecurityW(str(path), 4, descriptor):  # DACL; a P in the SDDL protects it
        raise ctypes.WinError(ctypes.get_last_error())


def _game_folder(parent: Path) -> Path:
    """A folder that, like a game folder under Program Files, also lets every user read and run what it holds."""
    game = parent / "game"
    game.mkdir()
    _set_access(game, "D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)(A;OICI;0x1200a9;;;BU)")
    (game / "stock").write_bytes(b"x")
    return game


def _moved_out_of(folder: Path, game: Path, name: str) -> Path:
    (folder / name).write_bytes(b"x")
    return Path(shutil.move(folder / name, game / name))


@pytest.mark.real_windows("Wine reports a file moved out of a private folder as inheriting its new folder's access")
def test_files_built_in_a_staging_folder_get_the_access_of_files_made_in_the_game_folder(tmp_path):
    # Bug: the staging folder is made like tempfile.mkdtemp's (owner, SYSTEM and Administrators only, cut off from
    # its folder), so the placed files lock out every other account and the game fails to start for them
    # (0xc0000022).
    game = _game_folder(tmp_path)
    private = _moved_out_of(Path(tempfile.mkdtemp(dir=tmp_path)), game, "private")
    staged = _moved_out_of(platform.staging_folder(game, ".staging-"), game, "staged")
    assert _access(private) != _access(game / "stock")  # the check can see a file cut off from its folder
    assert _access(staged) == _access(game / "stock")


@pytest.mark.real_windows("Wine cannot make a file that is cut off from its folder's access list by a move")
def test_a_file_cut_off_from_its_folders_access_gets_it_back_once(tmp_path):
    # Bug: the repair misses files that releases up to 0.2.1 placed with an access list of their own, or resets
    # them to an empty list (no account may open them), or counts them again on every install.
    game = _game_folder(tmp_path)
    locked = _moved_out_of(Path(tempfile.mkdtemp(dir=tmp_path)), game, "locked")
    assert _access(locked) != _access(game / "stock")
    assert platform.match_folder_access([game / "stock", locked]) == 1
    assert _access(locked) == _access(game / "stock")
    assert platform.match_folder_access([game / "stock", locked]) == 0


# Repairs one file with this account's privileges (backup, restore, take ownership) switched off, as a player's
# program runs: an administrator's SSH session has them on, and they let it past any access list.
UNPRIVILEGED = ("import ctypes, sys\nfrom ctypes import wintypes\nfrom pathlib import Path\nfrom wozmod import platform\n"
                "advapi32, kernel32 = ctypes.WinDLL('advapi32'), ctypes.WinDLL('kernel32')\n"
                "kernel32.GetCurrentProcess.restype = wintypes.HANDLE\n"
                "advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]\n"
                "advapi32.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL] + [ctypes.c_void_p] * 4\n"
                "token = wintypes.HANDLE()\n"
                "assert advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x20, ctypes.byref(token))\n"
                "assert advapi32.AdjustTokenPrivileges(token, True, None, 0, None, None)\n"
                "try:\n    platform.match_folder_access([Path(sys.argv[1])])\nexcept Exception as error:\n"
                "    print(type(error).__name__)\nelse:\n    print('repaired')\n")


@pytest.mark.real_windows("Wine does not check access lists when a program reads or changes one")
def test_a_file_this_account_may_not_repair_asks_for_administrator_rights(tmp_path):
    # Bug: the denial comes back as some other error, so the player sees a crash instead of "Run as administrator".
    locked = tmp_path / "locked"
    locked.write_bytes(b"x")
    _set_access(locked, "D:P(A;;0x1;;;OW)")  # its owner may read its bytes, not its permissions
    seen = subprocess.run([sys.executable, "-c", UNPRIVILEGED, str(locked)], capture_output=True, encoding="utf-8",
                          env={**os.environ, "PYTHONPATH": str(SOURCE)})
    assert (seen.stdout, seen.stderr) == ("PermissionError\n", "")
