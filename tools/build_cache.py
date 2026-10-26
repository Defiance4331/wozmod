"""Reuse the outputs of the installer's build steps whose inputs did not change (developer machines only).

tools/dev_install.py hands a BuildCache to wozmod.install.install; `wozmod install` and the release never do.
Entries live in work/build-cache/ of the checkout that runs it, and a step whose key has no entry is built
exactly as without the cache.

A step's key covers everything its output can depend on. It is worked out from the source files, not declared:
- the installer function that runs the step (STEPS), the installer functions, classes and module-level values it
  names, and every wozmod module they import, transitively (an import anywhere in a module counts, as does each
  package's __init__);
- every file under each folder the release bundles (resources.PACKAGED) that a string in that code names;
- the WOZMOD_* environment variables that code names, but WOZMOD_JOBS;
- each input the function takes: the game folder by the stock pieces the step reads (the installer checks them
  before any step starts), any other file or folder by its bytes;
- the Python and its packages, the native codec's binary and the ffmpeg.

The worker count is left out: a step writes the same bytes with any number of workers.

Movies are kept by content instead: each one is a donor member the installer checks against the pin in
data/movies.json as it copies it from the disc, so the file kept under that pin is the movie.

A build's files are kept as second names of the files it wrote, so a kept file is the installed one until the
next install replaces it. Each is reused only while its size and modification time are those it was kept with.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCHEME = 1  # a change to how keys are made: no older entry is reused
STEPS = {"text": "_text_table", "voices": "_voice_banks", "art": "_menu_art"}  # step -> its function in wozmod.install
SCRATCH = {"out", "staging", "jobs"}  # parameters of a step function that are not inputs: where it writes, its workers
UNKEYED = {"WOZMOD_JOBS"}  # the worker count
NOT_READ = {"donors": ("movie",)}  # _build puts the movies there only to move them to the output; no step names it
KEEP = 2  # entries kept per step, the most recently used
FICLONE = 0x40049409  # Linux ioctl: the copy shares the blocks of its source (btrfs, XFS)
_ENV = re.compile(r"WOZMOD_[A-Z0-9_]+")


class CacheError(Exception):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _files(path: Path, skip: tuple[str, ...] = ()) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                  and p.relative_to(path).parts[0] not in skip)


def _tree(path: Path, skip: tuple[str, ...] = ()) -> dict[str, str]:
    """Each file of a file or folder, relative to it, with its SHA-256 (hashed side by side)."""
    if not path.exists():
        raise CacheError(f"{path} is not there")
    files = _files(path, skip)
    with ThreadPoolExecutor(8) as pool:
        digests = list(pool.map(_file_digest, files))
    return {(p.relative_to(path).as_posix() if p != path else p.name): d for p, d in zip(files, digests, strict=True)}


def _clone(source: Path, target: Path) -> None:
    """Copy a file, sharing its blocks where the file system can."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl

        with source.open("rb") as read, target.open("wb") as write:
            fcntl.ioctl(write.fileno(), FICLONE, read.fileno())
        return
    except (ImportError, OSError):
        pass
    shutil.copyfile(source, target)


