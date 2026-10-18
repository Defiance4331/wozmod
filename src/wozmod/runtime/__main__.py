"""Build d3d9.dll from a stock executable, for in-game checks."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from .build import build_dll


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, default=os.environ.get("WOZMOD_GAME"),
                        help="stock game folder holding OROCHI_Z.exe (default: $WOZMOD_GAME)")
    parser.add_argument("--output-dir", type=Path, required=True, help="receives d3d9.dll")
    parser.add_argument("--test", action="store_true",
                        help="ENRT_TEST: the harness screen, wait, route and tour commands")
    parser.add_argument("--log", action="store_true", help="ENRT_LOG: text and quad log")
    parser.add_argument("--preview", action="store_true",
                        help="ENRT_TAG_PREVIEW: also nops five unlock jumps; not a layout build")
    parser.add_argument("--with-render-distance", action="store_true",
                        help="ENRT_RENDER_DISTANCE: the unreleased render distance row and view patch")
    parser.add_argument("--with-moving-camera-fix", action="store_true",
                        help="ENRT_MOVING_FIX: the moving camera fix row and hook, out of release 1")
    parser.add_argument("--cell", type=int, default=24,
                        help="font cell override. Changes layout (28 draws text 17%% wider); "
                        "never use it for layout verdicts")
    args = parser.parse_args(argv)
    if args.game is None:
        parser.error("pass --game or set WOZMOD_GAME")
    dll = build_dll(Path(args.game) / "OROCHI_Z.exe", args.output_dir / "d3d9.dll",
                    log=args.log, preview=args.preview, test=args.test, cell=args.cell,
                    render_distance=args.with_render_distance, moving_fix=args.with_moving_camera_fix)
    print(f"{dll} sha256 {hashlib.sha256(dll.read_bytes()).hexdigest()}")
    return 0


raise SystemExit(main())
