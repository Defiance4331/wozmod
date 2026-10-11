from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[1]
FACES = ("buttonvec", "headervec", "labelvec", "mainvec", "valuevec", "wo1vec", "menusans")


@pytest.mark.skipif(os.environ.get("WOZMOD_FONT_FACE_SOURCE") != "1",
                    reason="set WOZMOD_FONT_FACE_SOURCE=1 for relocated face rendering")
def test_face_source_lookup_cannot_depend_on_cwd_or_break_dynamic_sibling_faces(tmp_path):
    python = os.environ.get("WOZMOD_FONT_PYTHON", "/usr/bin/python3")
    if not Path(python).is_file():
        pytest.skip("font Python absent")
    probe = subprocess.run([python, "-c", "import importlib.util,json; print(json.dumps([m for m in ('fontforge','cairo','numpy','PIL','psMat') if importlib.util.find_spec(m) is None]))"],
                           capture_output=True, text=True, check=True)
    if missing := json.loads(probe.stdout):
        pytest.skip("optional font libraries absent: " + ", ".join(missing))
    code = """
import hashlib,json
from wozmod.fonts import buttonraster,headerraster,labelraster,mainraster,valueraster,menu,raster
images = {name: renderer('A').image for name,renderer in (
    ('button',buttonraster.render),('header',headerraster.render),
    ('label',labelraster.render),('main',mainraster.render),('value',valueraster.render))}
images['menu'] = menu.render('A', 'dark.pause')
out = {name:[image.mode,list(image.size),hashlib.sha256(image.tobytes()).hexdigest()]
       for name,image in images.items()}
strip,advances = raster.cells(24, 'legacy')
out['latin'] = [list(strip.shape),hashlib.sha256(strip.tobytes()).hexdigest(),
                hashlib.sha256(advances).hexdigest()]
print(json.dumps(out,sort_keys=True))
"""
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    results = {}
    for variant in ("direct", "relocated"):
        project = tmp_path / variant
        package = project / "src/wozmod"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        shutil.copytree(REPO / "src/wozmod/fonts", package / "fonts")
        (package / "runtime").mkdir()
        (package / "runtime/__init__.py").write_text("")
        shutil.copyfile(REPO / "src/wozmod/runtime/latin_font.py", package / "runtime/latin_font.py")
        if variant == "direct":
            for face in FACES:
                shutil.copyfile(REPO / f"data/art/faces/{face}.py", package / f"fonts/{face}.py")
        else:
            shutil.copytree(REPO / "data/art/faces", project / "data/art/faces")
        environment = dict(os.environ, PYTHONPATH=str(project / "src"), PYTHONDONTWRITEBYTECODE="1")
        run = subprocess.run([python, "-c", code], cwd=unrelated, env=environment,
                             capture_output=True, text=True)
        assert run.returncode == 0, run.stdout + run.stderr
        results[variant] = json.loads(run.stdout.splitlines()[-1])
    failures = {name: {variant: results[variant].get(name) for variant in results}
                for name in results["direct"] | results["relocated"]
                if results["direct"].get(name) != results["relocated"].get(name)}
    assert failures == {}
