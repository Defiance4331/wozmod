"""Put d3d9.dll and lettering.zip on the draft release of a tag, where the release workflow fetches them.

CI cannot build these two files: the DLL build reads the stock game, and the lettering render needs
FontForge, Cairo and NumPy. Run this on the developer machine, before you push the tag:

    uv run python packaging/release_assets.py --tag v0.1.0 --dll PATH --lettering PATH
    uv run python packaging/release_assets.py --tag v0.1.0 --build --game "<stock Musou Orochi Z folder>"

--build renders both files first (Docker or MinGW for the DLL, the system python3 with FontForge, Cairo
and NumPy for the lettering). Both files must match the sizes and SHA-256 pinned in
src/wozmod/install/__init__.py; the script stops otherwise. Add --dry-run to check without uploading.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from wozmod.install import DLL, LETTERING


def check(path: Path, pin: tuple[int, str]) -> None:
    size, digest = pin
    if not path.is_file() or path.stat().st_size != size or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise SystemExit(f"{path} is not the file pinned in src/wozmod/install/__init__.py; "
                         "rebuild it, or update the pin together with the release")


def build(game: Path, folder: Path) -> tuple[Path, Path]:
    """Render d3d9.dll (from the stock executable) and lettering.zip into folder."""
    subprocess.run([sys.executable, "-m", "wozmod.runtime", "--game", str(game), "--output-dir", str(folder)], check=True)
    subprocess.run([sys.executable, "-m", "wozmod.art", "render", str(folder / "lettering.zip")], check=True)
    return folder / "d3d9.dll", folder / "lettering.zip"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", required=True, help="the release tag, for example v0.1.0")
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--lettering", type=Path)
    parser.add_argument("--build", action="store_true", help="render both files first (needs --game)")
    parser.add_argument("--game", type=Path, help="the stock Musou Orochi Z folder, for --build")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="wozmod-assets-") as scratch:
        if args.build:
            if args.game is None:
                parser.error("--build needs --game")
            dll, lettering = build(args.game, Path(scratch))
        elif args.dll and args.lettering:
            # gh names an asset after its file, so copy under the release names
            dll, lettering = Path(scratch, "d3d9.dll"), Path(scratch, "lettering.zip")
            shutil.copyfile(args.dll, dll)
            shutil.copyfile(args.lettering, lettering)
        else:
            parser.error("give --dll and --lettering, or --build with --game")
        check(dll, DLL)
        check(lettering, LETTERING)
        print(f"both files match their pins: {dll}, {lettering}")
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
