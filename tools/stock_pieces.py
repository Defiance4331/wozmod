"""Record which pieces of the stock game files the installer's build reads, into data/stock_pieces.json.

    uv run tools/stock_pieces.py            # inputs from [tests] in work/harness.toml: WOZMOD_GAME, WOZMOD_DONORS,
                                            # WOZMOD_LETTERING; writes data/stock_pieces.json
    uv run tools/stock_pieces.py --check    # exits 1 when a fresh record differs from the committed one

The three build steps (text, voices, art) run in this process, one after another, with one worker, the art's
worker pool turned into a thread. While they run:
- every open of a file in the game folder goes through a tracking opener: reads of LINKDATA1/2.BIN and Voice.bin
  are kept as byte ranges, and the exe's bytes come back as TrackedBytes, which keep each span the code looks at
  (indexing, find/index, struct.unpack_from). The .IDX files are only located through, so they are not pieces;
- an audit hook stops the run at any other open of a game file (os.open, a subprocess given its path, ...);
- the text step's scan of every NUL-ended exe string (text.stock.EXE_STRING) keeps only the strings a translation row
  is found by and that no other source holds: the rest of the scan cannot change what is built.

Byte ranges become pieces through the stock indexes: a LINKDATA entry, or a Voice.bin clip (through the voice index,
LINKDATA1 4058, which only locates the clips the voices step reads). An entry read only unpacked (Archive.read,
entry_bytes) also passes the installer's check when its unpacked bytes match. The served pieces are read off the
build's output: wozmod/entries, wozmod/captions and the clips of wozmod/voice/Voice.bin.

tests/test_stock_pieces.py (WOZMOD_STOCK_PIECES=1) proves the list complete: a build from a copy with every unlisted
entry replaced by junk, the exe's bytes outside the spans scrambled and the unread clips zeroed is byte-identical.
"""

from __future__ import annotations

import argparse
import bisect
import builtins
import contextlib
import hashlib
import io
import json
import os
import random
import struct
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import checkout

REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / "data/stock_pieces.json"
ABOUT = ("Written by tools/stock_pieces.py: hashes and locations only. The pieces of the stock game files the "
         "installer's build steps read (and the ones wozmod serves its own in place of), which install/stock.py checks.")


def run_steps(game: Path, donors: Path, lettering: Path, scratch: Path, jobs: int, on_step=lambda step: None) -> Path:
    """Run the installer's three build steps into scratch/out (laid out as the game folder) and return it."""
    from wozmod import install
    from wozmod.install.stock import STEPS

    out = scratch / "out"
    out.mkdir(parents=True, exist_ok=True)
    runs = {"text": lambda: install._text_table(game, donors, out),
            "voices": lambda: install._voice_banks(game, donors, out, jobs=jobs),
            "art": lambda: install._menu_art(game, donors, lettering, scratch / "art", out, jobs=jobs)}
    for step in STEPS:
        on_step(step)
        started = time.monotonic()
        runs[step]()
        print(f"{step}: {time.monotonic() - started:.0f} s", file=sys.stderr, flush=True)
    on_step(None)
    return out


# ---------------------------------------------------------------- tracking

class TrackedBytes(bytes):
    """The exe's bytes: each span the build looks at is passed to sink(start, end)."""

    def __new__(cls, data: bytes, sink):
        self = super().__new__(cls, data)
        self._sink = sink
        return self

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            if step != 1:
                self._sink(0, len(self))
            elif stop > start:
                self._sink(start, stop)
        else:
            at = key + len(self) if key < 0 else key
            self._sink(at, at + 1)
        return bytes.__getitem__(self, key)

    def _searched(self, found: int, sub, start, end, *, forward: bool) -> int:
        start = 0 if start is None else start + len(self) if start < 0 else start
        end = len(self) if end is None else end + len(self) if end < 0 else end
        length = len(sub) if isinstance(sub, (bytes, bytearray)) else 1
        if found < 0:
            self._sink(start, end)
        elif forward:
            self._sink(start, found + length)
        else:
            self._sink(found, end)
        return found

    def find(self, sub, start=None, end=None):
        return self._searched(bytes.find(self, sub, start, end), sub, start, end, forward=True)

    def index(self, sub, start=None, end=None):
        return self._searched(bytes.index(self, sub, start, end), sub, start, end, forward=True)

    def rfind(self, sub, start=None, end=None):
        return self._searched(bytes.rfind(self, sub, start, end), sub, start, end, forward=False)

    def rindex(self, sub, start=None, end=None):
        return self._searched(bytes.rindex(self, sub, start, end), sub, start, end, forward=False)


