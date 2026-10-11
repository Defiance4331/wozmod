#!/usr/bin/env python3
"""Stage a disposable MOZ harness run under work/runtime-tests/.

The run gets a reflinked copy of the stock backup, the install's d3d9.dll and wozmod/ folder,
the template Proton prefix and Steam runtime, a seeded save and a game.sha256 over everything
launch.sh must find unchanged. Machine paths come from the ignored work/harness.toml.
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 24):
            digest.update(block)
    return digest.hexdigest()


def cached_sha256(path: Path, cache_file: Path = HASH_CACHE) -> str:
    """sha256 of a read-only stock file, remembered by path, size, mtime and inode; any change rehashes.
    Shortcut: a file edited in place with its size and mtime restored is not noticed; keep the stock backup read-only."""
    info = path.stat()
    key, stamp = str(path.resolve()), [info.st_size, info.st_mtime_ns, info.st_ino]
    cache = json.loads(cache_file.read_text()) if cache_file.is_file() else {}
    if (hit := cache.get(key)) and hit["stamp"] == stamp:
        return hit["sha256"]
    cache[key] = {"stamp": stamp, "sha256": sha256(path)}
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_file.with_suffix(".next")
    temporary.write_text(json.dumps(cache, indent=1) + "\n")
    temporary.replace(cache_file)
    return cache[key]["sha256"]


def reflink(source: Path, target: Path) -> None:
    subprocess.run(["cp", "-a", "--reflink=always", str(source), str(target)], check=True)


def stock_backup() -> Path:
    if not CONFIG.is_file():
        raise ValueError(f"no {CONFIG}; write stock_backup = \"/path/to/stock/game\" there")
    stock = Path(tomllib.loads(CONFIG.read_text())["stock_backup"])
    if not (stock / "OROCHI_Z.exe").is_file():
        raise ValueError(f"no stock game at stock_backup: {stock}")
    return stock


def prepare(name: str, install: Path, dll: Path | None, save: str, cache_file: Path = HASH_CACHE) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name):
        raise ValueError("run name is a short filename of letters, digits, . _ and -")
    dll = (dll or install / "d3d9.dll").resolve(strict=True)
    if not (install / "wozmod").is_dir() or not dll.is_file():
        raise ValueError(f"{install} needs d3d9.dll (or --dll) and a wozmod/ folder")
    template = WORK / "runtime-tests/_template"
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
    reflink(stock, root / "game")
    shutil.rmtree(root / "game/moz-english", ignore_errors=True)  # the old build's folder
    reflink(dll, root / "game/d3d9.dll")
    reflink(install / "wozmod", root / "game/wozmod")
    game = root / "game"
    # d3d9.dll and wozmod/ are hashed fresh, in threads beside the prefix copies; the stock files come from the
    # read-only backup (the run's copies are reflinks of it) through the cache
    fresh = ["d3d9.dll", *sorted(p.relative_to(game).as_posix() for p in (game / "wozmod").rglob("*") if p.is_file())]
    with ThreadPoolExecutor() as pool:
        digests = pool.map(lambda f: sha256(game / f), fresh)
        for part in ("prefix", "steamrt"):
            reflink(template / part, root / part)
        (root / "prefix/pfx/drive_c/Games/Musou Orochi Z").unlink(missing_ok=True)
        (root / "prefix/pfx/drive_c/Games/Musou Orochi Z").symlink_to(root / "game")
        shutil.copyfile(template / "launch.sh", root / "launch.sh")
        shutil.copymode(template / "launch.sh", root / "launch.sh")
        (root / "game.sha256").write_text(
            "".join(f"{cached_sha256(stock / f, cache_file)}  {f}\n" for f in STOCK_HASHED)
            + "".join(f"{digest}  {f}\n" for digest, f in zip(digests, fresh)))
    target = root / SAVE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)  # never write through the template's shared extent
    target.write_bytes(data)
    (root / "fixtures.json").write_text(json.dumps(
        {"save": {"name": save, "sha256": meta["save_sha256"]}, "dll": str(dll), "install": str(install)},
        indent=2) + "\n")
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="new run directory name under work/runtime-tests")
    parser.add_argument("--install", type=Path, required=True, help="directory holding d3d9.dll and wozmod/")
    parser.add_argument("--dll", type=Path, help="use this d3d9.dll instead of the install's, such as instrumented/d3d9.dll")
    parser.add_argument("--save", default="full-unlock", help="fixture under work/saves (default full-unlock)")
    args = parser.parse_args(argv)
    try:
        print(prepare(args.name, args.install.resolve(strict=True), args.dll, args.save))
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"prepare_run: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
