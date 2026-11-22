"""packaging/notices.py: the licence notices for the third-party code frozen into a release."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("notices", REPO / "packaging/notices.py")
notices = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = notices  # its dataclass looks itself up there
spec.loader.exec_module(notices)

def _metadata(tmp_path: Path, crates: dict[str, dict[str, str]]) -> dict:
    """cargo metadata as `cargo metadata --format-version 1` writes it, for a root crate and crates ("name version")
    whose source folders (under tmp_path) hold the given files."""
    packages = []
    for crate, files in {"root 0.1.0": {}, **crates}.items():
        name, version = crate.split()
        folder = tmp_path / f"{name}-{version}"
        folder.mkdir()
        (folder / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        for file, text in files.items():
            (folder / file).write_text(text, encoding="utf-8")
        packages.append({"id": f"{name}@{version}", "name": name, "version": version, "license": "MIT",
                         "license_file": None, "manifest_path": str(folder / "Cargo.toml")})
    return {"packages": packages, "resolve": {"root": "root@0.1.0", "nodes": []}}


def _listed(text: str) -> set[str]:
    """The parts a notice lists, as "name version": the rows between the intro and the first text."""
    rows = text.split(notices.RULE)[0].strip().split("\n\n")[-1]
    return {" ".join(line.split()[:2]) for line in rows.splitlines()}


def test_every_compiled_crate_and_its_licence_texts_are_in_the_notice(tmp_path):
    """Each crate cargo tree says the module compiles, both versions of a crate compiled twice among them, is listed
    with every licence text it carries: a new dependency cannot ship without its licence. A package cargo metadata
    names but the module does not compile (an optional dependency) is not listed."""
    crates = {"direct 1.0.0": {"LICENSE-MIT": "MIT text, direct's holder\n", "LICENSE-APACHE": "Apache text\n"},
              "twice 0.8.9": {"LICENSE": "twice's old terms\n"}, "twice 0.9.1": {"LICENSE": "twice's new terms\n"},
              "macros 1.0.0": {"LICENSE-APACHE": "Apache text\n", "NOTICE.md": "macros' notice\n"},
              "optional 1.0.0": {"LICENSE": "optional's text\n"}}
    tree = ("root v0.1.0 (/src/native)\ndirect v1.0.0\ntwice v0.8.9\nmacros v1.0.0 (proc-macro)\ntwice v0.9.1\n"
            "direct v1.0.0 (*)\n")
    text = notices.crates_notice(tree, _metadata(tmp_path, crates), "linux", "0" * 64)
    compiled = {"direct 1.0.0", "twice 0.8.9", "twice 0.9.1", "macros 1.0.0"}
    assert _listed(text) == compiled
    for crate in compiled:
        for licence in crates[crate].values():
            assert text.count(licence) == 1, (crate, licence)
    assert "optional's text" not in text


def test_a_part_without_a_licence_text_stops_the_build(tmp_path):
    meta = _metadata(tmp_path, {"bare 1.0.0": {"README.md": "no licence here\n"}})
    with pytest.raises(SystemExit, match="bare 1.0.0"):
        notices.crates_notice("bare v1.0.0\n", meta, "linux", "0" * 64)


def _workpath(tmp_path: Path, collect: list, pkg: list, pyz: list) -> Path:
    """PyInstaller's work folder with its COLLECT, PKG and PYZ tables, written as PyInstaller writes them."""
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "COLLECT-00.toc").write_text(repr((collect,)), encoding="utf-8")
    (folder / "PKG-00.toc").write_text(repr((str(folder / "wozmod.pkg"), pkg)), encoding="utf-8")
    (folder / "PYZ-00.toc").write_text(repr((str(folder / "PYZ-00.pyz"), pyz)), encoding="utf-8")
    return folder


def _module(distribution: str) -> tuple[str, str, str]:
    dist = importlib.metadata.distribution(distribution)
    source = next(file for file in dist.files if file.suffix == ".py")
    return (".".join(source.with_suffix("").parts), str(dist.locate_file(source)), "PYMODULE")


def test_every_frozen_package_is_in_the_notice_and_our_code_is_not(tmp_path, monkeypatch):
    """A Python package PyInstaller freezes in, from any of its tables, is listed with its wheel's licence: a new
    dependency cannot ship without its licence. Our own code is not listed."""
    monkeypatch.setattr(notices, "_python_parts", lambda collected, system: [])
    ours = REPO / "src/wozmod/__main__.py"
    workpath = _workpath(tmp_path, collect=[("data/text/x", str(REPO / "data/text/dub_captions.json"), "DATA")],
                         pkg=[("__main__", str(ours), "PYSOURCE"), _module("pluggy")],
                         pyz=[_module("pytest"), ("wozmod.cli", str(ours), "PYMODULE")])
    parts = notices.runtime_parts(notices.frozen(workpath), "linux")
    assert {part.name.lower() for part in parts} == {"pytest", "pluggy"}
    text = notices.runtime_notice(parts, "linux")
    for part in parts:
        assert part.files and all(notices._text(path) in text for path in part.files), part.name


def test_a_frozen_file_nobody_owns_stops_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(notices, "_python_parts", lambda collected, system: [])
    stray = tmp_path / "libstray.so"
    stray.write_bytes(b"\x7fELF")
    workpath = _workpath(tmp_path, collect=[("libstray.so", str(stray), "BINARY")], pkg=[], pyz=[])
    with pytest.raises(SystemExit, match="libstray.so"):
        notices.runtime_parts(notices.frozen(workpath), "linux")


def test_another_linux_python_build_stops_the_build(tmp_path, monkeypatch):
    """packaging/python/linux/ holds one python-build-standalone build's licence texts: a release on another build,
    which may link other libraries, must not ship them."""
    (tmp_path / "licences.json").write_text(json.dumps({"python": "3.0.0", "build": "19990101", "core": {},
                                                        "extensions": {}}), encoding="utf-8")
    monkeypatch.setattr(notices, "PYTHON_LICENCES", tmp_path)
    with pytest.raises(SystemExit, match="refresh-python"):
        notices._python_parts(set(), "linux")


def test_the_crates_std_links_are_found_through_its_dependencies_only(tmp_path):
    """The crates.io crates in the Rust standard library are those std reaches in rust-src's lock file, however deep:
    one only libtest uses is not in the module, and one std reaches through another must not be dropped."""
    lock = tmp_path / "lib/rustlib/src/rust/library/Cargo.lock"
    lock.parent.mkdir(parents=True)
    registry = 'source = "registry+https://github.com/rust-lang/crates.io-index"'
    lock.write_text(f"""
[[package]]
name = "std"
version = "0.0.0"
dependencies = ["alloc", "hashbrown"]
[[package]]
name = "alloc"
version = "0.0.0"
dependencies = ["compiler_builtins"]
[[package]]
name = "compiler_builtins"
version = "0.1.0"
dependencies = ["libm 0.2.1"]
[[package]]
name = "hashbrown"
version = "0.17.1"
{registry}
[[package]]
name = "libm"
version = "0.2.1"
{registry}
[[package]]
name = "test"
version = "0.0.0"
dependencies = ["std", "getopts"]
[[package]]
name = "getopts"
version = "0.2.24"
{registry}
""", encoding="utf-8")
    assert notices._std_vendored(tmp_path) == [("hashbrown", "0.17.1"), ("libm", "0.2.1")]