def _link(source: Path, target: Path) -> None:
    """Keep a file the build just wrote: a second name for it (sharing its blocks would wait for them to reach the
    disk, minutes on a busy one), else a copy."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        _clone(source, target)


def _stamp(path: Path) -> list[int]:
    """What changes when a kept file is written to (the installed copy of a build is the same file)."""
    found = path.stat()
    return [found.st_size, found.st_mtime_ns]


# What the code reads, from its source


class _Code:
    """The wozmod modules under root/src, parsed once."""

    def __init__(self, root: Path):
        self.root = root
        self.parsed: dict[str, tuple[Path, ast.Module]] = {}
        resources = self.module("wozmod.resources")
        self.packaged: tuple[str, ...] = ()
        for node in resources[1].body:  # the folders the release bundles: the only files the installer can read
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PACKAGED" for t in node.targets):
                self.packaged = tuple(ast.literal_eval(node.value))
                self.listing = node  # names what the release bundles; reads none of it
        if not self.packaged:
            raise CacheError("wozmod.resources has no PACKAGED list")

    def module(self, name: str) -> tuple[Path, ast.Module] | None:
        if name not in self.parsed:
            base = self.root / "src" / Path(*name.split("."))
            path = next((p for p in (base / "__init__.py", base.with_suffix(".py")) if p.is_file()), None)
            if path is None:
                return None
            self.parsed[name] = (path, ast.parse(path.read_bytes(), str(path)))
        return self.parsed[name]

    @staticmethod
    def imports(node: ast.AST, package: str) -> set[str]:
        """The module names an import anywhere in node may load (absolute; a from-import's names may be modules)."""
        found = set()
        for each in ast.walk(node):
            if isinstance(each, ast.Import):
                found |= {alias.name for alias in each.names}
            elif isinstance(each, ast.ImportFrom):
                base = each.module or ""
                if each.level:
                    parts = package.split(".")[: len(package.split(".")) - each.level + 1]
                    base = ".".join([*parts, *([each.module] if each.module else [])])
                found |= {base, *(f"{base}.{alias.name}" for alias in each.names)}
        return found

    def closure(self, names: set[str]) -> dict[str, Path]:
        """Every wozmod module these load, with the packages above each."""
        seen: dict[str, Path] = {}
        todo = [n for n in names if n == "wozmod" or n.startswith("wozmod.")]
        while todo:
            name = todo.pop()
            if name in seen or (found := self.module(name)) is None:
                continue
            path, tree = found
            seen[name] = path
            parts = name.split(".")
            todo += [".".join(parts[:i]) for i in range(1, len(parts))]
            package = name if path.name == "__init__.py" else ".".join(parts[:-1])
            todo += [n for n in self.imports(tree, package) if n.startswith("wozmod")]
        return seen

    def strings(self, node: ast.AST) -> set[str]:
        skip = {id(n) for n in ast.walk(self.listing)}
        return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in skip}

    def named(self, strings: set[str]) -> list[str]:
        """The bundled folders and files these strings name (a string inside one, or a folder above one)."""
        names = {s.strip("/") for s in strings}
        return sorted(r for r in self.packaged if any(s == r or s.startswith(r + "/") or r.startswith(s + "/")
                                                      for s in names if s))


