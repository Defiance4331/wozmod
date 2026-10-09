"""Verify player-supplied program files and run optional external tools."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


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
PSP_ENCRYPTED_PROGRAM = ProgramIdentity(
    "Encrypted PSP program", 3_198_240,
    "e7c3714e77f13a83beb0cd0586fc2340efc2214122b1d184fc72bd40c9c39d11", "psp",
)

Recipe = Literal["xex2ool", "xextool", "pspdecrypt"]
_RECIPE_PLATFORMS: dict[str, str] = {
    "xex2ool": "x360", "xextool": "x360", "pspdecrypt": "psp",
}
_XEXTOOL_PADDING = 32_768


def verify_program(path: str | os.PathLike[str], identity: ProgramIdentity) -> Path:
    """Return a read-only input path after checking its exact size and SHA-256."""
    source = Path(path)
    try:
        with source.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != identity.size:
                raise ProgramError(f"{identity.name} has the wrong size or file type")
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise ProgramError(f"Cannot read {identity.name}") from exc
    if digest != identity.sha256:
        raise ProgramError(f"{identity.name} has the wrong SHA-256")
    return source


def prepare_program(
    identity: ProgramIdentity,
    scratch_dir: str | os.PathLike[str],
    *,
    decrypted_file: str | os.PathLike[str] | None = None,
    encrypted_file: str | os.PathLike[str] | None = None,
    tool: Sequence[str | os.PathLike[str]] | None = None,
    recipe: Recipe | None = None,
) -> Path:
    """Return a verified supplied file or a canonical output in unique scratch.

    Tool prefixes are argument lists; relative prefix paths are resolved by the
    caller. Each tool receives a scratch input copy and a fresh output path.
    """
    if decrypted_file is not None:
        if encrypted_file is not None or tool is not None or recipe is not None:
            raise ProgramError("Choose either a supplied program or an external tool")
        return verify_program(decrypted_file, identity)

    if encrypted_file is None or tool is None or recipe is None:
        raise ProgramError("An encrypted input, tool argument list and recipe are required")
    if _RECIPE_PLATFORMS.get(recipe) != identity.platform:
        raise ProgramError("The recipe does not match the program platform")
    if isinstance(tool, (str, bytes)) or not tool:
        raise ProgramError("The tool must be a nonempty argument list")
    try:
        prefix = [os.fspath(part) for part in tool]
    except TypeError as exc:
        raise ProgramError("Tool arguments must be paths or strings") from exc
    if any(not isinstance(part, str) or "\0" in part for part in prefix) or not prefix[0]:
        raise ProgramError("Tool arguments must be strings without null characters")

    scratch = Path(scratch_dir).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="program-", dir=scratch))
    try:
        source = run_dir / ("input.xex" if identity.platform == "x360" else "input.bin")
        output = run_dir / ("tool-output.pe" if identity.platform == "x360" else "tool-output.prx")
        result = run_dir / ("program.pe" if identity.platform == "x360" else "eboot.prx")
        shutil.copyfile(encrypted_file, source)
        arguments = _recipe_arguments(recipe, source, output)
        completed = subprocess.run(
            [*prefix, *arguments], cwd=run_dir, shell=False,
            capture_output=True, check=False,
        )
        if completed.returncode != 0:
            raise ProgramError("The external tool did not complete successfully")
        try:
            info = output.lstat()
        except FileNotFoundError as exc:
            raise ProgramError("The external tool did not create its output") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProgramError("The external tool output must be a new regular file")
        if recipe == "xextool":
            _copy_without_padding(output, result, identity.size)
        else:
            with output.open("rb") as source_stream, result.open("xb") as destination:
                shutil.copyfileobj(source_stream, destination)
        return verify_program(result, identity)
    except OSError as exc:
        shutil.rmtree(run_dir)
        raise ProgramError("Cannot prepare the external tool input or output") from exc
    except BaseException:
        shutil.rmtree(run_dir)
        raise


def _recipe_arguments(recipe: Recipe, source: Path, output: Path) -> list[str]:
    if recipe == "xex2ool":
        return ["basefile", str(source), "-o", str(output)]
    if recipe == "xextool":
        return ["-b", str(output), str(source)]
    return ["-o", str(output), str(source)]


def _copy_without_padding(output: Path, result: Path, size: int) -> None:
    if output.stat().st_size != size + _XEXTOOL_PADDING:
        raise ProgramError("The external tool output has the wrong padded size")
    with output.open("rb") as source, result.open("xb") as destination:
        remaining = size
        while remaining:
            block = source.read(min(remaining, 1024 * 1024))
            if not block:
                raise ProgramError("The external tool output is truncated")
            destination.write(block)
            remaining -= len(block)
        padding = source.read()
        if len(padding) != _XEXTOOL_PADDING or any(padding):
            raise ProgramError("The external tool output has nonzero or truncated padding")