def _whole(name):
    def method(self, *args, **kwargs):
        self._sink(0, len(self))
        return getattr(bytes, name)(self, *args, **kwargs)
    return method


for _name in ("decode", "split", "rsplit", "count", "startswith", "endswith", "replace", "strip", "translate", "hex",
              "__iter__", "__contains__", "__add__", "splitlines", "partition"):
    setattr(TrackedBytes, _name, _whole(_name))


class TrackedFile:
    """A stock file opened for reading: each read is passed to sink(start, end); a whole read of the exe returns
    TrackedBytes instead."""

    def __init__(self, raw, sink, exe: bool):
        self._raw, self._sink, self._exe = raw, sink, exe

    def read(self, size=-1):
        at = self._raw.tell()
        data = self._raw.read(size)
        if self._exe:
            return TrackedBytes(data, lambda start, end: self._sink(at + start, at + end))
        self._sink(at, at + len(data))
        return data

    def readinto(self, buffer):
        at = self._raw.tell()
        count = self._raw.readinto(buffer)
        self._sink(at, at + (count or 0))
        return count

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._raw.close()

    def __iter__(self):
        raise TypeError("a stock file is read by line")

    def __getattr__(self, name):
        return getattr(self._raw, name)


class Recorder:
    """Install with `with Recorder(game) as recorder:`; set recorder.step while each step runs."""

    def __init__(self, game: Path):
        self.game = os.path.realpath(game)
        self.step: str | None = None
        self.reads: dict[str, dict[str, list]] = {}  # step -> kind -> [(start, end, form)]
        self.violations: list[str] = []
        self.whole_exe: set[str] = set()
        self._local = threading.local()
        self._lock = threading.Lock()
        self._active = False
        self._undo: list = []

    # where reads go
    def record(self, kind: str, start: int, end: int) -> None:
        if end <= start or self.step is None:
            return
        form = getattr(self._local, "form", "stored")
        with self._lock:
            self.reads.setdefault(self.step, {}).setdefault(kind, []).append((start, end, form))

    @contextlib.contextmanager
    def form(self, form: str):
        before = getattr(self._local, "form", "stored")
        self._local.form = form
        try:
            yield
        finally:
            self._local.form = before

    def _stock(self, file) -> str | None:
        if isinstance(file, int):
            return None
        try:
            path = os.path.realpath(os.fsdecode(os.fspath(file)))
        except TypeError:
            return None
        return os.path.relpath(path, self.game).replace(os.sep, "/") if path.startswith(self.game + os.sep) else None

    def _open(self, real_open):
        def tracking_open(file, mode="r", *args, **kwargs):
            name = self._stock(file) if self._active else None
            if name is None:
                return real_open(file, mode, *args, **kwargs)
            if any(flag in mode for flag in "wax+"):
                raise PermissionError(f"the build opened stock {name} to write")
            kind = {"OROCHI_Z.exe": "exe", "LINKDATA1.BIN": "LINKDATA1", "LINKDATA2.BIN": "LINKDATA2",
                    "data/Sound/Voice/Voice.bin": "voice", "LINKDATA1.IDX": "index", "LINKDATA2.IDX": "index"}.get(name)
            if kind is None:
                self.violations.append(f"{self.step}: opened {name}, which no piece covers")
                raise PermissionError(f"stock_pieces: the build read stock {name}, which no piece covers")
            self._local.allowed = True
            try:
                raw = real_open(file, mode, *args, **kwargs)
            finally:
                self._local.allowed = False
            if kind == "index":
                return raw
            return TrackedFile(raw, lambda start, end: self.record(kind, start, end), kind == "exe")
        return tracking_open

    def _audit(self, event: str, args) -> None:
        if not self._active:
            return
        if event == "open" and not getattr(self._local, "allowed", False) and (name := self._stock(args[0])):
            self.violations.append(f"{self.step}: opened {name} outside the tracking opener")
            raise PermissionError(f"stock_pieces: {name} was opened outside the tracking opener")
        if event == "subprocess.Popen":
            words = [os.fsdecode(a) for a in (args[1] if isinstance(args[1], (list, tuple)) else [args[1]])
                     if isinstance(a, (str, bytes, os.PathLike))]
            if any(self.game in os.path.realpath(w) or self.game in w for w in words):
                self.violations.append(f"{self.step}: a subprocess was given a stock path: {words}")
                raise PermissionError("stock_pieces: a subprocess was given a stock path")

    def _patch(self, owner, name, value) -> None:
        self._undo.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def _patch_everywhere(self, original, replacement) -> None:
        for module in list(sys.modules.values()):
            if getattr(module, "__name__", "").startswith("wozmod"):
                for name, value in list(vars(module).items()):
                    if value is original:
                        self._patch(module, name, replacement)

    def __enter__(self):
        import wozmod.art.build  # noqa: F401  (load what holds the names patched below)
        import wozmod.art.faces  # noqa: F401
        import wozmod.art.vector  # noqa: F401
        import wozmod.install
        import wozmod.text.build  # noqa: F401
        import wozmod.text.inventory  # noqa: F401
        import wozmod.text.stock as text_stock
        import wozmod.voices  # noqa: F401
        from wozmod.art import packing
        from wozmod.formats import linkdata

        for owner in (builtins, io):
            self._patch(owner, "open", self._open(owner.open))
        real_unpack_from = struct.unpack_from

        def unpack_from(fmt, buffer, offset=0):
            if isinstance(buffer, TrackedBytes):
                at = offset + len(buffer) if offset < 0 else offset
                buffer._sink(at, at + struct.calcsize(fmt))
            return real_unpack_from(fmt, buffer, offset)
        self._patch(struct, "unpack_from", unpack_from)

        real_read, real_entry_bytes = linkdata.Archive.read, linkdata.entry_bytes

        def read(archive, ordinals, unpack=True):
            with self.form("decoded" if unpack else "stored"):
                return real_read(archive, ordinals, unpack)

        def entry_bytes(stream, entry):
            with self.form("decoded"):
                return real_entry_bytes(stream, entry)
        self._patch(linkdata.Archive, "read", read)
        self._patch_everywhere(real_entry_bytes, entry_bytes)
        self._patch_everywhere(packing.worker_pool, lambda workers: ThreadPoolExecutor(1))

        scan = _ExeScan(text_stock.EXE_STRING)
        self._patch(text_stock, "EXE_STRING", scan)
        real_stock_strings = text_stock.stock_strings

        def stock_strings(stock):
            scan.enabled = False
            rest = real_stock_strings(stock)  # what the exe scan does not add
            scan.enabled, scan.matches = True, []
            return _UsedCatalog(real_stock_strings(stock), rest, list(scan.matches),
                                lambda start, end: self.record("exe", start, end))
        self._patch(text_stock, "stock_strings", stock_strings)

        sys.addaudithook(self._audit)  # cannot be removed: _active turns it off
        self._active = True
        return self

    def __exit__(self, *exc):
        self._active = False
        for owner, name, value in reversed(self._undo):
            setattr(owner, name, value)
        self._undo.clear()


