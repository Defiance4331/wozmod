from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

from wozmod.art import build


BASELINE = "2fa29d5efebc052eaf2865824319f5b3e3df73db"


def _inputs():
    values = {}
    for name in ("WOZMOD_GAME", "WOZMOD_OLD_REPO"):
        value = os.environ.get(name)
        if not value or not Path(value).is_dir():
            pytest.skip(f"set {name} to the input directory")
        values[name] = Path(value)
    old = values["WOZMOD_OLD_REPO"]
    for relative in (
        "work/donors/wo1/linkdata_ens.idx", "work/donors/wo1/linkdata_ens.lnk",
        "work/donors/wo2-x360/linkdata_ens.idx", "work/donors/wo2-x360/linkdata_ens.lnk",
        "work/font/latin-24.png",
    ):
        if not (old / relative).is_file():
            pytest.skip(f"old-repository input absent: {relative}")
    for name in ("OROCHI_Z.exe", "LINKDATA1.IDX", "LINKDATA1.BIN", "LINKDATA2.IDX", "LINKDATA2.BIN"):
        if not (values["WOZMOD_GAME"] / name).is_file():
            pytest.skip(f"stock input absent: {name}")
    return values["WOZMOD_GAME"], old


def _cache(source_env, destination):
    source = os.environ.get(source_env)
    if source and Path(source).is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def _payloads(root):
    files = [*sorted((root / "entries").glob("*.bin")), root / "hires.bin", root / "prompts-xbox.bin"]
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


@pytest.mark.skipif(os.environ.get("WOZMOD_ART_ORACLE") != "1", reason="set WOZMOD_ART_ORACLE=1 for full art builds")
def test_full_art_build_does_not_drop_or_change_fixed_baseline_payloads(tmp_path):
    game, old_repo = _inputs()
    font_python = os.environ.get("WOZMOD_FONT_PYTHON", build.FONT_PYTHON)
    probe = subprocess.run([font_python, "-c", "import PIL, cairo, fontforge, numpy"], capture_output=True, text=True)
    if probe.returncode:
        pytest.skip(f"font interpreter dependencies unavailable: {probe.stderr.strip()}")

    snapshot = tmp_path / "baseline"
    snapshot.mkdir()
    archived = subprocess.run([
        "git", "-C", str(old_repo), "archive", BASELINE,
        "src", "scripts", "translations", "pyproject.toml", "uv.lock",
    ], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
        archive.extractall(snapshot, filter="data")
    for relative in ("work/donors/wo1", "work/donors/wo2-x360", "work/font/latin-24.png"):
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(old_repo / relative, target_is_directory=(old_repo / relative).is_dir())
    quoted_game = json.dumps(str(game))
    (snapshot / "config.toml").write_text(
        '[paths]\nwork_dir = "work"\n\n'
        '[targets.musou-orochi-z-pc-jp]\ndefault_installation = "oracle"\n\n'
        '[targets.musou-orochi-z-pc-jp.installations.oracle]\n'
        f'game_dir = {quoted_game}\nbackup_dir = {quoted_game}\n'
    )
    old_output, new_output = tmp_path / "old", tmp_path / "new"
    _cache("WOZMOD_ART_OLD_CACHE", snapshot / "work/build/lzp2-cache")
    _cache("WOZMOD_ART_NEW_CACHE", new_output / "lzp2-cache")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(snapshot / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    old_run = subprocess.run([
        font_python, str(snapshot / "scripts/build_moz_english_textures.py"),
        "--output-dir", str(old_output), "--hires", "vector", "--jobs", "4",
    ], cwd=snapshot, env=environment, capture_output=True, text=True)
    assert old_run.returncode == 0, old_run.stdout + old_run.stderr

    new_environment = build.font_environment()
    for key in ("WOZMOD_WO1_ISO", "WOZMOD_X360_ISO", "WOZMOD_WO1_DONOR", "WOZMOD_X360_DONOR",
                "WOZMOD_LATIN_CELLS", "WOZMOD_GAME"):
        new_environment.pop(key, None)
    bundle = tmp_path / "lettering.zip"
    render_run = subprocess.run([
        font_python, "-m", "wozmod.art.render", str(bundle),
        "--font-python", font_python, "--jobs", "4", "--full-width",
    ], cwd=tmp_path, env=new_environment, capture_output=True, text=True)
    assert render_run.returncode == 0, render_run.stdout + render_run.stderr

    guard = tmp_path / "import-guard"
    guard.mkdir()
    attempts, activated = guard / "attempts.txt", guard / "activated.txt"
    attempts.write_text("")
    (guard / "sitecustomize.py").write_text(
        'import importlib.abc, os, sys\n'
        'from pathlib import Path\n'
        'class RejectFontTools(importlib.abc.MetaPathFinder):\n'
        '    def find_spec(self, fullname, path=None, target=None):\n'
        '        if fullname.split(".")[0] in {"fontforge", "cairo", "numpy"}:\n'
        '            with Path(os.environ["WOZMOD_IMPORT_ATTEMPTS"]).open("a") as output:\n'
        '                output.write(fullname + "\\n")\n'
        '            raise ModuleNotFoundError("compose imported a font tool: " + fullname)\n'
        'sys.meta_path.insert(0, RejectFontTools())\n'
        'Path(os.environ["WOZMOD_IMPORT_GUARD"]).write_text("active")\n'
    )
    compose_environment = dict(new_environment)
    compose_environment["PYTHONPATH"] = str(guard) + os.pathsep + new_environment["PYTHONPATH"]
    compose_environment["WOZMOD_IMPORT_ATTEMPTS"] = str(attempts)
    compose_environment["WOZMOD_IMPORT_GUARD"] = str(activated)
    new_run = subprocess.run([
        sys.executable, "-m", "wozmod.art.compose", "--game", str(game),
        "--wo2-donor", str(old_repo / "work/donors/wo2-x360/linkdata_ens"),
        "--bundle", str(bundle), "--output-dir", str(new_output), "--oracle-full-width",
    ], cwd=tmp_path, env=compose_environment, capture_output=True, text=True)
    assert new_run.returncode == 0, new_run.stdout + new_run.stderr
    assert activated.read_text() == "active"
    assert attempts.read_text() == "", "compose attempted to import font tools"
    old_payloads, new_payloads = _payloads(old_output / "moz-english"), _payloads(new_output / "wozmod")
    assert len(old_payloads) == 167
    assert new_payloads.keys() == old_payloads.keys()
    # Intended changes: X360 skill tag plates (4079), faction labels (4060, 4064, 4069) and grade plates (4060),
    # the still-event plates (4104), and no hires labels on the faction tabs.
    assert {name for name in old_payloads if new_payloads[name] != old_payloads[name]} == {
        "entries/4079.bin", "entries/4060.bin", "entries/4064.bin", "entries/4069.bin", "entries/4104.bin", "hires.bin"}
