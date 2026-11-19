#!/usr/bin/env python3
"""The fast suite under Windows Python, in Wine, for a tree.

    python3 tools/windows_suite.py [--tree DIR] [--out DIR] [PYTEST_ARG...]

runs pytest on DIR (default: this checkout) with the Windows CPython of packaging/windows-build.json, on a private
virtual display (Xvfb), and writes OUT (default DIR/work/windows-suite/) junit.xml, pytest.log and summary.txt. It
exits non-zero when a test fails or errors. Each run gets its own copy of the Windows environment (copy on write), so
runs from several checkouts at once share nothing they write.

The Windows environment is a Wine prefix with that Python, the packages uv.lock pins (the dev group), the native
codecs cross-built for Windows from DIR/native/, and a full Windows ffmpeg on PATH (all pinned in
packaging/windows-build.json). It lives in the main checkout's work/windows-env/KEY/, where KEY hashes everything it
is built from, so a change to any of them builds a new one and an unchanged tree reuses it. It is built in KEY.partial/ and renamed into place, under a lock, so
a crashed build leaves nothing a later run would take for finished. Environments unused for a week are removed.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import fcntl
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import checkout

ENVS = checkout.work() / "windows-env"
PIN = "packaging/windows-build.json"
KEYED = ("uv.lock", PIN)  # with every file in native/ but its target/, the rustc and RECIPE
PYTHON = "C:\\wozmod-python\\python.exe"
TOOLS = "C:\\wozmod-tools"  # on the Windows PATH: ffmpeg.exe and its libraries
RUST_TARGET = "x86_64-pc-windows-gnu"
UNUSED = 7 * 24 * 3600  # an environment no run used for this long is removed
WINE = {"WINEARCH": "win64", "WINEDEBUG": "-all", "WINEDLLOVERRIDES": "mscoree,mshtml="}  # no Mono or Gecko prompts


def windows(path: Path) -> str:
    """path as Windows programs under Wine name it: Wine maps the Linux root to Z:."""
    return "Z:" + str(Path(path).resolve()).replace("/", "\\")


def _rust() -> dict[str, str]:
    return dict(os.environ, PATH=f"{Path.home() / '.cargo/bin'}{os.pathsep}{os.environ['PATH']}",
                RUSTUP_TOOLCHAIN="stable")


def _run(command: list, **options) -> None:
    subprocess.run([str(part) for part in command], check=True, **options)


def rustc(tree: Path) -> str:
    """The compiler that builds the codecs: the step keys name it, and Wine cannot run it (WOZMOD_RUSTC_VERSION)."""
    return subprocess.run(["rustc", "-V"], cwd=tree / "native", env=_rust(), capture_output=True, encoding="utf-8",
                          check=True).stdout.strip()


def key(tree: Path) -> str:
    """What the environment for tree is built from, hashed."""
    native = tree / "native"
    files = [tree / name for name in KEYED] + sorted(
        p for p in native.rglob("*") if p.is_file() and p.relative_to(native).parts[0] != "target")
    digest = hashlib.sha256(repr((WINE, PYTHON, TOOLS, RUST_TARGET)).encode("utf-8"))
    for step in RECIPE:
        digest.update(inspect.getsource(step).encode("utf-8"))
    digest.update(rustc(tree).encode("utf-8"))
    for path in files:
        digest.update(f"\0{path.relative_to(tree).as_posix()}\0".encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:16]


def _fetch(url: str, sha256: str) -> Path:
    """url's file, downloaded once into work/windows-env/dl/ and checked against its pin."""
    path = ENVS / "dl" / url.rsplit("/", 1)[1]
    if not (path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256):
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        with urllib.request.urlopen(url) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out)
        if hashlib.sha256(partial.read_bytes()).hexdigest() != sha256:
            raise SystemExit(f"{url} does not match its pinned SHA-256")
        partial.replace(path)
    return path


def _build(tree: Path, out: Path) -> None:
    pin = json.loads((tree / PIN).read_text(encoding="utf-8"))
    prefix = out / "prefix"
    out.mkdir(parents=True)
    with _wine(prefix, out / "xvfb.log") as wine:  # the Python installer needs a display
        _install(tree, pin, out, wine)
    (out / "env.json").write_text(json.dumps({"key": out.name.removesuffix(".partial"), "rustc": rustc(tree),
                                              "python": pin["python"]["version"]}, indent=1), encoding="utf-8")


