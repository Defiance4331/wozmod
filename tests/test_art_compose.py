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
