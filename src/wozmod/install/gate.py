"""The read gate: a step reads only what its manifest (install/manifest.py), which its step key hashes, declares.

While watch() runs a step, an audit hook sees every file the process opens, every program it starts and every piece
of a game file wozmod.game_files serves, os.environ sees every WOZMOD_* setting it looks up, and a sys.meta_path
finder sees every wozmod module it imports. All of these hold in the threads it starts (ordered_map's). Checked
are:

- a piece of a game file (wozmod.game_files): one of the manifest's stock pieces (a span of the exe or another file,
  a LINKDATA entry, read unpacked or as stored, or a Voice.bin clip), so the installer's check of those pieces before
  the step runs covers every byte the step reads. A stored read of an entry the record says is only read unpacked is
  not declared: the check lets a repacked entry through;
- a file in the game folder opened other than by wozmod.game_files: never declared;
- a file in the donors folder: a member or a decrypted program the manifest names;
- a bundled file (resources.PACKAGED, below resources.root()): one the manifest names;
- a program started: one the manifest names, and each path in its arguments as a file above;
- a WOZMOD_* setting looked up: one the manifest names (the worker gets no other, so the step would get nothing);
- a wozmod module imported while the step runs: one the manifest names (the worker's own, imported before, are not
  the step's: the manifest covers the installer names the step uses).

Everything else (the step's own output and scratch folders, its other inputs, the temporary folder, Python's files,
ffmpeg) is not checked. In each mode:

- enforce: an undeclared read raises UndeclaredRead at the read, and again at the end if the step's code caught it;
- observe: an undeclared read goes through, and one line per undeclared item goes to the install log (stderr, which
  a worker sends there), starting with LINE;
- record: every read goes through.

Given a trace, any mode writes each item read to it once, as a JSON line {"step", "kind", "item", "declared"}: a
reference build (tools/verify.py ref) enforces and keeps the trace. records() reads it back, unread() says what the
manifest declares that was never read.

default_mode() is the one place that decides which mode an install runs in (D1).
"""

from __future__ import annotations

import bisect
import json
import os
import shlex
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from wozmod import game_files, platform, resources

ENFORCE, OBSERVE, RECORD = "enforce", "observe", "record"
MODES = (ENFORCE, OBSERVE, RECORD)
LINE = "read gate: "  # how each observe line in the install log starts (the packaged e2e run looks for none)
KINDS = {"stock": "a piece of a game file", "game": "a game file, opened other than through wozmod.game_files",
         "donor": "a donor file", "bundled": "a bundled file", "program": "a program", "setting": "a setting",
         "module": "a module"}
