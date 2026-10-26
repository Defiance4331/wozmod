"""Check the pieces of the game files that wozmod's build reads, so other mods can stay installed beside it.

data/stock_pieces.json (written by tools/stock_pieces.py) lists each piece by where it is and its SHA-256, with the
build steps that read it and whether wozmod serves its own in its place:
- spans of OROCHI_Z.exe, by file offset;
- LINKDATA1 and LINKDATA2 entries, found through the player's own .IDX (another mod may have resized entries);
- Voice.bin clips, found through the player's own voice index (LINKDATA1 entry 4058).

An entry every step reads unpacked also passes when its unpacked bytes match, so a repacked entry is fine.
"""

from __future__ import annotations

import hashlib
import json
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from wozmod import resources
from wozmod.formats import pe
from wozmod.formats.linkdata import LZP2_MAGIC, Archive, ArchiveError, align, decoded_member, parse_index, parse_lzp2

PIECES = resources.root() / "data/stock_pieces.json"
STEPS = ("text", "voices", "art")  # the installer's build steps (install._build), in the order messages name them
PURPOSE = {"text": "text", "voices": "voices", "art": "menu art"}
EXE, VOICE = "OROCHI_Z.exe", "data/Sound/Voice/Voice.bin"
ARCHIVES = ("LINKDATA1", "LINKDATA2")
NEEDED = (EXE, "LINKDATA1.IDX", "LINKDATA1.BIN", "LINKDATA2.IDX", "LINKDATA2.BIN", VOICE)
VOICE_INDEX = 4058  # LINKDATA1: (u32 offset, u32 size) big-endian at 8 + 8 * clip, for each Voice.bin clip
# The OROCHI_Z.exe of the 1.01 update, told by its PE header: linked 2010-01-26 07:28:39 UTC, SizeOfImage 0x1194000
# (1.00's is in the pieces file). The update's LINKDATA4 beside a 1.00 exe changes nothing wozmod reads.
UPDATE_101 = (0x4B5E99A7, 0x1194000)
UPDATE_101_FOUND = ("This game has the 1.01 update. wozmod needs version 1.00, and adds the update's sound and menu fixes "
                    "itself. Reinstall the game from its disc without the update, then pick its folder.")
WRONG_GAME = ("This is not Musou Orochi Z for Windows version 1.00 (the Japanese release). Pick the folder of that "
              "game.")
SHOWN = 6  # changed pieces named per file; the rest are counted


@dataclass(frozen=True)
class Piece:
    """One stock piece: an exe span (at = file offset, length), a LINKDATA entry or a Voice.bin clip (at = number)."""
    file: str  # EXE, "LINKDATA1", "LINKDATA2" or VOICE
    at: int
    sha256: str
    steps: frozenset[str]  # the build steps that read it; empty for a piece wozmod only serves its own in place of
    served: bool = False
    length: int = 0
    decoded_sha256: str | None = None  # an entry every step reads unpacked

    @property
    def shown_file(self) -> str:
        return f"{self.file}.BIN" if self.file in ARCHIVES else self.file


@dataclass(frozen=True)
class Verdict:
    """What check_game found, in its order: missing files, then the exe's version, then files not from version 1.00,
    then exe code wozmod patches that another mod changed, then changed pieces. Each changed piece keeps the steps
    that read it, so an update that reuses some steps can stop only for pieces the steps it rebuilds read (blocking),
    and name the others (reused). Changed code always stops: the DLL would turn all of wozmod off in game."""
    missing: tuple[str, ...] = ()
    version: str = "1.00"  # "1.00", "1.01" or "other"
    wrong_files: tuple[str, ...] = ()
    guarded: tuple[str, ...] = ()  # the DLL's names of the features whose code (its guards) another mod changed
    changed: tuple[Piece, ...] = ()
    pieces: int = 0  # how many pieces were hashed

    def blocking(self, rebuilt=STEPS) -> tuple[Piece, ...]:
        return tuple(p for p in self.changed if p.steps & set(rebuilt))

    def reused(self, rebuilt=STEPS) -> tuple[Piece, ...]:
        """Changed pieces that only steps kept from an earlier install read."""
        return tuple(p for p in self.changed if p.steps and not p.steps & set(rebuilt))

    def overlaps(self) -> tuple[Piece, ...]:
        """Changed pieces that wozmod serves its own in place of without reading them."""
        return tuple(p for p in self.changed if p.served and not p.steps)

    def problem(self, rebuilt=STEPS) -> str | None:
        """The reason the install stops, one line per file, or None."""
        if self.missing:
            names = _and(list(self.missing))
            return (f"{names} {'is' if len(self.missing) == 1 else 'are'} missing. Pick the folder that contains the "
                    "game's .exe (the Japanese PC release).")
        if self.version != "1.00":
            return UPDATE_101_FOUND if self.version == "1.01" else WRONG_GAME
        lines = [f"{name} is not from Musou Orochi Z version 1.00. Reinstall the game, then pick its folder."
                 for name in self.wrong_files]
        if self.guarded and EXE not in self.wrong_files:  # whatever the update rebuilds (row 31 does not apply)
            lines.append(_guarded_line(self.guarded))
        blocking = self.blocking(rebuilt)
        for name in dict.fromkeys(p.shown_file for p in blocking):
            lines.append(_changed_line(name, [p for p in blocking if p.shown_file == name]))
        return "\n".join(lines) or None

    def notes(self) -> list[str]:
        """Notes that do not stop the install: what another mod also replaces."""
        overlaps = self.overlaps()
        if not overlaps:
            return []
        counts = {name: sum(p.shown_file == name for p in overlaps) for name in dict.fromkeys(p.shown_file for p in overlaps)}
        things = "; ".join(f"{Path(name).name}: {_count(n, _noun(name))}" for name, n in counts.items())
        return [f"Note: another mod also replaces {_count(len(overlaps), 'thing')} wozmod replaces ({things}). "
                f"wozmod's {'is' if len(overlaps) == 1 else 'are'} used while it is installed."]


