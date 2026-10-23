"""Install and uninstall wozmod in a stock Musou Orochi Z folder from the player's own inputs."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import partial
from multiprocessing import get_context
from pathlib import Path

from wozmod import __version__ as VERSION
from wozmod import progress as reporting
from wozmod import process, remembered, resources
from wozmod.donors.discs import DISCS, DonorsFolder
from wozmod.progress import Cancelled, Progress

MANIFEST = "wozmod/install.json"
RUNTIME_FILES = ("wozmod/settings.ini", "wozmod/runtime.log", "wozmod/runtime.crash.log")  # written by the game, not by the installer
TEXT_DATA = resources.root() / "data/text/table.json"
DUB_CAPTIONS = resources.root() / "data/text/dub_captions.json"

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
LETTERING = (18_399_206, "5c45d5a0d200c3fe55f31140285f9ac6c22cff172850a79484fe3dbb5a4c6ec0")
DLL = (222_397, "5f974c193c870d8156a08ae9b6ee0c8ddf6023e1deaea05818f4d6ce6865ff11")

_MOD_PATH = re.compile(r"d3d9\.dll|wozmod(/[A-Za-z0-9_][A-Za-z0-9_.-]*)+")


class InstallError(Exception):
    """An input, the game folder or an existing install stops the operation."""


GIB = 1 << 30
PARENT_MEMORY = 4 * GIB  # the art stage's main process peaks at 3.2-3.7 GB whatever the worker count
WORKER_MEMORY = GIB // 2  # one art worker plus its share of the ffmpeg batches, measured at about 0.25 GB


def _memory() -> tuple[int, int] | None:
    """(total, available) physical memory in bytes, or None when the system does not say."""
    try:
        if os.name == "nt":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                            ("page_total", ctypes.c_ulonglong), ("page_available", ctypes.c_ulonglong),
                            ("virtual_total", ctypes.c_ulonglong), ("virtual_available", ctypes.c_ulonglong),
                            ("extended", ctypes.c_ulonglong)]
            status = Status(length=ctypes.sizeof(Status))
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return status.total, status.available
        fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines())
        return int(fields["MemTotal"].split()[0]) * 1024, int(fields["MemAvailable"].split()[0]) * 1024
    except (OSError, KeyError, ValueError, AttributeError):
        return None


def default_workers(cpus: int, memory: tuple[int, int] | None) -> int:
    """Workers that leave the player one core and a quarter of RAM (at least 2 GiB) free."""
    workers = max(1, cpus - 1)
    if memory:
        total, available = memory
        spare = available - max(total // 4, 2 * GIB) - PARENT_MEMORY
        workers = min(workers, max(1, spare // WORKER_MEMORY))
    return workers


def worker_count() -> int:
    """Worker processes for each build stage: WOZMOD_JOBS, else what the CPUs and free RAM allow."""
    value = os.environ.get("WOZMOD_JOBS", "")
    if not value:
        return default_workers(os.process_cpu_count() or 1, _memory())
    if not value.isdecimal() or int(value) < 1:
        raise InstallError(f"WOZMOD_JOBS must be a whole number of 1 or more, not {value!r}")
    return int(value)


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


@dataclass(frozen=True)
class Input:
    """A path the player gives, and how the command line, the setup window and the install name it."""
    label: str  # error lines, the terminal's prompts and the window's row
    help: str | None  # --help; None hides the option
    kind: str | None = None  # the window's picker: folder, image or file; None keeps it out of the window
    note: str = ""  # the window's line under the row: what to pick
    row: str | None = None  # the window's row, where it differs from label
    disc: str | None = None  # an already decrypted program file: its disc, and the program input it stands in for
    tool: str | None = None


def _decrypted(disc: str, tool: str, what: str, help_what: str, note: str) -> Input:
    title = DISCS[disc].title
    return Input(f"Already decrypted {title} {what}", f"advanced: in place of --{tool}, the {title} {help_what} already "
                 "decrypted", "file", note, disc=disc, tool=tool)


# Every input, once, in the order the command line, the terminal and the window list them.
INPUTS = {
    "game": Input("Game folder", "the stock Musou Orochi Z folder", "folder",
                  "The folder that contains the game's .exe (the Japanese PC release).", "Musou Orochi Z folder"),
    **{name: Input(f"{disc.title} disc image", f"{disc.title} disc image", "image", "The .iso file made from this disc.",
                   disc.title) for name, disc in DISCS.items()},
    "x360-tool": Input("Xbox 360 decryption program",
                       "a decryption program you already have, for the default.xex on both Xbox 360 discs", "file",
                       "A decryption program you already have for default.xex, the encrypted file on both Xbox 360 discs."),
    "psp-tool": Input("PSP decryption program", "a decryption program you already have, for the PSP EBOOT.BIN", "file",
                      "A decryption program you already have for EBOOT.BIN, the PSP game's encrypted file."),
    "wo2-x360-file": _decrypted("wo2-x360", "x360-tool", "program file", "program file",
                            "The decrypted program inside its default.xex (about 18 MB). Use it instead of the program above."),
    "wo1-x360-file": _decrypted("wo1-x360", "x360-tool", "program file", "program file",
                                "The decrypted program inside its default.xex (about 17 MB). Use it instead of the program above."),
    "wo2-psp-file": _decrypted("wo2-psp", "psp-tool", "EBOOT", "EBOOT.BIN",
                           "The decrypted PSP_GAME/SYSDIR/EBOOT.BIN. Use it instead of the program above."),
    "lettering": Input("lettering.zip", None),  # for development: the release lettering bundle
    "dll": Input("d3d9.dll", None),  # for development: the release d3d9.dll
}
# disc image input: (the encrypted file inside the image, the decryption program input the player has for it,
# the alternative input: that file already decrypted, checked against the pin and never run). One program serves
# both Xbox 360 discs.
FILES = {disc: file for disc in DISCS for file, each in INPUTS.items() if each.disc == disc}
PROGRAMS = {disc: (DISCS[disc].program.member, INPUTS[file].tool, file) for disc, file in FILES.items()}
TOOLS = {tool: tuple(disc for disc, (_, each, _) in PROGRAMS.items() if each == tool) for _, tool, _ in PROGRAMS.values()}
FILE_PROGRAMS = {file: disc for disc, file in FILES.items()}
NEITHER = "Pick a decryption program, or a decrypted file under Advanced."
WRONG_FILE = ("This is not the decrypted file wozmod needs. Pick the file your decryption program made, "
              "or give wozmod the program instead.")
FILE_IN_IMAGE = "Not needed: your disc image already has the decrypted file. Clear this box."
FILE_AND_PROGRAM = "You also gave the decryption program. Give one or the other: clear this box or the program."
IMAGE_FIRST = "Pick the right disc image first."
_TOOL_RUNS: dict[tuple, tuple[bytes | None, str]] = {}  # one run per program and image, whoever asks (window, check, build)
_TOOL_LOCK = threading.Lock()


def _trimmed(disc: str, path: Path | str) -> bytes | None:
    """The pinned decrypted program when this file is it (extra trailing bytes allowed); else None. Nothing runs."""
    from wozmod.donors import read_pinned

    try:
        return read_pinned(path, DISCS[disc].program.plain)
    except OSError:
        return None


def program_in_image(disc: str, image: Path | str) -> bytes | None:
    """The decrypted program the disc image already holds, or None."""
    from wozmod.donors import read_disc_member, trim_program

    try:
        data = read_disc_member(Path(image), DISCS[disc].donor, DISCS[disc].program.member)
    except (OSError, ValueError):
        return None
    return None if data is None else trim_program(data, DISCS[disc].program.plain)


def resolve_program(disc: str, inputs: dict, log=None) -> tuple[str, bytes | str]:
    """(the input the outcome belongs to, the pinned program of this disc image or the plain problem).

    Each disc takes one source: a decrypted copy in the disc image, a decrypted file, or the decryption program.
    A decrypted file given beside either other source is refused on its own row. Only the program runs anything.
    """
    _, tool, file_input = PROGRAMS[disc]
    image, file = inputs.get(disc), inputs.get(file_input)
    held = program_in_image(disc, image) if image and not check_input(disc, image) else None
    if file:
        if held is not None:
            return file_input, FILE_IN_IMAGE
        if inputs.get(tool):
            return file_input, FILE_AND_PROGRAM
        return file_input, check_input(file_input, file) or _trimmed(disc, file)
    if held is not None:
        return disc, held
    return tool, decrypt_with_tool(disc, inputs.get(tool), image, log)


def decrypt_with_tool(disc: str, tool: Path | str | None, image: Path | str | None, log=None) -> bytes | str:
    """The pinned program, made by running the player's decryption program on the encrypted file from the disc
    image; or the plain reason it cannot be made. The result is kept, so the window and the install run it once."""
    from wozmod.donors import NOT_PRODUCED, read_disc_member, run_tool, trim_program

    member = DISCS[disc].program.member
    if not tool:
        return NEITHER
    if not image or check_input(disc, image):
        return IMAGE_FIRST
    tool, image = Path(tool), Path(image)
    if not tool.is_file():
        return "File not found."
    try:
        data = read_disc_member(image, DISCS[disc].donor, member)
    except (OSError, ValueError):
        return "The disc image cannot be read."
    decrypted, encrypted = DISCS[disc].program.plain, DISCS[disc].program.encrypted
    if data is None or data != trim_program(data, encrypted):
        return f"{member.rsplit('/', 1)[-1]} on the disc image is not the supported file."
    key = (disc, tool, image, tool.stat().st_mtime_ns, image.stat().st_mtime_ns)
    with _TOOL_LOCK:  # the input check (on its own thread) and the build ask at once: run the program once
        if key not in _TOOL_RUNS:
            if len(_TOOL_RUNS) >= 4:
                _TOOL_RUNS.clear()
            _TOOL_RUNS[key] = run_tool(tool, decrypted, data)
        program, transcript = _TOOL_RUNS[key]
    if log:
        log(transcript)
    return program if program is not None else NOT_PRODUCED


@dataclass(frozen=True)
class ProgramStatus:
    """One decryption program input's verdict. The install, the command line and the window all word theirs from it."""

    covered: str | None  # None while a disc it serves has no usable decrypted file, so the program is needed; else
    # "disc" when each disc image holds the file, "file" when a decrypted file under Advanced stands in
    problems: dict[str, str]  # each plain problem on the input that caused it: the program (also when it is given
    # but not needed), a decrypted file, or a disc image the program cannot be checked against (IMAGE_FIRST)
    made: bool  # the program made the file for at least one disc

    def verdict(self, tool: str) -> str | None:
        """What the program's own row says: its own problem, else that a disc image it needs is wrong."""
        return self.problems.get(tool) or (IMAGE_FIRST if IMAGE_FIRST in self.problems.values() else None)


