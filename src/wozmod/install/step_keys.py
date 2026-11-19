"""One key per install step (text, voices, art): the installer keeps a step's installed files when its key is the one
the install record holds.

A key hashes the step's manifest (install/manifest.py: everything its output may depend on, which the read gate also
holds the step to) and the toolchain, in parts the install log compares:

- The mod's part, fixed when the release is built and shipped in it (step_keys.json beside data/: players have no
  source tree); a source checkout works it out live: the manifest's inputs, installer names, settings and programs,
  the bytes of its modules and bundled files, and the toolchain: on a release, the Python, uv.lock, the native codec's
  source (native/) and the Rust compiler that builds it, the pinned ffmpeg and the platform; on a checkout, the
  Python, its packages, the codec's binary and the ffmpeg found.
- The player's part, one per input the step function takes: the game folder by the stock pieces the step reads (the
  installer checks them before any step runs), the discs by the pins of the members and decrypted programs it reads,
  lettering.zip by its bytes and the speech model by its pins; and the values of the settings it reads.

Copied movies have no step key: each is a donor member copied whole, so its pin (data/movies.json) is its key. The
Dream movies the voices step builds (voices/dream_movies.py) are that step's files.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import sys
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from platform import machine

from wozmod import platform, resources
from wozmod.install import manifest
from wozmod.install.manifest import Manifest, StepKeyError

SCHEME = 3  # how keys are made; a change means no earlier key matches (3: the key hashes the step's manifest)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def key(document) -> str:
    return digest(json.dumps(document, sort_keys=True, separators=(",", ":")).encode())


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def mod_part(found: Manifest, root: Path, toolchain: dict) -> dict:
    """The mod's part of a step's key, as one JSON document (see the module docstring)."""
    modules = {name: manifest.source_file(root, name) for name in found.modules}
    return {"scheme": SCHEME, "step": found.step, "inputs": list(found.inputs), "installer": found.installer,
            "modules": {name: _file_digest(path) if path else None for name, path in modules.items()},
            "bundled": {name: _file_digest(root / name) if (root / name).is_file() else None for name in found.bundled},
            "environment": list(found.environment), "starts": list(found.starts), "toolchain": toolchain}


# The toolchain


def live_toolchain() -> dict:
    """A checkout's: the Python, its packages, the native codec's binary and the ffmpeg on this machine."""
    native = importlib.util.find_spec("wozmod_native")  # rebuilt in place from native/, so its version never changes
    binaries = [] if native is None or not native.origin else sorted(
        p for p in Path(native.origin).parent.iterdir() if p.suffix in {".so", ".pyd", ".dll"})
    ffmpeg = resources.ffmpeg()
    if ffmpeg:
        run = platform.run_hidden([ffmpeg, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        ffmpeg = {"binary": _file_digest(Path(shutil.which(ffmpeg) or ffmpeg)), "version": run.stdout}
    return {"python": [sys.version, platform.NAME, machine()],
            "packages": sorted(f"{d.name}=={d.version}" for d in importlib.metadata.distributions()),
            "native": {p.name: _file_digest(p) for p in binaries}, "ffmpeg": ffmpeg}


def rust_compiler(native: Path) -> str:
    """`rustc -V` of the compiler that builds native/: cargo's (RUSTC, else rustc on PATH, which rustup resolves
    from native/'s toolchain file when there is one). A cross build packaged where that compiler cannot run (the
    Windows zip, packaged under Wine) declares its `rustc -V` in WOZMOD_RUSTC_VERSION instead."""
    if declared := os.environ.get("WOZMOD_RUSTC_VERSION", "").strip():
        return declared
    rustc = os.environ.get("RUSTC") or "rustc"
    try:
        run = platform.run_hidden([rustc, "-V"], cwd=native, capture_output=True, text=True, encoding="utf-8",
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
    return {"python": [sys.version, platform.NAME, machine()],
            "uv.lock": _file_digest(root / "uv.lock"),
            "native": {p.relative_to(native).as_posix(): _file_digest(p) for p in manifest.files(native)
                       if "target" not in p.relative_to(native).parts},
            "rustc": rust_compiler(native),
            "ffmpeg": json.loads((root / "packaging/pins.json").read_text(encoding="utf-8"))
            ["ffmpeg"][platform_name]["sha256"],
            "platform": platform_name}


def shipped_document(root: Path, platform_name: str) -> dict:
    """What a release ships as step_keys.json: per step, the digest of its mod part and the source parts of its
    manifest (manifest.source_parts), which a release cannot derive."""
    toolchain = release_toolchain(root, platform_name)
    return {"scheme": SCHEME, "platform": platform_name,
            "steps": {step: {"mod": key(mod_part(found, root, toolchain)), "manifest": manifest.source_parts(found)}
                      for step, found in manifest.manifests(root).items()}}


# The player's part


def _stock_pieces(found: Manifest) -> str:
    read = [[p.file, p.at, p.length, p.sha256, p.decoded_sha256] for p in found.stock]
    return digest(json.dumps([list(found.stock_stamp), read]).encode())


def _donor_pins(found: Manifest) -> dict:
    from wozmod.donors import DISCS
    from wozmod.donors.inventory import DONORS

    members = {disc: {f.path: f.sha256 for f in DONORS[disc].files} for disc in DISCS}
    pins = {disc: {name: members[disc][name] for name in names} for disc, names in found.donors.items()}
    if found.programs:
        pins["programs"] = {disc: DISCS[disc].program.plain.sha256 for disc in found.programs}
    return pins


def lettering_file(root: Path | None = None) -> Path:
    """The lettering.zip a step build gets where its install names none: the release's own, else a checkout's
    WOZMOD_LETTERING (steps.Inputs.environment)."""
    bundled = Path(root or resources.root()) / "lettering.zip"
    if bundled.is_file() or "WOZMOD_LETTERING" not in os.environ:
        return bundled
    return Path(os.environ["WOZMOD_LETTERING"])


def _lettering(lettering: Path) -> str:
    """The bytes of the lettering.zip the install gives the step: a checkout's need not be the release's (decision 55),
    so its pin would keep the art built from another one."""
    try:
        return _file_digest(lettering)
    except OSError as error:
        raise StepKeyError(f"{lettering} cannot be read ({error.strerror}), so the art step has no key") from None


def _speech_pins() -> str:
    from wozmod.voices.phones import MODEL

    return key(MODEL)


# How the player's part covers each input a step function takes (manifest.INPUTS), given the install's lettering.zip.
PLAYER_INPUTS: dict[str, Callable[[Manifest, Path], object]] = {
    "game": lambda found, lettering: _stock_pieces(found),
    "donors": lambda found, lettering: _donor_pins(found),
    "lettering": lambda found, lettering: _lettering(lettering),
    "speech": lambda found, lettering: _speech_pins(),
}


def player_part(found: Manifest, lettering: Path) -> dict:
    return {"inputs": {name: PLAYER_INPUTS[name](found, lettering) for name in found.inputs},
            "environment": {name: os.environ.get(name) for name in found.environment}}


# Keys


@lru_cache(maxsize=4)
def _live_toolchain() -> str:
    return json.dumps(live_toolchain())


def documents(root: Path | None = None, toolchain: dict | None = None, lettering: Path | None = None) -> dict[str, dict]:
    """Per step, its key document {"mod": the mod part (a digest when shipped), "player": the player's part}.

    A release reads its mod parts from its shipped step_keys.json; a checkout works them out from its source. lettering:
    the install's lettering.zip (lettering_file where it names none)."""
    root = Path(root or resources.root())
    shipped = root / manifest.SHIPPED
    mods = None
    if shipped.is_file():
        document = json.loads(shipped.read_text(encoding="utf-8"))
        if document.get("scheme") != SCHEME:
            raise StepKeyError(f"{manifest.SHIPPED} has key scheme {document.get('scheme')}, not {SCHEME}")
        mods = {step: each["mod"] for step, each in document["steps"].items()}
    found = manifest.manifests(root)
    if mods is None:
        toolchain = toolchain if toolchain is not None else json.loads(_live_toolchain())
        mods = {step: mod_part(each, root, toolchain) for step, each in found.items()}
    lettering = lettering or lettering_file(root)
    return {step: {"mod": mods[step], "player": player_part(each, lettering)} for step, each in found.items()}


def step_key(document: dict) -> dict:
    """{"key": the key of a step's key document, "parts": the digest of each part, which the install log compares}."""
    parts = {"mod": document["mod"] if isinstance(document["mod"], str) else key(document["mod"]),
             **{name: key(value) for name, value in document["player"]["inputs"].items()},
             "environment": key(document["player"]["environment"])}
    return {"key": key(parts), "parts": parts}


def keys(root: Path | None = None, toolchain: dict | None = None, lettering: Path | None = None) -> dict[str, dict]:
    """Per step, its step_key."""
    return {step: step_key(document) for step, document in documents(root, toolchain, lettering).items()}


def movie_keys() -> dict[str, str]:
    """Each movie's place in the game folder, with its key: the pin it is copied and checked against."""
    from wozmod.movies import movie_donors

    return {f"wozmod/movie/{f.path}": f.sha256 for donor in movie_donors().values() for f in donor.files}
