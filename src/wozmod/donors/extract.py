"""Extract selected, verified donor members into a destination folder."""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Callable, Iterable

from wozmod.discs.cabinet import CabinetSet
from wozmod.discs.common import DiscError
from wozmod.discs.iso9660 import Iso9660
from wozmod.discs.xdvdfs import Xdvdfs

from .inventory import DONORS, Donor, DonorFile


def verify_file(path: Path | str, identity: DonorFile) -> Path:
    path = Path(path)
    with path.open("rb") as stream:
        if os.fstat(stream.fileno()).st_size != identity.size:
            raise DiscError(f"Wrong donor member size: {identity.path}")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != identity.sha256:
        raise DiscError(f"Wrong donor member hash: {identity.path}")
    return path


def _open_disc(image: Path | str, donor: Donor):
    return (Iso9660 if donor.format == "iso9660" else Xdvdfs)(image)


def check_donor_image(image: Path | str, donor: Donor | str) -> None:
    """Raise DiscError (or OSError) unless the image lists every donor member at its pinned size.

    A listing check, not a hash check: extract_donor hashes the members it reads.
    """
    if isinstance(donor, str):
        donor = DONORS[donor]
    with _open_disc(image, donor) as disc:
        archive = CabinetSet(disc.open, donor.first_cabinet) if donor.first_cabinet else disc
        available = {member.path.casefold(): member.size for member in archive.files()}
    for member in donor.files:
        if available.get(member.source.casefold()) != member.size:
            raise DiscError(f"Missing or wrong-sized donor member: {member.source}")


def read_disc_member(image: Path | str, donor: Donor | str, path: str) -> bytes | None:
    """The bytes of one file in the image, or None when the image does not hold it."""
    if isinstance(donor, str):
        donor = DONORS[donor]
    with _open_disc(image, donor) as disc:
        try:
            with disc.open(path) as stream:
                return stream.read()
        except FileNotFoundError:
            return None


def _target(destination: Path, name: str) -> Path:
    target = destination / name
    if not target.parent.resolve().is_relative_to(destination.resolve()):
        raise ValueError("Destination member escapes through a symlink")
    return target


def extract_donor(
    image: Path | str,
    donor: Donor | str,
    destination: Path | str,
    *,
    files: Iterable[str] | None = None,
    on_bytes: Callable[[int, int], None] | None = None,
) -> tuple[Path, ...]:
    """Publish selected members only after every size and hash matches.

    on_bytes(done, total) is called as the members are read (it may raise to stop).
    """
    if isinstance(donor, str):
        donor = DONORS[donor]
    by_path = {member.path: member for member in donor.files}
    selected = tuple(by_path.values()) if files is None else tuple(
        by_path[name] for name in dict.fromkeys(files)
    )
    if not selected:
        return ()
    destination = Path(destination)
    targets = tuple(_target(destination, member.path) for member in selected)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _open_disc(image, donor) as disc, tempfile.TemporaryDirectory(
        prefix=".donor-", dir=destination.parent
    ) as staging:
        archive = CabinetSet(disc.open, donor.first_cabinet) if donor.first_cabinet else disc
        available = {member.path.casefold(): member for member in archive.files()}
        resolved = {}
        for member in selected:
            actual = available.get(member.source.casefold())
            if actual is None or actual.size != member.size:
                raise DiscError(f"Missing or wrong-sized donor member: {member.source}")
            resolved[member.source] = actual.path
        total, done = sum(member.size for member in selected), 0
        with ExitStack() as stack:
            outputs = {}
            staged = {}
            for member in selected:
                path = Path(staging) / member.path
                path.parent.mkdir(parents=True, exist_ok=True)
                staged[member.path] = path
                outputs[member.source.casefold()] = stack.enter_context(path.open("wb"))
            if donor.first_cabinet:
                for name, chunk in archive.iter_files(resolved[member.source] for member in selected):
                    outputs[name.casefold()].write(chunk)
                    done += len(chunk)
                    if on_bytes:
                        on_bytes(done, total)
            else:
                for member in selected:
                    with disc.open(resolved[member.source]) as source:
                        while chunk := source.read(1024 * 1024):
                            outputs[member.source.casefold()].write(chunk)
                            done += len(chunk)
                            if on_bytes:
                                on_bytes(done, total)
        for member in selected:
            verify_file(staged[member.path], member)
        for member, target in zip(selected, targets, strict=True):
            _target(destination, member.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged[member.path], target)
    return targets