def file_problem(file: str, inputs: dict) -> str | None:
    """The problem a given decrypted file input shows, worded as `program_status` words it. Nothing runs."""
    found = resolve_program(FILE_PROGRAMS[file], inputs)[1]
    return found if isinstance(found, str) else None


def program_status(tool: str, inputs: dict, log=None) -> ProgramStatus:
    """Check each disc the program serves on its own, in `resolve_program`'s order. The program runs (once, and the
    result is kept) only on a disc whose image holds no decrypted copy and that has no decrypted file given."""
    problems, covered, made = {}, [], False
    for disc in TOOLS[tool]:
        source, found = resolve_program(disc, inputs, log)
        if found == IMAGE_FIRST:  # the program cannot be checked here: the problem is the image's
            source = disc
        if found == FILE_IN_IMAGE:  # the image's copy stands; only the file row is wrong
            covered.append("disc")
        if isinstance(found, str):
            problems.setdefault(source, found)
        elif source == tool:
            made = True
        else:
            covered.append("disc" if source == disc else "file")
    whole = len(covered) == len(TOOLS[tool])
    if whole and inputs.get(tool):  # a file covers a disc only with no program given, so here each image holds it
        problems[tool] = ("Not needed: your disc images already have the decrypted files. Clear this box."
                          if len(TOOLS[tool]) > 1 else FILE_IN_IMAGE)
    return ProgramStatus(("disc" if set(covered) == {"disc"} else "file") if whole else None, problems, made)


