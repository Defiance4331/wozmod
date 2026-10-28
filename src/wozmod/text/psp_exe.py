"""Read string slots from the player-supplied decrypted PSP program."""

from __future__ import annotations

import re

from wozmod.formats.scripts import donor_text

PSP_STRING = re.compile(rb"(?<=\0)[\x09\x0a\x20-\x7e\x80-\xff]{2,}(?=\0)")
EXE_STRING = re.compile(rb"(?<=\0)[^\0]{1,400}(?=\0)")


def eboot_strings(data: bytes) -> list[str | None]:
    """Strings of the decrypted PSP program (checked by the caller) in file order; invalid donor strings retain a
    None slot."""
    return [donor_text(m.group()) for m in PSP_STRING.finditer(data)]

