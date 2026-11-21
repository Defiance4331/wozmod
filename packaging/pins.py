"""The release pins (packaging/pins.json): the size and SHA-256 of the d3d9.dll and lettering.zip a release ships
beside its program, of each ffmpeg it carries, with ffmpeg's file name, and of the Dream voice separator's runtime
(ONNX Runtime and its GPU plugin, with the wheels they come from) and the checkpoint its model is made from.

Between releases nothing checks them: an install from a checkout takes the checkout's own files (installer decision
55). packaging/release_assets.py writes the DLL and lettering pins from the files it builds, and the release commit
carries them; `tools/verify.py release` builds both again and compares (tests/test_release_pins.py). ffmpeg changes
only with its recipe, the FFmpeg version, packaging/ffmpeg/components.json or a bundled library's source, not with a
compiler update (decision 56): the proven programs stay in the main checkout's work/release-assets/, and packaging
and the timed install test take them from there, checked against the pin, instead of building one. A release's step
keys hash its ffmpeg pin (wozmod.install.step_keys.release_toolchain).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = "packaging/pins.json"


def pins() -> dict:
    return json.loads((ROOT / PINS).read_text(encoding="utf-8"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def asset_pin(name: str) -> tuple[int, str]:
    """(size, SHA-256) of d3d9.dll or lettering.zip."""
    pin = pins()[name]
    return pin["size"], pin["sha256"]


def write_assets(dll: Path, lettering: Path) -> None:
    """Pin the d3d9.dll and lettering.zip a release ships (packaging/release_assets.py)."""
    document = pins()
    for name, path in (("d3d9.dll", dll), ("lettering.zip", lettering)):
        data = path.read_bytes()
        document[name] = {"size": len(data), "sha256": _sha256(data)}
    (ROOT / PINS).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def release_assets() -> Path:
    """The main checkout's work/release-assets/, from it or any linked worktree (tools/checkout.py)."""
    sys.path.insert(0, str(ROOT / "tools"))
    from checkout import work

    return work() / "release-assets"


def ffmpeg_pin(system: str) -> dict:
    """{"file", "sha256", "size"} of the ffmpeg a release carries for system."""
    return pins()["ffmpeg"][system]


def ffmpeg_problem(system: str, program: Path) -> str | None:
    """Why program is not the ffmpeg pinned for system, with how to get it back; None when it is."""
    pin = ffmpeg_pin(system)
    if program.is_file() and program.stat().st_size == pin["size"] and _sha256(program.read_bytes()) == pin["sha256"]:
        return None
    inside = "_internal/ffmpeg/bin/" + ("ffmpeg.exe" if system == "windows" else "ffmpeg")
    return (f"{program} is {'not the program' if program.exists() else 'missing, the program'} {PINS} pins "
            f"for {system} ({pin['sha256'][:16]}..., {pin['size']} bytes). Put it back from the latest published "
            f"wozmod-*-{system}.zip that carries it ({inside}, checked against the pin), or else build and prove it "
            "by packaging/ffmpeg/README.md (A new build)")


def release_ffmpeg(system: str) -> Path:
    """The proven ffmpeg for system in work/release-assets/, checked against its pin; SystemExit naming the recovery
    where it is missing or another program."""
    program = release_assets() / ffmpeg_pin(system)["file"]
    if problem := ffmpeg_problem(system, program):
        raise SystemExit(problem)
    return program


def separator_folder() -> Path:
    """The Dream voice separator folder in work/release-assets/: the model (packaging/separator_model.py) and each
    system's runtime (packaging/separator_runtime.py)."""
    return release_assets() / "dream-voice"


def separator_problem(system: str, folder: Path) -> str | None:
    """Why folder does not hold the separator's runtime and notices pinned for system; None when it does."""
    pinned = pins()["separator"]
    for kind, into in (("files", folder / system), ("licenses", folder / "licenses" / system)):
        for pin in pinned[kind][system]:
            path = into / pin["file"]
            if not (path.is_file() and path.stat().st_size == pin["size"] and _sha256(path.read_bytes()) == pin["sha256"]):
                return (f"{path} is {'not the file' if path.exists() else 'missing, the file'} {PINS} pins for "
                        f"{system}; python packaging/separator_runtime.py fetches it")
            if system == "windows" and kind == "files":
                import windows_imports

                beside = {each.name.lower() for each in into.iterdir()}
                if lacking := [each for each in windows_imports.needed(path.read_bytes()) if each not in beside]:
                    return (f"{path} imports {', '.join(lacking)}, which {'is' if len(lacking) == 1 else 'are'} not "
                            f"beside it; python packaging/separator_runtime.py fetches them")
    return None


if __name__ == "__main__":  # packaging/build_windows.sh: the Windows program, checked here where the main checkout is
    print(release_ffmpeg(sys.argv[1]))