def check_input(name: str, path: Path | str) -> str | None:
    """None when this input is the supported file or folder; else one plain reason.

    The setup window and `check_inputs` both call this, so they never disagree.
    """
    path = Path(path)
    if name == "game":
        if not path.is_dir():
            return "Not a folder. Pick the folder that contains the game's .exe."
        with ThreadPoolExecutor(len(STOCK_FILES)) as pool:  # hashlib lets go of the GIL: hash the big files side by side
            results = list(pool.map(lambda item: _pinned(path / item[0], item[1]), STOCK_FILES.items()))
        for stock, wrong in zip(STOCK_FILES, results, strict=True):
            if wrong:
                return (f"{stock} is missing. Pick the folder that contains the game's .exe (the Japanese PC release)."
                        if wrong == "missing" else
                        f"{stock} is not the stock Japanese PC release. Reinstall the game, then pick its folder.")
        return None
    if name in DISCS:
        from wozmod.donors import check_donor_image

        if not path.is_file():
            return "File not found. Pick the .iso file again."
        try:
            check_donor_image(path, DISCS[name].donor)
        except (OSError, ValueError):
            return f"Not the {INPUTS[name].label}. Pick the .iso made from that disc."
        return None
    if name in FILE_PROGRAMS:  # an already decrypted file: read, never run
        if not path.is_file():
            return "File not found. Pick the file again."
        return None if _trimmed(FILE_PROGRAMS[name], path) is not None else WRONG_FILE
    pin = {"lettering": LETTERING, "dll": DLL}[name]
    if wrong := _pinned(path, pin):
        return "File not found. Download wozmod again." if wrong == "missing" else "Not the file that goes with this release. Download wozmod again."
    return None


