"""Compose player art using a verified lettering bundle and Pillow."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from . import build, lettering
from .bundle import read_bundle
from .render import RECIPE, RECIPE_VERSION


def compose(stock, donors, bundle, output_dir, squish=True, jobs=None) -> Path:
    """Build output_dir/wozmod and nothing else beside it; donors maps wo1-x360 and x360 to archive stems, and text
    to the donors folder the menu words resolve from. Raises build.ArtError for a donor archive that fails its pin.

    The installer always squishes. squish=False is the developer oracle and needs a
    bundle rendered with full_width=True. jobs is the worker process count (default: CPU count).
    """
    recipe_bytes = RECIPE.read_bytes()
    recipe = json.loads(recipe_bytes)
    if recipe.get("format_version") != RECIPE_VERSION or not isinstance(recipe.get("requests"), dict):
        raise ValueError("unsupported lettering recipe")
    layers, outlines = read_bundle(Path(bundle), hashlib.sha256(recipe_bytes).hexdigest())
    if set(layers) != set(recipe["requests"]):
        raise ValueError("lettering bundle dropped or added a recipe layer")
    with lettering.using(layers, outlines):
        build.build_art(Path(stock), Path(donors["wo1-x360"]), Path(donors["x360"]), Path(donors["text"]),
                        Path(output_dir), hires="vector", squeeze=squish, jobs=jobs)
    return Path(output_dir) / "wozmod"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--wo1-x360-donor", type=Path, required=True)
    parser.add_argument("--wo2-donor", type=Path, required=True)
    parser.add_argument("--text-donors", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--oracle-full-width", action="store_true",
                        help="developer oracle only: full-width lettering from a --full-width bundle")
    args = parser.parse_args(argv)
    try:
        compose(args.game, {"wo1-x360": args.wo1_x360_donor, "x360": args.wo2_donor,
                                    "text": args.text_donors}, args.bundle,
                args.output_dir, squish=not args.oracle_full_width)
    except build.ArtError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