class _ExeScan:
    """text.stock.EXE_STRING, keeping the matches it finds in the exe (or none, while disabled)."""

    def __init__(self, pattern):
        self.pattern, self.enabled, self.matches = pattern, True, []

    def finditer(self, data, *args):
        if not self.enabled:
            return
        for match in self.pattern.finditer(data, *args):
            if isinstance(data, TrackedBytes):
                self.matches.append((match.start(), match.end(), match.group()))
            yield match

    def __getattr__(self, name):
        return getattr(self.pattern, name)


class _UsedCatalog(dict):
    """stock_strings' catalog: looking up a key only the exe scan gave keeps the span of its first string, with the
    NULs the scan needs on both sides."""

    def __init__(self, found: dict, rest: dict, matches: list, sink):
        super().__init__(found)
        self._rest, self._matches, self._sink, self._spans = rest, matches, sink, None

    def _used(self, key) -> None:
        if key in self._rest or not dict.__contains__(self, key):
            return
        if self._spans is None:
            from wozmod.text.stock import stock_key
            self._spans = {}
            for start, end, value in self._matches:
                if (keyed := stock_key(value)) and keyed[0] not in self._spans:
                    self._spans[keyed[0]] = (start - 1, end + 1)
        self._sink(*self._spans[key])

    def get(self, key, default=None):
        self._used(key)
        return dict.get(self, key, default)

    def __getitem__(self, key):
        self._used(key)
        return dict.__getitem__(self, key)

    def __contains__(self, key):
        self._used(key)
        return dict.__contains__(self, key)


