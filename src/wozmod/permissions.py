"""The Windows permissions of the files wozmod places in the game folder.

A placed file must take the game folder's permissions, as a stock file does, so that every account that can run the
game can open it. On Windows a moved file keeps the permissions it had where it was built, so wozmod builds in
folders that inherit their parent's (`staging_folder`), and `inherit` repairs the files that releases up to 0.2.1
placed with an access list of their own, which locks out every account but the one that installed them.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterable
from pathlib import Path


def staging_folder(parent: Path, prefix: str) -> Path:
    """A new folder in parent, with parent's permissions, for files that will be moved into place.

    Not tempfile.mkdtemp: since Python 3.12.4 its folder on Windows admits only SYSTEM, Administrators and its owner,
    and cuts off what parent grants. Files moved out of it keep that, and the game then fails to start (0xc0000022,
    access denied) for every other account, including the player's own account after Windows is reinstalled.
    """
    path = Path(parent) / f"{prefix}{uuid.uuid4().hex[:12]}"
    path.mkdir()
    return path


def inherit(paths: Iterable[Path]) -> int:
    """Give each existing file that has an access list of its own (cut off from its folder's) its folder's
    permissions instead; return how many changed. Raises PermissionError when this account may not change one.
    Elsewhere than Windows, permissions do not travel with a move, so there is nothing to repair."""
    if sys.platform != "win32":
        return 0
    changed = 0
    for path in paths:
        if path.is_file() and not _inherits(path):
            _reset(path)
            changed += 1
    return changed


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _SE_FILE_OBJECT = 1
    _DACL_SECURITY_INFORMATION = 0x4
    _UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
    _SE_DACL_PROTECTED = 0x1000
    _ACL_REVISION = 2
    _ACL_HEADER = 8  # sizeof(ACL): an access list with no entries of its own
    _ERROR_ACCESS_DENIED = 5

    _advapi32.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                                                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.POINTER(ctypes.c_void_p)]
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
                                                       ctypes.POINTER(wintypes.DWORD)]
    _advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    _advapi32.InitializeAcl.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    _advapi32.InitializeAcl.restype = wintypes.BOOL
    _advapi32.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                                                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

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
