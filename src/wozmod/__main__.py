"""wozmod command line: install and uninstall."""

import argparse
import sys
from functools import partial
from pathlib import Path

from wozmod.install import InstallError, install, uninstall

INPUTS = (
    ("wo1", "Warriors Orochi PC (Europe) disc image"),
    ("x360", "Warriors Orochi 2 Xbox 360 (USA) disc image"),
    ("psp", "Warriors Orochi 2 PSP (USA) disc image"),
    ("x360-exe", "the already decrypted Xbox 360 program file"),
    ("psp-eboot", "the already decrypted PSP EBOOT"),
    ("lettering", "the release lettering bundle (lettering.zip)"),
    ("dll", "the release d3d9.dll"),
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wozmod", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser(
        "install", help="build the mod into a Musou Orochi Z folder",
        epilog="environment: WOZMOD_JOBS=N runs each build stage on at most N worker processes "
               "(default: the number of CPUs)")
    add.add_argument("--game", type=Path, required=True, help="the stock Musou Orochi Z folder")
    for name, text in INPUTS:
        add.add_argument(f"--{name}", type=Path, required=True, help=text)
    add.add_argument("--force", action="store_true", help="replace an existing install")
    remove = commands.add_parser("uninstall", help="delete the files the install wrote")
    remove.add_argument("--game", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            install(args.game, {name: getattr(args, name.replace("-", "_")) for name, _ in INPUTS},
                    force=args.force, log=partial(print, flush=True))
            return 0
        return 1 if uninstall(args.game, log=partial(print, flush=True)) else 0
    except InstallError as error:
        print(f"wozmod: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