def _noun(name: str) -> str:
    return "voice clip" if name == VOICE else "span" if name == EXE else "entry"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun if n == 1 else 'entries' if noun == 'entry' else noun + 's'}"


def _listed(values: list[str]) -> str:
    shown = values[:SHOWN]
    rest = len(values) - len(shown)
    if rest:
        return f"{', '.join(shown)} and {rest} more"
    return _and(shown)


def _and(values: list[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    return " and ".join([", ".join(values[:-1]), values[-1]]) if len(values) > 1 else values[0]


def _changed_line(name: str, pieces: list[Piece]) -> str:
    pieces = sorted(pieces, key=lambda p: p.at)
    if name == EXE:
        what = f"the bytes at {_listed([f'{p.at:#x}' for p in pieces])}"
    else:
        noun = _noun(name)
        plural = "entries" if noun == "entry" else noun + "s"
        what = f"{noun if len(pieces) == 1 else plural} {_listed([str(p.at) for p in pieces])}"
    steps = [PURPOSE[s] for s in STEPS if any(s in p.steps for p in pieces)]
    purpose = _and(steps)
    return (f"{name}: another mod changed {what}, which wozmod reads to build its {purpose}. Put back the original "
            f"{Path(name).name}, or remove that mod, then install again.")


def _guarded_line(names: tuple[str, ...]) -> str:
    """The stop for exe code another mod changed, naming the features as players know them (guards.Feature.shown):
    core by the features that need it, or as the code wozmod hooks into when nothing else is touched."""
    from wozmod.runtime.guards import FEATURES

    by_name = {f.name: f for f in FEATURES}
    touched = {by_name[name].key for name in names}
    if touched == {"core"}:
        where = "the game's code that wozmod hooks into"
    else:
        if "core" in touched:
            touched |= {f.key for f in FEATURES if "core" in f.requires}
        shown = [f.shown or f.name for f in FEATURES if f.key in touched and f.key != "core"]
        where = f"the game's code where wozmod patches {_and(shown)}"
    return (f"{EXE}: another mod changed {where}. wozmod works only with all of its changes, so it was not "
            f"installed. Put back the original {EXE}, or remove that mod, then install again.")


@dataclass(frozen=True)
class Recorded:
    """data/stock_pieces.json: version 1.00's build stamp, and the pieces."""
    stamp: tuple[int, int]
    pieces: tuple[Piece, ...]
    recorded_from: dict = field(default_factory=dict)  # (size, SHA-256) of each whole stock file the record was made from


def parse(document: dict) -> Recorded:
    if document.get("format") != 1:
        raise ValueError("unsupported stock pieces format")
    pieces = []
    for offset, length, digest, steps in document["pieces"][EXE]:
        pieces.append(Piece(EXE, offset, digest, frozenset(steps.split()), length=length))
    for name in ARCHIVES:
        for entry, digest, decoded, steps, served in document["pieces"][name]:
            pieces.append(Piece(name, entry, digest, frozenset(steps.split()), bool(served), decoded_sha256=decoded))
    for clip, digest, steps, served in document["pieces"][VOICE]:
        pieces.append(Piece(VOICE, clip, digest, frozenset(steps.split()), bool(served)))
    return Recorded(tuple(document["stamp"]), tuple(pieces),
                    {name: tuple(pin) for name, pin in document.get("recorded_from", {}).items()})


@lru_cache(maxsize=1)
def recorded(path: Path = PIECES) -> Recorded:
    return parse(json.loads(Path(path).read_text(encoding="utf-8")))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version(exe: Path, stamp: tuple[int, int]) -> str:
    try:
        with exe.open("rb") as file:
            found = pe.build_stamp(file.read(0x1000))
    except (OSError, pe.PEError, struct.error):
        return "other"
    return "1.00" if found == tuple(stamp) else "1.01" if found == UPDATE_101 else "other"


def _player_index(game: Path, name: str):
    """The player's index of an archive, or None when it does not describe its BIN."""
    try:
        return parse_index((game / f"{name}.IDX").read_bytes(), (game / f"{name}.BIN").stat().st_size)
    except (OSError, ArchiveError):
        return None


def _decoded(parts: list[bytes]) -> bytes | None:
    """The parts unpacked as the build's readers unpack them, or None when one does not unpack."""
    try:
        return b"".join(parse_lzp2(part).decoded if part[:4] == LZP2_MAGIC else part for part in parts)
    except ArchiveError:
        return None


def _changed_entries(game: Path, name: str, index, pieces: list[Piece]) -> list[Piece]:
    changed = []
    with (game / f"{name}.BIN").open("rb") as stream:
        for piece in sorted(pieces, key=lambda p: p.at):
            if piece.at >= len(index.entries):
                changed.append(piece)
                continue
            entry, parts = index.entries[piece.at], []
            offset = entry.offset
            for size in entry.stored_sizes:
                stream.seek(offset)
                parts.append(stream.read(size))
                offset += align(size)
            if _sha(b"".join(parts)) == piece.sha256:
                continue
            if piece.decoded_sha256 and (decoded := _decoded(parts)) is not None and _sha(decoded) == piece.decoded_sha256:
                continue
            changed.append(piece)
    return changed


def _changed_clips(game: Path, index, pieces: list[Piece]) -> list[Piece]:
    """Clips found through the player's voice index; all of them count as changed when it cannot be read."""
    try:
        entry = index.entries[VOICE_INDEX]
        table = decoded_member(Archive(game / "LINKDATA1.BIN", ((entry.offset, entry.stored_sizes),)).read([0])[0])
    except (IndexError, OSError, ArchiveError, TypeError):
        return list(pieces)
    changed = []
    with (game / VOICE).open("rb") as stream:
        for piece in sorted(pieces, key=lambda p: p.at):
            if 8 + 8 * piece.at + 8 > len(table):
                changed.append(piece)
                continue
            offset, size = struct.unpack_from(">II", table, 8 + 8 * piece.at)
            stream.seek(offset)
            if _sha(stream.read(size)) != piece.sha256:
                changed.append(piece)
    return changed


def _changed_spans(game: Path, pieces: list[Piece]) -> list[Piece]:
    exe = (game / EXE).read_bytes()
    return [p for p in pieces if _sha(exe[p.at:p.at + p.length]) != p.sha256 or p.at + p.length > len(exe)]


def check_game(game: Path, record: Recorded | None = None, dll: Path | None = None) -> Verdict:
    """Check the game folder in order: the files are there, the exe is version 1.00, then every guard of the DLL
    ``dll`` (none checked without one), then every piece."""
    game = Path(game)
    if missing := tuple(name for name in NEEDED if not (game / name).is_file()):
        return Verdict(missing=missing)
    record = record or recorded()
    if (version := _version(game / EXE, record.stamp)) != "1.00":
        return Verdict(version=version)
    by_file = {name: [p for p in record.pieces if p.file == name] for name in (EXE, *ARCHIVES, VOICE)}
    indexes = {name: _player_index(game, name) for name in ARCHIVES}
    jobs = {EXE: lambda: _changed_spans(game, by_file[EXE])}
    for name in ARCHIVES:
        if indexes[name] is not None:
            jobs[name] = lambda name=name: _changed_entries(game, name, indexes[name], by_file[name])
    if indexes["LINKDATA1"] is not None:
        jobs[VOICE] = lambda: _changed_clips(game, indexes["LINKDATA1"], by_file[VOICE])
    with ThreadPoolExecutor(len(jobs)) as pool:  # hashlib lets go of the GIL: hash the files side by side
        found = dict(zip(jobs, pool.map(lambda job: job(), jobs.values()), strict=True))
    wrong, changed = [], []
    for name in by_file:
        pieces = found.get(name)
        if name in ARCHIVES and indexes[name] is None:
            wrong.append(f"{name}.BIN")  # its index does not describe it
        elif pieces is not None and 2 * sum(bool(p.steps) for p in pieces) > sum(bool(p.steps) for p in by_file[name]):
            wrong.append(f"{name}.BIN" if name in ARCHIVES else name)  # most of what the build reads differs: another
            # release, or another game's file. Pieces wozmod only serves its own in place of do not count.
        elif pieces:
            changed += pieces
    guarded = ()
    if dll is not None:
        from wozmod.runtime.guard_report import refused_features  # here: guards reads the runtime's C at import

        guarded = tuple(refused_features((game / EXE).read_bytes(), Path(dll).read_bytes()))
    return Verdict(wrong_files=tuple(wrong), guarded=guarded, changed=tuple(changed),
                   pieces=sum(map(len, by_file.values())))
