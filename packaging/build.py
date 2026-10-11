"""Build wozmod-<version>-<windows|linux>.zip for this platform.

    uv sync --extra native --group build
    uv run python packaging/build.py --assets DIR

DIR holds the release d3d9.dll and lettering.zip (see packaging/release_assets.py).
Run it on the platform you build for: PyInstaller cannot cross-build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build/packaging"
WINDOWS = sys.platform == "win32"
PLATFORM = "windows" if WINDOWS else "linux"
EXE = "wozmod.exe" if WINDOWS else "wozmod"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pinned_download(pin: dict) -> Path:
    """Download pin['url'] into the cache unless a file with pin['sha256'] is already there."""
    cached = OUT / "cache" / pin["url"].rsplit("/", 1)[1]
    if not cached.is_file() or sha256(cached) != pin["sha256"]:
        cached.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {pin['url']}", flush=True)
        with urllib.request.urlopen(pin["url"]) as reply, cached.open("wb") as stream:
            shutil.copyfileobj(reply, stream)
        if sha256(cached) != pin["sha256"]:
            raise SystemExit(f"{cached.name} does not match its pinned SHA-256; update packaging/ffmpeg.json")
    return cached


def prepare_ffmpeg() -> Path:
    """Unpack the pinned LGPL ffmpeg into OUT/ffmpeg as bin/ (program and DLLs, or program) and lib/."""
    archive = pinned_download(json.loads((ROOT / "packaging/ffmpeg.json").read_text())[PLATFORM])
    target, unpacked = OUT / "ffmpeg", OUT / "ffmpeg-unpacked"
    shutil.rmtree(target, ignore_errors=True)
    shutil.rmtree(unpacked, ignore_errors=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(unpacked)
    else:
        with tarfile.open(archive) as bundle:
            bundle.extractall(unpacked, filter="data")
    (source,) = unpacked.iterdir()
    (target / "bin").mkdir(parents=True)
    if WINDOWS:
        for path in (source / "bin").iterdir():
            if path.name == "ffmpeg.exe" or path.suffix == ".dll":
                shutil.copy2(path, target / "bin" / path.name)
    else:
        shutil.copy2(source / "bin/ffmpeg", target / "bin/ffmpeg")
        (target / "lib").mkdir()
        for path in (source / "lib").glob("lib*.so.[0-9]*"):  # the soname links; copy2 follows them
            if path.name.count(".") == 2:
                shutil.copy2(path, target / "lib" / path.name)
    shutil.copy2(source / "LICENSE.txt", OUT / "ffmpeg-LICENSE.txt")
    shutil.rmtree(unpacked)
    program = target / "bin" / ("ffmpeg.exe" if WINDOWS else "ffmpeg")
    for flag, wanted in (("-decoders", " xma2 "), ("-encoders", " libvorbis ")):
        listing = subprocess.run([program, "-hide_banner", flag], capture_output=True, text=True, check=True).stdout
        if wanted not in listing:
            raise SystemExit(f"the pinned ffmpeg lacks{wanted}({flag[1:]}); pick another build")
    return target


def check_assets(assets: Path) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from wozmod.install import DLL, LETTERING

    for name, (size, digest) in (("d3d9.dll", DLL), ("lettering.zip", LETTERING)):
        path = assets / name
        if not path.is_file() or path.stat().st_size != size or sha256(path) != digest:
            raise SystemExit(f"{path} is missing or not the file pinned in src/wozmod/install/__init__.py")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--assets", type=Path, required=True, help="folder with d3d9.dll and lettering.zip")
    parser.add_argument("--tag", help="the release tag (for example v0.1.0); it must match the version in pyproject.toml")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    if args.tag and args.tag != f"v{version}":
        raise SystemExit(f"tag {args.tag} does not match version {version} in pyproject.toml")
    check_assets(args.assets)
    import wozmod_native  # noqa: F401  the release must carry the Rust codecs (uv sync --extra native)

    prepare_ffmpeg()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(OUT / "dist"),
                    "--workpath", str(OUT / "work"), str(ROOT / "packaging/wozmod.spec")], check=True)

    name = f"wozmod-{version}-{PLATFORM}"
    stage = OUT / "stage" / name
    shutil.rmtree(OUT / "stage", ignore_errors=True)
    shutil.copytree(OUT / "dist/wozmod", stage, symlinks=True)
    shutil.copytree(OUT / "ffmpeg", stage / "_internal/ffmpeg")
    for file in (args.assets / "d3d9.dll", args.assets / "lettering.zip", ROOT / "LICENSE"):
        shutil.copy2(file, stage)
    (stage / "licenses").mkdir()
    shutil.copy2(OUT / "ffmpeg-LICENSE.txt", stage / "licenses")
    shutil.copy2(ROOT / "packaging/FFMPEG-NOTICE.txt", stage / "licenses")

    codecs = list((stage / "_internal").rglob("wozmod_native*"))
    if not codecs or not (stage / "_internal/ffmpeg").is_dir():
        raise SystemExit("the packaged program lacks the Rust codecs or ffmpeg")
    smoke = subprocess.run([stage / EXE, "--help"], capture_output=True, text=True)
    if smoke.returncode or "install" not in smoke.stdout:
        raise SystemExit(f"the packaged program does not start:\n{smoke.stdout}{smoke.stderr}")

    zip_path = OUT / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(stage.rglob("*")):
            bundle.write(path, path.relative_to(stage.parent))  # keeps the executable bit
    print(f"{zip_path} {zip_path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
