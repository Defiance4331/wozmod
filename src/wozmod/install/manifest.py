"""What each install step's output may depend on: one Manifest per step (text, voices, art).

The step key (step_keys) hashes a step's manifest and nothing else but the toolchain, and the read gate (gate) lets
the step read only what its manifest names, so the key and the gate cannot disagree. A manifest holds:

- from the source, derived and not declared:
  - inputs: the step function's parameters that are player inputs (INPUTS; the others are SCRATCH);
  - installer: the step function in wozmod.install (steps.STEPS) and the installer names it uses, each by the digest
    of its source;
  - modules: every wozmod module those import, transitively (an import anywhere in a module counts, as does each
    package's __init__);
  - bundled: the bundled files (resources.PACKAGED) that a string in that code names: the file or folder it names
    exactly, or every bundled one below a folder it names; but NOT_READ, files the code names and the step never
    opens;
- declared here: the disc members it reads (disc_reads), the discs whose decrypted program it reads (PROGRAMS), the
  WOZMOD_* settings it reads (ENVIRONMENT, the only ones its worker gets) and the programs it starts (STARTS);
- from data/stock_pieces.json: the pieces of the game files it reads (the installer checks them before any step runs).

A source checkout derives the source parts live. A release has no source: it reads them from the step_keys.json it
ships (step_keys.shipped_document writes it when the release is built).
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from wozmod import resources
from wozmod.install import steps

INPUTS = ("game", "donors", "lettering", "speech")  # step_keys.PLAYER_INPUTS says how a key covers each
SCRATCH = {"out", "staging", "jobs"}  # parameters of a step function that are not inputs: where it writes, its workers
SHIPPED = "step_keys.json"  # a release's source parts and mod parts, beside data/ (packaging/build.py writes it)
SOURCE_PARTS = ("inputs", "installer", "modules", "bundled")  # what a release reads from SHIPPED

# Bundled files a step's code names but the step never opens (the read gate refuses a read of one).
NOT_READ = {
    "voices": {"data/movies.json": "the movies are copied, not built"},
    "art": {"data/movies.json": "the movies are copied, not built",
            "data/art/faces": "the faces draw lettering.zip, which the release ships built"},
}
# The discs whose decrypted program each step reads, None for every disc's; the voices read WO2 X360's Dream teams.
PROGRAMS = {"text": None, "voices": ("wo2-x360",), "art": None}
# The WOZMOD_* settings each step reads. The art modules' own command lines read more (WOZMOD_GAME, ...), as argparse
# defaults that the step never reaches; those are not the step's.
ENVIRONMENT = {
    "text": ("WOZMOD_NATIVE",),
    "voices": ("WOZMOD_NATIVE",),
    "art": ("WOZMOD_NATIVE", "WOZMOD_FONT_PYTHON", "WOZMOD_ART_TRACE"),
}
STARTS = {"text": (), "voices": ("ffmpeg",), "art": ()}  # the programs each step starts, by name without .exe


def disc_reads(step: str) -> dict[str, tuple[str, ...]]:
    """The members of each disc the step reads, as extracted under the donors folder."""
    from wozmod.text.movie_captions import DUB_SCHEDULE_FILES
    from wozmod.voices.build import PSP_TEXT, PSP_VOICE_FILES, VOICE_DONOR_FILES

    ens = ("linkdata_ens.idx", "linkdata_ens.lnk")
    return {"text": {"wo1-x360": ens + DUB_SCHEDULE_FILES, "wo2-x360": ens, "wo2-psp": (PSP_TEXT,)},
            "voices": {"wo1-x360": VOICE_DONOR_FILES, "wo2-x360": VOICE_DONOR_FILES, "wo2-psp": PSP_VOICE_FILES},
            "art": {"wo1-x360": ens, "wo2-x360": ens}}[step]


class StepKeyError(Exception):
    """A manifest or key cannot be made (a step takes an input no part covers, the release ships no source parts, or
    the record of stock pieces is unreadable)."""


@dataclass(frozen=True)
class Manifest:
    """Everything one step's output may depend on (see the module docstring); paths are relative, with /."""
    step: str
    inputs: tuple[str, ...]
    installer: dict[str, str]  # installer name -> SHA-256 of its source
    modules: tuple[str, ...]
    bundled: tuple[str, ...]  # relative to resources.root()
    donors: dict[str, tuple[str, ...]]  # disc -> members, relative to its folder under the donors folder
    programs: tuple[str, ...]  # the discs whose decrypted program it reads
    stock_stamp: tuple[int, int]  # the build stamp of the OROCHI_Z.exe the pieces were recorded from
    stock: tuple  # the stock.Piece objects it reads
    environment: tuple[str, ...]
    starts: tuple[str, ...]

    def donor_files(self) -> tuple[str, ...]:
        """The donors folder's files it reads: the members, and the programs where the installer stages them."""
        from wozmod.donors.discs import DISCS

        found = {f"{DISCS[disc].donor.name}/{name}" for disc, names in self.donors.items() for name in names}
        found |= {f"{DISCS[disc].donor.name}/{DISCS[disc].program.staged}" for disc in self.programs}
        return tuple(sorted(found))


