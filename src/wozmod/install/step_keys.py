"""One key per install step (text, voices, art): the installer keeps a step's installed files when its key is the one
the install record holds, and the developer build cache (tools/build_cache.py) reuses its entries by the same key.

A key covers everything the step's output can depend on, in two parts:

- The mod's part, fixed when the release is built and shipped in it (step_keys.json beside data/: players have no
  source tree); a source checkout works it out live. It is derived from the source, not declared:
  - the installer function that runs the step (STEPS), the installer names it uses, and every wozmod module they
    import, transitively (an import anywhere in a module counts, as does each package's __init__);
  - the bundled files (resources.PACKAGED) that a string in that code names: the file or folder it names exactly;
    a string naming a folder above bundled ones names all of each;
  - the names of the WOZMOD_* environment variables that code names, but WOZMOD_JOBS (the worker count);
  - the toolchain: on a release, the Python, uv.lock, the native codec's source (native/) and the Rust compiler
    that builds it, the pinned ffmpeg and the platform; on a checkout, the Python, its packages, the codec's binary and the ffmpeg found.
  Two declared tables narrow the bundled files, and tests/test_step_keys.py proves each by running the steps with an
  audit hook: NOT_READ (named by the code, never opened by the step) and PROJECTED (a file the step reads only
  through one function, keyed by what that function returns).
- The player's part, from the pins of each input the step function takes: the game folder by the stock pieces the
  step reads (data/stock_pieces.json; the installer checks them before any step runs), the discs by the pins of the
  members and decrypted programs the step reads (DISC_READS, PROGRAMS), lettering.zip by its pin; and the values of
  the environment variables named above.

Movies have no step key: each is a donor member copied whole, so its pin (data/movies.json) is its key.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import sys
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from wozmod import process, resources

SCHEME = 2  # how keys are made; a change means no earlier key matches (1: the developer cache before this module)
STEPS = {"text": "_text_table", "voices": "_voice_banks", "art": "_menu_art"}  # step -> its function in wozmod.install
SCRATCH = {"out", "staging", "jobs"}  # parameters of a step function that are not inputs: where it writes, its workers
UNKEYED = {"WOZMOD_JOBS"}  # the worker count: a step writes the same bytes with any (tests/test_step_keys.py)
SHIPPED = "step_keys.json"  # the release's mod parts, beside data/ (packaging/build.py writes it)
_ENV = re.compile(r"WOZMOD_[A-Z0-9_]+")

# Bundled files a step's code names but the step never opens (proved by the audit run in tests/test_step_keys.py).
NOT_READ = {
    "voices": {"data/text": "the voices pair each stage line by its trigger, not through the translation",
               "data/movies.json": "the movies are copied, not built"},
    "art": {"data/text": "the menu words come from data/art and the donors' programs, not from the translation",
            "data/movies.json": "the movies are copied, not built",
            "data/art/faces": "the faces draw lettering.zip, which the release ships built"},
}


# A bundled file a step reads only through one function (the reader), keyed by the digest of what that reader
# returns: {step: {file: (module, reader, digest function)}}. The audit run proves the reader is its only opener.
PROJECTED: dict[str, dict[str, tuple[str, str, str]]] = {}


def _projection(step: str, name: str, path: Path) -> str:
    module, _, function = PROJECTED[step][name]
    return getattr(importlib.import_module(module), function)(path)
PROGRAMS = {"text", "art"}  # the steps that read the decrypted programs


def disc_reads(step: str) -> dict[str, tuple[str, ...]]:
    """The members of each disc the step reads, as extracted under the donors folder."""
    from wozmod.text.movie_captions import DUB_SCHEDULE_FILES
    from wozmod.voices.build import PSP_TEXT, PSP_VOICE_FILES, VOICE_DONOR_FILES

    ens = ("linkdata_ens.idx", "linkdata_ens.lnk")
    return {"text": {"wo1-x360": ens + DUB_SCHEDULE_FILES, "wo2-x360": ens, "wo2-psp": (PSP_TEXT,)},
            "voices": {"wo1-x360": VOICE_DONOR_FILES, "wo2-x360": VOICE_DONOR_FILES, "wo2-psp": PSP_VOICE_FILES},
            "art": {"wo1-x360": ens, "wo2-x360": ens}}[step]


class StepKeyError(Exception):
    """A key cannot be made (a step takes an input no part covers, or the record of stock pieces is unreadable)."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def key(document) -> str:
    return digest(json.dumps(document, sort_keys=True, separators=(",", ":")).encode())


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


# What the code reads, from its source


