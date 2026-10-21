"""wozmod command line: install, update and uninstall. With nothing to go on, it asks."""

import argparse
import multiprocessing
import sys
from pathlib import Path

from wozmod import __version__, remembered, resources
from wozmod.install import (FILES, IMAGE_FIRST, INPUTS, PROGRAMS, TOOLS, InstallError, check_input, existing_install,
                            install, install_state, program_status, uninstall)
from wozmod.interactive import ask_choice, ask_path, ask_yes, interactive
from wozmod.progress import Cancelled, Progress, TerminalView, duration

ASKED = ("game", *PROGRAMS, *TOOLS)  # the inputs the terminal asks for when they are missing
SHIPPED = {"lettering": "lettering.zip", "dll": "d3d9.dll"}  # inside the packaged program's resource folder


def attribute(name: str) -> str:
    return name.replace("-", "_")


def program_inputs(args: argparse.Namespace) -> dict:
    return {name: getattr(args, attribute(name)) for name in (*PROGRAMS, *TOOLS, *FILES.values())}


def missing(name: str, args: argparse.Namespace) -> bool:
    """True when the terminal still needs this input: absent, and for a program, a disc has nothing else to go on."""
    return getattr(args, attribute(name)) is None and (name not in TOOLS or name in program_status(name, program_inputs(args)).problems)


def ask_missing(args: argparse.Namespace) -> None:
    """Ask for each missing input, offering what the setup window remembered, and check the answer at once."""
    saved = remembered.load_settings()
    for name in ASKED:
        if missing(name, args):
            if name in TOOLS:  # only the program's own problems ask again: a wrong disc image is named on the image
                check, label, keep = ((lambda p, n=name: program_status(n, {**program_inputs(args), n: p}).verdict(n)),
                                      f"{INPUTS[name].label} you already have", (IMAGE_FIRST,))
            else:
                check, label, keep = (lambda p, n=name: check_input(n, p)), INPUTS[name].label, ()
            setattr(args, attribute(name), ask_path(label, saved.get(name), check, keep))


def remember(args: argparse.Namespace) -> None:
    values = {name: str(getattr(args, attribute(name)))
              for name in (*ASKED, *FILES.values()) if getattr(args, attribute(name), None)}
    remembered.save_settings({**remembered.load_settings(), **values})


def run_install(args: argparse.Namespace) -> int:
    absent = [f"--{name} or " + " and ".join(f"--{FILES[disc]}" for disc in TOOLS[name]) if name in TOOLS else f"--{name}"
              for name in ASKED if missing(name, args)]
    if any(getattr(args, name) is None for name in SHIPPED):
        print("wozmod: --lettering and --dll are needed outside the release program.", file=sys.stderr)
        return 2
    if absent and not interactive():
        print(f"wozmod: missing {', '.join(absent)}. Give each one, or run wozmod in a terminal and it asks.",
              file=sys.stderr)
        return 2
    if absent:
        print(f"wozmod {__version__}. Give the paths below. Drag a file into the window to paste its path.\n")
        ask_missing(args)
        remember(args)
    state, found = install_state(args.game) if args.game.is_dir() else ("none", None)
    if state == "update":
        before = existing_install(args.game)
        print(f"Updating wozmod {'(an earlier version)' if before == '0' else before} to {__version__}. "
              "Your settings stay.")
    elif state == "reinstall":
        if not args.force:
            if not interactive():
                print(f"wozmod: wozmod {__version__} is already installed in {args.game}. Pass --force to "
                      "reinstall it.", file=sys.stderr)
                return 2
            if not ask_yes(f"wozmod {__version__} is already installed here. Reinstall it? Your settings stay.", False):
                print("Nothing was changed.")
                return 1
        print(f"Reinstalling wozmod {__version__}. Your settings stay.")
    elif state == "downgrade" and not args.force and interactive():
        if not ask_yes(f"wozmod {found or 'of an unknown version'} is installed here, and this is wozmod "
                       f"{__version__}. Go back to this older version?", False):
            print("Nothing was changed.")
            return 1
        args.force = True
    view = TerminalView()
    progress = Progress(view)
    install(args.game, {name: getattr(args, attribute(name)) for name in INPUTS if name != "game"},
            force=args.force, log=lambda _: None, progress=progress)
    view.finish(progress, f"Installed in {duration(progress.elapsed())}. Start the game as usual.")
    return 0


def run_uninstall(args: argparse.Namespace) -> int:
    if args.game is None:
        if not interactive():
            print("wozmod: missing --game. Give it, or run wozmod in a terminal and it asks.", file=sys.stderr)
            return 2
        args.game = ask_path(INPUTS["game"].label, remembered.load_settings().get("game"),
                             lambda p: None if (p / "wozmod/install.json").is_file() else "wozmod is not installed there.")
    keep = not args.remove_settings
    if not (args.keep_settings or args.remove_settings) and interactive():
        keep = ask_yes("Keep your wozmod settings (language, field of view, frame rate, camera)?", True)
    return 1 if uninstall(args.game, log=print, keep_settings=keep) else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wozmod", description=__doc__)
    parser.add_argument("-v", "--version", action="version", version=f"wozmod {__version__}")
    commands = parser.add_subparsers(dest="command")
    add = commands.add_parser(
        "install", help="build the mod into a Musou Orochi Z folder (also updates an older install)",
        epilog="environment: WOZMOD_JOBS=N runs each build stage on at most N worker processes "
               "(default: one fewer than the CPUs, fewer if RAM is short). In a terminal, a path left out is asked for.")
    packaged = getattr(sys, "frozen", False)
    for name, each in INPUTS.items():
        shipped = resources.root() / SHIPPED[name] if packaged and name in SHIPPED else None
        add.add_argument(f"--{name}", type=Path, default=shipped, help=argparse.SUPPRESS if each.help is None else each.help)
    add.add_argument("--force", action="store_true", help="reinstall, or go back to an older version, without asking")
    remove = commands.add_parser("uninstall", help="delete the files the install wrote")
    remove.add_argument("--game", type=Path, help="the Musou Orochi Z folder")
    settings = remove.add_mutually_exclusive_group()
    settings.add_argument("--keep-settings", action="store_true", help="keep your settings (the default)")
    settings.add_argument("--remove-settings", action="store_true",
                          help="also delete the settings and log the game wrote, leaving the stock folder")
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            if not interactive():
                parser.print_help()
                return 2
            print(f"wozmod {__version__}")
            choice = ask_choice(["Install or update", "Uninstall", "Quit"])
            if choice is None:
                return 0
            args = parser.parse_args(["install" if choice == 1 else "uninstall"])
        return run_install(args) if args.command == "install" else run_uninstall(args)
    except InstallError as error:
        print(f"wozmod: {error}", file=sys.stderr)
        return 1
    except (Cancelled, KeyboardInterrupt, EOFError):
        print("\nwozmod: cancelled. Nothing was changed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()  # a packaged program starts its spawned workers through here
    raise SystemExit(main())
