"""Put d3d9.dll and lettering.zip on the draft release of a tag, where the release workflow fetches them.

The workflow packs both into each zip, then takes them off the draft: the published release holds only the zips.

CI cannot build these two files: the DLL build reads the stock game, and the lettering render needs
FontForge, Cairo and NumPy. Run this on the developer machine, before you push the tag:

    uv run python packaging/release_assets.py --tag v0.1.0 --dll PATH --lettering PATH
    uv run python packaging/release_assets.py --tag v0.1.0 --build --game "<stock Musou Orochi Z folder>" \
        --donors "<donors folder, as the installer lays it out>"

--build renders both files first (MinGW, Fedora's mingw32-gcc, for the DLL, the system python3 with FontForge, Cairo
and NumPy for the lettering, which reads the menu words from the donors). It then writes both files' pins in
packaging/pins.json (packaging/pins.py), which the release commit carries: a release pins what it ships
(installer decision 55). A changed DLL pin needs a play-test of that DLL before the release. Add --dry-run to write the
pins without uploading.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pins  # noqa: E402  packaging/pins.py, beside this file


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
    parser.add_argument("--tag", required=True, help="the release tag, for example v0.1.0")
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--lettering", type=Path)
    parser.add_argument("--build", action="store_true", help="render both files first (needs --game and --donors)")
    parser.add_argument("--game", type=Path, help="the stock Musou Orochi Z folder, for --build")
    parser.add_argument("--donors", type=Path, help="the donors folder the menu words resolve from, for --build")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="wozmod-assets-") as scratch:
        if args.build:
            if args.game is None or args.donors is None:
                parser.error("--build needs --game and --donors")
            dll, lettering = build(args.game, args.donors, Path(scratch))
        elif args.dll and args.lettering:
            # gh names an asset after its file, so copy under the release names
            dll, lettering = Path(scratch, "d3d9.dll"), Path(scratch, "lettering.zip")
            shutil.copyfile(args.dll, dll)
            shutil.copyfile(args.lettering, lettering)
        else:
            parser.error("give --dll and --lettering, or --build with --game")
        pins.write_assets(dll, lettering)
        print(f"pinned in {pins.PINS}: {dll}, {lettering}")
        if args.dry_run:
            return 0
        if subprocess.run(["gh", "release", "view", args.tag], capture_output=True).returncode:
            subprocess.run(["gh", "release", "create", args.tag, "--draft", "--title", args.tag,
                            "--notes", ""], check=True)
        subprocess.run(["gh", "release", "upload", args.tag, dll, lettering,
                        "--clobber"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