def _installer_parts(code: _Code, function: str) -> tuple[dict[str, str], set[str], set[str], list[str]]:
    """What a step function in wozmod.install stands on: ({installer name: source}, modules, strings, parameters)."""
    path, tree = code.module("wozmod.install")
    source = path.read_text(encoding="utf-8")
    defined: dict[str, ast.AST] = {}
    imported: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defined[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        defined[name.id] = node
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                imported[local] = code.imports(node, "wozmod.install")
    if not isinstance(defined.get(function), ast.FunctionDef):
        raise CacheError(f"wozmod.install has no function {function}")
    node = defined[function]
    parameters = [a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
    parts, modules, strings = {}, set(), set()
    todo = [function]
    while todo:
        name = todo.pop()
        if name in parts:
            continue
        node = defined[name]
        parts[name] = ast.get_source_segment(source, node)
        modules |= code.imports(node, "wozmod.install")
        strings |= code.strings(node)
        for each in ast.walk(node):
            if isinstance(each, ast.Name):
                if each.id in defined:
                    todo.append(each.id)
                modules |= imported.get(each.id, set())
    return parts, modules, strings, parameters


def stock_pieces(root: Path, step: str) -> str:
    """The digest of the stock pieces the step reads (data/stock_pieces.json): the installer checks each one before
    any step starts, and tests/test_stock_pieces.py proves a step reads nothing else of the game folder."""
    from wozmod.install.stock import parse

    try:
        record = parse(json.loads((root / "data/stock_pieces.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CacheError(f"data/stock_pieces.json is unreadable: {error}") from error
    read = [[p.file, p.at, p.length, p.sha256, p.decoded_sha256] for p in record.pieces if step in p.steps]
    return _digest(json.dumps([record.stamp, read]).encode())


def machine() -> dict:
    """The Python, its packages, the native codec's binary and the ffmpeg on this machine."""
    from wozmod import resources

    native = importlib.util.find_spec("wozmod_native")  # rebuilt in place from native/, so its version never changes
    binaries = [] if native is None or not native.origin else sorted(
        p for p in Path(native.origin).parent.iterdir() if p.suffix in {".so", ".pyd", ".dll"})
    ffmpeg = resources.ffmpeg()
    if ffmpeg:
        run = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True)
        ffmpeg = {"binary": _file_digest(Path(shutil.which(ffmpeg) or ffmpeg)), "version": run.stdout}
    return {"python": [sys.version, sys.platform, platform.machine()],
            "packages": sorted(f"{d.name}=={d.version}" for d in importlib.metadata.distributions()),
            "native": {p.name: _file_digest(p) for p in binaries}, "ffmpeg": ffmpeg}


def step_inputs(step: str, root: Path, sources: dict[str, Path], here: dict, trees: dict | None = None) -> dict:
    """Everything the output of a step can depend on (see the module docstring), as one JSON document.

    trees holds the input hashes of one build, so the steps that share an input hash it once."""
    trees = {} if trees is None else trees
    code = _Code(root)
    parts, roots, strings, parameters = _installer_parts(code, STEPS[step])
    modules = code.closure(roots)
    for path, tree in (code.parsed[name] for name in modules):
        strings |= code.strings(tree)
    unknown = [p for p in parameters if p not in SCRATCH and p not in sources]
    if unknown:
        raise CacheError(f"{STEPS[step]} takes {', '.join(unknown)}: say how its key covers them (SCRATCH, sources)")
    inputs = {}
    for name in (p for p in parameters if p not in SCRATCH):
        if name == "game":
            inputs[name] = {"stock_pieces": stock_pieces(root, step)}
        else:
            if name not in trees:
                trees[name] = _tree(Path(sources[name]), NOT_READ.get(name, ()))
            inputs[name] = trees[name]
    return {"scheme": SCHEME, "step": step,
            "installer": {name: _digest(text.encode()) for name, text in parts.items()},
            "modules": {name: _file_digest(path) for name, path in sorted(modules.items())},
            "data": {f"{named}/{rel}" if (root / named).is_dir() else named: digest
                     for named in code.named(strings) for rel, digest in _tree(root / named).items()},
            "environment": {name: os.environ.get(name) for name in sorted(s for s in strings if _ENV.fullmatch(s))
                            if name not in UNKEYED},
            "inputs": inputs, "machine": here}


def key(document: dict) -> str:
    return _digest(json.dumps(document, sort_keys=True, separators=(",", ":")).encode())


def _leaves(document, prefix=()):
    if isinstance(document, dict) and document:
        for name, value in document.items():
            yield from _leaves(value, (*prefix, str(name)))
    else:
        yield prefix, json.dumps(document, sort_keys=True)


def changes(old: dict, new: dict, most: int = 6) -> list[str]:
    """The leaves of two key documents that differ, as "inputs: donors/x.idx"."""
    before, after = dict(_leaves(old)), dict(_leaves(new))
    found = sorted(f"{path[0]}: {'/'.join(path[1:])}" if len(path) > 1 else "/".join(path)
                   for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    return found[:most] + ([f"and {len(found) - most} more"] if len(found) > most else [])


class BuildCache:
    """The `cache` of wozmod.install.install: fetch fills a step's folder from an entry, keep saves one."""

    def __init__(self, folder: Path, root: Path | None = None, here: dict | None = None):
        from wozmod import resources

        self.folder = Path(folder)
        self.root = Path(root or resources.root())  # the code that is running, not necessarily this checkout
        self._here = here
        self._keys: dict[tuple, tuple[str, dict]] = {}
        self._trees: dict[tuple, dict] = {}
        self.report: list[str] = []

    def here(self) -> dict:
        if self._here is None:
            self._here = machine()
        return self._here

    def _key(self, step: str, sources: dict[str, Path]) -> tuple[str, dict]:
        build = tuple(sorted((name, str(path)) for name, path in sources.items()))  # one build: its staging folder
        if (step, build) not in self._keys:  # made once, before the step runs: what it writes cannot change its key
            document = step_inputs(step, self.root, sources, self.here(), self._trees.setdefault(build, {}))
            self._keys[step, build] = (key(document), document)
        return self._keys[step, build]

    def fetch(self, step: str, sources: dict[str, Path], folder: Path) -> bool:
        if step == "movies":
            return self._fetch_movies(Path(folder))
        start = time.monotonic()
        digest, document = self._key(step, sources)
        entry = self.folder / step / digest
        try:
            listed = json.loads((entry / "entry.json").read_text(encoding="utf-8"))["files"]
            for name, stamp in listed.items():
                if _stamp(entry / "files" / name) != stamp:
                    raise CacheError(f"{name} was changed after it was kept")
                _clone(entry / "files" / name, Path(folder) / name)
        except (OSError, ValueError, KeyError, CacheError) as error:
            shutil.rmtree(folder, ignore_errors=True)
            if not isinstance(error, FileNotFoundError):
                shutil.rmtree(entry, ignore_errors=True)  # so that this build's output is kept in its place
            self.report.append(f"{step}: built ({self._why(step, document, error)})")
            return False
        os.utime(entry)
        self.report.append(f"{step}: reused {len(listed)} files ({time.monotonic() - start:.1f} s)")
        return True

    def _why(self, step: str, document: dict, error: Exception) -> str:
        entries = sorted((p for p in (self.folder / step).glob("*/entry.json")), key=lambda p: p.stat().st_mtime)
        if not isinstance(error, FileNotFoundError) or not entries:
            return "nothing kept" if isinstance(error, FileNotFoundError) else f"kept entry unusable: {error}"
        last = json.loads(entries[-1].read_text(encoding="utf-8"))["inputs"]
        return "changed: " + ", ".join(changes(last, document))

    def keep(self, step: str, sources: dict[str, Path], folder: Path) -> None:
        try:
            if step == "movies":
                return self._keep_movies(Path(folder))
            digest, document = self._key(step, sources)
            entry = self.folder / step / digest
            if entry.is_dir():
                return
            partial = self.folder / step / f".{digest}.{os.getpid()}"
            shutil.rmtree(partial, ignore_errors=True)
            files = _files(Path(folder))
            for path in files:
                _link(path, partial / "files" / path.relative_to(folder))
            (partial / "entry.json").write_text(json.dumps(
                {"inputs": document, "files": {p.relative_to(folder).as_posix(): _stamp(p) for p in files}},
                indent=1, sort_keys=True), encoding="utf-8")
            os.rename(partial, entry)
            for old in sorted((p for p in (self.folder / step).iterdir() if p.is_dir() and not p.name.startswith(".")),
                              key=lambda p: p.stat().st_mtime)[:-KEEP]:
                shutil.rmtree(old)
        except OSError as error:  # a full disk: the install goes on, the next one builds the step again
            self.report.append(f"{step}: not kept ({error})")

    @staticmethod
    def _movies() -> list[tuple[str, int, str]]:
        from wozmod.movies import movie_donors

        return [(f.path, f.size, f.sha256) for donor in movie_donors().values() for f in donor.files]

    def _fetch_movies(self, folder: Path) -> bool:
        start, movies, blobs = time.monotonic(), self._movies(), self.folder / "movies"
        try:
            stamps = json.loads((self.folder / "movies.json").read_text(encoding="utf-8"))
            missing = [path for path, _, sha in movies if sha not in stamps or _stamp(blobs / sha) != stamps[sha]]
        except (OSError, ValueError):
            missing = [path for path, _, _ in movies]
        try:
            if not missing:
                for path, _, sha in movies:
                    _clone(blobs / sha, folder / path)
        except OSError as error:
            missing = [str(error)]
        if missing:
            shutil.rmtree(folder, ignore_errors=True)
            self.report.append(f"movies: copied from the discs ({len(missing)} of {len(movies)} not kept or changed)")
            return False
        self.report.append(f"movies: reused {len(movies)} files ({time.monotonic() - start:.1f} s)")
        return True

    def _keep_movies(self, folder: Path) -> None:
        movies, blobs, index = self._movies(), self.folder / "movies", self.folder / "movies.json"
        blobs.mkdir(parents=True, exist_ok=True)
        index.unlink(missing_ok=True)
        stamps = {}
        for path, _, sha in movies:  # each was checked against its pin as it was copied from the disc
            (blobs / sha).unlink(missing_ok=True)
            _link(folder / path, blobs / sha)
            stamps[sha] = _stamp(blobs / sha)
        for old in blobs.iterdir():
            if old.name not in stamps:
                old.unlink()
        index.write_text(json.dumps(stamps, indent=1, sort_keys=True), encoding="utf-8")