def manifests(root: Path | None = None) -> dict[str, Manifest]:
    """Per step, its manifest: from root's source in a checkout, from the shipped step_keys.json in a release."""
    root = Path(root or resources.root())
    shipped = root / SHIPPED
    if shipped.is_file():
        document = json.loads(shipped.read_text(encoding="utf-8"))
        try:
            source = {step: {part: document["steps"][step]["manifest"][part] for part in SOURCE_PARTS}
                      for step in steps.STEPS}
        except (KeyError, TypeError):
            raise StepKeyError(f"{SHIPPED} holds no source parts for the steps") from None
    elif getattr(sys, "frozen", False):
        raise StepKeyError(f"this release has no {SHIPPED}")
    else:
        code = _Code(root)
        source = {step: _source_parts(code, step) for step in steps.STEPS}
    stamp, pieces = _stock(root)
    return {step: Manifest(step=step, inputs=tuple(parts["inputs"]), installer=dict(parts["installer"]),
                           modules=tuple(parts["modules"]), bundled=tuple(parts["bundled"]),
                           donors=disc_reads(step), programs=_programs(step), stock_stamp=stamp,
                           stock=tuple(p for p in pieces if step in p.steps), environment=ENVIRONMENT[step],
                           starts=STARTS[step])
            for step, parts in source.items()}


def manifest(step: str, root: Path | None = None) -> Manifest:
    return manifests(root)[step]


def source_parts(found: Manifest) -> dict:
    """The parts a release cannot derive, as JSON (what step_keys.json ships per step)."""
    return {part: getattr(found, part) for part in SOURCE_PARTS}


def _programs(step: str) -> tuple[str, ...]:
    from wozmod.donors.discs import DISCS

    return (PROGRAMS[step] or tuple(DISCS)) if step in PROGRAMS else ()


