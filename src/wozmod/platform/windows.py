"""wozmod on Windows: the adapter that wozmod.platform picks there."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from collections.abc import Iterable
from ctypes import wintypes
from pathlib import Path

# subprocess.DETACHED_PROCESS. Unlike CREATE_NO_WINDOW it starts no hidden console host, which outlives the program
# with the program's folder as its own, so that folder cannot be removed.
DETACHED_PROCESS = 0x00000008
CREATE_SUSPENDED = 0x00000004

_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)


def _declare(library, name: str, result, *arguments) -> None:
    function = getattr(library, name)
    function.restype, function.argtypes = result, list(arguments)


_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x4
_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
_SE_DACL_PROTECTED = 0x1000
_ACL_REVISION = 2
_ACL_HEADER = 8  # sizeof(ACL): an access list with no entries of its own
_ERROR_ACCESS_DENIED = 5
_declare(_advapi32, "GetNamedSecurityInfoW", wintypes.DWORD, wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
_declare(_advapi32, "GetSecurityDescriptorControl", wintypes.BOOL, ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
         ctypes.POINTER(wintypes.DWORD))
_declare(_advapi32, "InitializeAcl", wintypes.BOOL, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD)
_declare(_advapi32, "SetNamedSecurityInfoW", wintypes.DWORD, wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_declare(_kernel32, "LocalFree", ctypes.c_void_p, ctypes.c_void_p)


def match_folder_access(paths: Iterable[Path]) -> int:
    """Give each existing file that has an access list of its own (cut off from its folder's) its folder's
    permissions instead; return how many changed. Raises PermissionError when this account may not change one.

    A moved file keeps the permissions it had where it was built: releases up to 0.2.1 placed files with an access
    list of their own, which locks out every account but the one that installed them."""
    changed = 0
    for path in paths:
        if path.is_file() and not _inherits(path):
            _reset(path)
            changed += 1
    return changed


def _inherits(path: Path) -> bool:
    """Whether path's access list is its folder's: not protected. A file this account may not even read the
    permissions of is one the old installs locked."""
    descriptor = ctypes.c_void_p()
    error = _advapi32.GetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None,
                                            None, None, ctypes.byref(descriptor))
    if error == _ERROR_ACCESS_DENIED:
        return False
    if error:
        raise ctypes.WinError(error)
    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not _advapi32.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        return not control.value & _SE_DACL_PROTECTED
    finally:
        _kernel32.LocalFree(descriptor)


def _reset(path: Path) -> None:
    """Drop path's own access entries and let it inherit its folder's, as a file made there would. The empty
    list matters: a NULL one would grant everyone everything."""
    empty = ctypes.create_string_buffer(_ACL_HEADER)
    if not _advapi32.InitializeAcl(empty, _ACL_HEADER, _ACL_REVISION):
        raise ctypes.WinError(ctypes.get_last_error())
    error = _advapi32.SetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION
                                            | _UNPROTECTED_DACL_SECURITY_INFORMATION, None, None, empty, None)
    if error:
        raise ctypes.WinError(error)  # 5 (access denied) is a PermissionError


def run_hidden(command: list[str], **options) -> subprocess.CompletedProcess:
    """subprocess.run without a console, so without a console window.

    The setup window is a windowed program, so Windows gives each console program it starts a console of its own.
    Every caller redirects stdin, stdout and stderr, so the program never needs one.
    """
    options["creationflags"] = options.get("creationflags", 0) | DETACHED_PROCESS
    return subprocess.run(command, **options)


_PROCESS_TERMINATE, _PROCESS_SET_QUOTA = 0x0001, 0x0100
_THREAD_SUSPEND_RESUME = 0x0002
_TH32CS_SNAPTHREAD = 0x00000004
_INVALID_HANDLE = wintypes.HANDLE(-1).value


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("thread", wintypes.DWORD),
                ("process", wintypes.DWORD), ("base_priority", wintypes.LONG), ("delta_priority", wintypes.LONG),
                ("flags", wintypes.DWORD)]


_declare(_kernel32, "CreateJobObjectW", wintypes.HANDLE, ctypes.c_void_p, wintypes.LPCWSTR)
_declare(_kernel32, "AssignProcessToJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE)
_declare(_kernel32, "TerminateJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
_declare(_kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_declare(_kernel32, "OpenThread", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_declare(_kernel32, "ResumeThread", wintypes.DWORD, wintypes.HANDLE)
_declare(_kernel32, "CreateToolhelp32Snapshot", wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
_declare(_kernel32, "Thread32First", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
_declare(_kernel32, "Thread32Next", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
_declare(_kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)


def _handle(value):
    """value, unless it is the null or invalid handle that a failed call returns: then raise its error."""
    if not value or value == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


def _succeeded(done) -> None:
    if not done:
        raise ctypes.WinError(ctypes.get_last_error())


class ProcessTree(subprocess.Popen):
    """A started program, without a console (as run_hidden), in a Job Object of its own, which every program it
    starts joins, so that kill_tree() ends them together, even those whose parent has already ended.

    The program starts suspended and runs only once it is in the job, so nothing it starts can escape it. Its
    children join the job when they are created; none can break away, since the job does not allow it.
    """

    def __init__(self, command: list[str], **options):
        self._job = None
        job = _handle(_kernel32.CreateJobObjectW(None, None))
        options["creationflags"] = options.get("creationflags", 0) | DETACHED_PROCESS | CREATE_SUSPENDED
        try:
            super().__init__(command, **options)
        except BaseException:
            _kernel32.CloseHandle(job)
            raise
        try:
            # Popen holds the process's handle, so its number cannot name another program meanwhile.
            process = _handle(_kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, self.pid))
            try:
                _succeeded(_kernel32.AssignProcessToJobObject(job, process))
            finally:
                _kernel32.CloseHandle(process)
            _resume(self.pid)
        except BaseException:
            self.kill()
            self.__exit__(None, None, None)  # its pipes, and wait for its end
            _kernel32.CloseHandle(job)
            raise
        self._job = job

    def kill_tree(self) -> None:
        """Kill the program and everything it started."""
        _succeeded(_kernel32.TerminateJobObject(self._job, 1))  # 1, the exit code taskkill /F gave

    def close(self) -> None:
        """Let go of the job; kill_tree() cannot be called after this. What still runs keeps running."""
        if self._job is not None:
            _kernel32.CloseHandle(self._job)
            self._job = None


def _resume(pid: int) -> None:
    """Start the threads of a process created suspended: its one thread."""
    snapshot = _handle(_kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0))
    resumed = 0
    try:
        entry = _ThreadEntry(size=ctypes.sizeof(_ThreadEntry))
        found = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.process == pid:
                thread = _handle(_kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.thread))
                try:
                    if _kernel32.ResumeThread(thread) == 0xFFFFFFFF:
                        raise ctypes.WinError(ctypes.get_last_error())
                finally:
                    _kernel32.CloseHandle(thread)
                resumed += 1
            found = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    if not resumed:
        raise OSError(f"process {pid} has no thread to start")


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD),
                ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                ("page_total", ctypes.c_ulonglong), ("page_available", ctypes.c_ulonglong),
                ("virtual_total", ctypes.c_ulonglong), ("virtual_available", ctypes.c_ulonglong),
                ("extended", ctypes.c_ulonglong)]


_declare(_kernel32, "GlobalMemoryStatusEx", wintypes.BOOL, ctypes.POINTER(_MemoryStatus))


def memory() -> tuple[int, int] | None:
    """(total, available) physical memory in bytes, or None when the system does not say."""
    status = _MemoryStatus(length=ctypes.sizeof(_MemoryStatus))
    if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status.total, status.available


_STD_OUTPUT, _STD_ERROR = -11, -12
_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
_declare(_kernel32, "GetStdHandle", wintypes.HANDLE, wintypes.DWORD)
_declare(_kernel32, "GetConsoleMode", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_declare(_kernel32, "SetConsoleMode", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD)


def console_ansi(stream) -> bool:
    """Whether the terminal stream (sys.stdout or sys.stderr) understands ANSI cursor codes: a Windows 10 console
    does once its virtual terminal mode is switched on, which this does."""
    handle = _kernel32.GetStdHandle(_STD_OUTPUT if stream is sys.stdout else _STD_ERROR)
    mode = wintypes.DWORD()
    return bool(_kernel32.GetConsoleMode(handle, ctypes.byref(mode))
                and _kernel32.SetConsoleMode(handle, mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING))


def settings_folder() -> Path:
    """The folder that holds the player's settings for programs: APPDATA, else XDG_CONFIG_HOME, else ~/.config."""
    return Path(os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


_declare(_shell32, "CommandLineToArgvW", ctypes.POINTER(wintypes.LPWSTR), wintypes.LPCWSTR,
         ctypes.POINTER(ctypes.c_int))


def split_command(command) -> list[str]:
    """The program and its arguments, from the command a subprocess.Popen audit event names: here one command line
    (subprocess joins a list before the event), split as the program's C runtime splits it."""
    if not isinstance(command, str):
        return list(command)
    count = ctypes.c_int()
    words = _shell32.CommandLineToArgvW(command, ctypes.byref(count))
    if not words:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [words[k] for k in range(count.value)]
    finally:
        _kernel32.LocalFree(words)


def executable(name: str) -> str:
    """The file name of the program called name."""
    return f"{name}.exe"


def lower_priority() -> None:
    """Nothing: below-normal priority for the voice step made installs on Windows slower (an 8 GiB VM, where the
    voice step finishes last: median 863 s against 801 s, 5 runs each, work/handbacks/unit-8-d3.md)."""