class _Code:
    """The wozmod modules under root/src, parsed once."""

    def __init__(self, root: Path):
        self.root = root
        self.parsed: dict[str, tuple[Path, ast.Module]] = {}
        resources_module = self.module("wozmod.resources")
        self.packaged: tuple[str, ...] = ()
        self.listing = None
        for node in resources_module[1].body if resources_module else ():  # the folders the release bundles
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PACKAGED" for t in node.targets):
                self.packaged = tuple(ast.literal_eval(node.value))
                self.listing = node  # names what the release bundles; reads none of it
        if not self.packaged:
            raise StepKeyError("wozmod.resources has no PACKAGED list")

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
        skip = {id(n) for n in ast.walk(self.listing)} if self.listing else set()
        return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in skip}

    def named(self, strings: set[str]) -> list[str]:
        """The bundled paths these strings name: exactly the file or folder a string names inside a bundled one,
        or every bundled one below a folder a string names."""
        found = set()
        for name in {s.strip("/") for s in strings} - {""}:
            for bundled in self.packaged:
                if name == bundled or name.startswith(bundled + "/"):
                    found.add(name)
                elif bundled.startswith(name + "/"):
                    found.add(bundled)
        return sorted(found)


def _installer_parts(code: _Code, function: str) -> tuple[dict[str, str], set[str], set[str], list[str]]:
    """What a step function in wozmod.install stands on: ({installer name: source}, modules, strings, parameters)."""
    found = code.module("wozmod.install")
    if found is None:
        raise StepKeyError("there is no wozmod.install")
    path, tree = found
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
        raise StepKeyError(f"wozmod.install has no function {function}")
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


def bundled_reads(step: str, root: Path, code: _Code | None = None) -> dict[str, list[str]]:
    """{"modules": the wozmod modules its key holds, "named": the bundled paths the step's code names, "keyed": the
    files its key holds by bytes, "projected": by projection}: paths relative to root (the audit run checks a step
    against these)."""
    code = code or _Code(root)
    _, roots, strings, _ = _installer_parts(code, STEPS[step])
    modules = code.closure(roots)
    for name in modules:
        strings |= code.strings(code.parsed[name][1])
    named = code.named(strings)
    files = {p.relative_to(root).as_posix() for n in named for p in _files(root / n)} | {
        n for n in named if not (root / n).exists()}  # a named file that is not there: keyed as absent
    skipped = NOT_READ.get(step, {})
    projected = sorted(f for f in files if f in PROJECTED.get(step, {}))
    keyed = sorted(f for f in files if f not in projected
                   and not any(f == s or f.startswith(s + "/") for s in skipped))
    return {"modules": sorted(modules), "named": named, "keyed": keyed, "projected": projected}


def mod_part(step: str, root: Path, toolchain: dict) -> dict:
    """The mod's part of a step's key, as one JSON document (see the module docstring)."""
    code = _Code(root)
    parts, roots, strings, parameters = _installer_parts(code, STEPS[step])
    modules = code.closure(roots)
    for name in modules:
        strings |= code.strings(code.parsed[name][1])
    unknown = [p for p in parameters if p not in SCRATCH and p not in PLAYER_INPUTS]
    if unknown:
        raise StepKeyError(f"{STEPS[step]} takes {', '.join(unknown)}: say how its key covers it (SCRATCH, "
                           "PLAYER_INPUTS)")
    reads = bundled_reads(step, root, code)
    return {"scheme": SCHEME, "step": step,
            "installer": {name: digest(text.encode()) for name, text in parts.items()},
            "modules": {name: _file_digest(path) for name, path in sorted(modules.items())},
            "data": {name: _file_digest(root / name) if (root / name).is_file() else None for name in reads["keyed"]},
            "projected": {name: _projection(step, name, root / name) for name in reads["projected"]},
            "environment": sorted(s for s in strings if _ENV.fullmatch(s) and s not in UNKEYED),
            "inputs": [p for p in parameters if p not in SCRATCH],
            "toolchain": toolchain}


# The toolchain