def _stock(root: Path) -> tuple[tuple[int, int], tuple]:
    from wozmod.install.stock import parse

    try:
        record = parse(json.loads((root / "data/stock_pieces.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise StepKeyError(f"data/stock_pieces.json is unreadable: {error}") from error
    return tuple(record.stamp), record.pieces


def files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def source_file(root: Path, module: str) -> Path | None:
    """The file of a wozmod module under root/src (a package's __init__.py), None when there is none."""
    base = root / "src" / Path(*module.split("."))
    return next((p for p in (base / "__init__.py", base.with_suffix(".py")) if p.is_file()), None)


# The source parts, derived from the code


# (module, SHA-256 of its source) -> (the wozmod modules it may load, its strings): keyed by the bytes, so never
# stale. A checkout derives the manifests at every key and every steps.run, from mostly unchanged source.
_FACTS: dict[tuple[str, str], tuple[frozenset[str], frozenset[str]]] = {}


def _listing(tree: ast.Module) -> ast.Assign | None:
    """wozmod.resources' PACKAGED = (...): names what the release bundles, reads none of it."""
    return next((node for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "PACKAGED" for t in node.targets)), None)


def _walk(node: ast.AST, package: str, skip: set[int] = frozenset()) -> tuple[set[str], set[str]]:
    """(the module names an import anywhere in node may load, absolute (a from-import's names may be modules); the
    strings in node but those below skip), in one walk."""
    found, strings = set(), set()
    for each in ast.walk(node):
        if isinstance(each, ast.Constant):
            if isinstance(each.value, str) and id(each) not in skip:
                strings.add(each.value)
        elif isinstance(each, ast.Import):
            found |= {alias.name for alias in each.names}
        elif isinstance(each, ast.ImportFrom):
            base = each.module or ""
            if each.level:
                parts = package.split(".")[: len(package.split(".")) - each.level + 1]
                base = ".".join([*parts, *([each.module] if each.module else [])])
            found |= {base, *(f"{base}.{alias.name}" for alias in each.names)}
    return found, strings


class _Code:
    """The wozmod modules under root/src."""

    def __init__(self, root: Path):
        self.root = root
        found = self.module("wozmod.resources")
        listing = _listing(found[1]) if found else None
        if listing is None:
            raise StepKeyError("wozmod.resources has no PACKAGED list")
        self.packaged: tuple[str, ...] = tuple(ast.literal_eval(listing.value))  # the folders the release bundles

    def module(self, name: str) -> tuple[Path, ast.Module] | None:
        path = source_file(self.root, name)
        return None if path is None else (path, ast.parse(path.read_bytes(), str(path)))

    def facts(self, name: str) -> tuple[frozenset[str], frozenset[str]] | None:
        """(the wozmod modules a module may load, its strings), None when it has no source file."""
        path = source_file(self.root, name)
        if path is None:
            return None
        data = path.read_bytes()
        cached = (name, hashlib.sha256(data).hexdigest())
        if cached not in _FACTS:
            tree = ast.parse(data, str(path))
            listing = _listing(tree) if name == "wozmod.resources" else None
            imports, strings = _walk(tree, name if path.name == "__init__.py" else name.rpartition(".")[0],
                                     {id(n) for n in ast.walk(listing)} if listing else set())
            _FACTS[cached] = (frozenset(n for n in imports if n.startswith("wozmod")), frozenset(strings))
        return _FACTS[cached]

    def closure(self, names: set[str]) -> dict[str, frozenset[str]]:
        """Every wozmod module these load, with the packages above each, and the strings of each."""
        seen: dict[str, frozenset[str]] = {}
        todo = [n for n in names if n == "wozmod" or n.startswith("wozmod.")]
        while todo:
            name = todo.pop()
            if name in seen or (found := self.facts(name)) is None:
                continue
            seen[name] = found[1]
            parts = name.split(".")
            todo += [".".join(parts[:i]) for i in range(1, len(parts))]
            todo += found[0]
        return seen

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
                imported[local] = _walk(node, "wozmod.install")[0]
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
        imports, found = _walk(node, "wozmod.install")
        modules |= imports
        strings |= found
        for each in ast.walk(node):
            if isinstance(each, ast.Name):
                if each.id in defined:
                    todo.append(each.id)
                modules |= imported.get(each.id, set())
    return parts, modules, strings, parameters


def _source_parts(code: _Code, step: str) -> dict:
    function = steps.STEPS[step].function
    parts, roots, strings, parameters = _installer_parts(code, function)
    unknown = [p for p in parameters if p not in SCRATCH and p not in INPUTS]
    if unknown:
        raise StepKeyError(f"{function} takes {', '.join(unknown)}: say how its key covers it (SCRATCH, INPUTS)")
    modules = code.closure(roots)
    for each in modules.values():
        strings |= each
    root = code.root
    found = {p.relative_to(root).as_posix() for n in code.named(strings) for p in files(root / n)} | {
        n for n in code.named(strings) if not (root / n).exists()}  # a named file that is not there: read as absent
    skipped = NOT_READ.get(step, {})
    return {"inputs": [p for p in parameters if p not in SCRATCH],
            "installer": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in parts.items()},
            "modules": sorted(modules),
            "bundled": sorted(f for f in found if not any(f == s or f.startswith(s + "/") for s in skipped))}
