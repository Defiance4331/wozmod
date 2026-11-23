"""Make the release d3d9.dll and lettering.zip in work/release-assets/ and write their pins.

Releases are built on the developer machine: the DLL build reads the stock game, the lettering render needs
FontForge, Cairo and NumPy, and work/release-assets/ also holds the proven ffmpeg (packaging/ffmpeg/README.md), none of
which a hosted runner has. Run this first, then packaging/build.py and packaging/build_windows.sh, which take all
three from work/release-assets/:

    uv run python packaging/release_assets.py --build --game "<stock Musou Orochi Z folder>" \
        --donors "<donors folder, as the installer lays it out>"
    uv run python packaging/release_assets.py --dll PATH --lettering PATH

--build renders both files (MinGW, Fedora's mingw32-gcc, for the DLL, the system python3 with FontForge, Cairo and
NumPy for the lettering, which reads the menu words from the donors); --dll and --lettering take files made before.
Either way both land in work/release-assets/ and their pins in packaging/pins.json (packaging/pins.py), which the
release commit carries: a release pins what it ships (installer decision 55). A changed DLL pin needs a play-test of
that DLL before the release.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pins  # noqa: E402  packaging/pins.py, beside this file
from wozmod.atomic import replacing  # noqa: E402


def build_dll(game: Path, folder: Path) -> Path:
    """The release d3d9.dll, from the stock executable, in folder."""
    subprocess.run([sys.executable, "-m", "wozmod.runtime", "--game", str(game), "--output-dir", str(folder)], check=True)
    return folder / "d3d9.dll"


def build_lettering(donors: Path, folder: Path) -> Path:
    """The release lettering.zip, with the menu words from the donors, in folder."""
    subprocess.run([sys.executable, "-m", "wozmod.art", "render", str(folder / "lettering.zip"),
                    "--text-donors", str(donors), "--jobs", str(os.cpu_count() or 4)], check=True)
    return folder / "lettering.zip"


def build(game: Path, donors: Path, folder: Path) -> tuple[Path, Path]:
    """Render d3d9.dll and lettering.zip into folder (tests/test_install_e2e.py too)."""
    return build_dll(game, folder), build_lettering(donors, folder)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--lettering", type=Path)
    parser.add_argument("--build", action="store_true", help="render both files first (needs --game and --donors)")
    parser.add_argument("--game", type=Path, help="the stock Musou Orochi Z folder, for --build")
    parser.add_argument("--donors", type=Path, help="the donors folder the menu words resolve from, for --build")
    args = parser.parse_args()
    folder = pins.release_assets()
    folder.mkdir(parents=True, exist_ok=True)
    if args.build:
        if args.game is None or args.donors is None:
            parser.error("--build needs --game and --donors")
        dll, lettering = build(args.game, args.donors, folder)
    elif args.dll and args.lettering:
        dll, lettering = folder / "d3d9.dll", folder / "lettering.zip"
        for source, target in ((args.dll, dll), (args.lettering, lettering)):
            if source.resolve() != target.resolve():
                with replacing(target) as temporary:  # a build reading work/release-assets/ sees the old file or the new
                    shutil.copyfile(source, temporary)
    else:
        parser.error("give --dll and --lettering, or --build with --game and --donors")
    pins.write_assets(dll, lettering)
    print(f"{dll} and {lettering}, pinned in {pins.PINS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
