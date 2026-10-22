#!/usr/bin/env python3
"""`wozmod install` that reuses the build steps whose inputs did not change: for developers, not players.

    uv run python tools/dev_install.py --game work/install [the options of wozmod install] [--cache DIR]

A step is reused when its key (tools/build_cache.py) matches an entry in DIR (default: work/build-cache of this
checkout); any other step is built as `wozmod install` builds it. The installed files are the same either way.
After the install it prints, per step, whether it was reused and, if not, what changed. Delete DIR to start over.
"""

from __future__ import annotations

import argparse
import functools
import sys
from pathlib import Path

import build_cache


def main(argv: list[str] | None = None) -> int:
    import wozmod.__main__ as cli
    from wozmod import install as installer

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--cache", type=Path, default=build_cache.REPO / "work/build-cache")
    args, rest = parser.parse_known_args(argv)
    cache = build_cache.BuildCache(args.cache)
    cli.install = functools.partial(installer.install, cache=cache)  # the command line's own install, with the cache
    status = cli.main(["install", *rest])
    for line in cache.report:
        print(f"cache: {line}")
    if status == 0 and not cache.report:
        print("cache: the install never asked the cache (wozmod.__main__ no longer calls install?)", file=sys.stderr)
        return 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
