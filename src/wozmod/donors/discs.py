"""The three donor discs, and the folder the install extracts them to."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .inventory import WO1_X360, WO2_PSP, WO2_X360, Donor
from .programs import (
    WO1_X360_ENCRYPTED_PROGRAM, WO1_X360_PROGRAM, WO2_PSP_ENCRYPTED_PROGRAM, WO2_PSP_PROGRAM,
    WO2_X360_ENCRYPTED_PROGRAM, WO2_X360_PROGRAM, ProgramError, ProgramIdentity, read_pinned,
)


@dataclass(frozen=True)
class Program:
    member: str  # the encrypted program inside the disc image
    staged: str  # the name of its decrypted copy in the disc's donor folder
    plain: ProgramIdentity
    encrypted: ProgramIdentity


@dataclass(frozen=True)
class Disc:
    donor: Donor  # its name is the disc's key in DISCS and its folder in the donors folder
    program: Program
    title: str  # the disc as players see it named, everywhere; each place adds "disc image", "disc" or "program file"


# Keyed by the donor's name, which is also its disc image's input name, its folder in the donors folder and the
# first item of every donor pointer in data/. The only copy of this mapping.
DISCS = {disc.donor.name: disc for disc in (
    Disc(WO1_X360, Program("default.xex", "default.pe", WO1_X360_PROGRAM, WO1_X360_ENCRYPTED_PROGRAM),
         "Warriors Orochi (Xbox 360, USA)"),
    Disc(WO2_X360, Program("default.xex", "default.pe", WO2_X360_PROGRAM, WO2_X360_ENCRYPTED_PROGRAM),
         "Warriors Orochi 2 (Xbox 360, USA)"),
    Disc(WO2_PSP, Program("PSP_GAME/SYSDIR/EBOOT.BIN", "eboot.prx", WO2_PSP_PROGRAM, WO2_PSP_ENCRYPTED_PROGRAM),
         "Warriors Orochi 2 (PSP, USA)"),
)}


# The short keys the art data once used for an art pointer, and the disc name that keys it now.
RETIRED_POINTER_KEYS = {"donor": WO1_X360.name, "wo2": WO2_X360.name}


def check_disc_names(value, where: str) -> None:
    """Raise ValueError at the first disc name in JSON data that is no key of DISCS: the first item of a donor
    pointer (a "ref", or an edit's replacement that inserts a donor string) and a "donor" that is a name; and at
    an art pointer under a retired key (a "wo2", or a "donor" that holds a pointer). Data loaders call it once, so
    a misspelt or retired name stops the load instead of matching no branch later on."""
    if isinstance(value, list):
        for item in value:
            check_disc_names(item, where)
        return
    if not isinstance(value, dict):
        return
    for key, disc in RETIRED_POINTER_KEYS.items():
        if key in value and not (key == "donor" and (value[key] is None or isinstance(value[key], str))):
            raise ValueError(f"{where}: {key!r} is a retired key for a pointer into {DISCS[disc].title}; "
                             f"key it {disc!r}")
    named = [value["donor"]] if isinstance(value.get("donor"), str) else []
    if isinstance(value.get("ref"), list) and value["ref"]:
        named.append(value["ref"][0])
    named += [edit[2][0] for edit in value.get("edits", ())
              if isinstance(edit, list) and len(edit) == 3 and isinstance(edit[2], list) and edit[2]]
    for name in named:
        if name not in DISCS:
            raise ValueError(f"{where}: {name!r} is not a donor disc ({', '.join(DISCS)})")
    for item in value.values():
        check_disc_names(item, where)


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
