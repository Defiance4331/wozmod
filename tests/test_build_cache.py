"""The developer build cache (tools/build_cache.py): a step is reused only when nothing its output depends on changed."""

from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path

import pytest

import build_cache
from wozmod import install as installer
from wozmod import movies, remembered
from wozmod.donors import Donor, DonorFile, DonorsFolder
from wozmod.progress import Progress

STEPS = set(build_cache.STEPS)
REPO = {
    "src/wozmod/__init__.py": '__version__ = "1"\n',
    "src/wozmod/resources.py": 'PACKAGED = ("data/art", "data/text", "runtime")\n',
    "src/wozmod/install/__init__.py": """
        from wozmod.lib import helper

        TEXT_DATA = "data/text/table.json"
        NICE = 10

        def _text_table(game, donors, out):
            from wozmod.text import build
            build(TEXT_DATA, out)

        def _voice_banks(game, donors, out, jobs):
            from wozmod.voices import run
            run(NICE)

        def _menu_art(game, donors, lettering, staging, out, jobs):
            from wozmod.art import compose
            compose()

        def install():
            helper()
        """,
    "src/wozmod/text.py": "def build(table, out): pass\n",
    "src/wozmod/voices/__init__.py": "from .build import run\n",
    "src/wozmod/voices/build.py": "def run(nice):\n    from wozmod import deep\n",
    "src/wozmod/deep.py": 'import os\nSOURCE = "runtime"\nKNOB = os.environ.get("WOZMOD_VOICE_KNOB")\n',
    "src/wozmod/art.py": 'MAPPING = "data/art/map.json"\ndef compose(): pass\n',
    "src/wozmod/lib.py": "def helper(): pass\n",
    "data/stock_pieces.json": json.dumps({"format": 1, "stamp": [1, 2], "pieces": {
        "OROCHI_Z.exe": [[512, 16, "aa", "text"]], "LINKDATA1": [[4079, "bb", None, "voices art", 1]],
        "LINKDATA2": [], "data/Sound/Voice/Voice.bin": [[7, "cc", "", 1]]}}),
    "data/text/table.json": "{}",
    "data/art/map.json": "{}",
    "data/checks/route.toml": "",
    "runtime/english_runtime.c": "int x;",
    "donors/wo2-x360/link_vodat.bdx": "voices",
    "donors/movie/voice/movie001.wmv": "movie",
    "lettering.zip": "bundle",
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


def test_each_step_key_changes_with_exactly_the_inputs_its_output_depends_on(tmp_path, monkeypatch):
    """Bug: an input drops out of a step's key, so a stale step ships (a donor file changes and the old voices
    are reused); or one leaks into the wrong key, so a translation edit rebuilds the voices for minutes."""
    root = _repo(tmp_path)
    install, pieces = root / "src/wozmod/install/__init__.py", root / "data/stock_pieces.json"
    sources = {"game": root / "game", "donors": root / "donors", "lettering": root / "lettering.zip"}
    here = {"python": "3.14.0", "ffmpeg": "7.1"}
    edits = {
        "a donor file the steps read": ({"text", "voices", "art"},
                                        lambda: (root / "donors/wo2-x360/link_vodat.bdx").write_text("other voices")),
        "a stock piece only the text reads": ({"text"}, lambda: _edit(pieces, '"aa"', '"ab"')),
        "a stock entry the voices and the art read": ({"voices", "art"}, lambda: _edit(pieces, '"bb"', '"bc"')),
        "a clip wozmod only serves": (set(), lambda: _edit(pieces, '"cc"', '"cd"')),
        "the lettering bundle": ({"art"}, lambda: (root / "lettering.zip").write_text("other bundle")),
        "a translation row": ({"text"}, lambda: (root / "data/text/table.json").write_text('{"row": 1}')),
        "an art recipe": ({"art"}, lambda: (root / "data/art/map.json").write_text('{"row": 1}')),
        "the runtime C named by a module the voices load": ({"voices"},
                                                           lambda: (root / "runtime/english_runtime.c").write_text("")),
        "a module imported inside a function": ({"voices"}, lambda: _edit(root / "src/wozmod/deep.py", "import os", "import os  # x")),
        "a package's __init__": ({"voices"}, lambda: _edit(root / "src/wozmod/voices/__init__.py", "run", "run  # x")),
        "the step function": ({"voices"}, lambda: _edit(install, "run(NICE)", "run(NICE + 1)")),
        "an installer value the step names": ({"voices"}, lambda: _edit(install, "NICE = 10", "NICE = 11")),
        "the package version every module sits under": (STEPS, lambda: _edit(root / "src/wozmod/__init__.py", "1", "2")),
        "an environment variable the code names": ({"voices"}, lambda: monkeypatch.setenv("WOZMOD_VOICE_KNOB", "1")),
        "the ffmpeg": (STEPS, lambda: here.update(ffmpeg="7.2")),
        "a module only the rest of the installer loads": (set(), lambda: _edit(root / "src/wozmod/lib.py", "pass", "return")),
        "installer code outside the steps": (set(), lambda: _edit(install, "helper()", "helper(); helper()")),
        "data the release does not bundle": (set(), lambda: (root / "data/checks/route.toml").write_text("x = 1")),
        "a movie put beside the donors": (set(), lambda: (root / "donors/movie/voice/movie001.wmv").write_text("m")),
        "the worker count": (set(), lambda: monkeypatch.setenv("WOZMOD_JOBS", "3")),
    }

    def keys():
        return {step: build_cache.key(build_cache.step_inputs(step, root, sources, dict(here))) for step in STEPS}

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
    """Bug: _build starts passing a step a new input; the key leaves it out and the old output is reused."""
    root = _repo(tmp_path)
    _edit(root / "src/wozmod/install/__init__.py", "def _text_table(game, donors, out)", "def _text_table(game, donors, table, out)")
    with pytest.raises(build_cache.CacheError, match="table"):
        build_cache.step_inputs("text", root, {"game": root / "game", "donors": root / "donors"}, {})


# Through the installer's real _build: fake disc reads and steps that record each run in the game folder.

MOVIE = b"movie bytes"


def _fake_text(game, donors, out):
    _ran(game, "text")
    (out / "wozmod").mkdir(parents=True, exist_ok=True)
    (out / "wozmod/table.bin").write_bytes(b"table of " + DonorsFolder(donors).folder("wo2-x360").joinpath("link_vodat.bdx").read_bytes())


def _fake_voices(game, donors, out, jobs):
    _ran(game, "voices")
    (out / "wozmod/voice").mkdir(parents=True, exist_ok=True)
    (out / "wozmod/voice/Voice.bin").write_bytes(b"voices of " + DonorsFolder(donors).folder("wo2-x360").joinpath("link_vodat.bdx").read_bytes())


def _fake_art(game, donors, lettering, staging, out, jobs):
    _ran(game, "art")
    (out / "wozmod/hires").mkdir(parents=True, exist_ok=True)
    (out / "wozmod/hires/hires.bin").write_bytes(b"art of " + Path(lettering).read_bytes())


def _ran(game, step):
    with open(game / "runs.txt", "a") as runs:
        runs.write(step + "\n")


def test_a_reused_step_puts_the_bytes_its_build_wrote_and_runs_nothing(tmp_path, monkeypatch):
    """Bug: a reused step lands in the wrong folder or drops a file, so the install differs from a build; a hit
    still runs the step or reads the movies from the disc; a kept file written to after the build is reused; or a
    changed donor still gets the old output."""
    game, donor = tmp_path / "game", {"bytes": b"donor one"}
    game.mkdir()
    (tmp_path / "lettering.zip").write_bytes(b"bundle")
    (tmp_path / "d3d9.dll").write_bytes(b"dll")
    inputs = {**{flag: tmp_path / flag for flag in ("wo1-x360", "wo2-x360", "wo2-psp")},
              "lettering": tmp_path / "lettering.zip", "dll": tmp_path / "d3d9.dll"}
    reads = []

    def extract(flag, image, donor_disc, destination, files, *rest, **options):
        reads.append("movies" if files is None else flag)
        for name in [m.path for m in donor_disc.files] if files is None else files:
            (destination / name).parent.mkdir(parents=True, exist_ok=True)
            (destination / name).write_bytes(MOVIE if files is None else donor["bytes"])

    movie = DonorFile("voice/movie001.wmv", "movie001.wmv", len(MOVIE), hashlib.sha256(MOVIE).hexdigest())
    monkeypatch.setattr(movies, "movie_donors", lambda: {"wo2-x360": Donor("wo2-x360", "xdvdfs", (movie,))})
    monkeypatch.setattr(installer, "_extract", extract)
    monkeypatch.setattr(installer, "_place_programs", lambda inputs, donors: None)
    monkeypatch.setattr(remembered, "settings_path", lambda: tmp_path / "config/settings.json")
    for name, fake in (("_text_table", _fake_text), ("_voice_banks", _fake_voices), ("_menu_art", _fake_art)):
        monkeypatch.setattr(installer, name, fake)
    cache = build_cache.BuildCache(tmp_path / "cache", here={"machine": "this one"})

    def build(run, cache=None):
        (game / "runs.txt").unlink(missing_ok=True)
        reads.clear()
        out = installer._build(game, inputs, tmp_path / run, Progress(), 1, lambda wait=True: None, cache=cache)
        runs = sorted((game / "runs.txt").read_text().split()) if (game / "runs.txt").exists() else []
        return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}, runs, list(reads)

    plain, runs, _ = build("plain")
    assert runs == ["art", "text", "voices"]
    assert build("cold", cache) == (plain, runs, ["wo1-x360", "wo2-x360", "wo2-psp", "movies"])
    assert build("warm", cache) == (plain, [], ["wo1-x360", "wo2-x360", "wo2-psp"])

    with open(tmp_path / "cold/out/wozmod/table.bin", "r+b") as built:  # the cache keeps this very file
        built.write(b"TABLE")
    assert build("edited", cache) == (plain, ["text"], ["wo1-x360", "wo2-x360", "wo2-psp"])
    assert build("again", cache) == (plain, [], ["wo1-x360", "wo2-x360", "wo2-psp"])

    donor["bytes"] = b"donor two"
    changed, runs, _ = build("changed", cache)
    assert runs == ["art", "text", "voices"]  # all three read the donors
    assert changed == {**plain, "wozmod/table.bin": b"table of donor two", "wozmod/voice/Voice.bin": b"voices of donor two"}