def live_toolchain() -> dict:
    """A checkout's: the Python, its packages, the native codec's binary and the ffmpeg on this machine."""
    native = importlib.util.find_spec("wozmod_native")  # rebuilt in place from native/, so its version never changes
    binaries = [] if native is None or not native.origin else sorted(
        p for p in Path(native.origin).parent.iterdir() if p.suffix in {".so", ".pyd", ".dll"})
    ffmpeg = resources.ffmpeg()
    if ffmpeg:
        run = process.run([ffmpeg, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        ffmpeg = {"binary": _file_digest(Path(shutil.which(ffmpeg) or ffmpeg)), "version": run.stdout}
    return {"python": [sys.version, sys.platform, platform.machine()],
            "packages": sorted(f"{d.name}=={d.version}" for d in importlib.metadata.distributions()),
            "native": {p.name: _file_digest(p) for p in binaries}, "ffmpeg": ffmpeg}


def rust_compiler(native: Path) -> str:
    """`rustc -V` of the compiler that builds native/: cargo's (RUSTC, else rustc on PATH, which rustup resolves
    from native/'s toolchain file when there is one)."""
    rustc = os.environ.get("RUSTC") or "rustc"
    try:
        run = process.run([rustc, "-V"], cwd=native, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    except OSError as error:
        raise StepKeyError(f"{rustc} cannot be run ({error}); a release builds its codecs (native/) with it") from None
    if run.returncode or not run.stdout.strip():
        raise StepKeyError(f"{rustc} -V failed: {run.stderr.strip()}")
    return run.stdout.strip()


def release_toolchain(root: Path, platform_name: str) -> dict:
    """A release's, by declared identities (a frozen bundle's bytes differ on every build): the Python it freezes,
    uv.lock, the native codec's source and the Rust compiler that builds it, the pinned ffmpeg and the platform."""
    native = root / "native"
    return {"python": [sys.version, sys.platform, platform.machine()],
            "uv.lock": _file_digest(root / "uv.lock"),
            "native": {p.relative_to(native).as_posix(): _file_digest(p) for p in _files(native)
                       if "target" not in p.relative_to(native).parts},
            "rustc": rust_compiler(native),
            "ffmpeg": json.loads((root / "packaging/ffmpeg.json").read_text(encoding="utf-8"))[platform_name]["sha256"],
            "platform": platform_name}


def shipped_document(root: Path, platform_name: str) -> dict:
    """What a release ships as step_keys.json: per step, the digest of its mod part and what the player's part
    needs (its inputs and the environment variables its code names)."""
    toolchain = release_toolchain(root, platform_name)
    steps = {}
    for step in STEPS:
        document = mod_part(step, root, toolchain)
        steps[step] = {"mod": key(document), "inputs": document["inputs"], "environment": document["environment"]}
    return {"scheme": SCHEME, "platform": platform_name, "steps": steps}


# The player's part


def stock_pieces(root: Path, step: str) -> str:
    """The digest of the stock pieces the step reads (data/stock_pieces.json): the installer checks each one before
    any step starts, and tests/test_stock_pieces.py proves a step reads nothing else of the game folder."""
    from wozmod.install.stock import parse

    try:
        record = parse(json.loads((root / "data/stock_pieces.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise StepKeyError(f"data/stock_pieces.json is unreadable: {error}") from error
    read = [[p.file, p.at, p.length, p.sha256, p.decoded_sha256] for p in record.pieces if step in p.steps]
    return digest(json.dumps([record.stamp, read]).encode())


def _donor_pins(step: str) -> dict:
    from wozmod.donors import DISCS
    from wozmod.donors.inventory import DONORS

    members = {disc: {f.path: f.sha256 for f in DONORS[disc].files} for disc in DISCS}
    pins = {disc: {name: members[disc][name] for name in names} for disc, names in disc_reads(step).items()}
    if step in PROGRAMS:
        pins["programs"] = {disc: each.program.plain.sha256 for disc, each in DISCS.items()}
    return pins


def _lettering_pin() -> str:
    from wozmod.install import LETTERING

    return LETTERING[1]


# How the player's part covers each input a step function takes (its other parameters are SCRATCH).
PLAYER_INPUTS: dict[str, Callable[[str, Path], object]] = {
    "game": lambda step, root: stock_pieces(root, step),
    "donors": lambda step, root: _donor_pins(step),
    "lettering": lambda step, root: _lettering_pin(),
}


def player_part(step: str, root: Path, inputs: list[str], environment: list[str]) -> dict:
    return {"inputs": {name: PLAYER_INPUTS[name](step, root) for name in inputs},
            "environment": {name: os.environ.get(name) for name in environment}}


# Keys


@lru_cache(maxsize=4)
def _live_toolchain() -> str:
    return json.dumps(live_toolchain())


def documents(root: Path | None = None, toolchain: dict | None = None) -> dict[str, dict]:
    """Per step, its key document {"mod": the mod part (a digest when shipped), "player": the player's part}.

    A release reads its shipped step_keys.json; a checkout works the mod parts out from its source."""
    root = Path(root or resources.root())
    shipped = root / SHIPPED
    if shipped.is_file():
        document = json.loads(shipped.read_text(encoding="utf-8"))
        if document.get("scheme") != SCHEME:
            raise StepKeyError(f"{SHIPPED} has key scheme {document.get('scheme')}, not {SCHEME}")
        mods = {step: (each["mod"], each["inputs"], each["environment"]) for step, each in document["steps"].items()}
    elif getattr(sys, "frozen", False):
        raise StepKeyError(f"this release has no {SHIPPED}")
    else:
        toolchain = toolchain if toolchain is not None else json.loads(_live_toolchain())
        mods = {}
        for step in STEPS:
            part = mod_part(step, root, toolchain)
            mods[step] = (part, part["inputs"], part["environment"])
    return {step: {"mod": mod, "player": player_part(step, root, inputs, environment)}
            for step, (mod, inputs, environment) in mods.items()}


def step_key(document: dict) -> dict:
    """{"key": the key of a step's key document, "parts": the digest of each part, which the install log compares}."""
    parts = {"mod": document["mod"] if isinstance(document["mod"], str) else key(document["mod"]),
             **{name: key(value) for name, value in document["player"]["inputs"].items()},
             "environment": key(document["player"]["environment"])}
    return {"key": key(parts), "parts": parts}


def keys(root: Path | None = None, toolchain: dict | None = None) -> dict[str, dict]:
    """Per step, its step_key."""
    return {step: step_key(document) for step, document in documents(root, toolchain).items()}


def movie_keys() -> dict[str, str]:
    """Each movie's place in the game folder, with its key: the pin it is copied and checked against."""
    from wozmod.movies import movie_donors

    return {f"wozmod/movie/{f.path}": f.sha256 for donor in movie_donors().values() for f in donor.files}
