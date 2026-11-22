"""The licence notices for the third-party code frozen into a release, made at release build time from what goes in.

    python3 packaging/notices.py crates linux|windows OUT     the Rust crates notice (packaging/build_windows.sh)
    python3 packaging/notices.py refresh-python ARCHIVE        the Linux Python's licences, from its full archive

RUST-CRATES-NOTICE.txt: the crates wozmod_native compiles. cargo tree (offline, from the registry the module's build
filled) names them for that system's target with the features the release builds it with, which leaves out
dev-dependencies, build-dependencies (they run on the build machine only) and the off-by-default cuda feature. packaging/build.py writes
it on Linux; Wine cannot run cargo, so packaging/build_windows.sh writes the Windows one before build.py, which checks
it was made from this tree (crates_problem()).

PYTHON-RUNTIME-NOTICE.txt: what PyInstaller froze into the wozmod program besides our own code, read from its build
records (COLLECT, PKG and PYZ tables): each file is the Python runtime's, a Python package's (its wheel's licence
files), PyInstaller's, or on Linux a system library's (its Fedora package's licence files). The Linux Python is
python-build-standalone, which links libraries such as OpenSSL into libpython but ships only Python's own licence:
packaging/python/linux/ holds that build's licence texts, taken from its full archive by refresh-python, and the build
stops when it runs on another Python build. To refresh them for the build uv installed (its BUILD file names it):

    gh release download BUILD -R astral-sh/python-build-standalone -p 'cpython-VERSION+BUILD-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst'
    uv run python packaging/notices.py refresh-python cpython-VERSION+BUILD-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst

Each notice lists every part with its version and licence, then each different licence text once. A part with no
licence text stops the build.
"""

from __future__ import annotations

import ast
import hashlib
import html
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native"
CRATES = "RUST-CRATES-NOTICE.txt"
RUNTIME = "PYTHON-RUNTIME-NOTICE.txt"
# The targets the releases build the module for: Linux by uv sync on the build machine, Windows by build_windows.sh.
TARGETS = {"linux": "x86_64-unknown-linux-gnu", "windows": "x86_64-pc-windows-gnu"}
LICENCE_FILE = re.compile(r"(?i)^(licen[cs]e|copying|notice)")
PYTHON_LICENCES = ROOT / "packaging/python/linux"
OURS = {"wozmod", "wozmod-native"}  # our distributions: under LICENSE
RULE = "=" * 100


@dataclass
class Part:
    name: str
    version: str
    licence: str
    files: list[Path | tuple[str, str]] = field(default_factory=list)  # licence files, or (name, text)


def _text(file: Path | tuple[str, str]) -> str:
    text = file[1] if isinstance(file, tuple) else file.read_bytes().decode("utf-8", errors="replace")
    return "\n".join(line.rstrip() for line in text.strip("\n").splitlines()) + "\n"


def _name(file: Path | tuple[str, str]) -> str:
    return file[0] if isinstance(file, tuple) else file.name


def render(intro: list[str], parts: list[Part]) -> str:
    """intro, a row per part (name, version, the texts it carries, its licence), then each different text once with
    the parts it comes from. Stops when a part has no licence text."""
    if missing := [" ".join(filter(None, (part.name, part.version))) for part in parts if not part.files]:
        raise SystemExit("these parts of the release carry no licence text, so it cannot ship their notice: "
                         + ", ".join(missing))
    texts: dict[str, list[str]] = {}  # each different text, with the files that carry it
    rows = []
    for part in parts:
        labels = []
        for file in part.files:
            text = _text(file)
            texts.setdefault(text, []).append(" ".join(filter(None, (part.name, part.version, f"({_name(file)})"))))
            labels.append(f"[{list(texts).index(text) + 1}]")
        rows.append((part.name, part.version, " ".join(dict.fromkeys(labels)), part.licence))
    widths = [max(len(row[column]) for row in rows) for column in range(3)]
    lines = [*intro, "", *(f"{name:<{widths[0]}}  {version:<{widths[1]}}  {labels:<{widths[2]}}  {licence}".rstrip()
                           for name, version, labels, licence in rows)]
    for number, (text, users) in enumerate(texts.items(), 1):
        lines += ["", RULE, f"[{number}] from {', '.join(dict.fromkeys(users))}", RULE, "", text.rstrip("\n")]
    return "\n".join(lines) + "\n"


