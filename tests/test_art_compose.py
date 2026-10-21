from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
import zipfile

import pytest
from PIL import Image

def test_compose_cannot_accept_a_layer_dropped_from_bundle_and_index(tmp_path, monkeypatch):
    from wozmod.art.bundle import write_bundle

    composer = importlib.import_module("wozmod.art.compose")
    recipe = tmp_path / "recipe.json"
    recipe_bytes = json.dumps({"format_version": 2, "metrics_only": [],
                               "requests": {"layer/a": {}, "layer/b": {}}}).encode()
    recipe.write_bytes(recipe_bytes)
    monkeypatch.setattr(composer, "RECIPE", recipe)
    bundle = tmp_path / "layers.zip"
    write_bundle(bundle, {"layer/a": (Image.new("L", (1, 1), 17), {}),
                          "layer/b": (Image.new("L", (1, 1), 31), {})},
                 hashlib.sha256(recipe_bytes).hexdigest(), outlines=b"outlines")
    with zipfile.ZipFile(bundle) as archive:
        index = json.loads(archive.read("index.json"))
        dropped = next(row for row in index["layers"] if row["key"] == "layer/b")
        members = [(name, archive.read(name)) for name in archive.namelist()
                   if name not in {"index.json", dropped["path"]}]
    index["layers"] = [row for row in index["layers"] if row["key"] != "layer/b"]
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("index.json", json.dumps(index))
        for name, payload in members:
            archive.writestr(name, payload)
    with pytest.raises(ValueError, match="dropped or added a recipe layer"):
        composer.compose(tmp_path / "missing-stock", {}, bundle, tmp_path / "output")


def test_public_compose_import_cannot_eagerly_require_font_tools(tmp_path):
    script = """
import importlib
import importlib.abc
import sys

blocked = {"fontforge", "cairo", "numpy"}
attempted = []
class DenyFontTools(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in blocked:
            attempted.append(fullname)
            raise ImportError("font tool import forbidden: " + fullname)

sys.meta_path.insert(0, DenyFontTools())
from wozmod.art import compose
assert callable(compose)
importlib.import_module("wozmod.art.compose")
assert not attempted, attempted
assert not any(name.split(".")[0] in blocked for name in sys.modules)
print("compose imported without font tools")
"""
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "compose imported without font tools\n"


@pytest.mark.skipif(os.environ.get("WOZMOD_ART_COMPOSE") != "1", reason="set WOZMOD_ART_COMPOSE=1 (about a minute)")
def test_the_install_art_build_writes_only_the_player_files(tmp_path):
    """Bug: the player's art build still writes a developer file beside wozmod/ (the LZP2 cache, a review folder,
    manifest.json): hundreds of MB the install writes and hashes for nothing."""
    from pathlib import Path

    from wozmod.art.compose import compose

    game, donors, bundle = (Path(os.environ.get(name, "")) for name in ("WOZMOD_GAME", "WOZMOD_DONORS",
                                                                         "WOZMOD_LETTERING"))
    if not ((game / "LINKDATA1.BIN").is_file() and (donors / "wo2-x360/linkdata_ens.idx").is_file()
            and bundle.is_file()):
        pytest.skip("set WOZMOD_GAME, WOZMOD_DONORS (the installer's donors folder) and WOZMOD_LETTERING")
    stems = {"wo1-x360": donors / "wo1-x360/linkdata_ens", "x360": donors / "wo2-x360/linkdata_ens", "text": donors}
    compose(game, stems, bundle, tmp_path / "art")
    assert sorted(p.relative_to(tmp_path / "art").as_posix() for p in (tmp_path / "art").rglob("*")
                  if p.parent.name != "entries") == [
        "wozmod", "wozmod/entries", "wozmod/glyphs.bin", "wozmod/hires.bin", "wozmod/prompts-xbox.bin"]