# ---------------------------------------------------------------- from reads to pieces

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_pin(path: Path) -> list:
    with path.open("rb") as stream:
        return [path.stat().st_size, hashlib.file_digest(stream, "sha256").hexdigest()]


def stock_layout(game: Path):
    """The stock indexes, and each Voice.bin clip's (offset, size) by clip number."""
    from wozmod.formats.linkdata import archive, decoded_member, parse_index
    from wozmod.install.stock import ARCHIVES, VOICE_INDEX

    indexes = {name: parse_index((game / f"{name}.IDX").read_bytes(), (game / f"{name}.BIN").stat().st_size)
               for name in ARCHIVES}
    table = decoded_member(archive(game / "LINKDATA1")[VOICE_INDEX])
    clips = {clip: struct.unpack_from(">II", table, 8 + 8 * clip) for clip in range((len(table) - 8) // 8)}
    return indexes, clips


def _entries_hit(index, ranges) -> dict[int, set[str]]:
    starts = [e.offset for e in index.entries]
    hit: dict[int, set[str]] = {}
    for start, end, form in ranges:
        k = max(bisect.bisect_right(starts, start) - 1, 0)
        while k < len(index.entries) and index.entries[k].offset < end:
            entry = index.entries[k]
            if entry.offset + entry.allocated_bytes > start and entry.allocated_bytes:
                hit.setdefault(k, set()).add(form)
            k += 1
    return hit


def _clips_hit(clips: dict[int, tuple[int, int]], ranges) -> set[int]:
    spans = sorted((offset, offset + size, clip) for clip, (offset, size) in clips.items() if size)
    starts, longest = [s[0] for s in spans], max(s[1] - s[0] for s in spans)
    hit = set()
    for start, end, _ in ranges:
        for k in range(bisect.bisect_left(starts, start - longest), bisect.bisect_left(starts, end)):
            if spans[k][1] > start:
                hit.add(spans[k][2])
    return hit


def _merged(spans) -> list[tuple[int, int, set[str]]]:
    """(start, end, step) spans joined where they touch or overlap, each with the steps that read it."""
    merged: list[tuple[int, int, set[str]]] = []
    for start, end, step in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end), merged[-1][2] | {step})
        else:
            merged.append((start, end, {step}))
    return merged


def served_pieces(out: Path) -> tuple[dict[str, set[int]], set[int]]:
    """The LINKDATA entries and Voice.bin clips the build's output serves wozmod's own in place of."""
    from wozmod.formats.audio import MOZ_VOICE_IDS
    from wozmod.formats.linkdata import LINKDATA2_FIRST

    entries: dict[str, set[int]] = {"LINKDATA1": set(), "LINKDATA2": set()}
    for folder in ("entries", "captions", "captions/voice"):
        for path in (out / "wozmod" / folder).glob("*.bin"):
            key = int(path.stem)
            if key < LINKDATA2_FIRST:
                entries["LINKDATA1"].add(key)
            else:
                entries["LINKDATA2"].add(key - LINKDATA2_FIRST)
    bank = (out / "wozmod/voice/Voice.bin").read_bytes()
    # Ids past the stock range are a stage line's own slot (runtime/voice_slots.h): they stand in for no stock clip.
    count = min(struct.unpack_from("<I", bank)[0], MOZ_VOICE_IDS)
    clips = {clip for clip in range(count) if struct.unpack_from("<II", bank, 4 + 8 * clip)[1]}
    return entries, clips