# The Rust crates in wozmod_native

def crates_digest(native: Path = NATIVE) -> str:
    """What decides the crates and their features: Cargo.toml and Cargo.lock."""
    digest = hashlib.sha256()
    for name in ("Cargo.toml", "Cargo.lock"):
        digest.update((native / name).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def cargo_metadata(target: str, native: Path = NATIVE) -> dict:
    """cargo metadata for the module's packages: where each one's source is, and its licence."""
    return json.loads(_cargo(["metadata", "--format-version", "1", "--filter-platform", target], native))


def cargo_tree(target: str, native: Path = NATIVE) -> str:
    """The packages the module compiles for target, one "name vVERSION" per line, from cargo's feature resolver with
    the default features the release builds with: normal dependencies only (proc-macros among them), so neither
    dev-dependencies nor build-dependencies (they run on the build machine only). cargo metadata's graph cannot tell
    this: it turns on an optional dependency that a feature names only weakly ("ndarray?/std" in ort)."""
    return _cargo(["tree", "-e", "normal", "--target", target, "--prefix", "none", "--format", "{p}"], native)


def _cargo(arguments: list[str], native: Path) -> str:
    return _run(["cargo", *arguments, "--offline", "--locked"], native)


def _run(command: list[str], folder: Path) -> str:
    return subprocess.run(command, cwd=folder, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def compiled(tree: str, meta: dict) -> list[dict]:
    """The metadata of each package in tree (cargo_tree()) but the module itself, by name and version."""
    packages = {(package["name"], package["version"]): package for package in meta["packages"]}
    root = next(package for package in meta["packages"] if package["id"] == meta["resolve"]["root"])
    names = {(line.split()[0], line.split()[1].removeprefix("v")) for line in tree.splitlines() if line.strip()}
    names.discard((root["name"], root["version"]))
    if missing := sorted(names - packages.keys()):
        raise SystemExit(f"cargo tree names crates that cargo metadata does not: {missing}")
    return [packages[name] for name in sorted(names)]


def crate_licence_files(package: dict) -> list[Path]:
    folder = Path(package["manifest_path"]).parent
    files = {path for path in folder.iterdir() if path.is_file() and LICENCE_FILE.match(path.name)}
    if package.get("license_file"):
        files.add(folder / package["license_file"])
    return sorted(files)


def toolchain_parts(system: str) -> list[Part]:
    """What the toolchain links into the module besides the crates: the Rust standard library with the crates.io
    crates it is built from (those the toolchain has a library of for the target), and on Windows the MinGW-w64 C
    runtime. The GCC support library linked in with it needs no notice (GCC Runtime Library Exception)."""
    sysroot = Path(_run(["rustc", "--print", "sysroot"], NATIVE).strip())
    rustc = _run(["rustc", "-V"], NATIVE).split()[1]
    doc = sysroot / "share/doc/rust"
    text = html.unescape(re.sub(r"<[^>]+>", "", (doc / "COPYRIGHT-library.html").read_text(encoding="utf-8")))
    start, end = text.find("The following licenses cover the in-tree"), text.find("The following licenses cover the out-of-tree")
    if start < 0 or end < start:
        raise SystemExit(f"{doc}/COPYRIGHT-library.html does not say which licences cover the standard library")
    in_tree = re.sub(r"\n\s*\n+", "\n", text[start:end]).strip()
    ids = sorted({token for expression in re.findall(r"License: (.+)", in_tree)
                  for token in re.findall(r"[A-Za-z0-9.+-]+", expression) if token not in ("AND", "OR", "WITH")})
    if missing := [id_ for id_ in ids if not (doc / "licenses" / f"{id_}.txt").is_file()]:
        raise SystemExit(f"{doc}/licenses lacks the texts of {missing}, which the Rust standard library uses")
    parts = [Part("Rust standard library", rustc, "Apache-2.0 OR MIT, and as COPYRIGHT-library says",
                  [("COPYRIGHT-library", f"Copyright notices for the Rust Standard Library {rustc}\n\n{in_tree}\n"),
                   *(doc / "licenses" / f"{id_}.txt" for id_ in ids)])]
    libraries = {re.sub(r"-[0-9a-f]+$", "", path.stem).removeprefix("lib")
                 for path in (sysroot / "lib/rustlib" / TARGETS[system] / "lib").glob("lib*.rlib")}
    for name, version in _std_vendored(sysroot):
        if name.replace("-", "_") in libraries:
            parts.append(_registry_crate(name, version, "in the Rust standard library"))
    if system == "windows":
        runtime = _run(["x86_64-w64-mingw32-gcc", "-print-file-name=libmingwex.a"], NATIVE).strip()
        parts.append(system_package(Path(runtime).resolve()))
    return parts


def _std_vendored(sysroot: Path) -> list[tuple[str, str]]:
    """The crates.io crates the toolchain's std links (not those only libtest or proc_macro use): from the lock file
    of rustup's rust-src, or the list Fedora's rust-std-static package keeps, which does not say which part uses
    which, so getopts (libtest's) and rustc-literal-escaper (proc_macro's) are named here."""
    lock = sysroot / "lib/rustlib/src/rust/library/Cargo.lock"
    if lock.is_file():
        packages = tomllib.loads(lock.read_text(encoding="utf-8"))["package"]
        by_name = {package["name"]: package for package in packages}
        seen, todo = set(), ["std"]
        while todo:
            for dependency in by_name[todo.pop()].get("dependencies", ()):
                name = dependency.split()[0]
                if name not in seen and name in by_name:
                    seen.add(name)
                    todo.append(name)
        return sorted((by_name[name]["name"], by_name[name]["version"]) for name in seen if "source" in by_name[name])
    fedora = sysroot / "share/licenses/rust-std-static/cargo-vendor.txt"
    if fedora.is_file():
        return sorted((line.split()[0], line.split()[1].removeprefix("v"))
                      for line in fedora.read_text(encoding="utf-8").splitlines()
                      if line.strip() and line.split()[0] not in ("getopts", "rustc-literal-escaper"))
    raise SystemExit(f"the toolchain at {sysroot} does not say which crates its standard library is built from "
                     "(rustup component add rust-src gives its lock file)")


def _registry_crate(name: str, version: str, where: str) -> Part:
    home = Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")
    folders = sorted((home / "registry/src").glob(f"*/{name}-{version}"))
    if not folders:
        raise SystemExit(f"{name} {version}, {where}, is not in the local cargo registry ({home}), so the release "
                         f"cannot ship its licence: `cargo fetch` a project that depends on {name} = \"={version}\"")
    manifest = tomllib.loads((folders[0] / "Cargo.toml").read_text(encoding="utf-8"))["package"]
    package = {"manifest_path": str(folders[0] / "Cargo.toml"), "license_file": manifest.get("license-file")}
    return Part(f"{name} ({where})", version, manifest.get("license") or "see its licence file",
                crate_licence_files(package))


def crates_notice(tree: str, meta: dict, system: str, digest: str, toolchain: list[Part] = ()) -> str:
    parts = [*toolchain, *(Part(p["name"], p["version"], p.get("license") or "see its licence file",
                                crate_licence_files(p)) for p in compiled(tree, meta))]
    return render([
        "Third-party code in wozmod's Rust module",
        "",
        f"wozmod_native (in _internal/) is wozmod's own Rust module, under wozmod's licence (LICENSE). Built for {system}",
        f"({TARGETS[system]}), it also holds the code of the {len(parts) - len(toolchain)} crates from crates.io and what",
        "the toolchain links in, listed below, each under the licence its authors give it; where a part offers a choice",
        "(\"A OR B\"), wozmod uses it under any one of them. The licence texts they carry follow the list, each different",
        "text once, with the parts it comes from.",
        "",
        f"Made by packaging/notices.py from native/Cargo.toml and native/Cargo.lock, sha256 {digest}",
    ], parts)


def write_crates(system: str, out: Path) -> None:
    target = TARGETS[system]
    out.write_text(crates_notice(cargo_tree(target), cargo_metadata(target), system, crates_digest(),
                                 toolchain_parts(system)), encoding="utf-8", newline="\n")


def crates_problem(path: Path, system: str) -> str | None:
    """Why a crates notice written beforehand (the Windows build's) is not this tree's for that system, or None."""
    if not path.is_file():
        return f"{path} is missing: packaging/build_windows.sh writes it with packaging/notices.py"
    text = path.read_text(encoding="utf-8")
    if f"sha256 {crates_digest()}" not in text or f"({TARGETS[system]})" not in text:
        return (f"{path} was made from another native/Cargo.toml or Cargo.lock, or for another system: "
                "run packaging/build_windows.sh again")
    return None


# What PyInstaller froze into the wozmod program

def frozen(workpath: Path) -> list[tuple[str, str | None, str]]:
    """(name in the program, source file, kind) of everything PyInstaller put in it: the folder beside the program
    (COLLECT), the program's own archive (PKG) and the modules in its PYZ."""
    entries = []
    for table in ("COLLECT-00.toc", "PKG-00.toc", "PYZ-00.toc"):
        for item in ast.literal_eval((workpath / table).read_text(encoding="utf-8")):
            if isinstance(item, list):
                entries += [tuple(entry) for entry in item]
    return entries


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _distribution_files() -> dict[Path, importlib.metadata.Distribution]:
    owners = {}
    for dist in importlib.metadata.distributions():
        for file in dist.files or ():
            owners[Path(dist.locate_file(file)).resolve()] = dist
    return owners


def distribution_licence_files(dist: importlib.metadata.Distribution) -> list[Path]:
    """The licence files a wheel declares (License-File, under .dist-info/licenses/ or .dist-info/), or failing that
    any LICENSE*, COPYING* or NOTICE* in its .dist-info."""
    files = [Path(dist.locate_file(file)) for file in dist.files or ()
             if ".dist-info/" in file.as_posix() and LICENCE_FILE.match(file.name)]
    declared = set(dist.metadata.get_all("License-File") or ())
    chosen = [path for path in files if path.name in {Path(name).name for name in declared}] or files
    return sorted(set(chosen))


def _distribution_licence(dist: importlib.metadata.Distribution) -> str:
    metadata = dist.metadata
    if expression := metadata.get("License-Expression"):
        return expression
    if (licence := (metadata.get("License") or "").strip()) and "\n" not in licence and len(licence) < 120:
        return licence
    classifiers = [c.rsplit(" :: ", 1)[-1] for c in metadata.get_all("Classifier") or () if c.startswith("License ::")]
    return "; ".join(classifiers) or "see its licence file"


def system_package(path: Path) -> Part:
    """The Fedora package a system library came from, with its licence files, or those of the packages built from the
    same source (zlib-ng-compat's are in zlib-ng)."""
    query = subprocess.run(["rpm", "-qf", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{LICENSE}\t%{SOURCERPM}\n",
                            str(path)], capture_output=True, text=True, encoding="utf-8")
    if query.returncode:
        raise SystemExit(f"{path}, which PyInstaller put in the program, belongs to no system package, so the release "
                         "cannot name its licence")
    name, version, licence, source = query.stdout.splitlines()[0].split("\t")
    siblings = subprocess.run(["rpm", "-qa", "--qf", "%{NAME}\t%{SOURCERPM}\n"], capture_output=True, text=True,
                              encoding="utf-8", check=True).stdout.splitlines()
    files = set()
    for package in [name, *sorted({line.split("\t")[0] for line in siblings if line.endswith(f"\t{source}")})]:
        listed = subprocess.run(["rpm", "-qL", package], capture_output=True, text=True, encoding="utf-8").stdout.split()
        files |= {Path(file) for file in listed if Path(file).is_file()}
        if files:
            break
    return Part(name, version, licence, sorted(files))


def _python_parts(collected: set[Path], system: str) -> list[Part]:
    """The Python runtime. On Windows python.org's LICENSE.txt covers it and the libraries in its build (OpenSSL,
    Tcl/Tk, ...). On Linux, python-build-standalone's licence texts (packaging/python/linux/) for the core and for each
    extension module whose code the program holds: those linked into libpython, and those PyInstaller collected."""
    version = platform.python_version()
    base = Path(sys.base_prefix)
    if system == "windows":
        return [Part("CPython", version, "PSF-2.0 and the licences in its LICENSE.txt", [base / "LICENSE.txt"])]
    pin = json.loads((PYTHON_LICENCES / "licences.json").read_text(encoding="utf-8"))
    build = (base / "BUILD").read_text(encoding="utf-8").strip() if (base / "BUILD").is_file() else "unknown"
    if (pin["python"], pin["build"]) != (version, build):
        raise SystemExit(f"this is Python {version}, python-build-standalone build {build}, but packaging/python/linux/ "
                         f"holds the licences of {pin['python']} build {pin['build']}: run packaging/notices.py "
                         "refresh-python on this build's full archive")
    dynload = base / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "lib-dynload"
    shipped = {name for name in pin["extensions"]
               if not list(dynload.glob(f"{name}.*")) or any(p.name.startswith(f"{name}.") for p in collected)}
    parts = [Part(f"CPython (python-build-standalone {build})", version, " AND ".join(pin["core"]["licences"]),
                  [PYTHON_LICENCES / file for file in pin["core"]["files"]])]
    for name in sorted(shipped):  # each with the libraries it links, as python-build-standalone gives them
        extension = pin["extensions"][name]
        parts.append(Part(f"{name} (CPython module)", "", " AND ".join(extension["licences"]) or "see its licence",
                          [PYTHON_LICENCES / file for file in extension["files"]]))
    return parts


def runtime_parts(entries: list[tuple[str, str | None, str]], system: str) -> list[Part]:
    """Whose each frozen file is. Stops on a file that is nobody's we know: the release could not name its licence."""
    sys.path.insert(0, str(ROOT / "src"))
    from wozmod.resources import PACKAGED  # the data the spec freezes in

    ours = [ROOT / "src", *(ROOT / name for name in PACKAGED)]
    base, owners = Path(sys.base_prefix).resolve(), _distribution_files()
    python, dists, system_files, unknown = set(), {}, {}, []
    for name, source, kind in entries:
        if kind in ("OPTION", "SYMLINK", "PYZ"):  # settings, links to files listed too, and the PYZ, listed module by module
            continue
        path = Path(source).resolve()
        if dist := owners.get(path):
            if dist.metadata["Name"].lower().replace("_", "-") not in OURS:
                dists[dist.metadata["Name"]] = dist
        elif kind in ("PYMODULE", "PYSOURCE") and name.split(".")[0] in sys.stdlib_module_names:
            python.add(path)  # compiled into PyInstaller's work folder: struct, which the bootloader needs first
        elif kind == "PYMODULE" and name.startswith("pyimod"):
            dists["pyinstaller"] = importlib.metadata.distribution("pyinstaller")
        elif kind == "EXECUTABLE":  # the bootloader
            dists["pyinstaller"] = importlib.metadata.distribution("pyinstaller")
        elif name == "base_library.zip" or _inside(path, base):  # the standard library, and the runtime's own files
            python.add(path)
        elif any(_inside(path, folder) for folder in ours):
            continue  # our own code and data
        elif system == "linux" and path.parts[1:2] in (("usr",), ("lib",), ("lib64",)):
            system_files.setdefault(path, None)
        else:
            unknown.append(f"{name} ({source})")
    if unknown:
        raise SystemExit("PyInstaller put files in the program that packaging/notices.py cannot name the licence of:\n  "
                         + "\n  ".join(unknown))
    parts = _python_parts({Path(name) for name, _, _ in entries}, system)
    parts += [Part(dist.metadata["Name"], dist.version, _distribution_licence(dist), distribution_licence_files(dist))
              for _, dist in sorted(dists.items(), key=lambda item: item[0].lower())]
    packages = {}
    for path in system_files:
        part = system_package(path)
        packages.setdefault(part.name, part)
    return parts + [packages[name] for name in sorted(packages)]


def runtime_notice(parts: list[Part], system: str) -> str:
    return render([
        "Third-party code in the wozmod program",
        "",
        "wozmod and wozmod-setup are wozmod's own code, under wozmod's licence (LICENSE), frozen by PyInstaller into a",
        f"program for {system} that also holds the Python runtime, packages and libraries below, each under the licence",
        "its authors give it (RUST-CRATES-NOTICE.txt lists the Rust crates in wozmod_native). Their licence texts follow",
        "the list, each different text once, with the parts it comes from.",
        "",
        "Made by packaging/notices.py from PyInstaller's record of what it put in the program.",
    ], parts)


def write_runtime(workpath: Path, system: str, out: Path) -> None:
    out.write_text(runtime_notice(runtime_parts(frozen(workpath), system), system), encoding="utf-8", newline="\n")


def refresh_python(archive: Path) -> None:
    """Take the Linux Python's licence texts from its python-build-standalone full archive
    (cpython-<version>+<build>-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst, from that build's GitHub release):
    the core's and each extension module's, with which modules need which (licences.json). The installed build,
    which uv makes from the same release, keeps only Python's own."""
    import tarfile  # Python 3.14 reads zstd

    found = re.fullmatch(r"cpython-([0-9.]+)\+([0-9]+)-x86_64-unknown-linux-gnu-pgo\+lto-full\.tar\.zst", archive.name)
    if not found:
        raise SystemExit(f"{archive.name} is not a python-build-standalone x86_64 Linux pgo+lto full archive")
    with tarfile.open(archive, "r:zst") as bundle:
        info = json.load(bundle.extractfile("python/PYTHON.json"))
        if info["python_version"] != found[1]:
            raise SystemExit(f"{archive.name} holds Python {info['python_version']}")
        extensions = {name: {"licences": variants[0].get("licenses") or [],
                             "files": sorted(f"licenses/{Path(path).name}" for path in variants[0].get("license_paths") or ())}
                      for name, variants in sorted(info["build_info"]["extensions"].items())
                      if variants[0].get("license_paths")}  # the rest are Python's own code
        core = {"licences": info["licenses"], "files": [f"licenses/{Path(info['license_path']).name}"]}
        shutil.rmtree(PYTHON_LICENCES / "licenses", ignore_errors=True)
        (PYTHON_LICENCES / "licenses").mkdir(parents=True)
        for name in sorted({file for entry in (core, *extensions.values()) for file in entry["files"]}):
            (PYTHON_LICENCES / name).write_bytes(bundle.extractfile(f"python/{name}").read())
    pin = {"python": found[1], "build": found[2], "core": core, "extensions": extensions}
    (PYTHON_LICENCES / "licences.json").write_text(json.dumps(pin, indent=1, sort_keys=True) + "\n", encoding="utf-8")

if __name__ == "__main__":
    if sys.argv[1:2] == ["crates"] and len(sys.argv) == 4 and sys.argv[2] in TARGETS:
        write_crates(sys.argv[2], Path(sys.argv[3]))
    elif sys.argv[1:2] == ["refresh-python"] and len(sys.argv) == 3:
        refresh_python(Path(sys.argv[2]))
    else:
        raise SystemExit(__doc__)