def _install(tree: Path, pin: dict, out: Path, wine: dict) -> None:
    # 1. CPython from the python.org installer, run silently: the NuGet build has no tkinter, which the window needs
    installer = _fetch(pin["python"]["url"], pin["python"]["sha256"])
    _run(["wine", installer, "/quiet", "InstallAllUsers=0", "TargetDir=C:\\wozmod-python", "Include_tcltk=1",
          "Include_pip=1", "Include_doc=0", "Include_dev=0", "Include_test=0", "Include_launcher=0",
          "Include_tools=0", "Shortcuts=0", "AssociateFiles=0", "PrependPath=0"], env=wine)
    pip = ["wine", PYTHON, "-m", "pip", "install", "-q", "--no-warn-script-location", "--disable-pip-version-check"]
    # 2. the packages the tests use, exactly as uv.lock pins them, with their hashes
    _run(["uv", "export", "--frozen", "--no-emit-project", "--no-emit-package", "wozmod-native", "--group", "dev",
          "--format", "requirements-txt", "-o", out / "requirements.txt"], cwd=tree, stdout=subprocess.DEVNULL)
    _run([*pip, "--require-hashes", "--only-binary=:all:", "-r", windows(out / "requirements.txt")], env=wine)
    # 3. the codecs, cross-built with MinGW as packaging/build_windows.sh builds them for the release
    rust = dict(_rust(), CARGO_TARGET_DIR=str(ENVS / "cargo-target"))
    _run(["rustup", "target", "add", RUST_TARGET], env=rust, stdout=subprocess.DEVNULL)
    _run(["uvx", "--from", f"maturin=={pin['maturin']}", "maturin", "build", "--release", "--target", RUST_TARGET,
          "-o", out / "wheels"], cwd=tree / "native", env=rust)
    (wheel,) = (out / "wheels").glob("*.whl")
    _run([*pip, "--no-deps", windows(wheel)], env=wine)
    # 4. a full ffmpeg on the Windows PATH (the runner adds TOOLS to it), as the Linux run finds the system's: the
    # tests make their inputs with filters and codecs that our own build, made for what an install needs, leaves out
    tools = out / "prefix/drive_c/wozmod-tools"
    tools.mkdir()
    with zipfile.ZipFile(_fetch(pin["test_ffmpeg"]["url"], pin["test_ffmpeg"]["sha256"])) as archive:
        for member in archive.infolist():
            if Path(member.filename).parent.name == "bin" and not member.is_dir():
                (tools / Path(member.filename).name).write_bytes(archive.read(member))


