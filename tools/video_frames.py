#!/usr/bin/env python3
"""Stills from a recording at given times, and one labelled montage of them to read instead of each still.

    uv run python tools/video_frames.py VIDEO --at 1.5 3 12.25 [--out DIR] [--sheet OUT.jpg]

Writes DIR/<video stem>-<seconds>.png for each time (DIR defaults to the video's folder) and, with --sheet, the stills
side by side (tools/montage.py). A recording made while the game ran slow shows the machine's timing, not the mod's:
check `stop`'s slow_recordings first.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from montage import montage


def stills(video: Path, times: list[float], out: Path) -> list[Path]:
    """One PNG per time (seconds from the start), from the frame shown at that moment."""
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for at in times:
        path = out / f"{video.stem}-{at:.2f}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-ss", f"{at:.3f}", "-frames:v", "1",
                        str(path)], check=True)
        if not path.is_file():
            raise ValueError(f"{video.name} has no frame at {at:.2f} s")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path)
    parser.add_argument("--at", type=float, nargs="+", required=True, metavar="SECONDS")
    parser.add_argument("--out", type=Path, help="folder for the stills (default: the video's)")
    parser.add_argument("--sheet", type=Path, help="also lay the stills side by side in this JPEG")
    args = parser.parse_args(argv)
    try:
        paths = stills(args.video.resolve(strict=True), args.at, args.out or args.video.parent)
        if args.sheet:
            paths.append(montage([(p, f"{t:.2f} s") for p, t in zip(paths, args.at, strict=True)], args.sheet))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"video_frames: {exc}", file=sys.stderr)
        return 1
    print("\n".join(map(str, paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
