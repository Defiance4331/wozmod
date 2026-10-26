"""Reuse the outputs of the installer's build steps whose inputs did not change (developer machines only).

tools/dev_install.py hands a BuildCache to wozmod.install.install; `wozmod install` and the release never do.
Entries live in work/build-cache/ of the checkout that runs it, and a step whose key has no entry is built
exactly as without the cache. An install over an earlier one already keeps the unchanged steps in place; this cache
also keeps the last two builds of each step, so switching between branches rebuilds nothing.

A step's key is the installer's own (wozmod.install.step_keys), worked out live from this checkout's source and
toolchain. Movies are kept by content instead: each one is a donor member the installer checks against the pin in
data/movies.json as it copies it from the disc, so the file kept under that pin is the movie.

A build's files are kept as second names of the files it wrote, so a kept file is the installed one until the
next install replaces it. Each is reused only while its size and modification time are those it was kept with.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from wozmod.install import step_keys

REPO = Path(__file__).resolve().parents[1]
KEEP = 2  # entries kept per step, the most recently used
FICLONE = 0x40049409  # Linux ioctl: the copy shares the blocks of its source (btrfs, XFS)


class CacheError(Exception):
    pass


def _files(path: Path) -> list[Path]:
    return sorted(p for p in path.rglob("*") if p.is_file())


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


def _leaves(document, prefix=()):
    if isinstance(document, dict) and document:
        for name, value in document.items():
            yield from _leaves(value, (*prefix, str(name)))
    else:
        yield prefix, json.dumps(document, sort_keys=True)


def changes(old: dict, new: dict, most: int = 6) -> list[str]:
    """The leaves of two key documents that differ, as "player: inputs/donors/wo2-x360/link_vodat.bdx"."""
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
        self._keys: dict[tuple, dict[str, tuple[str, dict]]] = {}
        self.report: list[str] = []

    def here(self) -> dict:
        if self._here is None:
            self._here = step_keys.live_toolchain()
        return self._here

    def _key(self, step: str, sources: dict[str, Path]) -> tuple[str, dict]:
        build = tuple(sorted((name, str(path)) for name, path in sources.items()))  # one build: its staging folder
        if build not in self._keys:  # made once, before the steps run: what they write cannot change their keys
            self._keys[build] = {name: (step_keys.step_key(document)["key"], document)
                                 for name, document in step_keys.documents(self.root, self.here()).items()}
        return self._keys[build][step]

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