def pieces_document(game: Path, reads: dict, out: Path) -> dict:
    """data/stock_pieces.json from the recorded reads and the build's output."""
    from wozmod.formats import pe
    from wozmod.install.stock import ARCHIVES, EXE, NEEDED, STEPS, VOICE, VOICE_INDEX, _decoded

    indexes, clips = stock_layout(game)
    exe = (game / EXE).read_bytes()
    spans = [(start, end, step) for step, kinds in reads.items() for start, end, _ in kinds.get("exe", ())]
    document = {"format": 1, "about": ABOUT, "stamp": list(pe.build_stamp(exe[:0x1000])),
                "recorded_from": {name: _file_pin(game / name) for name in NEEDED}, "pieces": {}}
    order = lambda steps: " ".join(s for s in STEPS if s in steps)  # noqa: E731
    document["pieces"][EXE] = [[start, end - start, _sha(exe[start:end]), order(steps)]
                               for start, end, steps in _merged(spans)]
    served, served_clips = served_pieces(out)
    for name in ARCHIVES:
        index, readers, forms = indexes[name], {}, {}
        for step, kinds in reads.items():
            for entry, seen in _entries_hit(index, kinds.get(name, ())).items():
                if name == "LINKDATA1" and entry == VOICE_INDEX and step == "voices":
                    continue  # it only locates the clips the voices step reads, which are pieces of their own
                readers.setdefault(entry, set()).add(step)
                forms.setdefault(entry, set()).update(seen)
        rows = []
        with (game / f"{name}.BIN").open("rb") as stream:
            for entry in sorted(set(readers) | served[name]):
                geometry, parts, offset = index.entries[entry], [], index.entries[entry].offset
                for size in geometry.stored_sizes:
                    stream.seek(offset)
                    parts.append(stream.read(size))
                    offset += -(-size // 2048) * 2048
                decoded = (_sha(_decoded(parts)) if entry in readers and forms[entry] == {"decoded"}
                           and _decoded(parts) is not None else None)
                rows.append([entry, _sha(b"".join(parts)), decoded, order(readers.get(entry, ())),
                             int(entry in served[name])])
        document["pieces"][name] = rows
    readers: dict[int, set[str]] = {}
    for step, kinds in reads.items():
        for clip in _clips_hit(clips, kinds.get("voice", ())):
            readers.setdefault(clip, set()).add(step)
    with (game / VOICE).open("rb") as stream:
        rows = []
        for clip in sorted(set(readers) | served_clips):
            offset, size = clips[clip]
            stream.seek(offset)
            rows.append([clip, _sha(stream.read(size)), order(readers.get(clip, ())), int(clip in served_clips)])
    document["pieces"][VOICE] = rows
    return document


def dumps(document: dict) -> str:
    """The document with one piece per line, so a change in what the build reads shows as a short diff."""
    head = {k: v for k, v in document.items() if k != "pieces"}
    lines = ["{"] + [f" {json.dumps(k)}: {json.dumps(v)}," for k, v in head.items()] + [' "pieces": {']
    files = list(document["pieces"].items())
    for n, (name, rows) in enumerate(files):
        lines.append(f"  {json.dumps(name)}: [")
        lines += [f"   {json.dumps(row)}{',' if k < len(rows) - 1 else ''}" for k, row in enumerate(rows)]
        lines.append("  ]" + ("," if n < len(files) - 1 else ""))
    return "\n".join(lines + [" }", "}"]) + "\n"


def summary(document: dict) -> str:
    from wozmod.install.stock import STEPS

    lines = []
    for name, rows in document["pieces"].items():
        steps = [row[-1] if name == "OROCHI_Z.exe" else row[-2] for row in rows]
        per_step = {step: sum(step in s.split() for s in steps) for step in STEPS}
        unread = sum(not s for s in steps)
        extra = f", {sum(row[1] for row in rows)} bytes" if name == "OROCHI_Z.exe" else ""
        lines.append(f"{name}: {len(rows)} pieces{extra}; read by "
                     + ", ".join(f"{step} {n}" for step, n in per_step.items()) + f"; served only {unread}")
    return "\n".join(lines)


def record(game: Path, donors: Path, lettering: Path, scratch: Path) -> tuple[dict, Path]:
    """Run the steps under the recorder; return the pieces document and the build's output folder."""
    with Recorder(game) as recorder:
        out = run_steps(game, donors, lettering, scratch, 1, on_step=lambda step: setattr(recorder, "step", step))
    if recorder.violations:
        raise RuntimeError("stock files read outside the tracked readers:\n" + "\n".join(recorder.violations))
    return pieces_document(game, recorder.reads, out), out


# ---------------------------------------------------------------- the junked copy (tests/test_stock_pieces.py)

def junk_game(game: Path, document: dict, destination: Path) -> None:
    """A copy of the game files the build reads, with only the listed pieces kept: every other LINKDATA entry
    replaced by a small junk entry of as many parts (so every later entry moves), the exe's bytes outside the spans
    scrambled, and the Voice.bin clips no piece lists zeroed (a sparse file)."""
    from wozmod.formats.linkdata import align
    from wozmod.install.stock import ARCHIVES, EXE, VOICE, VOICE_INDEX

    rng = random.Random(4251)
    pieces = document["pieces"]
    exe = bytearray(rng.randbytes((game / EXE).stat().st_size))
    stock_exe = (game / EXE).read_bytes()
    header = struct.unpack_from("<I", stock_exe, 0x3C)[0]
    for offset, length in [(row[0], row[1]) for row in pieces[EXE]] + [(header + 8, 4), (header + 24 + 56, 4)]:
        exe[offset:offset + length] = stock_exe[offset:offset + length]  # the stamp fields: the check's version test
    destination.mkdir(parents=True, exist_ok=True)
    (destination / EXE).write_bytes(exe)
    indexes, clips = stock_layout(game)
    for name in ARCHIVES:
        kept = {row[0] for row in pieces[name] if row[3]} | ({VOICE_INDEX} if name == "LINKDATA1" else set())
        records, trailer = [], b""
        with (game / f"{name}.BIN").open("rb") as source, (destination / f"{name}.BIN").open("wb") as target:
            for entry in indexes[name].entries:
                if entry.ordinal in kept:
                    source.seek(entry.offset)
                    allocation = source.read(entry.allocated_bytes)
                    sizes = list(entry.stored_sizes)
                else:
                    sizes = [rng.randrange(17, 3000) for _ in entry.stored_sizes]
                    allocation = b"".join(rng.randbytes(s) + bytes(align(s) - s) for s in sizes)
                target.write(allocation)
                if entry.record.allocated_bytes:
                    records.append((entry.record.size_or_descriptor, len(allocation), len(trailer)))
                    trailer += struct.pack(f"<{len(sizes) + 1}I", *sizes, 0)
                else:
                    records.append((sizes[0] if sizes else 0, 0, 0))
        (destination / f"{name}.IDX").write_bytes(b"".join(struct.pack("<3I", *r) for r in records) + trailer)
    (destination / VOICE).parent.mkdir(parents=True, exist_ok=True)
    with (game / VOICE).open("rb") as source, (destination / VOICE).open("wb") as target:
        target.truncate((game / VOICE).stat().st_size)
        for clip, _, steps, _ in pieces[VOICE]:
            if not steps:
                continue
            offset, size = clips[clip]
            source.seek(offset)
            target.seek(offset)
            target.write(source.read(size))


def inputs(args) -> tuple[Path, Path, Path]:
    found = checkout.inputs()
    paths = []
    for value, name in ((args.game, "WOZMOD_GAME"), (args.donors, "WOZMOD_DONORS"), (args.lettering, "WOZMOD_LETTERING")):
        value = value or found.get(name)
        if not value:
            raise SystemExit(f"stock_pieces: give --{name.removeprefix('WOZMOD_').lower()} or set {name}")
        paths.append(Path(value))
    return tuple(paths)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--game", type=Path)
    parser.add_argument("--donors", type=Path, help="a donors folder as the installer lays it out")
    parser.add_argument("--lettering", type=Path)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="compare with --output instead of writing it")
    args = parser.parse_args(argv)
    game, donors, lettering = inputs(args)
    with tempfile.TemporaryDirectory(prefix="stock-pieces-", dir=checkout.work()) as scratch:
        document, _ = record(game, donors, lettering, Path(scratch))
    text = dumps(document)
    print(summary(document))
    if args.check:
        same = args.output.is_file() and args.output.read_text(encoding="utf-8") == text
        print("the record matches" if same else f"{args.output} differs from a fresh record")
        return 0 if same else 1
    args.output.write_text(text, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
