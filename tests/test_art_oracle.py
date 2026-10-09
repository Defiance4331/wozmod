from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
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
    new_run = subprocess.run([
        font_python, "-m", "wozmod.art.build", "--game", str(game),
        "--wo1-donor", str(old_repo / "work/donors/wo1/linkdata_ens"),
        "--wo2-donor", str(old_repo / "work/donors/wo2-x360/linkdata_ens"),
        "--latin-cells", str(old_repo / "work/font/latin-24.png"),
        "--output-dir", str(new_output), "--hires", "vector", "--jobs", "4",
        "--font-python", font_python,
    ], cwd=tmp_path, env=new_environment, capture_output=True, text=True)
    assert new_run.returncode == 0, new_run.stdout + new_run.stderr
    old_payloads, new_payloads = _payloads(old_output / "moz-english"), _payloads(new_output / "moz-english")
    assert len(old_payloads) == 167
    assert new_payloads == old_payloads
