"""Build wozmod-<version>-<windows|linux>.zip (the wozmod and wozmod-setup programs) for this platform.

    uv sync --extra native --group build
    uv run python packaging/build.py --assets DIR --speech SPEECH --separator SEPARATOR

DIR holds the release d3d9.dll and lettering.zip: work/release-assets/, where packaging/release_assets.py makes them. SPEECH is the speech model folder
(voices/phones.py MODEL, PocketSphinx's US English model) and SEPARATOR the Dream voice separator's folder
(voices/separation.py MODEL, with each system's ONNX Runtime: work/release-assets/dream-voice/, where
packaging/separator_model.py and packaging/separator_runtime.py make it); the zip carries both, so an install needs
nothing more.
Run it on the platform you build for: PyInstaller cannot cross-build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import pins  # noqa: E402  packaging/pins.py: the release pins and where the proven ffmpeg is
import separator_runtime  # noqa: E402  the pinned wheels
import windows_imports  # noqa: E402  the DLLs a Windows library needs beside it
OUT = ROOT / "build/packaging"
WINDOWS = sys.platform == "win32"
PLATFORM = "windows" if WINDOWS else "linux"
EXE = "wozmod.exe" if WINDOWS else "wozmod"
SETUP = "wozmod-setup.exe" if WINDOWS else "wozmod-setup"
# Each platform builds in its own folder: Wine cannot delete the symlinks a Linux build leaves.
BUILD = OUT / PLATFORM


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare_ffmpeg() -> Path:
    """Our own static ffmpeg (packaging/ffmpeg/), the proven program packaging/pins.json pins: on Linux from the
    main checkout's work/release-assets/, for Windows as packaging/build_windows.sh staged it from there (Wine cannot
    reach the main checkout's work/ by its path). Built only when its recipe changes (installer decision 56)."""
    if not WINDOWS:
        return pins.release_ffmpeg(PLATFORM)
    program = OUT / "ffmpeg" / PLATFORM / pins.ffmpeg_pin(PLATFORM)["file"]
    if problem := pins.ffmpeg_problem(PLATFORM, program):
        raise SystemExit(f"{problem}; packaging/build_windows.sh stages it")
    return program


def add_setup_launcher(stage: Path) -> None:
    """wozmod-setup beside wozmod, which it starts in window mode: on Windows the small windowed launcher
    packaging/build_windows.sh builds (packaging/setup_launcher.c), on Linux a script."""
    sys.path.insert(0, str(ROOT / "src"))
    from wozmod.__main__ import SETUP_WINDOW

    if WINDOWS:
        launcher = OUT / "launcher/wozmod-setup.exe"
        if not launcher.is_file():
            raise SystemExit(f"{launcher} is missing: packaging/build_windows.sh builds it")
        shutil.copy2(launcher, stage / SETUP)
    else:
        (stage / SETUP).write_text(f'#!/bin/sh\nexec "$(dirname "$0")/wozmod" {SETUP_WINDOW}\n', encoding="utf-8")
        (stage / SETUP).chmod(0o755)


def add_speech_model(source: Path, stage: Path) -> None:
    """The pinned speech model files, and only those, at _internal/speech/ (where the release looks for them)."""
    sys.path.insert(0, str(ROOT / "src"))
    from wozmod.voices.phones import MODEL, model_problem

    if problem := model_problem(source):
        raise SystemExit(f"{source}: {problem}")
    for name in MODEL:
        target = stage / "_internal/speech" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)


def _runtime_wheel() -> str:
    return pins.pins()["separator"]["imports"]["windows"]


def add_separator(source: Path, stage: Path) -> None:
    """The Dream voice separator at _internal/dream-voice/ (where the release looks for it): the pinned model files,
    the pinned Vulkan shaders as SPIR-V (which packaging/separator_kernels.py compiles), this system's ONNX
    Runtime as packaging/pins.json pins it, and their notices in licenses/. No CUDA kernels: that engine is out of
    every build (native/src/cuda.rs)."""
    sys.path.insert(0, str(ROOT / "src"))
    from wozmod.voices.separation import MODEL, SHADERS, model_problem, pinned_problem

    if problem := (pins.separator_problem(PLATFORM, source) or model_problem(source) or pinned_problem(source, SHADERS)):
        raise SystemExit(f"{source}: {problem}")
    target = stage / "_internal/dream-voice"
    (target / PLATFORM).mkdir(parents=True)
    for name in (*MODEL, *SHADERS):
        shutil.copy2(source / name, target / name)
    for pin in pins.pins()["separator"]["files"][PLATFORM]:
        shutil.copy2(source / PLATFORM / pin["file"], target / PLATFORM / pin["file"])
        if PLATFORM == "windows":  # with the Visual C++ runtime it imports, which no Windows has by itself
            windows_imports.bundle(target / PLATFORM / pin["file"], separator_runtime.wheel(source, _runtime_wheel()))
    shutil.copy2(ROOT / "packaging/SEPARATOR-NOTICE.txt", stage / "licenses")
    for licence in sorted((ROOT / "packaging/separator/licenses").iterdir()):  # the network's design
        shutil.copy2(licence, stage / "licenses")
    for pin in pins.pins()["separator"]["licenses"][PLATFORM]:  # ONNX Runtime's, and what it needs on Windows
        shutil.copy2(source / "licenses" / PLATFORM / pin["file"], stage / "licenses" / pin["file"])


