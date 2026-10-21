#!/usr/bin/env python3
"""Stage a disposable MOZ harness run under work/runtime-tests/.

The run gets links to the stock backup's files, reflinks of the install's d3d9.dll and wozmod/ folder (checked
against the installer's record), an overlay folder over the template Proton prefix, a seeded save
and a game.sha256 over the mod files, which game_harness.py run finds unchanged before each launch. Machine paths
come from the ignored work/harness.toml.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORK = REPO / "work"
CONFIG = Path(os.environ.get("WOZMOD_HARNESS_CONFIG", WORK / "harness.toml"))
SAVE = "prefix/pfx/drive_c/users/steamuser/Documents/KOEI/Musou OROCHI Z/Savedata/save.dat"
STOCK_HASHED = ["OROCHI_Z.exe", *(f"LINKDATA{n}.{ext}" for n in "123" for ext in ("BIN", "IDX")),
                "data/Sound/Voice/Voice.bin"]


HASH_CACHE = WORK / "hash-cache.json"
MANIFEST = "wozmod/install.json"  # the installer's record of every file it wrote, with its SHA-256
# the mod's own settings, as its Settings menu writes them to wozmod/settings.ini: section.key -> allowed values
SETTINGS = {"language.text": ("english", "japanese"), "language.voice": ("english", "japanese"),
            "camera.fov": None, "camera.moving_fix": ("on", "off"), "display.fps": ("60", "120"),
            "display.distance": ("on", "off"),
            "display.mode": ("fullscreen", "windowed", "borderless"),
            # the runtime's display size list, read from its one source
            "display.size": tuple(f"{w}x{h}" for w, h in re.findall(
                r"\{(\d+), (\d+)\}", (REPO / "runtime/display_fit.h").read_text().split("display_sizes[][2] = {")[1]
                .split("};")[0]))}
OVERLAID = ("prefix",)  # template parts a run sees through an overlay instead of a copy (the Steam runtime is its
# game slot's own: game_harness.RUNTIMES)
EXTRAS = {"wozmod/review.bin"}  # test-only files a run may hold beyond the record: the `review` reference


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 24):
            digest.update(block)
    return digest.hexdigest()


def cached_sha256s(paths: list[Path], cache_file: Path = HASH_CACHE) -> list[str]:
    """sha256 of each file, remembered by path, size, mtime and inode; any change rehashes. The misses are hashed in
    threads and the cache is written once.
    Shortcut: a file edited in place with its size and mtime restored is not noticed; keep the stock backup and
    installs read-only."""
    cache = json.loads(cache_file.read_text()) if cache_file.is_file() else {}
    keys = []
    for path in paths:
        info = path.stat()
        keys.append((str(path.resolve()), [info.st_size, info.st_mtime_ns, info.st_ino]))
    missing = [(key, stamp) for key, stamp in keys if (cache.get(key) or {}).get("stamp") != stamp]
    if missing:
        with ThreadPoolExecutor() as pool:
            for (key, stamp), digest in zip(missing, pool.map(lambda m: sha256(Path(m[0])), missing), strict=True):
                cache[key] = {"stamp": stamp, "sha256": digest}
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        # a temporary file of its own: two checks staging at once must not rename each other's
        with tempfile.NamedTemporaryFile("w", dir=cache_file.parent, suffix=".next", delete=False) as temporary:
            temporary.write(json.dumps(cache, indent=1) + "\n")
        Path(temporary.name).replace(cache_file)
    return [cache[key]["sha256"] for key, _ in keys]


def reflink(source: Path, target: Path) -> None:
    subprocess.run(["cp", "-a", "--reflink=always", str(source), str(target)], check=True)


def stock_backup() -> Path:
    if not CONFIG.is_file():
        raise ValueError(f"no {CONFIG}; write stock_backup = \"/path/to/stock/game\" there")
    stock = Path(tomllib.loads(CONFIG.read_text())["stock_backup"])
    if not (stock / "OROCHI_Z.exe").is_file():
        raise ValueError(f"no stock game at stock_backup: {stock}")
    return stock


def installed_files(install: Path, cache_file: Path = HASH_CACHE) -> dict[str, str]:
    """The install's files and their SHA-256, each matching the installer's own record, so a run plays what a
    player's install holds and nothing else (EXTRAS aside)."""
    try:
        listed = json.loads((install / MANIFEST).read_text())["files"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{install} has no installer record {MANIFEST}; stage a folder `wozmod install` wrote") from exc
    names = sorted(p.relative_to(install).as_posix() for p in [install / "d3d9.dll", *(install / "wozmod").rglob("*")]
                   if p.is_file())
    found = dict(zip(names, cached_sha256s([install / f for f in names], cache_file), strict=True))
    found.pop(MANIFEST)
    wrong = sorted(f for f in set(found) | set(listed)
                   if f not in EXTRAS and found.get(f) != listed.get(f))
    if wrong:
        raise ValueError(f"{install} differs from its installer record at: {', '.join(wrong[:8])}")
    return found


def settings_ini(settings: list[str]) -> str:
    """settings.ini for section.key=value pairs, the file a player's earlier session left (Windows INI, CRLF)."""
    sections: dict[str, list[str]] = {}
    for pair in settings:
        key, _, value = pair.partition("=")
        allowed = SETTINGS.get(key, ())
        if key not in SETTINGS or (value not in allowed if allowed else not value.isdigit()):
            raise ValueError(f"setting {pair!r}: use one of {', '.join(SETTINGS)} with a value the Settings menu writes")
        section, _, name = key.partition(".")
        sections.setdefault(section, []).append(f"{name}={value}")
    return "".join(f"[{section}]\r\n" + "".join(f"{line}\r\n" for line in lines) for section, lines in sections.items())


def check_template(template: Path) -> None:
    """Refuse a template whose prefix a launch would rebuild. Wine rewrites its built-in DLLs when the prefix's
    .update-timestamp is not wine.inf's mtime, and Proton recopies them when another Proton build wrote config_info;
    in a run those writes land in its own overlay, about 250 MB of unshared data per run. So launch.sh runs a pinned
    copy of Proton in the template, and the prefix must have been set up by that copy."""
    proton = template / "proton"
    if f'PROTON="{proton}/proton"' not in (template / "launch.sh").read_text():
        raise ValueError(f"{template}/launch.sh must run the pinned Proton {proton}/proton, not a copy that updates "
                         "itself (copy it there with cp -a --reflink=always)")
    stamp = (template / "prefix/pfx/.update-timestamp").read_text().strip()
    if (stamp != str(int((proton / "files/share/wine/wine.inf").stat().st_mtime))
            or f"{proton}/files/" not in (template / "prefix/config_info").read_text()):
        raise ValueError(f"the template prefix was set up by another Proton build; refresh it once: "
                         f"STEAM_COMPAT_DATA_PATH={template}/prefix STEAM_COMPAT_CLIENT_INSTALL_PATH=$HOME/.local/share/"
                         f"Steam $HOME/.local/share/Steam/steamapps/common/SteamLinuxRuntime_4/run --batch -- "
                         f"{proton}/proton run wineboot.exe -u")


def prepare(name: str, install: Path, dll: Path | None, save: str, cache_file: Path = HASH_CACHE,
            settings: list[str] = (), replace: list[str] = ()) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name):
        raise ValueError("run name is a short filename of letters, digits, . _ and -")
    template = WORK / "runtime-tests/_template"
    check_template(template)
    files = installed_files(install, cache_file)
    replaced = {}  # an installed file swapped for a working build, as --dll does for d3d9.dll; fixtures.json lists them
    for pair in replace:
        listed, _, source = pair.partition("=")
        if listed not in files or listed == "d3d9.dll":
            raise ValueError(f"--file {pair!r}: name a file of the install record, such as wozmod/table.bin=PATH "
                             "(d3d9.dll goes in with --dll)")
        replaced[listed] = Path(source).resolve(strict=True)
    ini = settings_ini(settings) if settings else None
    dll = (dll or install / "d3d9.dll").resolve(strict=True)
    fixture = WORK / "saves" / save
    meta = json.loads((fixture / "meta.json").read_text())
    data = (fixture / "save.dat").read_bytes()
    if hashlib.sha256(data).hexdigest() != meta["save_sha256"]:
        raise ValueError(f"{fixture}/save.dat changed since export")
    stock, root = stock_backup(), WORK / "runtime-tests" / name
    if root.exists():
        raise ValueError(f"exists: {root}")
    # Proton's lsteamclient loads $HOME/.steam/sdk64/steamclient.so; the harness HOME is run-local.
    (root / "home/.steam").mkdir(parents=True)
    for sdk in ("sdk32", "sdk64"):
        (root / "home/.steam" / sdk).symlink_to((Path.home() / ".steam" / sdk).resolve(strict=True))
    # The game reads the stock files through links into the read-only backup (it only adds files of its own), so a
    # run copies none of its 5.5 GB and only the mod's files are checked.
    game = root / "game"
    game.mkdir()
    for entry in sorted(stock.iterdir()):
        if entry.name not in ("d3d9.dll", "wozmod", "moz-english"):  # moz-english: the old build's folder
            (game / entry.name).symlink_to(entry)
    reflink(dll, game / "d3d9.dll")
    reflink(install / "wozmod", game / "wozmod")
    for listed, source in replaced.items():
        (game / listed).unlink()  # never write through the install's shared extent
        reflink(source, game / listed)
    if ini:  # the game rewrites it, so game.sha256 leaves it out
        (game / "wozmod/settings.ini").write_text(ini, newline="")
    files |= dict(zip(["d3d9.dll", MANIFEST, *replaced], cached_sha256s([dll, install / MANIFEST, *replaced.values()],
                                                                       cache_file), strict=True))
    # The prefix is the template's seen through an overlay (game_harness.py run): the run's prefix/ holds only what the
    # run writes, here the game link and the save.
    for part in OVERLAID:
        (root / part).mkdir()
    link = root / "prefix/pfx/drive_c/Games/Musou Orochi Z"
    link.parent.mkdir(parents=True)
    link.symlink_to(game)
    shutil.copyfile(template / "launch.sh", root / "launch.sh")
    shutil.copymode(template / "launch.sh", root / "launch.sh")
    (root / "game.sha256").write_text("".join(f"{files[f]}  {f}\n" for f in sorted(files)))
    target = root / SAVE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    stock_sha256 = dict(zip(STOCK_HASHED, cached_sha256s([stock / f for f in STOCK_HASHED], cache_file), strict=True))
    (root / "fixtures.json").write_text(json.dumps(
        {"save": {"name": save, "sha256": meta["save_sha256"]}, "dll": str(dll), "install": str(install),
         "version": json.loads((install / MANIFEST).read_text()).get("version"),
         "extras": sorted(EXTRAS & set(files)), "settings": list(settings),
         "replaced": {listed: str(source) for listed, source in replaced.items()},
         "stock": {"path": str(stock), "sha256": stock_sha256},
         "overlays": {part: str(template / part) for part in OVERLAID},
         # each checked file's size, mtime and inode as staged: game_harness.py run rehashes only a file that differs
         "stamps": {f: [(info := (game / f).stat()).st_size, info.st_mtime_ns, info.st_ino] for f in sorted(files)}},
        indent=2) + "\n")
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="new run directory name under work/runtime-tests")
    parser.add_argument("--install", type=Path, required=True, help="directory holding d3d9.dll and wozmod/")
    parser.add_argument("--dll", type=Path, help="use this d3d9.dll instead of the install's, such as instrumented/d3d9.dll")
    parser.add_argument("--save", default="full-unlock", help="fixture under work/saves (default full-unlock)")
    parser.add_argument("--setting", action="append", default=[], metavar="SECTION.KEY=VALUE",
                        help="a mod setting the player saved earlier, such as display.fps=120 (default: none, as installed)")
    parser.add_argument("--file", action="append", default=[], metavar="INSTALLED=PATH",
                        help="swap one installed file for a working build, such as wozmod/table.bin=work/x/table.bin")
    args = parser.parse_args(argv)
    try:
        print(prepare(args.name, args.install.resolve(strict=True), args.dll, args.save, settings=args.setting,
                      replace=args.file))
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"prepare_run: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
