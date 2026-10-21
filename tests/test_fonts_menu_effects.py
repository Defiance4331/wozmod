"""The shared raster helpers in wozmod.fonts.menu_effects, on hand-built faces under the font Python."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import textwrap

import pytest

REPO = Path(__file__).resolve().parents[1]

FACE = """
import json
from types import SimpleNamespace
from wozmod.fonts.menu_effects import ink_layout, outline_table, pen_layout

class P:
    def __init__(self, x, y, on): self.x, self.y, self.on_curve = x, y, on

class Layer(list):
    def boundingBox(self):
        xs = [p.x for c in self for p in c]; ys = [p.y for c in self for p in c]
        return min(xs), min(ys), max(xs), max(ys)
"""


def _run(code: str):
    python = os.environ.get("WOZMOD_FONT_PYTHON", "/usr/bin/python3")
    if not Path(python).is_file():
        pytest.skip("font Python absent")
    probe = subprocess.run([python, "-c", "import importlib.util; print([m for m in ('fontforge', 'cairo', 'numpy') "
                                          "if importlib.util.find_spec(m) is None])"], capture_output=True, text=True)
    if probe.stdout.strip() != "[]":
        pytest.skip(f"font libraries absent: {probe.stdout.strip()}")
    run = subprocess.run([python, "-c", FACE + textwrap.dedent(code)], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=str(REPO / "src"), PYTHONDONTWRITEBYTECODE="1"))
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_a_face_table_holds_each_outline_in_cap_units_measured_down_from_the_cap_line():
    """Bug: the shared table drops the /U or the CAP - y flip (every face then draws its letters scaled or upside
    down), loses the first curve of a contour that starts off-curve, or spaces a slanted face by its slanted ink."""
    table = _run("""
        # U = 10 font units per cap unit, CAP = 20. "a": a curve then a line back. "b" starts on an off-curve point.
        a = Layer([[P(0, 0, True), P(10, 20, False), P(20, 20, False), P(30, 0, True)]])
        b = Layer([[P(0, 50, False), P(50, 0, True), P(100, 0, True)]])
        upright = {"a": Layer([[P(-10, 0, True), P(40, 0, True)]]), "b": b}
        face = SimpleNamespace(BUILD={"a": None, "b": None}, U=10, CAP=20)
        print(json.dumps([outline_table(face, {"a": a, "b": b}.get),
                          outline_table(face, {"a": a, "b": b}.get, upright=upright.get)]))
    """)
    plain, slanted = table
    assert plain == {
        "a": [[[[0, 20], ["C", [1, 18], [2, 18], [3, 20]], ["L", [0, 20]]]], [0, 18, 3, 20], [0, 3]],
        "b": [[[[5, 20], ["L", [10, 20]], ["C", [0, 15], [5, 20]]]], [0, 15, 10, 20], [0, 10]],
    }
    assert slanted["a"][2] == [-1, 4] and slanted["a"][:2] == plain["a"][:2]


def test_letters_without_a_fitted_row_are_spaced_by_their_bearings_and_a_fitted_row_by_its_ink():
    """Bug: the shared ink layout adds a bearing on the wrong side, drops a space, or ignores the fitted row's
    ink, so header, value and main lettering would all shift letters in one go."""
    placed = _run("""
        face = SimpleNamespace(__name__="wozmod.fonts.testvec", SPACE=5.0, BEARINGS={"A": (1.0, 2.0), "B": (3.0, 6.0)})
        g = {"A": (None, None, (10.0, 16.0)), "B": (None, None, (20.0, 30.0))}
        print(json.dumps([ink_layout("AB A", face, g), ink_layout("AB", face, g, [0.0, 7.5])]))
    """)
    # A: ink 0..6; B after A's right 2 and B's left 3: 11..21; space 5, then B's right 6 and A's left 1: ink 33.
    # Each letter sits at its first bearing (1) + ink - span start; the advance ends at the last ink + width + right.
    assert placed == [[[["A", -9.0], ["B", -8.0], ["A", 24.0]], 42.0],
                      [[["A", -9.0], ["B", -11.5]], 1.0 + 7.5 + 10.0 + 6.0]]


def test_a_pen_tracks_after_every_character_and_sets_a_row_s_own_space():
    """Bug: the shared pen layout tracks before the first letter, skips the track at a space, or ignores the row's
    space, so tracked label rows and every button shift their letters."""
    placed = _run("""
        face = SimpleNamespace(__name__="wozmod.fonts.testvec", SPACE=5.0, BEARINGS={"A": (1.0, 2.0)})
        g = {"A": (None, (10.0, 0.0, 16.0, 0.0))}
        print(json.dumps([pen_layout("A A", face, g, 0.5, 3.0), pen_layout("A A", face, g)]))
    """)
    # A: left 1, ink 6, right 2 = 9 wide. Tracked: 9 + 0.5 + 3 space + 0.5 = 13; untracked: 9 + 5 = 14.
    assert placed == [[[["A", -9.0], ["A", 4.0]], 22.0], [[["A", -9.0], ["A", 5.0]], 23.0]]
