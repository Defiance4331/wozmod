"""Differential guard for the active font renderers."""

import json
import os
from pathlib import Path
import subprocess

import pytest

from test_fonts_rendering import system_python


@pytest.mark.skipif(os.environ.get("WOZMOD_FONT_ORACLE") != "1",
                    reason="set WOZMOD_FONT_ORACLE=1 for baseline rendering comparison")
def test_relocated_font_renderers_cannot_shift_a_baseline_or_select_the_wrong_face(tmp_path):
    old = os.environ.get("WOZMOD_OLD_REPO")
    if not old or not Path(old).is_dir():
        pytest.skip("set WOZMOD_OLD_REPO to the migration source")
    source = tmp_path / "baseline"
    source.mkdir()
    files = ["buttonvec", "buttonraster", "headervec", "headerraster", "labelvec", "labelraster",
             "mainvec", "mainraster", "valuevec", "valueraster", "menu", "menu_batch",
             "menu_effects", "menu_styles", "menusans", "menu_stroke", "skeleton", "wo1vec"]
    for name in files:
        original = subprocess.run(
            ["git", "-C", old, "show", f"2fa29d5:scripts/font/{name}.py"],
            capture_output=True, check=True,
        ).stdout
        (source / f"{name}.py").write_bytes(original)
    code = """
import hashlib,importlib,json,sys
if sys.argv[1] == 'old':
    sys.path.insert(0, sys.argv[2])
    prefix = ''
else:
    prefix = 'wozmod.fonts.'
def signature(image, metrics):
    return [list(image.size),hashlib.sha256(image.tobytes()).hexdigest(),metrics]
rows = []
menu = importlib.import_module(prefix + 'menu')
styles = importlib.import_module(prefix + 'menu_styles')
for style in styles.STYLES:
    st = dict(styles.style_for(style), scale=1)
    image = menu.render('Amw1', st)
    rows.append([style,signature(image,dict(image.info))])
for style, variants in styles.ROLE_VARIANTS.items():
    for role in variants:
        image = menu.render('Amw1', styles.style_for(style,role=role))
        rows.append([style + ':' + role,signature(image,dict(image.info))])
for name in ('buttonraster','headerraster','labelraster','mainraster','valueraster'):
    module = importlib.import_module(prefix + name)
    for scale in (1,2):
        label = module.render('A',scale)
        rows.append([name + ':' + str(scale),signature(label.image,
                     [label.baseline,list(label.ink),label.pen,label.advance])])
print(json.dumps(rows))
"""
    expected = system_python(code, tmp_path, "old", source)
    actual = system_python(code, tmp_path, "new", source)
    differences = [before[0] for before, after in zip(expected, actual, strict=True) if before != after]
    assert differences == []
