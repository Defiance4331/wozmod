"""wozmod command line: install, update and uninstall. With nothing to go on, it asks."""

import argparse
import multiprocessing
import sys
from pathlib import Path

from wozmod import __version__, remembered, resources
from wozmod.install import (FILES, LABELS, PROGRAMS, resolve_program, InstallError, check_input, decrypt_with_tool, existing_install, install,
                            install_state, uninstall)
from wozmod.interactive import ask_choice, ask_path, ask_yes, interactive
from wozmod.progress import Cancelled, Progress, TerminalView, duration

INPUTS = (
    ("wo1", "Warriors Orochi PC (Europe) disc image"),
    ("x360", "Warriors Orochi 2 Xbox 360 (USA) disc image"),
    ("psp", "Warriors Orochi 2 PSP (USA) disc image"),
    ("x360-tool", "a decryption program you already have, for the Xbox 360 default.xex"),
    ("psp-tool", "a decryption program you already have, for the PSP EBOOT.BIN"),
    ("x360-file", "advanced: in place of --x360-tool, the Xbox 360 program file already decrypted"),
    ("psp-file", "advanced: in place of --psp-tool, the PSP EBOOT.BIN already decrypted"),
    ("lettering", None),  # for development: the release lettering bundle
    ("dll", None),  # for development: the release d3d9.dll
)
ASKED = ("game", "wo1", "x360", "psp", *PROGRAMS)  # the inputs the terminal asks for when they are missing
SHIPPED = {"lettering": "lettering.zip", "dll": "d3d9.dll"}  # inside the packaged program's resource folder


def attribute(name: str) -> str:
    return name.replace("-", "_")


def tool_problem(name: str, tool: Path, image: Path) -> str | None:
    """None when the program, run on the file from the disc image, gives the file wozmod needs."""
    found = decrypt_with_tool(name, tool, image)
    return found if isinstance(found, str) else None


def not_needed(name: str, args: argparse.Namespace) -> bool:
    """True when a program input is covered: a decrypted file was given, or the disc image holds the file."""
    disc = PROGRAMS[name][0]
    values = {disc: getattr(args, attribute(disc)), FILES[name]: getattr(args, attribute(FILES[name]))}
    return values[FILES[name]] is not None or resolve_program(name, values)[0] == disc


def ask_missing(args: argparse.Namespace) -> None:
    """Ask for each missing input, offering what the setup window remembered, and check the answer at once."""
    saved = remembered.load_settings()
    for name in ASKED:
        if getattr(args, attribute(name)) is None and not (name in PROGRAMS and not_needed(name, args)):
            if name in PROGRAMS:
                image = getattr(args, attribute(PROGRAMS[name][0]))
                check, label = (lambda p, n=name, i=image: tool_problem(n, p, i)), f"{LABELS[name]} you already have"
            else:
                check, label = (lambda p, n=name: check_input(n, p)), LABELS[name]
            setattr(args, attribute(name), ask_path(label, saved.get(name), check))


def remember(args: argparse.Namespace) -> None:
    values = {name: str(getattr(args, attribute(name)))
              for name in (*ASKED, *FILES.values()) if getattr(args, attribute(name), None)}
    remembered.save_settings({**remembered.load_settings(), **values})


def run_install(args: argparse.Namespace) -> int:
    absent = [f"--{name} or --{FILES[name]}" if name in FILES else f"--{name}" for name in ASKED
              if getattr(args, attribute(name)) is None and not (name in FILES and not_needed(name, args))]
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
    install(args.game, {name: getattr(args, attribute(name)) for name, _ in INPUTS},
            force=args.force, log=lambda _: None, progress=progress)
    view.finish(progress, f"Installed in {duration(progress.elapsed())}. Start the game as usual.")
    return 0


def run_uninstall(args: argparse.Namespace) -> int:
    if args.game is None:
        if not interactive():
            print("wozmod: missing --game. Give it, or run wozmod in a terminal and it asks.", file=sys.stderr)
            return 2
        args.game = ask_path(LABELS["game"], remembered.load_settings().get("game"),
                             lambda p: None if (p / "wozmod/install.json").is_file() else "wozmod is not installed there.")
    keep = not args.remove_settings
    if not (args.keep_settings or args.remove_settings) and interactive():
        keep = ask_yes("Keep your wozmod settings (language, field of view, frame rate)?", True)
    return 1 if uninstall(args.game, log=print, keep_settings=keep) else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wozmod", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    add = commands.add_parser(
        "install", help="build the mod into a Musou Orochi Z folder (also updates an older install)",
        epilog="environment: WOZMOD_JOBS=N runs each build stage on at most N worker processes "
               "(default: one fewer than the CPUs, fewer if RAM is short). In a terminal, a path left out is asked for.")
    add.add_argument("--game", type=Path, help="the stock Musou Orochi Z folder")
    packaged = getattr(sys, "frozen", False)
    for name, text in INPUTS:
        shipped = resources.root() / SHIPPED[name] if packaged and name in SHIPPED else None
        add.add_argument(f"--{name}", type=Path, default=shipped, help=argparse.SUPPRESS if text is None else text)
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
