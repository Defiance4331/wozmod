"""Verify player-supplied program files and run optional external tools."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from wozmod import process


class ProgramError(ValueError):
    """A program input or external tool output does not meet its identity."""


@dataclass(frozen=True, slots=True)
class ProgramIdentity:
    name: str
    size: int
    sha256: str
    platform: Literal["x360", "psp"]

    def __post_init__(self) -> None:
        if self.platform not in {"x360", "psp"}:
            raise ValueError("Unsupported program platform")
        if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 0:
            raise ValueError("Program size must be a nonnegative integer")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("Program SHA-256 must be 64 lowercase hexadecimal characters")


X360_PROGRAM = ProgramIdentity(
    "X360 program", 17_858_560,
    "a3064edfb90e851fbbfa3f183368aa91911a647f6cccc66215137f90cae9bdca", "x360",
)
PSP_PROGRAM = ProgramIdentity(
    "PSP program", 3_197_900,
    "adc763686495266265a484129e62f4d26040fafd02149b09b2ec71d65d84a669", "psp",
)
X360_ENCRYPTED_PROGRAM = ProgramIdentity(
    "Encrypted X360 program", 5_451_776,
    "5a99bd246d56e3a2362a65f1a617ebd427cc84b1c6468eb4f187496f7758f506", "x360",
)
WO1_X360_PROGRAM = ProgramIdentity(
    "WO1 X360 program", 16_973_824,
    "2038dbf356c3d33c6756337d00b6f48c63aa1f1129f323a6dd455b62c935ce77", "x360",
)
WO1_X360_ENCRYPTED_PROGRAM = ProgramIdentity(
    "Encrypted WO1 X360 program", 5_058_560,
    "2b57b71c803487ad6f8a3a04f3c1ffefdd0b727a4c4c8498d224b0a8ad9cf9ae", "x360",
)
PSP_ENCRYPTED_PROGRAM = ProgramIdentity(
    "Encrypted PSP program", 3_198_240,
    "e7c3714e77f13a83beb0cd0586fc2340efc2214122b1d184fc72bd40c9c39d11", "psp",
)

# Some decrypting tools leave extra bytes after the program (a dump of the encrypted file's tail, or zero padding).
MAX_EXTRA_BYTES = 65_536


def trim_program(data: bytes, identity: ProgramIdentity) -> bytes | None:
    """The pinned program when data is it, optionally followed by up to MAX_EXTRA_BYTES of extra bytes; else None.

    This only trims the end of a file. Nothing is decrypted.
    """
    if not identity.size <= len(data) <= identity.size + MAX_EXTRA_BYTES:
        return None
    program = data[:identity.size]
    return program if hashlib.sha256(program).hexdigest() == identity.sha256 else None


def verify_program(path: str | os.PathLike[str], identity: ProgramIdentity) -> Path:
    """Return a read-only input path after checking its size and SHA-256 (extra trailing bytes allowed)."""
    source = Path(path)
    try:
        with source.open("rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ProgramError(f"{identity.name} has the wrong size or file type")
            data = stream.read(identity.size + MAX_EXTRA_BYTES + 1)
    except OSError as exc:
        raise ProgramError(f"Cannot read {identity.name}") from exc
    if trim_program(data, identity) is None:
        raise ProgramError(f"{identity.name} has the wrong size or SHA-256")
    return source


# How to run each tool known to give a matching file: (name, platform, arguments from the input and output paths).
# The name is also looked for in the program's file name, to try the likely profile first.
PROFILES = (
    ("pspdecrypt", "psp", lambda src, dst: ["-o", dst, src]),
    ("xex2ool", "x360", lambda src, dst: ["basefile", src, "-o", dst]),
    ("xextool", "x360", lambda src, dst: ["-b", dst, src]),  # leaves 32 KiB of zeros after the program
)
TOOL_TIMEOUT = 300  # seconds for one run of one profile
NOT_PRODUCED = "This program did not produce the file wozmod needs. Check that you picked the right program for this game. The online guide lists the programs we tested."


def run_tool(
    tool: str | os.PathLike[str], identity: ProgramIdentity, encrypted: bytes, *, timeout: float = TOOL_TIMEOUT,
) -> tuple[bytes | None, str]:
    """Run the player's decryption program on the encrypted file; return (the pinned program or None, a transcript).

    Each profile for the platform is tried in turn, the one named like the program first. The program runs
    directly (no shell, arguments as a list) in a temporary folder; its output is accepted only if it matches
    the pin. Nothing is decrypted here.
    """
    tool = Path(tool).absolute()  # it runs in a temporary folder, where a relative path names nothing
    profiles = sorted((p for p in PROFILES if p[1] == identity.platform), key=lambda p: p[0] not in tool.name.lower())
    transcript: list[str] = []
    with tempfile.TemporaryDirectory(prefix="wozmod-tool-") as folder:
        source = Path(folder) / ("default.xex" if identity.platform == "x360" else "EBOOT.BIN")
        source.write_bytes(encrypted)
        for name, _, arguments in profiles:
            output, log = Path(folder) / f"{name}.out", Path(folder) / f"{name}.log"
            command = [str(tool), *arguments(str(source), str(output))]
            try:
                with log.open("wb") as stream:
                    code = process.run(command, cwd=folder, stdin=subprocess.DEVNULL, stdout=stream,
                                       stderr=subprocess.STDOUT, timeout=timeout, check=False).returncode
                result = f"exit code {code}"
            except subprocess.TimeoutExpired:
                result = f"stopped after {timeout:g} seconds"
            except OSError as error:
                transcript.append(f"{tool.name}: could not start ({error})")
                return None, "\n".join(transcript)
            data = None
            if output.is_file():
                with output.open("rb") as stream:
                    data = trim_program(stream.read(identity.size + MAX_EXTRA_BYTES + 1), identity)
            transcript.append(f"{tool.name} as {name}: {result}, " + ("output matches" if data else "output not accepted"))
            transcript.append(log.read_bytes()[:4000].decode("utf-8", "replace").strip())
            if data is not None:
                return data, "\n".join(transcript)
    return None, "\n".join(transcript)