_STARTS = {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.spawn", "os.startfile"}
_NAMED_AS_FILES = {"game", "donor", "bundled", "program"}  # whose items are file names, matched as the system does


def _key(kind: str, item: str) -> str:
    """item as it is matched against a manifest's names: a file or program name ignores case where the system does
    (normcase), and is reported in its own case."""
    return os.path.normcase(item) if kind in _NAMED_AS_FILES else item


class UndeclaredRead(Exception):
    """A step read something its manifest does not declare."""

    def __init__(self, step: str, kind: str, item: str):
        super().__init__(step, kind, item)
        self.step, self.kind, self.item = step, kind, item

    def __str__(self) -> str:
        return (f"the {self.step} step read {self.item} ({KINDS[self.kind]}), which its manifest does not declare "
                "(wozmod.install.manifest)")


def default_mode() -> str:
    """D1: our own builds (source checkouts: tests, dev installs, reference builds) enforce; a frozen release, which
    players run, only observes, permanently, so that a gap in a manifest can never stop a player's install."""
    return OBSERVE if getattr(sys, "frozen", False) else ENFORCE


def _wozmod(name: str) -> bool:
    return name == "wozmod" or name.startswith("wozmod.")


def _piece(name: str, at: int, end: int | None, form: str) -> str:
    """How a piece game_files announced is named in an error, the log and a trace."""
    if form == "span":
        return f"{name} bytes {at:#x} to {end:#x}"
    if form == "clip":
        return f"{name} clip {at}"
    return f"{name} entry {at} ({'unpacked' if form == 'decoded' else 'as stored'})"


class _Stock:
    """A manifest's stock pieces, to check a piece read against: spans by file, entries, clips."""

    def __init__(self, pieces):
        case = os.path.normcase
        spans: dict[str, list[tuple[int, int]]] = {}
        self.entries: dict[tuple[str, int], bool] = {}  # -> read only unpacked
        self.clips: set[tuple[str, int]] = set()
        for piece in pieces:
            if piece.file in game_files.ARCHIVES:
                self.entries[case(piece.file), piece.at] = piece.decoded_sha256 is not None
            elif piece.file == game_files.VOICE:
                self.clips.add((case(piece.file), piece.at))
            else:
                spans.setdefault(case(piece.file), []).append((piece.at, piece.at + piece.length))
        self.spans = {name: sorted(found) for name, found in spans.items()}
        self.starts = {name: [start for start, _ in found] for name, found in self.spans.items()}

    def declares(self, name: str, at: int, end: int | None, form: str) -> bool:
        name = os.path.normcase(name)
        if form == "span":
            k = bisect.bisect_right(self.starts.get(name, ()), at) - 1
            return k >= 0 and end <= self.spans[name][k][1]
        if form == "clip":
            return (name, at) in self.clips
        only_unpacked = self.entries.get((name, at))
        return only_unpacked is not None and not (only_unpacked and form == "stored")


class _Watch:
    def __init__(self, step: str, manifest, mode: str, arguments: dict, trace: Path | None):
        if mode not in MODES:
            raise ValueError(f"no read gate mode {mode!r}")
        case = os.path.normcase
        self.step, self.mode = step, mode
        self.declared = {"game": set(),  # every read of a game file goes through wozmod.game_files
                         "donor": {case(n) for n in manifest.donor_files()},
                         "bundled": {case(n) for n in manifest.bundled},
                         "program": {case(n) for n in manifest.starts},
                         "setting": set(manifest.environment), "module": set(manifest.modules)}
        root = resources.root()
        folders = [(value, None, None) for name, value in arguments.items()  # its other inputs and scratch folders
                   if isinstance(value, Path) and name not in ("game", "donors")]
        folders += [(arguments[name], kind, arguments[name]) for name, kind in (("game", "game"), ("donors", "donor"))
                    if isinstance(arguments.get(name), Path)]
        folders += [(root / name, "bundled", root) for name in resources.PACKAGED]
        # The innermost folder decides: an install builds in a folder inside the game folder.
        self.folders = sorted(((case(os.path.abspath(f)), kind, os.path.abspath(base) if base else "")
                               for f, kind, base in folders), key=lambda each: -len(each[0]))
        self.stock = _Stock(manifest.stock)
        self.seen: set[tuple[str, str]] = set()  # the items an observe line was written for
        self.written: set[tuple[str, str]] = set()  # the items written to the trace
        self.caught: list[UndeclaredRead] = []
        self.issued = threading.local()  # per thread: the game file game_files announced it opens next
        self.places: dict[str, str | None] = {}  # each path a piece was announced in: its game file, or None
        self.trace = os.open(trace, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644) if trace else None

    def where(self, path) -> tuple[str, str] | None:
        """(kind, path relative to its folder) of a file the gate checks, None for any other path."""
        path = os.path.abspath(os.fsdecode(path))
        cased = os.path.normcase(path)
        for folder, kind, base in self.folders:
            if cased == folder or cased.startswith(folder + os.sep):
                if kind is None or os.path.isdir(path):  # a folder is opened to list it: its files are what count
                    return None
                return kind, os.path.relpath(path, base)  # in its own case
        return None

    def read(self, kind: str, item: str, declared: bool | None = None, piece: list | None = None) -> None:
        key = _key(kind, item)
        declared = key in self.declared[kind] if declared is None else declared
        shown = item.replace(os.sep, "/")
        if self.trace is not None and (kind, key) not in self.written:
            self.written.add((kind, key))
            record = {"step": self.step, "kind": kind, "item": shown, "declared": declared}
            if piece is not None:
                record["piece"] = piece
            os.write(self.trace, (json.dumps(record) + "\n").encode("utf-8"))
        if self.mode == RECORD or declared:
            return
        if self.mode == ENFORCE:
            error = UndeclaredRead(self.step, kind, shown)
            self.caught.append(error)
            raise error
        elif (kind, key) not in self.seen:
            self.seen.add((kind, key))
            os.write(2, f"{LINE}{UndeclaredRead(self.step, kind, shown)}\n".encode("utf-8", "replace"))

    def opened(self, path) -> None:
        if isinstance(path, (str, bytes, os.PathLike)) and (found := self.where(path)):
            if found[0] == "game" and getattr(self.issued, "path", None) == found[1]:
                self.issued.path = None  # game_files opens it: what it reads of it is checked by piece
                return
            self.read(*found)

    def issue(self, path: str) -> None:
        """game_files opens path next, in this thread."""
        found = self.where(path)
        self.issued.path = found[1] if found and found[0] == "game" else None

    def piece(self, path: str, at: int, end: int | None, form: str) -> None:
        if path not in self.places:
            found = self.where(path)
            self.places[path] = found[1].replace(os.sep, "/") if found and found[0] == "game" else None
        if (name := self.places[path]) is None:
            return  # not the game's
        declared = self.stock.declares(name, at, end, form)
        if self.trace is not None or not declared:  # the exe's spans are many: name a piece only when it is written
            self.read("stock", _piece(name, at, end, form), declared, [name, at, end, form])

    def started(self, event: str, args: tuple) -> None:
        if event == "subprocess.Popen":
            executable, command = args[0], platform.split_command(args[1])
            program = executable or command[0]
        elif event == "os.system":
            command = shlex.split(os.fsdecode(args[0]))
            program = command[0] if command else ""
        elif event == "os.startfile":
            program, command = args[0], []
        else:  # os.spawn(mode, path, args, env); os.exec and os.posix_spawn(path, args, env)
            program, command = (args[1], args[2]) if event == "os.spawn" else (args[0], args[1])
            command = list(command)
        name = Path(os.fsdecode(program)).name
        if name.lower().endswith(".exe"):
            name = name[:-4]
        self.read("program", name)
        for argument in command[1:]:
            if isinstance(argument, (str, bytes, os.PathLike)) and os.path.isabs(os.fsdecode(argument)):
                self.opened(argument)

    def finish(self) -> None:
        """At the step's end: raise a read its code caught."""
        if self.caught:
            raise self.caught[0]

    def close(self) -> None:
        if self.trace is not None:
            os.close(self.trace)


_current: _Watch | None = None
_hooked = False


def _hook(event: str, args: tuple) -> None:
    watching = _current
    if watching is None:
        return
    if event == game_files.READ:
        watching.piece(*args)
    elif event == "open":
        watching.opened(args[0])
    elif event == game_files.OPEN:
        watching.issue(args[0])
    elif event in _STARTS:
        watching.started(event, args)


class _Modules:
    """A sys.meta_path finder, first while a step is watched: it sees each module about to be imported, however it is
    imported (an import statement, importlib.import_module), and finds none itself."""

    @staticmethod
    def find_spec(name, path=None, target=None):
        watching = _current
        if watching is not None and _wozmod(name):
            watching.read("module", name)
        return None


class _Settings(os._Environ):
    """os.environ while a step is watched: a WOZMOD_* lookup goes through the gate first."""

    def __getitem__(self, key):
        watching = _current
        if watching is not None and isinstance(key, str) and key.startswith("WOZMOD_"):
            watching.read("setting", key)
        return super().__getitem__(key)


@contextmanager
def watch(step: str, manifest, mode: str, arguments: dict, trace: Path | None = None):
    """Hold the step run inside to its manifest, in mode; arguments are the step function's (game, donors and the
    folders it writes in); trace is where each item read is written, in any mode."""
    global _current, _hooked
    if _current is not None:
        raise RuntimeError(f"the {_current.step} step is already watched in this process")
    watching = _Watch(step, manifest, mode, arguments, trace)
    if not _hooked:  # an audit hook stays for the life of the process: it does nothing while no step is watched
        sys.addaudithook(_hook)
        _hooked = True
    os.environ.__class__ = _Settings  # the object itself, so a module that bound os.environ earlier is watched too
    finder = _Modules()
    sys.meta_path.insert(0, finder)
    _current = watching
    try:
        for name in sorted(n for n in sys.modules if _wozmod(n) and n in watching.declared["module"]):
            watching.read("module", name)  # of its closure, what the worker imported before (record lists them)
        yield
        watching.finish()
    finally:
        _current = None
        sys.meta_path.remove(finder)
        os.environ.__class__ = os._Environ
        watching.close()


def records(trace: Path) -> list[dict]:
    """What a record-mode run wrote to trace."""
    return [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line]


def unread(manifest, read: list[dict]) -> dict[str, list[str]]:
    """Per kind, what the manifest declares that the step never read (from records() of its run)."""
    mine = [r for r in read if r["step"] == manifest.step]
    seen = {(r["kind"], _key(r["kind"], r["item"])) for r in mine}

    pieces = [r["piece"] for r in mine if r["kind"] == "stock"]

    def hit(piece) -> bool:
        if piece.file in game_files.ARCHIVES or piece.file == game_files.VOICE:
            return any(name == piece.file and at == piece.at for name, at, _, _ in pieces)
        return any(name == piece.file and at < piece.at + piece.length and end > piece.at
                   for name, at, end, form in pieces if form == "span")

    declared = {"donor": manifest.donor_files(), "bundled": manifest.bundled, "program": manifest.starts}
    found = {kind: sorted(n for n in names if (kind, _key(kind, n)) not in seen) for kind, names in declared.items()}
    stock = [_piece(p.file, p.at, p.at + p.length, "span" if p.length else "clip" if p.file == game_files.VOICE else
                    "decoded" if p.decoded_sha256 else "stored") for p in manifest.stock if not hit(p)]
    return {"stock": stock, **found, "setting": sorted(n for n in manifest.environment if ("setting", n) not in seen),
            "module": sorted(n for n in manifest.modules if ("module", n) not in seen)}