def check_inputs(game: Path, inputs: dict[str, Path | None], log=None) -> None:
    """Raise one InstallError naming every missing or wrong input.

    The two files of the release download come first, on their own: a wrong download is named in milliseconds,
    not after the seconds the game folder's hashes take. Every other wrong input is then named in one error.
    """
    if wrong := [f"{INPUTS[name].label}: {problem}" for name in ("lettering", "dll")
                 if (problem := check_input(name, inputs[name]))]:
        raise InstallError("\n".join(wrong))
    problems = {"game": check_input("game", game)}
    for name in DISCS:
        problems[name] = check_input(name, inputs[name])
    for tool in TOOLS:  # a wrong disc image is named once, on the image: its key is already set above
        for source, problem in program_status(tool, inputs, log).problems.items():
            problems.setdefault(source, problem)
    lines = [f"{INPUTS[name].label}: {problem}" for name, problem in problems.items() if problem]
    if resources.ffmpeg() is None:
        lines.append("ffmpeg was not found on PATH; the voice build needs it")
    if lines:
        raise InstallError("\n".join(lines))


def read_manifest(game: Path) -> dict[str, str]:
    """The install record's files and their SHA-256, refused (InstallError) when unreadable or naming a file the
    installer never writes."""
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


def _write_manifest(game: Path, files: dict[str, str], version: str | None = VERSION) -> None:
    path = game / MANIFEST
    temporary = path.with_name(path.name + ".tmp")
    record = {"format": 1, **({"version": version} if version else {}), "files": dict(sorted(files.items()))}
    temporary.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def installed_version(game: Path) -> str | None:
    """The wozmod version that wrote the install record, or None for a record that does not say."""
    try:
        version = json.loads((Path(game) / MANIFEST).read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return version if isinstance(version, str) else None


def existing_install(game: Path) -> str | None:
    """The wozmod version that installed here; "0" for a record written before versions were recorded;
    None when wozmod is not installed."""
    return (installed_version(game) or "0") if (Path(game) / MANIFEST).is_file() else None


def _version_key(version: str | None) -> tuple[int, ...] | None:
    if version is None:
        return (0,)  # records written before versions were recorded
    parts = version.split(".")
    return tuple(int(p) for p in parts) if all(p.isascii() and p.isdecimal() for p in parts) else None


def install_state(game: Path) -> tuple[str, str | None]:
    """How an install here would go: ("none" | "update" | "reinstall" | "downgrade", the installed version).

    "downgrade" (a newer version, or one this wozmod cannot read) is the only state that needs consent.
    """
    game = Path(game)
    if not (game / MANIFEST).is_file():
        return "none", None
    read_manifest(game)  # an unreadable record stops here, with its own message
    found = installed_version(game)
    have, now = _version_key(found), _version_key(VERSION)
    if found == VERSION:
        return "reinstall", found
    return ("update" if have is not None and have < now else "downgrade"), found


def uninstall(game: Path, keep_settings: bool = True, log=print) -> dict[str, str]:
    """Delete the files the install wrote; return (and keep listed) any the player changed.

    keep_settings=False also deletes the files the game wrote (settings and log), so a stock folder is left.
    """
    game = Path(game)
    if not (game / MANIFEST).is_file():
        raise InstallError(f"no wozmod install in {game} ({MANIFEST} is missing)")
    files = read_manifest(game)
    version = installed_version(game)
    changed = _remove(game, files)
    if changed:
        _write_manifest(game, changed, version)
        for name in changed:
            log(f"changed since install, left in place: {name}")
    else:
        (game / MANIFEST).unlink()
    if not keep_settings:
        for name in RUNTIME_FILES:
            (game / name).unlink(missing_ok=True)
        log("removed your wozmod settings")
    _prune(game, [*files, MANIFEST])
    log(f"removed {len(files) - len(changed)} of {len(files)} installed files")
    return changed


def _extract(flag: str, image: Path, donor, destination: Path, files, label: str, lo: float, hi: float,
             failed=lambda: None) -> None:
    from wozmod.donors import extract_donor

    def reading(done: int, total: int) -> None:
        reporting.report("discs", label, lo + (hi - lo) * done / total)
        failed()  # raises as soon as the input checks found a wrong input, so the read stops there

    try:
        extract_donor(image, donor, destination, files=files, on_bytes=reading)
    except (OSError, ValueError) as error:  # DiscError is a ValueError
        raise InstallError(f"{INPUTS[flag].label}: {image} is not the supported disc image ({error})") from error


def _place_programs(inputs: dict[str, Path | None], donors: Path) -> None:
    """Put the decrypted program files, made by the player's programs, where the text build reads them."""
    for disc in DISCS:
        source, found = resolve_program(disc, inputs)
        if isinstance(found, str):
            raise InstallError(f"{INPUTS[source].label}: {found}")
        DonorsFolder(donors).stage_program(disc, found)


def _text_table(game: Path, donors: Path, out: Path) -> None:
    from wozmod.text import DonorResolver
    from wozmod.text.build import build_table
    from wozmod.text.dub_captions import build_dub_schedules

    resolver = DonorResolver(donors)
    build_table(game, resolver, TEXT_DATA, out / "wozmod/table.bin")
    build_dub_schedules(resolver, DUB_CAPTIONS, out)


def _menu_art(game: Path, donors: Path, lettering: Path, staging: Path, out: Path, jobs: int) -> None:
    """The art stage: build the menu art in staging and move all of it into out/wozmod."""
    from wozmod.art.build import ArtError
    from wozmod.art.compose import compose

    folders = DonorsFolder(donors)
    try:
        art = compose(game, {"wo1-x360": folders.folder("wo1-x360") / "linkdata_ens",
                             "wo2-x360": folders.folder("wo2-x360") / "linkdata_ens", "text": donors},
                      lettering, staging, True, jobs)
    except ArtError as error:
        disc = next((f"{disc.title} disc: " for disc in DISCS.values() if disc.donor == error.donor), "")
        raise InstallError(f"Menu art: {disc}{error}") from error
    (out / "wozmod").mkdir(parents=True, exist_ok=True)
    for built in art.iterdir():
        os.replace(built, out / "wozmod" / built.name)


def _worker_start(events, stop, log_path=None) -> None:
    """Runs first in each build worker: its progress events go to the parent, and `stop` cancels it.

    What the build code prints goes to the log file, not to the terminal or the window.
    """
    reporting.attach(events.put, stop)
    if hasattr(os, "setpgrp"):
        os.setpgrp()  # the worker and what it starts (ffmpeg, a pool of its own) can be killed together
    if log_path:
        log = open(log_path, "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = log
        for descriptor in (1, 2):  # also what a child process or native code writes
            os.dup2(log.fileno(), descriptor)


def _voice_banks(game: Path, donors: Path, out: Path, jobs: int) -> None:
    """The voice stage, at a lower priority than the art stage: the banks go below out/wozmod.

    The voices are many independent ffmpeg runs that can fill any gap; the art stage has long serial steps
    that set the install time. ffmpeg inherits the lower priority. The batch policy also saves work: ffmpeg
    hands every 64-sample audio frame to its encoder thread, and without wake-up preemption it makes far fewer
    thread switches (measured: 25 % less system time, 8 % less user time).
    """
    from wozmod.voices import build_voice_banks

    if hasattr(os, "nice"):
        os.nice(VOICE_NICE)
    if hasattr(os, "sched_setscheduler"):  # Linux; threads and child processes started from here inherit it
        try:
            os.sched_setscheduler(0, os.SCHED_BATCH, os.sched_param(0))
        except OSError:
            pass  # the system does not allow it: the voices only take a little longer
    build_voice_banks(game, donors, out / "wozmod", jobs)


def _drain(events, progress: Progress) -> None:
    while True:
        try:
            progress.apply(events.get_nowait())
        except queue.Empty:
            return


VOICE_NICE = 10  # how much lower than the art stage the voice stage runs (Unix)
GRACE = 10  # seconds the build workers get to stop by themselves before they are killed


def _kill_tree(worker) -> None:
    """Kill a build worker and everything it started."""
    if hasattr(os, "killpg"):
        try:
            os.killpg(worker.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    elif os.name == "nt":
        process.run(["taskkill", "/F", "/T", "/PID", str(worker.pid)], capture_output=True, check=False)
    worker.kill()


def _stop_workers(pool, futures, stop) -> None:
    """Ask the build workers to stop, give them GRACE seconds, then kill what is left."""
    stop.set()
    wait(futures, timeout=GRACE)
    processes = list((getattr(pool, "_processes", None) or {}).values())  # shortcut: no public way to kill a pool
    pool.shutdown(wait=False, cancel_futures=True)
    for worker in processes:
        _kill_tree(worker)
    for worker in processes:
        worker.join(5)


def _run_stages(pool, futures: dict, events, stop, progress: Progress) -> None:
    pending, cancelled_at = dict(futures), None
    while pending:
        done, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
        _drain(events, progress)
        for future in done:
            future.result()  # a stage that failed or was cancelled raises here
            progress.finish(pending.pop(future))
        if progress.cancel.is_set():
            stop.set()
            cancelled_at = cancelled_at or time.monotonic()
            if time.monotonic() - cancelled_at > GRACE:
                raise Cancelled()


def _build(game: Path, inputs: dict[str, Path], staging: Path, progress: Progress, jobs: int, checked,
           cache=None) -> Path:
    """Build every mod file into staging/out (as laid out in the game folder) and return it.

    The input checks run while the discs are read; `checked()` waits for them and raises when an input is wrong,
    and `checked(wait=False)` raises only once they are done, so a wrong input stops the read at once.

    `cache` is for developer tools only (tools/build_cache.py); the installer never passes one. Its
    `fetch(step, sources, folder)` fills folder with what a build of that step from these sources wrote and returns
    True, or returns False; `keep(step, sources, folder)` saves what a build wrote. Each step's folder is laid out
    as the game folder, but for "movies", whose folder is wozmod/movie. Nothing is kept before the checks pass.
    """
    from wozmod.movies import movie_donors
    from wozmod.text.dub_captions import DUB_SCHEDULE_FILES
    from wozmod.voices import PSP_VOICE_FILES, VOICE_DONOR_FILES

    donors, out = staging / "donors", staging / "out"
    folders = DonorsFolder(donors)
    sources = {"game": game, "donors": donors, **inputs}
    movies_kept = cache is not None and cache.fetch("movies", sources, donors / "movie")
    reads = {"wo1-x360": VOICE_DONOR_FILES + DUB_SCHEDULE_FILES, "wo2-x360": VOICE_DONOR_FILES,
             "wo2-psp": PSP_VOICE_FILES}
    plan = [(disc, DISCS[disc].donor, folders.folder(disc), files, f"reading the {DISCS[disc].title} disc")
            for disc, files in reads.items()]
    if not movies_kept:
        plan += [(name, donor, donors / "movie", None, "copying the English movies")
                 for name, donor in movie_donors().items()]
    sizes = [sum(m.size for m in donor.files if files is None or m.path in files) for _, donor, _, files, _ in plan]
    progress.begin("discs")
    offset, unread = 0, None
    try:
        for (flag, donor, folder, files, label), size in zip(plan, sizes, strict=True):
            _extract(flag, inputs[flag], donor, folder, files, label,
                     offset / sum(sizes), (offset + size) / sum(sizes), lambda: checked(wait=False))
            offset += size
    except InstallError as error:
        unread = error
    checked()  # a wrong input is named before a disc that failed to read
    if unread:
        raise unread
    _place_programs(inputs, donors)
    if cache is not None and not movies_kept:
        cache.keep("movies", sources, donors / "movie")
    progress.finish("discs")
    progress.checkpoint()

    # The three builds are independent: run them side by side, each in its own process. Each writes to the
    # folder it is given, laid out as the game folder.
    progress.begin("build")
    builds = {"text": partial(_text_table, game, donors), "voices": partial(_voice_banks, game, donors, jobs=jobs),
              "art": partial(_menu_art, game, donors, inputs["lettering"], staging / "art", jobs=jobs)}
    targets = dict.fromkeys(builds, out) if cache is None else {name: staging / "steps" / name for name in builds}
    kept = [] if cache is None else [name for name in builds if cache.fetch(name, sources, targets[name])]
    context = get_context("spawn")
    events, stop = context.Queue(), context.Event()
    log_path = remembered.settings_path().with_name("install.log")  # one folder with the remembered paths
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
    except OSError:
        log_path = None  # no log is better than no install
    with ProcessPoolExecutor(3, mp_context=context, initializer=_worker_start,
                             initargs=(events, stop, log_path)) as pool:
        futures = {pool.submit(build, targets[name]): name for name, build in builds.items() if name not in kept}
        for key, text in (("text", "building the text table"), ("voices", "reading the voice banks"),
                          ("art", "preparing the menu art")):
            progress.begin(key, text)
        for name in kept:
            progress.finish(name)
        try:
            _run_stages(pool, futures, events, stop, progress)
        except BaseException:
            _stop_workers(pool, futures, stop)
            raise
    if cache is not None:
        for name, folder in targets.items():
            if name not in kept:
                cache.keep(name, sources, folder)
            for built in sorted(p for p in folder.rglob("*") if p.is_file()):
                target = out / built.relative_to(folder)
                if target.exists():
                    raise InstallError(f"two build steps wrote {built.relative_to(folder).as_posix()}")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(built, target)
    progress.finish("build")
    os.replace(donors / "movie", out / "wozmod/movie")
    shutil.copyfile(inputs["dll"], out / "d3d9.dll")
    return out


def install(game: Path, inputs: dict[str, Path], force: bool = False, log=print,
            progress: Progress | None = None, cache=None) -> dict[str, str]:
    """Build wozmod into game; return the written files with their SHA-256.

    `cache` reuses unchanged build steps on developer machines (see _build); players never have one.

    `progress` carries the listener that shows the steps and the cancel flag. A cancel leaves the game
    folder as it was, unless the files are already being placed: then the install finishes.
    """
    game = Path(game)
    progress = progress or Progress()
    old = read_manifest(game) if (game / MANIFEST).is_file() else {}
    state, found = install_state(game)
    if state == "downgrade" and not force:
        raise InstallError(f"wozmod {found or 'of an unknown version'} is installed in {game}. This is wozmod "
                           f"{VERSION}, so installing would go back to an older version. Pass --force to do that")
    foreign = [p.relative_to(game).as_posix() for p in [game / "d3d9.dll", *(game / "wozmod").rglob("*")]
               if p.is_file() and p.relative_to(game).as_posix() not in {*old, MANIFEST, *RUNTIME_FILES}]
    if foreign:
        raise InstallError("these files were not written by the installer; move them away first:\n"
                           + "\n".join(foreign))
    jobs = worker_count()
    reporting.attach(progress.apply, progress.cancel)
    try:
        progress.begin("check")
        checker = ThreadPoolExecutor(1)  # hashing the inputs overlaps with reading the discs; nothing but the build folder changes meanwhile
        check = checker.submit(check_inputs, game, inputs, log)
        done = []

        def checked(wait: bool = True) -> None:
            if not wait and not check.done():
                return
            check.result()  # raises the InstallError for a wrong input
            if not done:
                done.append(progress.finish("check"))
            progress.checkpoint()

        try:
            staging = Path(tempfile.mkdtemp(prefix=".wozmod-build-", dir=game))
        except OSError:
            checked()  # a game folder that is not there is named by the checks
            raise
        written: list[str] = []
        try:
            out = _build(game, inputs, staging, progress, jobs, checked, cache=cache)
            checked()
            progress.checkpoint()  # the last place to cancel: below, the files are put in place
            progress.begin("place")
            files = {p.relative_to(out).as_posix(): _sha256(p) for p in sorted(out.rglob("*")) if p.is_file()}
            # An installed file with the same bytes stays in place: rewriting it costs its size in new disk blocks.
            same = {name for name, digest in files.items()
                    if old.get(name) == digest and (game / name).is_file() and _sha256(game / name) == digest}
            _remove(game, {name: digest for name, digest in old.items() if name not in same})
            try:
                for name in [name for name in files if name not in same]:
                    (game / name).parent.mkdir(parents=True, exist_ok=True)
                    os.replace(out / name, game / name)
                    written.append(name)
                _write_manifest(game, files)
            except BaseException:
                for name in written:
                    (game / name).unlink(missing_ok=True)
                _prune(game, written)
                raise
            progress.finish("place")
        finally:
            checker.shutdown()
            shutil.rmtree(staging, ignore_errors=True)
    finally:
        reporting.attach(None, None)
    progress.end()
    log(f"Placed {len(files)} files in the game folder.")
    return files
