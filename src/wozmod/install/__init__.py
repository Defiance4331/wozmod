"""Install and uninstall wozmod in a stock Musou Orochi Z folder from the player's own inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from multiprocessing import get_context
from pathlib import Path

MANIFEST = "wozmod/install.json"
TEXT_DATA = Path(__file__).resolve().parents[3] / "data/text/table.json"

# Stock files the build reads: (size, SHA-256) of the supported Japanese PC release.
STOCK_FILES = {
    "OROCHI_Z.exe": (3_983_716, "ab3ac6deae8d135a5b59fe1c4de6c0893f3cf1fd8dd908f3ab3124edda699b49"),
    "LINKDATA1.IDX": (76_080, "3db85bcba960df21d5ecec1a83a7ae794f3a3e016717a7637685869176b8ddef"),
    "LINKDATA1.BIN": (1_637_697_536, "faf4ba7cf019750b037234feaeefaa4d2703944c7ddc6299e3d72841ae6f479d"),
    "LINKDATA2.IDX": (12_352, "ae1e7a99b58e8875ca8b07fe4130e834c007955e1c4830980458e8d98bd1fb5b"),
    "LINKDATA2.BIN": (1_837_785_088, "fb1004797008ff84f26c7f65c5a40c672ef09abd8083534bf67ebc3ce11711b0"),
    "data/Sound/Voice/Voice.bin": (971_871_792, "6d30494571b62dc1f56983c7c84c0f834c6203714ccec089a288e1a38d62f630"),
}
# Release assets built by us; a release updates both pins with the files it ships.
LETTERING = (11_552_649, "00b7c841ebe58eef2b0d53b3af07218414a33a41f060585a1418332701114a40")
DLL = (167_387, "2ebce4c97d9ffdf1c36a6ed59ea14e7080b5e668772d696aca9ecf2ea88b4369")

_MOD_PATH = re.compile(r"d3d9\.dll|wozmod(/[A-Za-z0-9_][A-Za-z0-9_.-]*)+")


class InstallError(Exception):
    """An input, the game folder or an existing install stops the operation."""


def worker_count() -> int:
    """Worker processes for each build stage: WOZMOD_JOBS, else the CPU count."""
    value = os.environ.get("WOZMOD_JOBS", "")
    if not value:
        return os.process_cpu_count() or 1
    if not value.isdecimal() or int(value) < 1:
        raise InstallError(f"WOZMOD_JOBS must be a whole number of 1 or more, not {value!r}")
    return int(value)


@contextmanager
def _timed(log, done: str):
    start = time.perf_counter()
    yield
    log(f"{done} ({time.perf_counter() - start:.0f} s)")


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pinned(path: Path, pin: tuple[int, str]) -> str | None:
    """None when the file matches its pin, else what is wrong with it."""
    if not path.is_file():
        return "missing"
    if path.stat().st_size != pin[0] or _sha256(path) != pin[1]:
        return "not the supported file (wrong size or SHA-256)"
    return None


def check_inputs(game: Path, inputs: dict[str, Path]) -> None:
    """Raise one InstallError naming every missing or wrong input."""
    from wozmod.donors import PSP_PROGRAM, X360_PROGRAM, ProgramError, verify_program

    problems = []
    if not game.is_dir():
        problems.append(f"--game: {game} is not a folder")
    else:
        for name, pin in STOCK_FILES.items():
            if wrong := _pinned(game / name, pin):
                problems.append(f"--game: {name} is {wrong}; the stock Japanese PC release is required")
    for flag in ("wo1", "x360", "psp"):
        if not inputs[flag].is_file():
            problems.append(f"--{flag}: disc image {inputs[flag]} not found")
    for flag, identity in (("x360-exe", X360_PROGRAM), ("psp-eboot", PSP_PROGRAM)):
        try:
            verify_program(inputs[flag], identity)
        except ProgramError as error:
            problems.append(f"--{flag}: {error} (an already decrypted file is required)")
    for flag, pin in (("lettering", LETTERING), ("dll", DLL)):
        if wrong := _pinned(inputs[flag], pin):
            problems.append(f"--{flag}: {inputs[flag]} is {wrong}")
    if shutil.which("ffmpeg") is None:
        problems.append("ffmpeg was not found on PATH; the voice build needs it")
    if problems:
        raise InstallError("\n".join(problems))


def _read_manifest(game: Path) -> dict[str, str]:
    path = game / MANIFEST
    try:
        files = json.loads(path.read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InstallError(f"{path} is unreadable: {error}") from error
    for name, digest in files.items():
        if (not _MOD_PATH.fullmatch(name) or name == MANIFEST or ".." in name.split("/")
                or not re.fullmatch("[0-9a-f]{64}", str(digest))):
            raise InstallError(f"{path} lists {name!r}, which the installer never writes; nothing was removed")
    return files


def _prune(game: Path, names) -> None:
    """Remove the now-empty folders that held these files, deepest first."""
    folders = {parent for name in names for parent in (game / name).parents
               if parent.is_relative_to(game / "wozmod")}
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass


def _remove(game: Path, files: dict[str, str]) -> dict[str, str]:
    """Delete each listed file whose SHA-256 still matches; return the changed ones."""
    changed = {}
    for name, digest in files.items():
        path = game / name
        if not path.is_file():
            continue
        if _sha256(path) == digest:
            path.unlink()
        else:
            changed[name] = digest
    return changed


def _write_manifest(game: Path, files: dict[str, str]) -> None:
    path = game / MANIFEST
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"format": 1, "files": dict(sorted(files.items()))}, indent=1) + "\n")
    os.replace(temporary, path)


def uninstall(game: Path, log=print) -> dict[str, str]:
    """Delete the files the install wrote; return (and keep listed) any the player changed."""
    game = Path(game)
    if not (game / MANIFEST).is_file():
        raise InstallError(f"no wozmod install in {game} ({MANIFEST} is missing)")
    files = _read_manifest(game)
    changed = _remove(game, files)
    if changed:
        _write_manifest(game, changed)
        for name in changed:
            log(f"changed since install, left in place: {name}")
    else:
        (game / MANIFEST).unlink()
    _prune(game, [*files, MANIFEST])
    log(f"removed {len(files) - len(changed)} of {len(files)} installed files")
    return changed


def _extract(flag: str, image: Path, donor, destination: Path, files) -> None:
    from wozmod.donors import extract_donor

    try:
        extract_donor(image, donor, destination, files=files)
    except (OSError, ValueError) as error:  # DiscError is a ValueError
        raise InstallError(f"--{flag}: {image} is not the supported disc image ({error})") from error


def _text_table(game: Path, donors: Path, table: Path) -> None:
    from wozmod.text import DonorResolver, build_table

    build_table(game, DonorResolver(donors), TEXT_DATA, table)


def _build(game: Path, inputs: dict[str, Path], staging: Path, log, jobs: int) -> Path:
    """Build every mod file into staging/out (as laid out in the game folder) and return it."""
    from wozmod.art.compose import compose
    from wozmod.donors import PSP, WO1, X360
    from wozmod.text.inputs import WO1_TEXT_PROGRAM
    from wozmod.voices import VOICE_DONOR_FILES, build_voice_banks

    donors, out = staging / "donors", staging / "out"
    with _timed(log, "read the disc images"):
        log("reading the WO1 disc image")
        _extract("wo1", inputs["wo1"], WO1, donors / "wo1", VOICE_DONOR_FILES)
        _extract("wo1", inputs["wo1"], WO1_TEXT_PROGRAM, donors / "wo1", None)
        log("reading the X360 disc image")
        _extract("x360", inputs["x360"], X360, donors / "wo2-x360", VOICE_DONOR_FILES)
        log("reading the PSP disc image")
        _extract("psp", inputs["psp"], PSP, donors / "wo2-psp", ["PSP_GAME/USRDIR/LINKDATA_NA.FNS"])
        shutil.copyfile(inputs["x360-exe"], donors / "wo2-x360/default.pe")
        shutil.copyfile(inputs["psp-eboot"], donors / "wo2-psp/eboot.prx")

    # The three builds are independent: run them side by side, each in its own process.
    log("building the text table, the voice banks and the menu art")
    start = time.perf_counter()
    with ProcessPoolExecutor(3, mp_context=get_context("spawn")) as pool:
        stages = {
            pool.submit(_text_table, game, donors, out / "wozmod/table.bin"): "the text table",
            pool.submit(build_voice_banks, game, donors, out / "wozmod", jobs): "the voice banks",
            pool.submit(compose, game, {"wo1": donors / "wo1/linkdata_ens", "x360": donors / "wo2-x360/linkdata_ens"},
                        inputs["lettering"], staging / "art", True, jobs): "the menu art",
        }
        for future in as_completed(stages):
            future.result()
            log(f"built {stages[future]} ({time.perf_counter() - start:.0f} s)")
    art = staging / "art/wozmod"
    for name in ("entries", "hires.bin", "prompts-xbox.bin"):
        os.replace(art / name, out / "wozmod" / name)
    shutil.copyfile(inputs["dll"], out / "d3d9.dll")
    return out


def install(game: Path, inputs: dict[str, Path], force: bool = False, log=print) -> dict[str, str]:
    """Build wozmod into game; return the written files with their SHA-256."""
    game = Path(game)
    old = _read_manifest(game) if (game / MANIFEST).is_file() else {}
    if (game / MANIFEST).is_file() and not force:
        raise InstallError(f"wozmod is already installed in {game}; uninstall it first or pass --force")
    foreign = [p.relative_to(game).as_posix() for p in [game / "d3d9.dll", *(game / "wozmod").rglob("*")]
               if p.is_file() and p.relative_to(game).as_posix() not in {*old, MANIFEST, "wozmod/settings.ini"}]
    if foreign:
        raise InstallError("these files were not written by the installer; move them away first:\n"
                           + "\n".join(foreign))
    jobs = worker_count()
    with _timed(log, "checked the inputs"):
        check_inputs(game, inputs)

    staging = Path(tempfile.mkdtemp(prefix=".wozmod-build-", dir=game))
    written: list[str] = []
    try:
        out = _build(game, inputs, staging, log, jobs)
        with _timed(log, "hashed the built files"):
            files = {p.relative_to(out).as_posix(): _sha256(p) for p in sorted(out.rglob("*")) if p.is_file()}
        if old:
            _remove(game, old)
        try:
            for name in files:
                (game / name).parent.mkdir(parents=True, exist_ok=True)
                os.replace(out / name, game / name)
                written.append(name)
            _write_manifest(game, files)
        except BaseException:
            for name in written:
                (game / name).unlink(missing_ok=True)
            _prune(game, written)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log(f"installed {len(files)} files; {MANIFEST} lists them")
    return files
