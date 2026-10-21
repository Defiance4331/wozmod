"""The three donor discs, and the folder the install extracts them to."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .inventory import PSP, WO1_X360, X360, Donor
from .programs import (
    PSP_ENCRYPTED_PROGRAM, PSP_PROGRAM, WO1_X360_ENCRYPTED_PROGRAM, WO1_X360_PROGRAM, X360_ENCRYPTED_PROGRAM,
    X360_PROGRAM, ProgramError, ProgramIdentity, read_pinned,
)


@dataclass(frozen=True)
class Program:
    member: str  # the encrypted program inside the disc image
    staged: str  # the name of its decrypted copy in the disc's donor folder
    plain: ProgramIdentity
    encrypted: ProgramIdentity


@dataclass(frozen=True)
class Disc:
    donor: Donor  # its name is the disc's folder in the donors folder
    program: Program
    title: str  # the disc as players see it named, everywhere; each place adds "disc image", "disc" or "program file"


# Keyed by the disc image's input name, which donor pointers also use. The only copy of this mapping.
DISCS = {
    "wo1-x360": Disc(WO1_X360, Program("default.xex", "default.pe", WO1_X360_PROGRAM, WO1_X360_ENCRYPTED_PROGRAM),
                     "Warriors Orochi (Xbox 360, USA)"),
    "x360": Disc(X360, Program("default.xex", "default.pe", X360_PROGRAM, X360_ENCRYPTED_PROGRAM),
                 "Warriors Orochi 2 (Xbox 360, USA)"),
    "psp": Disc(PSP, Program("PSP_GAME/SYSDIR/EBOOT.BIN", "eboot.prx", PSP_PROGRAM, PSP_ENCRYPTED_PROGRAM),
                "Warriors Orochi 2 (PSP, USA)"),
}


class DonorsFolder:
    """The extracted discs: one folder per disc, holding its members and its decrypted program."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def folder(self, disc: str) -> Path:
        return self.root / DISCS[disc].donor.name

    def program(self, disc: str) -> bytes:
        """The disc's decrypted program, checked against its pin and without trailing bytes; ProgramError when it
        is absent or wrong."""
        identity = DISCS[disc].program.plain
        try:
            data = read_pinned(self.folder(disc) / DISCS[disc].program.staged, identity)
        except OSError as exc:
            raise ProgramError(f"Cannot read {identity.name}") from exc
        if data is None:
            raise ProgramError(f"{identity.name} has the wrong size or SHA-256")
        return data

    def stage_program(self, disc: str, data: bytes) -> None:
        """Write the disc's decrypted program where `program` reads it."""
        (self.folder(disc) / DISCS[disc].program.staged).write_bytes(data)
