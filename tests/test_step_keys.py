"""The install step keys (wozmod.install.step_keys): a step's key changes with exactly what its output depends on.

The fast tests build a small repository in tmp_path. The slow one (WOZMOD_STEP_KEYS=1, about 20 minutes) builds the
three steps on the real inputs again with other worker counts and in another environment, to settle what the keys
assume: the worker count and every environment variable but the WOZMOD_* ones leave the output alone. What a step
reads (bundled files, disc members, modules, programs) the read gate holds to the key's manifest in every build from
source (install/gate.py, tests/test_gate.py).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import fake_program
from wozmod.install import manifest, step_keys, steps

STEPS = set(steps.STEPS)
ROWS = [{"key": "a1", "ref": ["wo1-x360", "ens", 3, "dialog", 1], "en": "Donor wording"},
        {"key": "b2", "en": "Our wording"}]
REPO = {
    "src/wozmod/__init__.py": '"""The package."""\n',
    "src/wozmod/version.py": '__version__ = "1"\n',
    "src/wozmod/resources.py": 'PACKAGED = ("data/art", "data/text", "data/movies.json", "data/stock_pieces.json", "runtime")\n',
    "src/wozmod/install/__init__.py": """
        from wozmod.lib import helper
        from wozmod.version import __version__ as VERSION

        DLL = (2, "dll pin")
        NICE = 10

        def _text_table(game, donors, out):
            from wozmod.text import build
            build(out)

        def _voice_banks(game, donors, speech, out, jobs):
            from wozmod.voices import run
            run(NICE)

        def _menu_art(game, donors, lettering, staging, out, jobs):
            from wozmod.art import compose
            compose()

        def install():
            helper()
            return VERSION, DLL
        """,
    "src/wozmod/text.py": 'from wozmod import shared\nTEXT = "data/text"\nMOVIES = "data/movies.json"\ndef build(out): pass\n',
    "src/wozmod/shared.py": 'SHARED_H = "runtime/shared.h"\n',
    "src/wozmod/voices/__init__.py": "from .build import run\n",
    "src/wozmod/voices/build.py": 'TABLE = "data/text/table.json"\ndef run(nice):\n    from wozmod import deep\n',
    "src/wozmod/deep.py": 'import os\nKNOB = os.environ.get("WOZMOD_VOICE_KNOB")\n',
    "src/wozmod/art.py": 'from wozmod import shared\nMAPPING = "data/art/map.json"\nFACES = "data/art/faces"\n'
                         'def compose(): pass\n',
    "src/wozmod/lib.py": "def helper(): pass\n",
    "data/stock_pieces.json": json.dumps({"format": 1, "stamp": [1, 2], "pieces": {
        "OROCHI_Z.exe": [[512, 16, "aa", "text"]], "LINKDATA1": [[4079, "bb", None, "voices art", 1]],
        "LINKDATA2": [], "data/Sound/Voice/Voice.bin": [[7, "cc", "", 1]]}}),
    "data/text/table.json": json.dumps({"rows": ROWS}),
    "data/text/movie_captions.json": "{}",
    "data/art/map.json": "{}",
    "data/art/faces/face.py": "",
    "data/movies.json": "[]",
    "data/checks/route.toml": "",
    "runtime/shared.h": "#define SITE 0x10\n",
    "runtime/english_runtime.c": "int x;",
    "uv.lock": "lock",
    "native/src/lib.rs": "fn main() {}",
    "packaging/ffmpeg.json": json.dumps({"linux": {"sha256": "ff"}, "windows": {"sha256": "fw"}}),
}


def _repo(tmp_path: Path) -> Path:
    for name, text in REPO.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(textwrap.dedent(text), encoding="utf-8")
    return tmp_path


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def _repin_member(monkeypatch, disc: str, member: str) -> None:
    from wozmod.donors.inventory import DONORS

    donor = DONORS[disc]
    files = tuple(dataclasses.replace(f, sha256="0" * 64) if f.path == member else f for f in donor.files)
    assert files != donor.files, member
    monkeypatch.setitem(DONORS, disc, dataclasses.replace(donor, files=files))


def _repin_program(monkeypatch, disc: str) -> None:
    from wozmod.donors import DISCS

    each = DISCS[disc]
    plain = dataclasses.replace(each.program.plain, sha256="0" * 64)
    monkeypatch.setitem(DISCS, disc, dataclasses.replace(each, program=dataclasses.replace(each.program, plain=plain)))


def _table(rows: list[dict]) -> str:
    return json.dumps({"rows": rows})


def test_each_step_key_changes_with_exactly_the_inputs_its_output_depends_on(tmp_path, monkeypatch):
    """Bug: an input drops out of a step's key, so a stale step stays installed (a donor pin or the speech model
    changes and the old voices are kept); or one leaks into the wrong key, so a caption edit or a DLL-only release
    rebuilds the voices for minutes."""
    import wozmod.install as installer
    from wozmod.voices import phones

    root = _repo(tmp_path)
    install, pieces, table = root / "src/wozmod/install/__init__.py", root / "data/stock_pieces.json", root / "data/text/table.json"
    toolchain = {"python": "3.14.0", "ffmpeg": "8.1"}
    edits = {
        "a stock piece only the text reads": ({"text"}, lambda: _edit(pieces, '"aa"', '"ab"')),
        "a stock entry the voices and the art read": ({"voices", "art"}, lambda: _edit(pieces, '"bb"', '"bc"')),
        "a clip wozmod only serves": (set(), lambda: _edit(pieces, '"cc"', '"cd"')),
        "a disc member only the voices read": ({"voices"}, lambda: _repin_member(monkeypatch, "wo2-x360", "link_vodat.bdx")),
        "a disc member all three read": (STEPS, lambda: _repin_member(monkeypatch, "wo2-x360", "linkdata_ens.idx")),
        "a dub schedule only the text reads": ({"text"}, lambda: _repin_member(monkeypatch, "wo1-x360", "linkdata_fns/369.bin")),
        "a decrypted program": ({"text", "art"}, lambda: _repin_program(monkeypatch, "wo2-psp")),
        "the lettering bundle": ({"art"}, lambda: monkeypatch.setattr(installer, "LETTERING", (1, "other bundle"))),
        "the speech model": ({"voices"}, lambda: monkeypatch.setattr(phones, "MODEL", {"en-us/mdef": (1, "other")})),
        # The voices read the translation for the words a Dream scene line's mouth says (voices.phones.shown_lines).
        "a translation's English": ({"text", "voices"}, lambda: table.write_text(_table([ROWS[0], {**ROWS[1], "en": "Other"}]), encoding="utf-8")),
        "a translation's donor pointer": ({"text", "voices"},
                                          lambda: table.write_text(_table([{**ROWS[0], "ref": ["x"]}, ROWS[1]]), encoding="utf-8")),
        "a caption file of the text": ({"text"}, lambda: (root / "data/text/movie_captions.json").write_text('{"a": 1}', encoding="utf-8")),
        "an art recipe": ({"art"}, lambda: (root / "data/art/map.json").write_text('{"row": 1}', encoding="utf-8")),
        "the faces, which only draw lettering.zip": (set(), lambda: (root / "data/art/faces/face.py").write_text("x = 1", encoding="utf-8")),
        "the movie list": ({"text"}, lambda: (root / "data/movies.json").write_text("[1]", encoding="utf-8")),
        "a define the text and the art read": ({"text", "art"}, lambda: (root / "runtime/shared.h").write_text("#define SITE 0x11\n", encoding="utf-8")),
        "the runtime's own C (a DLL-only release)": (set(), lambda: (root / "runtime/english_runtime.c").write_text("", encoding="utf-8")),
        "the DLL pin": (set(), lambda: _edit(install, '"dll pin"', '"new dll pin"')),
        "the version": (set(), lambda: _edit(root / "src/wozmod/version.py", "1", "2")),
        "a module imported inside a function": ({"voices"}, lambda: _edit(root / "src/wozmod/deep.py", "import os", "import os  # x")),
        "a package's __init__": ({"voices"}, lambda: _edit(root / "src/wozmod/voices/__init__.py", "run", "run  # x")),
        "the step function": ({"voices"}, lambda: _edit(install, "run(NICE)", "run(NICE + 1)")),
        "an installer value the step names": ({"voices"}, lambda: _edit(install, "NICE = 10", "NICE = 11")),
        "a setting only the art reads": ({"art"}, lambda: monkeypatch.setenv("WOZMOD_ART_TRACE", "1")),
        # Named in the code but declared by no step: a step that reads it is stopped by the read gate.
        "a setting no step declares": (set(), lambda: monkeypatch.setenv("WOZMOD_VOICE_KNOB", "1")),
        "the worker count": (set(), lambda: monkeypatch.setenv("WOZMOD_JOBS", "3")),
        "another environment variable": (set(), lambda: monkeypatch.setenv("LANG", "ja_JP.SJIS")),
        "the toolchain": (STEPS, lambda: toolchain.update(ffmpeg="8.2")),
        "a module only the rest of the installer loads": (set(), lambda: _edit(root / "src/wozmod/lib.py", "pass", "return")),
        "installer code outside the steps": (set(), lambda: _edit(install, "helper()", "helper(); helper()")),
        "data the release does not bundle": (set(), lambda: (root / "data/checks/route.toml").write_text("x = 1", encoding="utf-8")),
    }

    def keys():
        return {step: found["key"] for step, found in step_keys.keys(root, dict(toolchain)).items()}

    wrong = []
    for name, (expected, edit) in edits.items():
        before = keys()
        edit()
        after = keys()
        changed = {step for step in STEPS if after[step] != before[step]}
        if changed != expected:
            wrong.append(f"{name}: changed {sorted(changed)}, expected {sorted(expected)}")
    assert wrong == []


def test_every_part_of_a_manifest_is_in_its_steps_key(tmp_path, monkeypatch):
    """Bug: the key leaves out a part of the manifest the read gate admits (the programs a step starts, a setting, a
    disc's program, the record's stamp), so the gate lets a step read what its key does not cover and a stale step
    stays installed. A new Manifest field fails here until it has a change below and the key hashes it."""
    from wozmod.donors import DISCS

    root = _repo(tmp_path)
    found = manifest.manifests(root)["voices"]
    changes = {
        "step": None,  # the step itself: its key is its own
        "inputs": found.inputs[:-1],
        "installer": {**found.installer, "_other": "0" * 64},
        "modules": (*found.modules, "wozmod.other"),
        "bundled": (*found.bundled, "data/text/other.json"),
        "donors": {disc: names[:-1] for disc, names in found.donors.items()},
        "programs": tuple(DISCS) if not found.programs else (),
        "stock_stamp": (0, 0),
        "stock": found.stock[:-1],
        "environment": (*found.environment, "WOZMOD_OTHER"),
        "starts": (*found.starts, "other"),
    }

    def key(each: manifest.Manifest) -> str:
        monkeypatch.setattr(manifest, "manifests", lambda root=None: {"voices": each})
        return step_keys.keys(root, {})["voices"]["key"]

    before = key(found)
    unchanged = [f.name for f in dataclasses.fields(found) if changes[f.name] is not None
                 and key(dataclasses.replace(found, **{f.name: changes[f.name]})) == before]
    assert (unchanged, before == key(dataclasses.replace(found))) == ([], True)


def test_a_module_edited_between_two_manifests_is_read_again(tmp_path):
    """Bug: the parsed-module cache is keyed by the module's name or path rather than its bytes, so after an edit
    that adds an import the manifest keeps the old closure, and the gate refuses the step's new module (or the key
    misses it) until the process restarts."""
    root = _repo(tmp_path)
    before = {step: "wozmod.lib" in found.modules for step, found in manifest.manifests(root).items()}
    _edit(root / "src/wozmod/shared.py", 'SHARED_H', 'from wozmod import lib\nSHARED_H')
    after = {step: "wozmod.lib" in found.modules for step, found in manifest.manifests(root).items()}
    assert (before, after) == ({"text": False, "voices": False, "art": False}, {"text": True, "voices": False, "art": True})


def test_a_step_function_that_takes_an_unkeyed_input_is_refused(tmp_path):
    """Bug: _build starts passing a step a new input; the key leaves it out and the old output is kept."""
    root = _repo(tmp_path)
    _edit(root / "src/wozmod/install/__init__.py", "def _text_table(game, donors, out)", "def _text_table(game, donors, table, out)")
    with pytest.raises(step_keys.StepKeyError, match="table"):
        step_keys.keys(root, {})


def test_the_shipped_keys_are_the_ones_the_release_source_gives_and_follow_the_players_pins(tmp_path, monkeypatch):
    """Bug: step_keys.json leaves out part of a key or of a manifest (a step's inputs, modules or bundled files), so a
    release whose piece record or lettering.zip changed keeps the steps built from the old ones, or its read gate holds
    a step to another manifest than its key hashes."""
    import wozmod.install as installer

    root = _repo(tmp_path)
    live = step_keys.keys(root, step_keys.release_toolchain(root, "linux"))
    live_manifests = manifest.manifests(root)
    (root / manifest.SHIPPED).write_text(json.dumps(step_keys.shipped_document(root, "linux")), encoding="utf-8")
    (root / "src").rename(root / "no-source")  # a release has no source tree
    assert step_keys.keys(root) == live
    assert manifest.manifests(root) == live_manifests
    monkeypatch.setattr(installer, "LETTERING", (1, "other bundle"))
    monkeypatch.setenv("WOZMOD_NATIVE", "0")
    _edit(root / "data/stock_pieces.json", '"aa"', '"ab"')
    changed = step_keys.keys(root)
    assert {step for step in STEPS if changed[step]["key"] != live[step]["key"]} == STEPS


def test_a_release_built_with_another_rust_compiler_keeps_no_step(tmp_path, monkeypatch):
    """Bug: the release's keys leave out the Rust compiler that built its codecs (native/), so a release built with
    another compiler keeps the text, voices and art that the earlier codecs wrote, which it cannot vouch for."""
    root, tools = _repo(tmp_path), tmp_path / "bin"
    tools.mkdir()
    compiler = tools / "rustc"
    monkeypatch.delenv("RUSTC", raising=False)
    monkeypatch.delenv("WOZMOD_RUSTC_VERSION", raising=False)  # a cross build's declared compiler wins over rustc
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")

    def shipped(version: str) -> dict[str, str]:
        fake_program.program(compiler, f"print('rustc {version}')\n")
        return {step: part["mod"] for step, part in step_keys.shipped_document(root, "linux")["steps"].items()}

    first, again, other = shipped("1.99.0 (b940084d7 2026-09-28)"), shipped("1.99.0 (b940084d7 2026-09-28)"), \
        shipped("1.100.0 (0a1b2c3d4 2026-11-09)")
    assert first == again
    assert {step for step in STEPS if other[step] != first[step]} == STEPS



def test_a_cross_build_names_the_compiler_it_declares_without_running_one(tmp_path, monkeypatch):
    """Bug: the Windows zip, packaged under Wine where the compiler that cross-built its codecs cannot run, fails to
    package, or keys its steps by some other compiler than the one it declares."""
    root = _repo(tmp_path)
    monkeypatch.setenv("RUSTC", str(tmp_path / "no-such-rustc"))

    def shipped(version: str) -> dict[str, str]:
        monkeypatch.setenv("WOZMOD_RUSTC_VERSION", version)
        return {step: part["mod"] for step, part in step_keys.shipped_document(root, "windows")["steps"].items()}

    first, other = shipped("rustc 1.99.0 (b940084d7 2026-09-28)"), shipped("rustc 1.100.0 (0a1b2c3d4 2026-11-09)")
    assert {step for step in STEPS if other[step] != first[step]} == STEPS


def test_the_text_and_art_keys_hold_no_voices_module():
    """Bug: a text or art module imports from wozmod.voices (at its top or inside a function), so every voice-only
    update changes the text and art keys and every player rebuilds those steps with it."""
    found = manifest.manifests()
    voices = {step: [m for m in found[step].modules if m.split(".")[:2] == ["wozmod", "voices"]] for step in ("text", "art")}
    assert voices == {"text": [], "art": []}


# Slow: each step built once more on the real inputs, with other worker counts and in another environment, against
# its reference build (WOZMOD_STEP_KEYS=1).

REAL = pytest.mark.skipif(
    os.environ.get("WOZMOD_STEP_KEYS") != "1"
    or not all(os.environ.get(v) for v in ("WOZMOD_GAME", "WOZMOD_DONORS", "WOZMOD_LETTERING", "WOZMOD_SPEECH")),
    reason="set WOZMOD_STEP_KEYS=1, WOZMOD_GAME, WOZMOD_DONORS, WOZMOD_LETTERING and WOZMOD_SPEECH (about 20 minutes)")
RUN = Path(__file__).with_name("step_run.py")
# The other build's worker count per step: one, but two for the voices (one voice worker took 18 of the fixture's
# 21 minutes; two are the fewest whose finishing order can differ from the order the work was handed out). The
# reference build runs the installer's default count. ordered_map joins in hand-out order (tests/test_workers.py);
# this catches what it cannot: a worker that changes what the others read, or the native module's own threads.
FEW = {"text": 1, "voices": 2, "art": 1}


@pytest.fixture(scope="module")
def other_builds(tmp_path_factory, built):
    """(each step's reference build, the same step built in a fresh Python with FEW workers in another locale, time
    zone, hash seed and working folder). A frozen release ignores PYTHONHASHSEED (PyInstaller starts Python isolated),
    so packaging/wozmod.spec pins its seed as a run-time option."""
    base = tmp_path_factory.mktemp("step-keys")
    other = {**os.environ, "LC_ALL": "C", "LANG": "C", "TZ": "Pacific/Kiritimati", "PYTHONHASHSEED": "4242",
             "PYTHONUTF8": "0", "TMPDIR": str(base)}
    other.pop("WOZMOD_JOBS", None)
    runs = {}
    for step in steps.STEPS:
        (base / step).mkdir()
        with (base / step / "run.log").open("w", encoding="utf-8") as log:
            runs[step] = subprocess.Popen([sys.executable, str(RUN), step, str(base / step), str(FEW[step])], cwd=base,
                                          env=other, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    references = {step: built(step, tmp_path_factory.mktemp(f"reference-{step}")) for step in steps.STEPS}
    failed = {step: (base / step / "run.log").read_text(errors="replace", encoding="utf-8")[-4000:]
              for step, run in runs.items() if run.wait() != 0}
    assert failed == {}
    return {step: (references[step], base / step) for step in steps.STEPS}


def _tree(folder: Path) -> dict[str, str]:
    out = folder / "out"
    return {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob("*")) if p.is_file()}


@REAL
@pytest.mark.slow_first
def test_a_step_writes_the_same_bytes_with_any_worker_count_and_environment(other_builds):
    """Bug: a step's output depends on the worker count (results joined in finishing order) or on something outside
    its key (the locale's code page, the time zone, set or dict order under another hash seed, the working folder),
    so two players with the same key hold different files, and a kept step is not what this release builds."""
    differ = {}
    for step, (reference, other) in other_builds.items():
        first, second = _tree(reference), _tree(other)
        assert first, f"{step} wrote nothing"
        if first != second:
            differ[step] = sorted(name for name in first.keys() | second.keys() if first.get(name) != second.get(name))[:6]
    assert differ == {}
