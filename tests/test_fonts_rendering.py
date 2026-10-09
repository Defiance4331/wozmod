"""Font rendering regressions through public image interfaces."""

import json
import os
from pathlib import Path
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[1]
SYSTEM_PYTHON = "/usr/bin/python3"


def system_python(code, tmp_path, *args):
    if not Path(SYSTEM_PYTHON).exists():
        pytest.skip("system Python absent")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (environment.get("PYTHONPATH", ""), str(REPO / "src"))))
    probe = subprocess.run(
        [SYSTEM_PYTHON, "-c", "import importlib.util,json; print(json.dumps([m for m in ('fontforge','cairo','numpy','PIL','psMat') if importlib.util.find_spec(m) is None]))"],
        capture_output=True, text=True, env=environment, check=True,
    )
    missing = json.loads(probe.stdout)
    if missing:
        pytest.skip("optional font libraries absent: " + ", ".join(missing))
    result = subprocess.run([SYSTEM_PYTHON, "-c", code, *map(str,args)],
                            cwd=tmp_path, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_shadow_translation_cannot_wrap_pixels_onto_the_opposite_edge(tmp_path):
    actual = system_python("""
import json
import numpy as np
from wozmod.fonts.menu_effects import paint
fill = np.zeros((2, 4))
fill[0, 0] = fill[0, 3] = 1
image = paint(fill, lambda dilation: fill,
              {'fill': [255, 255, 255], 'shadow_offset': [1, 1],
               'shadow_fill': [20, 30, 40], 'shadow_alpha': .5}, 1, 1, 1)
print(json.dumps(image.tolist()))
""", tmp_path)
    assert actual == [
        [[255, 255, 255, 255], [0, 0, 0, 0], [0, 0, 0, 0], [255, 255, 255, 255]],
        [[0, 0, 0, 0], [20, 30, 40, 128], [0, 0, 0, 0], [0, 0, 0, 0]],
    ]


def test_latin_cli_cannot_select_the_wrong_spacing_profile_or_drop_a_slot(tmp_path):
    result = system_python("""
import hashlib,json,sys
from pathlib import Path
import subprocess
from PIL import Image
rows = []
for mode in ('baseline','legacy'):
    output = Path(mode + '.png')
    run = subprocess.run([sys.executable,'-m','wozmod.fonts.raster',str(output),
                          '--px','24','--spacing',mode,'--advances'],
                         capture_output=True,text=True,check=True)
    image = Image.open(output)
    rows.append([mode,list(image.size),hashlib.sha256(image.tobytes()).hexdigest(),
                 len(bytes.fromhex(run.stdout.strip()))])
print(json.dumps(rows))
""", tmp_path)
    assert result == [
        ["baseline", [2376, 24], "3912da05ea6eab55d1b38278304fe44d36770853e1e8cd3ee929aa8faf6d8049", 99],
        ["legacy", [2376, 24], "e10566c71b9bb29ed508efd5d83a7de939f44c67cfcc3435218ab21b9086b239", 99],
    ]


def test_menu_batch_cli_cannot_drop_a_request_or_overwrite_its_neighbours(tmp_path):
    result = system_python("""
import hashlib,json,sys
from pathlib import Path
import subprocess
from PIL import Image
requests = [{'text':'A','style':'dark.controls'},
            {'text':'W','style':'dark.controls'}]
Path('requests.json').write_text(json.dumps(requests))
subprocess.run([sys.executable,'-m','wozmod.fonts.menu_batch','requests.json','out'],
               capture_output=True,text=True,check=True)
manifest = json.loads(Path('out/manifest.json').read_text())
rows = []
for row in manifest:
    image = Image.open(Path('out') / row['file'])
    rows.append([row['index'],row['file'],row['request'],
                 image.mode,list(image.size) == row['size'],
                 hashlib.sha256(image.tobytes()).hexdigest()])
print(json.dumps(rows))
""", tmp_path)
    assert [row[:5] for row in result] == [
        [0, "000000.png", {"text": "A", "style": "dark.controls"}, "RGBA", True],
        [1, "000001.png", {"text": "W", "style": "dark.controls"}, "RGBA", True],
    ]
    assert result[0][5] != result[1][5]


def test_unknown_style_cannot_silently_render_the_default_menu_face(tmp_path):
    result = system_python("""
import json, sys
from wozmod.fonts import menu
try:
    menu.render('Menu', 'dark.misspelling')
except ValueError as error:
    print(json.dumps({'error': str(error)}))
else:
    raise AssertionError('unknown style rendered')
""", tmp_path)
    assert result["error"] == "unknown menu style: 'dark.misspelling'"


@pytest.mark.skipif(os.environ.get("WOZMOD_FONT_GEOMETRY") != "1",
                    reason="set WOZMOD_FONT_GEOMETRY=1 for outline geometry checks")
def test_stage_short_descender_does_not_leak_into_banner_or_wo1(tmp_path):
    result = system_python("""
import json, sys
import numpy as np
from wozmod.fonts import menu, wo1vec
def signature():
    font = wo1vec.make(only='gmW')
    points = [[(p.x,p.y,p.on_curve) for c in font[ord(ch)].foreground for p in c]
              for ch in 'gmW']
    font.close()
    return points
def letter(style):
    recipe = dict(menu.STYLES[style], cap=20, width=1, scale=8,
                  ring=0, glow=0, fill=(0,0,0))
    recipe.pop('gradient', None)
    for field in ('shadow_offset', 'shadow_sigma', 'shadow_fill', 'shadow_alpha'):
        recipe.pop(field, None)
    image = menu.render('g', recipe)
    alpha = np.asarray(image)[...,3]/255.
    rows = alpha.max(1)
    last = np.flatnonzero(rows>.1)[-1]
    descent = (last+rows[last]-image.info['baseline'])/8
    return np.asarray(image), descent
original = signature()
banner_before, full = letter('banner.large')
_, short = letter('stage.stage')
banner_after, again = letter('banner.large')
print(json.dumps(dict(wo1_unchanged=original==signature(),
                     banner_unchanged=bool(np.array_equal(banner_before,banner_after)),
                     full=full, short=short, again=again)))
""", tmp_path)
    assert result["wo1_unchanged"] and result["banner_unchanged"]
    assert abs(result["short"] / result["full"] - 0.5) < 0.04
    assert result["full"] == result["again"]


def test_long_menu_label_widens_its_box_without_condensing_each_letter(tmp_path):
    result = system_python("""
import json, sys
import numpy as np
from wozmod.fonts import menu
st = dict(menu.STYLES['dark.pause'], fill=(0,0,0), track=0,
          cap=17.2, width=1, ring=0)
one = menu.render('H', st)
many = menu.render('H'*80, st)
def widths(image):
    alpha = np.asarray(image)[...,3]
    occupied = alpha.max(0)>0
    changes = np.diff(np.r_[False,occupied,False].astype(int))
    return np.flatnonzero(changes==-1)-np.flatnonzero(changes==1)
print(json.dumps(dict(one=widths(one).tolist(), many=widths(many).tolist(),
                     size=many.width, one_mass=float(np.asarray(one)[...,3].sum()),
                     mean_mass=float(np.asarray(many)[...,3].sum())/80)))
""", tmp_path)
    assert len(result["many"]) == 80
    # Native pixel support changes by one texel with phase. Integrated area
    # catches actual geometric condensation without requiring hinted phases.
    assert abs(result["mean_mass"] / result["one_mass"] - 1) < 0.002
    assert result["size"] > 1000


@pytest.mark.skipif(os.environ.get("WOZMOD_FONT_GEOMETRY") != "1",
                    reason="set WOZMOD_FONT_GEOMETRY=1 for outline geometry checks")
def test_light_menu_i_cannot_keep_a_medium_weight_rectangular_dot(tmp_path):
    result = system_python("""
import json, sys
import numpy as np
from wozmod.fonts import menu
recipe = dict(menu.STYLES['dark.pause'], cap=20, width=1, scale=8, fill=(0,0,0))
def mass(gain):
    st = dict(recipe, axes=dict(recipe['axes'], pen=(gain,gain)))
    im = menu.render('i',st)
    dot = np.asarray(im)[:int(im.info['baseline']-20*8*.79),:,3]
    return float(dot.sum())
print(json.dumps({'ratio': mass(.65)/mass(1.)}))
""", tmp_path)
    assert abs(result["ratio"] - 0.65**2) < 0.02


@pytest.mark.skipif(os.environ.get("WOZMOD_FONT_GEOMETRY") != "1",
                    reason="set WOZMOD_FONT_GEOMETRY=1 for outline geometry checks")
def test_lowercase_w_width_axis_cannot_widen_capital_W(tmp_path):
    result = system_python("""
import json, sys
import numpy as np
from wozmod.fonts import menu
recipe = menu.STYLES['dark.help']
def image(ch,gain):
    st = dict(recipe,axes=dict(recipe['axes'],lower_w_width=gain))
    return np.asarray(menu.render(ch,st))
print(json.dumps(dict(capital_same=bool(np.array_equal(image('W',1),image('W',1.3))),
                     small_widths=[image('w',g).shape[1] for g in (1,1.3)])))
""", tmp_path)
    assert result["capital_same"]
    assert result["small_widths"][1] > result["small_widths"][0]


def test_narrow_figure_one_cannot_change_the_digit_pitch(tmp_path):
    result = system_python("""
import json, sys
import numpy as np
from wozmod.fonts import menu
recipe = dict(menu.STYLES['sound.sound'],cap=17.2,width=1,track=0,
              fill=(0,0,0))
def pitch(text):
    a = np.asarray(menu.render(text,recipe))[...,3]/255.
    profile = a.sum(0)
    changes = np.diff(np.r_[False,profile>0,False].astype(int))
    spans = zip(np.flatnonzero(changes==1),np.flatnonzero(changes==-1))
    centres = [float((np.arange(lo,hi)*profile[lo:hi]).sum()/profile[lo:hi].sum())
               for lo,hi in spans]
    if len(centres)!=2: raise AssertionError('two separated figures expected')
    return centres[1]-centres[0]
print(json.dumps({'one':pitch('11'),'two':pitch('22')}))
""", tmp_path)
    assert abs(result["one"] - 18) < 0.02
    assert abs(result["two"] - 18) < 0.02


def test_figures_double_quote_cannot_collapse_its_blank_display_cell_before_plus(tmp_path):
    result = system_python("""
import json, sys
import numpy as np
from wozmod.fonts import menu
recipe = dict(menu.STYLES['rounded.figures'], scale=8, ring=0, sigma=0,
              fill=(0,0,0))
recipe.pop('bands')
alpha = np.asarray(menu.render('”+', recipe))[...,3]
profile = alpha.sum(0)
changes = np.diff(np.r_[False,profile>0,False].astype(int))
spans = list(zip(np.flatnonzero(changes==1),np.flatnonzero(changes==-1)))
if len(spans)!=3: raise AssertionError('two quote strokes and one plus expected')
centres = [float((np.arange(lo,hi)*profile[lo:hi]).sum()/profile[lo:hi].sum())/8
           for lo,hi in spans]
print(json.dumps({'quote_to_plus':centres[2]-(centres[0]+centres[1])/2}))
""", tmp_path)
    # Two 21-texel cells plus the symbol offsets.
    assert abs(result["quote_to_plus"] - 43.4951) < 0.15


@pytest.mark.skipif(os.environ.get("WOZMOD_FONT_GEOMETRY") != "1",
                    reason="set WOZMOD_FONT_GEOMETRY=1 for outline geometry checks")
def test_italic_Y_overhang_cannot_push_the_following_u_away_at_the_baseline(tmp_path):
    result = system_python("""
import json, sys
import numpy as np
from wozmod.fonts import menu
recipe = dict(menu.STYLES['banner.small'],cap=17.2,width=1,scale=16,
              fill=(0,0,0),ring=0,glow=0)
def gap(slant):
    st = dict(recipe,axes=dict(recipe['axes'],slant=slant))
    image = menu.render('Yu',st)
    row = np.asarray(image)[int(image.info['baseline']-.75*16),:,3]/255.
    transitions = np.diff(np.r_[False,row>.1,False].astype(int))
    spans = list(zip(np.flatnonzero(transitions==1),np.flatnonzero(transitions==-1)))
    if len(spans)!=2: raise AssertionError('Y and u must remain separated')
    left = spans[1][0]+1-row[spans[1][0]]
    right = spans[0][1]-1+row[spans[0][1]-1]
    return float((left-right)/16)
print(json.dumps({'upright':gap(0),'italic':gap(.2)}))
""", tmp_path)
    assert abs(result["italic"] - result["upright"]) < 0.08


@pytest.mark.skipif(os.environ.get("WOZMOD_FONT_GEOMETRY") != "1",
                    reason="set WOZMOD_FONT_GEOMETRY=1 for outline geometry checks")
def test_banner_outline_does_not_draw_dark_pixels_inside_neighboring_glyph_fill(tmp_path):
    """Closely set names retain their fill colours when the renderer paints purple edges."""
    result = system_python("""
import json, sys
import numpy as np
from PIL import Image, ImageFilter
from wozmod.fonts import menu
rows = []
for style in ('banner.small', 'banner.large'):
    st = dict(menu.STYLES[style], scale=4)
    for text in ('minimum', 'ammonium', 'mnmn'):
        outlined = menu.render(text, st)
        bare = menu.render(text, dict(st, ring=0, glow=0, sigma=0))
        core = bare.getchannel('A').point(lambda a: 255 if a >= 254 else 0).filter(ImageFilter.MinFilter(9))
        mask = Image.new('L', outlined.size)
        mask.paste(core, (round(outlined.info['pen']-bare.info['pen']),
                          round(outlined.info['baseline']-bare.info['baseline'])))
        pixels = np.asarray(outlined)
        on = np.asarray(mask)>0
        expected = Image.new('RGBA', outlined.size)
        expected.paste(bare, (round(outlined.info['pen']-bare.info['pen']),
                              round(outlined.info['baseline']-bare.info['baseline'])))
        difference = np.abs(pixels[:,:,:3].astype(int)-np.asarray(expected)[:,:,:3].astype(int))
        rows.append(dict(style=style, text=text, core=int(on.sum()),
                         dark=int((on & (difference.max(2)>1)).sum())))
print(json.dumps(rows))
""", tmp_path)
    assert all(row["core"] > 0 and row["dark"] == 0 for row in result), result
