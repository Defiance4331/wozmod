"""Read string slots from the player-supplied decrypted PSP program."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from wozmod.donors.programs import PSP_PROGRAM
from wozmod.formats.scripts import donor_text

EBOOT_SHA256 = PSP_PROGRAM.sha256
PSP_STRING = re.compile(rb"(?<=\0)[\x09\x0a\x20-\x7e\x80-\xff]{2,}(?=\0)")
EXE_STRING = re.compile(rb"(?<=\0)[^\0]{1,400}(?=\0)")


def eboot_strings(prx: Path) -> list[str | None]:
    """Program strings in file order; invalid donor strings retain a None slot."""
    data = prx.read_bytes()
    if data[:4] == b"~PSP":
        raise ValueError(f"{prx} is still encrypted; supply the decrypted PSP program")
    if hashlib.sha256(data).hexdigest() != EBOOT_SHA256:
        raise ValueError(f"{prx} is not the decrypted WO2 PSP USA program")
    return [donor_text(m.group()) for m in PSP_STRING.finditer(data)]


def exe_strings(exe: bytes, keys: set[str]) -> list[str]:
    """Stock program strings matching table keys, in file order."""
    found = []
    for match in EXE_STRING.finditer(exe):
        try:
            text = match.group().decode("cp932")
        except UnicodeDecodeError:
            continue
        if text in keys:
            found.append(text)
    return found
