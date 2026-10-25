#!/usr/bin/env python3
"""Lay images side by side in one small labelled JPEG, so a single image read compares many: a before and after pair,
a movie's stills, a run's captures.

    uv run python tools/montage.py OUT.jpg IMAGE[=LABEL]... [--cols N] [--width PX]

Each image is scaled to --width (default 640) with a label bar above it (the file's stem, or LABEL); rows hold --cols
images (default: all of them, up to 3).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LABEL_HEIGHT = 28


def montage(items: list[tuple[Path, str]], out: Path, cols: int | None = None, width: int = 640) -> Path:
    """Write the labelled grid of ``items`` (path, label) to ``out``; rows of ``cols``, each image ``width`` wide."""
    if not items:
        raise ValueError("give at least one image")
    cols = cols or min(len(items), 3)
    tiles = []
    for path, label in items:
        with Image.open(path) as image:
            image = image.convert("RGB")
            tiles.append((image.resize((width, round(image.height * width / image.width))), label))
    cell = max(tile.height for tile, _ in tiles) + LABEL_HEIGHT
    sheet = Image.new("RGB", (cols * width, -(-len(tiles) // cols) * cell), "black")
    draw, font = ImageDraw.Draw(sheet), ImageFont.load_default(size=20)
    for n, (tile, label) in enumerate(tiles):
        x, y = n % cols * width, n // cols * cell
        draw.text((x + 6, y + 3), label, fill="white", font=font)
        sheet.paste(tile, (x, y + LABEL_HEIGHT))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=80)
    return out


def labelled(argument: str) -> tuple[Path, str]:
    path, _, label = argument.partition("=")
    return Path(path), label or Path(path).stem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", type=Path)
    parser.add_argument("images", nargs="+", type=labelled, metavar="IMAGE[=LABEL]")
    parser.add_argument("--cols", type=int)
    parser.add_argument("--width", type=int, default=640)
    args = parser.parse_args(argv)
    try:
        print(montage(args.images, args.out, args.cols, args.width))
    except (OSError, ValueError) as exc:
        print(f"montage: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