def environment(tree: Path) -> Path:
    """The folder of tree's Windows environment (prefix/, env.json), built first when there is none for its key."""
    found = ENVS / key(tree)
    ENVS.mkdir(parents=True, exist_ok=True)
    with (ENVS / ".build.lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)  # a build, and the removal of old environments, hold it alone
        if not found.is_dir():
            fcntl.flock(lock, fcntl.LOCK_EX)  # one build at a time: a .partial seen while holding it is a crash's
            if not found.is_dir():
                for stale in ENVS.glob("*.partial"):
                    shutil.rmtree(stale)
                partial = found.with_name(found.name + ".partial")
                print(f"building the Windows environment {found.name} (once per uv.lock, native/ and pins)",
                      file=sys.stderr)
                _build(tree, partial)
                partial.rename(found)
            for other in ENVS.iterdir():  # environments no run has used for a while
                if (other / "env.json").is_file() and other != found and time.time() - _used(other) > UNUSED:
                    shutil.rmtree(other)
        (found / "used").touch()  # under the lock, so a removal never takes one a run is about to copy
    return found


def _used(env: Path) -> float:
    return (env / "used").stat().st_mtime if (env / "used").exists() else (env / "env.json").stat().st_mtime


@contextlib.contextmanager
def _wine(prefix: Path, log: Path):
    """The environment for Wine programs in prefix, on a private virtual display (Xvfb) that lasts while it is used:
    Tk windows become viewable there, and nothing appears on the desktop. -noreset: Xvfb can abort when it resets
    after its last client leaves. At the end the prefix's wineserver goes, which writes its registry out."""
    read, write = os.pipe()
    with log.open("w", encoding="utf-8") as errors:
        xvfb = subprocess.Popen(["Xvfb", "-displayfd", str(write), "-nolisten", "tcp", "-noreset", "-screen", "0",
                                 "1280x1024x24"], pass_fds=(write,), stdout=subprocess.DEVNULL, stderr=errors)
    os.close(write)
    with os.fdopen(read, encoding="ascii") as numbers:
        number = numbers.readline().strip()
    environment = dict(os.environ, **WINE, WINEPREFIX=str(prefix), DISPLAY=f":{number}")
    environment.pop("WAYLAND_DISPLAY", None)
    try:
        if not number:
            raise SystemExit(f"Xvfb did not start (see {log})")
        yield environment
    finally:
        subprocess.run(["wineserver", "-k"], env=environment)
        subprocess.run(["wineserver", "-w"], env=environment)
        xvfb.terminate()
        xvfb.wait()


RECIPE = (_fetch, _wine, _build, _install)  # how an environment is built: the key hashes their code


def _copy(runs: Path, prefix: Path):
    """A copy of prefix for one run, and the lock that marks it in use; a copy whose lock is free is a crash's."""
    runs.mkdir(parents=True, exist_ok=True)
    with (runs / ".scan.lock").open("w", encoding="utf-8") as scan:
        fcntl.flock(scan, fcntl.LOCK_EX)  # so no other run takes a lock file this one has made but not locked yet
        for other in runs.glob("*.lock"):
            with other.open("a", encoding="utf-8") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                shutil.rmtree(other.with_suffix(""), ignore_errors=True)
                other.unlink()
        name = runs / uuid.uuid4().hex[:12]
        lock = name.with_suffix(".lock").open("a", encoding="utf-8")
        fcntl.flock(lock, fcntl.LOCK_EX)
    _run(["cp", "-a", "--reflink=auto", prefix, name])
    return name, lock


def summary(junit: Path) -> tuple[str, int]:
    """The run's counts, its failures and errors, and its skips by reason; and how many failed or errored."""
    counts: collections.Counter = collections.Counter()
    broken, skips = [], collections.Counter()
    for case in ET.parse(junit).getroot().iter("testcase"):
        name = f"{case.get('classname')}::{case.get('name')}"
        for kind in ("failure", "error", "skipped"):
            if (found := case.find(kind)) is not None:
                counts[kind] += 1
                message = (found.get("message") or "").splitlines()[0][:200] if found.get("message") else ""
                if kind == "skipped":
                    skips[message] += 1
                else:
                    broken.append(f"{kind.upper()} {name}: {message}")
                break
        else:
            counts["passed"] += 1
    lines = [", ".join(f"{counts[k]} {k}" for k in ("passed", "failure", "error", "skipped")), *broken, "skips:"]
    lines += [f"  {n:4} {reason}" for reason, n in skips.most_common()]
    return "\n".join(lines) + "\n", counts["failure"] + counts["error"]


def run(tree: Path, out: Path, arguments: list[str]) -> int:
    env = environment(tree)
    out.mkdir(parents=True, exist_ok=True)
    copy, held = _copy(ENVS / "runs", env / "prefix")
    junit = out / "junit.xml"
    junit.unlink(missing_ok=True)
    started = time.monotonic()
    try:
        with _wine(copy, out / "xvfb.log") as wine, (out / "pytest.log").open("w", encoding="utf-8") as log:
            wine.update(WINEPATH=TOOLS, PYTHONPATH=f"{windows(tree / 'src')};{windows(tree / 'tools')}",
                        PYTHONPYCACHEPREFIX=windows(ENVS / "pycache"),  # not the tree's __pycache__, Linux's
                        WOZMOD_RUSTC_VERSION=json.loads((env / "env.json").read_text(encoding="utf-8"))["rustc"])
            pytest = subprocess.run(["wine", PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs",
                                     f"--junitxml={windows(junit)}", *arguments], cwd=tree, env=wine, stdout=log,
                                    stderr=subprocess.STDOUT)
    finally:
        shutil.rmtree(copy)
        copy.with_suffix(".lock").unlink()
        held.close()
    if not junit.is_file():
        print(f"pytest wrote no results (exit {pytest.returncode}; see {out / 'pytest.log'})", file=sys.stderr)
        return 1
    text, broken = summary(junit)
    text = f"{tree} under Windows Python {env.name}, {time.monotonic() - started:.0f} s: {text}"
    (out / "summary.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    return 1 if broken or pytest.returncode else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tree", type=Path, default=checkout.REPO, help="the tree to test (default: this checkout)")
    parser.add_argument("--out", type=Path, help="where the results go (default: TREE/work/windows-suite)")
    options, arguments = parser.parse_known_args()
    tree = options.tree.resolve()
    return run(tree, (options.out or tree / "work/windows-suite").resolve(), arguments)


if __name__ == "__main__":
    sys.exit(main())