def check_assets(assets: Path) -> None:
    for name in ("d3d9.dll", "lettering.zip"):
        path, (size, digest) = assets / name, pins.asset_pin(name)
        if not path.is_file() or path.stat().st_size != size or sha256(path) != digest:
            raise SystemExit(f"{path} is missing or not the file pinned in {pins.PINS}: "
                             "packaging/release_assets.py makes both files and writes their pins")


def write_step_keys(folder: Path) -> Path:
    """Ship the mod's part of each install step's key and the source parts of its manifest, made from this source
    tree, where the frozen installer finds them (resources.root()): an update keeps the steps whose key did not change,
    the read gate holds each step to its manifest, and players have no source to make either from."""
    sys.path.insert(0, str(ROOT / "src"))
    from wozmod.install import manifest, step_keys

    path = folder / manifest.SHIPPED
    path.write_text(json.dumps(step_keys.shipped_document(ROOT, PLATFORM), indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--assets", type=Path, required=True, help="folder with d3d9.dll and lettering.zip")
    parser.add_argument("--speech", type=Path, required=True, help="the speech model folder (voices/phones.py MODEL)")
    parser.add_argument("--separator", type=Path, required=True,
                        help="the Dream voice separator's folder (voices/separation.py MODEL)")
    parser.add_argument("--tag", help="the release tag (for example v0.1.0); it must match the version in pyproject.toml")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if args.tag and args.tag != f"v{version}":
        raise SystemExit(f"tag {args.tag} does not match version {version} in pyproject.toml")
    check_assets(args.assets)
    import wozmod_native  # noqa: F401  the release must carry the Rust codecs (uv sync --extra native)

    ffmpeg = prepare_ffmpeg()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(BUILD / "dist"),
                    "--workpath", str(BUILD / "work"), str(ROOT / "packaging/wozmod.spec")], check=True)

    name = f"wozmod-{version}-{PLATFORM}"
    stage = BUILD / "stage" / name
    shutil.rmtree(BUILD / "stage", ignore_errors=True)
    shutil.copytree(BUILD / "dist/wozmod", stage, symlinks=True)
    (stage / "_internal/ffmpeg/bin").mkdir(parents=True)
    shutil.copy2(ffmpeg, stage / "_internal/ffmpeg/bin" / ("ffmpeg.exe" if WINDOWS else "ffmpeg"))  # resources.ffmpeg()
    for file in (args.assets / "d3d9.dll", args.assets / "lettering.zip"):
        shutil.copy2(file, stage / "_internal")  # players see only the programs and the licences
    write_step_keys(stage / "_internal")
    add_speech_model(args.speech, stage)
    add_setup_launcher(stage)
    shutil.copy2(ROOT / "LICENSE", stage)
    (stage / "licenses").mkdir()
    shutil.copy2(ROOT / "packaging/FFMPEG-NOTICE.txt", stage / "licenses")
    for licence in sorted((ROOT / "packaging/ffmpeg/licenses").iterdir()):  # FFmpeg's, libogg's and libvorbis's
        shutil.copy2(licence, stage / "licenses")
    shutil.copy2(ROOT / "packaging/POCKETSPHINX-NOTICE.txt", stage / "licenses")
    add_separator(args.separator, stage)

    codecs = list((stage / "_internal").rglob("wozmod_native*"))
    if not codecs or not (stage / "_internal/ffmpeg").is_dir():
        raise SystemExit("the packaged program lacks the Rust codecs or ffmpeg")
    internal = stage / "_internal"
    for codec in internal.rglob("wozmod_native*.pyd"):  # Windows: what our own library imports, as for ONNX Runtime
        windows_imports.bundle(codec, separator_runtime.wheel(args.separator, _runtime_wheel()), also=internal)
    tk_data = all((internal / folder).is_dir() for folder in ("_tcl_data", "_tk_data")) or all(
        list(internal.glob(f"libt{name}*.zip")) for name in ("cl", "k"))  # Tcl 9 on Windows ships zips
    if not (stage / SETUP).is_file() or not tk_data:
        raise SystemExit("the packaged setup window is missing or lacks the Tcl and Tk files")
    smoke = subprocess.run([stage / EXE, "--help"], capture_output=True, text=True, encoding="utf-8")
    if smoke.returncode or "install" not in smoke.stdout:
        raise SystemExit(f"the packaged program does not start:\n{smoke.stdout}{smoke.stderr}")

    zip_path = OUT / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(stage.rglob("*")):
            bundle.write(path, path.relative_to(stage.parent))  # keeps the executable bit
    if WINDOWS and (wrong := windows_imports.missing(zip_path) | windows_imports.differing(zip_path)):
        zip_path.unlink()
        raise SystemExit("the zip's libraries would not all load with its own DLLs (a DLL one imports is not where "
                         "Windows looks before System32, or the zip holds two versions of one):\n"
                         + "\n".join(f"  {name}: {', '.join(dlls)}" for name, dlls in wrong.items()))
    print(f"{zip_path} {zip_path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
