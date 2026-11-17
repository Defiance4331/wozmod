"""The install step keys (wozmod.install.step_keys): a step's key changes with exactly what its output depends on.

The fast tests build a small repository in tmp_path. The slow ones (WOZMOD_STEP_KEYS=1, about 18 minutes) run the
three steps on the real inputs to settle what the keys assume: the worker count and every environment variable but
the WOZMOD_* ones leave the output alone, a step opens no bundled file and no disc member its key leaves out, and the
art step runs no font Python.
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

from wozmod.install import step_keys

STEPS = set(step_keys.STEPS)
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
    "src/wozmod/voices/build.py": 'TABLE = "data/text/table.json"\ndef run(nice):\n    from wozmod import deep, text\n',
    "src/wozmod/deep.py": 'import os\nKNOB = os.environ.get("WOZMOD_VOICE_KNOB")\n',
    "src/wozmod/art.py": 'from wozmod import shared, text\nMAPPING = "data/art/map.json"\nFACES = "data/art/faces"\n'
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
        (tmp_path / name).write_text(textwrap.dedent(text))
    return tmp_path


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


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
        "a translation's English": ({"text", "voices"}, lambda: table.write_text(_table([ROWS[0], {**ROWS[1], "en": "Other"}]))),
        "a translation's donor pointer": ({"text", "voices"},
                                          lambda: table.write_text(_table([{**ROWS[0], "ref": ["x"]}, ROWS[1]]))),
        "a caption file of the text": ({"text"}, lambda: (root / "data/text/movie_captions.json").write_text('{"a": 1}')),
        "an art recipe": ({"art"}, lambda: (root / "data/art/map.json").write_text('{"row": 1}')),
        "the faces, which only draw lettering.zip": (set(), lambda: (root / "data/art/faces/face.py").write_text("x = 1")),
        "the movie list": ({"text"}, lambda: (root / "data/movies.json").write_text("[1]")),
        "a define the steps read": (STEPS, lambda: (root / "runtime/shared.h").write_text("#define SITE 0x11\n")),
        "the runtime's own C (a DLL-only release)": (set(), lambda: (root / "runtime/english_runtime.c").write_text("")),
        "the DLL pin": (set(), lambda: _edit(install, '"dll pin"', '"new dll pin"')),
        "the version": (set(), lambda: _edit(root / "src/wozmod/version.py", "1", "2")),
        "a module imported inside a function": ({"voices"}, lambda: _edit(root / "src/wozmod/deep.py", "import os", "import os  # x")),
        "a package's __init__": ({"voices"}, lambda: _edit(root / "src/wozmod/voices/__init__.py", "run", "run  # x")),
        "the step function": ({"voices"}, lambda: _edit(install, "run(NICE)", "run(NICE + 1)")),
        "an installer value the step names": ({"voices"}, lambda: _edit(install, "NICE = 10", "NICE = 11")),
        "an environment variable the code names": ({"voices"}, lambda: monkeypatch.setenv("WOZMOD_VOICE_KNOB", "1")),
        "the worker count": (set(), lambda: monkeypatch.setenv("WOZMOD_JOBS", "3")),
        "another environment variable": (set(), lambda: monkeypatch.setenv("LANG", "ja_JP.SJIS")),
        "the toolchain": (STEPS, lambda: toolchain.update(ffmpeg="8.2")),
        "a module only the rest of the installer loads": (set(), lambda: _edit(root / "src/wozmod/lib.py", "pass", "return")),
        "installer code outside the steps": (set(), lambda: _edit(install, "helper()", "helper(); helper()")),
        "data the release does not bundle": (set(), lambda: (root / "data/checks/route.toml").write_text("x = 1")),
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


def test_a_step_function_that_takes_an_unkeyed_input_is_refused(tmp_path):
    """Bug: _build starts passing a step a new input; the key leaves it out and the old output is kept."""
    root = _repo(tmp_path)
    _edit(root / "src/wozmod/install/__init__.py", "def _text_table(game, donors, out)", "def _text_table(game, donors, table, out)")
    with pytest.raises(step_keys.StepKeyError, match="table"):
        step_keys.keys(root, {})


def test_the_shipped_keys_are_the_ones_the_release_source_gives_and_follow_the_players_pins(tmp_path, monkeypatch):
    """Bug: step_keys.json leaves out part of a key (a step's inputs or the variables its code names), so a release
    whose piece record or lettering.zip changed keeps the steps built from the old ones."""
    import wozmod.install as installer

    root = _repo(tmp_path)
    live = step_keys.keys(root, step_keys.release_toolchain(root, "linux"))
    (root / step_keys.SHIPPED).write_text(json.dumps(step_keys.shipped_document(root, "linux")))
    (root / "src").rename(root / "no-source")  # a release has no source tree
    assert step_keys.keys(root) == live
    monkeypatch.setattr(installer, "LETTERING", (1, "other bundle"))
    monkeypatch.setenv("WOZMOD_VOICE_KNOB", "1")
    _edit(root / "data/stock_pieces.json", '"aa"', '"ab"')
    changed = step_keys.keys(root)
    assert {step for step in STEPS if changed[step]["key"] != live[step]["key"]} == STEPS


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in compiler is a shell script")
def test_a_release_built_with_another_rust_compiler_keeps_no_step(tmp_path, monkeypatch):
    """Bug: the release's keys leave out the Rust compiler that built its codecs (native/), so a release built with
    another compiler keeps the text, voices and art that the earlier codecs wrote, which it cannot vouch for."""
    root, tools = _repo(tmp_path), tmp_path / "bin"
    tools.mkdir()
    compiler = tools / "rustc"
    monkeypatch.delenv("RUSTC", raising=False)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")

    def shipped(version: str) -> dict[str, str]:
        compiler.write_text(f"#!/bin/sh\necho 'rustc {version}'\n")
        compiler.chmod(0o755)
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

# Slow: the three steps on the real inputs (WOZMOD_STEP_KEYS=1).

REAL = pytest.mark.skipif(
    os.environ.get("WOZMOD_STEP_KEYS") != "1"
    or not all(os.environ.get(v) for v in ("WOZMOD_GAME", "WOZMOD_DONORS", "WOZMOD_LETTERING")),
    reason="set WOZMOD_STEP_KEYS=1, WOZMOD_GAME, WOZMOD_DONORS and WOZMOD_LETTERING (about 18 minutes)")
REPO_ROOT = Path(__file__).resolve().parents[1]
RUN = Path(__file__).with_name("step_run.py")
# The few-worker build's worker count per step: one, but two for the voices. The art has code only one worker runs
# (a player with one core to spare runs it), while the voices' workers are threads with no such code, and two are
# the fewest whose finishing order can differ from the order the work was handed out. One voice worker took 18 of
# the fixture's 21 minutes.
FEW = {"text": 1, "voices": 2, "art": 1}


def _start(step: str, out: Path, jobs: int, env: dict, cwd: Path) -> subprocess.Popen:
    """Start one step in a fresh Python, as the installer's worker runs it; what it prints goes to OUT/run.log."""
    out.mkdir(parents=True)
    with (out / "run.log").open("w") as log:
        return subprocess.Popen([sys.executable, str(RUN), step, str(out), str(jobs)], cwd=cwd, env=env,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)


@pytest.fixture(scope="module")
def two_builds(tmp_path_factory):
    """Each step built twice: once with few workers, the audit log on and this environment; once with the default
    workers in another locale, time zone, hash seed and working folder."""
    base = tmp_path_factory.mktemp("step-keys")
    plain = {k: v for k, v in os.environ.items()}
    other = {**plain, "LC_ALL": "C", "LANG": "C", "TZ": "Pacific/Kiritimati", "PYTHONHASHSEED": "4242",
             "PYTHONUTF8": "0", "TMPDIR": str(base)}
    other.pop("WOZMOD_JOBS", None)
    # All six builds at once: the few-worker ones hold a core or two for minutes, and one after another the six
    # took half an hour; the default-worker builds fill the other cores meanwhile.
    audited = {**plain, "WOZMOD_STEP_AUDIT": "1"}
    runs = {(step, side): _start(step, base / side / step, jobs, env, cwd)
            for step in step_keys.STEPS
            for side, jobs, env, cwd in (("few", FEW[step], audited, REPO_ROOT),
                                         ("many", 0, other, base))}
    failed = {f"{side}/{step}": (base / side / step / "run.log").read_text(errors="replace")[-4000:]
              for (step, side), run in runs.items() if run.wait() != 0}
    assert failed == {}
    return {step: (base / "few" / step, base / "many" / step) for step in step_keys.STEPS}


def _tree(folder: Path) -> dict[str, str]:
    out = folder / "out"
    return {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob("*")) if p.is_file()}


