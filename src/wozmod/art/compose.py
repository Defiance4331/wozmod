"""Compose player art using a verified lettering bundle and Pillow."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from . import build, lettering
from .bundle import read_bundle
from .render import RECIPE, RECIPE_VERSION


def compose(stock, donors, bundle, output_dir, squish=True, jobs=None) -> Path:
    """Build output_dir/wozmod; donors maps wo1 and x360 to archive stems.

    The installer always squishes. squish=False is the developer oracle and needs a
    bundle rendered with full_width=True. jobs is the worker process count (default: CPU count).
    """
    recipe_bytes = RECIPE.read_bytes()
    recipe = json.loads(recipe_bytes)
    if recipe.get("format_version") != RECIPE_VERSION or not isinstance(recipe.get("requests"), dict):
        raise ValueError("unsupported lettering recipe")
    layers = read_bundle(Path(bundle), hashlib.sha256(recipe_bytes).hexdigest())
    if set(layers) != set(recipe["requests"]):
        raise ValueError("lettering bundle dropped or added a recipe layer")
    arguments = ["--game", str(stock), "--wo1-donor", str(donors["wo1"]),
                 "--wo2-donor", str(donors["x360"]), "--output-dir", str(output_dir),
                 "--hires", "vector", "--jobs", str(jobs or os.process_cpu_count())]
    if not squish:
        arguments.append("--no-squish")
    with lettering.using(layers):
        build.main(arguments)
    return Path(output_dir) / "wozmod"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--wo1-donor", type=Path, required=True)
    parser.add_argument("--wo2-donor", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--oracle-full-width", action="store_true",
                        help="developer oracle only: full-width lettering from a --full-width bundle")
    args = parser.parse_args(argv)
    compose(args.game, {"wo1": args.wo1_donor, "x360": args.wo2_donor}, args.bundle,
            args.output_dir, squish=not args.oracle_full_width)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
