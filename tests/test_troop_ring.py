"""Where the test spawner puts its troops (runtime/troop_ring.h), against the reach the harness counts them in."""

from __future__ import annotations

import ctypes
import importlib.util
from itertools import combinations
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("game_harness", Path(__file__).resolve().parents[1] / "tools/game_harness.py")
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def test_every_troop_stands_on_its_own_spot_within_the_reach_the_harness_counts(c_library):
    """Bug: a spot past the reach (the spawn's near count comes up short, as for troops far from the player), two
    troops on one spot (a perimeter side off by one), ring spacing changed on one side only, or squads despawned for
    room from inside the reach the harness counts."""
    lib = c_library("ring", r'''
#include "troop_ring.h"
void spot(unsigned int j, float inner, float *xz) { troop_spot(j, inner, &xz[0], &xz[1]); }
float spacing(void) { return TROOP_SPACING; }
float keep(void) { return TROOP_KEEP; }
''')
    lib.spacing.restype = lib.keep.restype = ctypes.c_float
    assert lib.spacing() == harness.SPAWN_SPACING
    wrong = []
    for inner in (100, 600, 1500):
        count = harness.SPAWN_MAX + harness.SPAWN_SQUAD - 1
        spots = []
        for j in range(count):
            xz = (ctypes.c_float * 2)()
            lib.spot(j, ctypes.c_float(inner), xz)
            spots.append((xz[0], xz[1]))
        reach = harness.ring_reach(count, inner)
        if reach + harness.SPAWN_SCATTER >= inner + lib.keep():  # a squad despawned for room was counted near
            wrong.append((inner, "keep", reach + harness.SPAWN_SCATTER, inner + lib.keep()))
        far = [s for s in spots if not inner <= max(abs(s[0]), abs(s[1])) <= reach]
        # a spacing apart along the ring, so at least half of one across a corner
        close = [(a, b) for a, b in combinations(spots, 2)
                 if max(abs(a[0] - b[0]), abs(a[1] - b[1])) < harness.SPAWN_SPACING * 0.49]
        if far or close:
            wrong.append((inner, far[:3], close[:3]))
    assert not wrong