@REAL
@pytest.mark.slow_first
def test_a_step_writes_the_same_bytes_with_any_worker_count_and_environment(two_builds):
    """Bug: a step's output depends on the worker count (results joined in finishing order) or on something outside
    its key (the locale's code page, the time zone, set or dict order under another hash seed, the working folder),
    so two players with the same key hold different files, and a kept step is not what this release builds."""
    differ = {}
    for step, (few, many) in two_builds.items():
        first, second = _tree(few), _tree(many)
        assert first, f"{step} wrote nothing"
        if first != second:
            differ[step] = sorted(name for name in first.keys() | second.keys() if first.get(name) != second.get(name))[:6]
    assert differ == {}


def _module(path: Path) -> str:
    """wozmod/a/b.py or wozmod/a/__pycache__/b.cpython-314.pyc -> wozmod.a.b (a package's __init__ -> the package)."""
    parts = [p for p in path.parts[:-1] if p != "__pycache__"] + [path.name.split(".")[0]]
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _opens(folder: Path) -> list[tuple[str, str]]:
    return [tuple(line.split("\t", 1)) for line in (folder / "audit.log").read_text(encoding="utf-8").splitlines()]


@REAL
@pytest.mark.slow_first
def test_a_step_opens_no_module_bundled_file_or_disc_member_its_key_leaves_out(two_builds):
    """Bug: a step loads a module its key leaves out (an import by name the source does not show), reads a bundled
    file its key leaves out (a NOT_READ folder it does read, or a projected file read past its reader), or a
    disc member disc_reads does not list, so a change to it keeps a stale step (and the disc read for an update
    leaves the member out)."""
    from wozmod import resources
    from wozmod.donors import DISCS

    root = resources.root()
    donors = Path(os.environ["WOZMOD_DONORS"]).resolve()
    unkeyed = {}
    for step, (few, _) in two_builds.items():
        reads = step_keys.bundled_reads(step, root)
        keyed = set(reads["keyed"]) | set(reads["projected"])  # a projected file: its reader is checked below
        members = {f"{disc}/{name}" for disc, names in step_keys.disc_reads(step).items() for name in names}
        if step in step_keys.PROGRAMS:
            members |= {f"{disc}/{each.program.staged}" for disc, each in DISCS.items()}
        modules = set(reads["modules"])
        found = set()
        for kind, path in _opens(few):
            path = Path(path)
            if kind == "open" and path.is_relative_to(root / "src/wozmod") and path.suffix in (".py", ".pyc"):
                if (module := _module(path.relative_to(root / "src"))) not in modules:
                    found.add(f"module {module}")
            elif kind == "open" and path.is_relative_to(root) and path.relative_to(root).parts[0] in ("data", "runtime"):
                name = path.relative_to(root).as_posix()
                if name not in keyed:
                    found.add(name)
            elif kind == "open" and path.is_relative_to(donors) and path.is_file():
                name = path.relative_to(donors).as_posix()
                if name not in members:
                    found.add(f"donors/{name}")
            elif kind == "projected-elsewhere":
                found.add(f"{path} read outside its projection")
        if found:
            unkeyed[step] = sorted(found)
    assert unkeyed == {}


@REAL
@pytest.mark.slow_first
def test_the_install_steps_start_no_program_but_ffmpeg(two_builds):
    """Bug: the art step falls back to the font Python (FontForge through the system Python) on a machine that has
    one, so its output there depends on a program no key covers, and players without it get other bytes."""
    started = {}
    for step, (few, _) in two_builds.items():
        programs = sorted({Path(path).name for kind, path in _opens(few) if kind == "start"} - {"ffmpeg", "ffmpeg.exe"})
        if programs:
            started[step] = programs
    assert started == {}
