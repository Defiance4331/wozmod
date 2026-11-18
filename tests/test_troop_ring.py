"""Where the test spawner puts its troops (runtime/troop_ring.h), against the reach the harness counts them in."""

from __future__ import annotations

import ctypes
import importlib.util
import sys
from itertools import combinations
from pathlib import Path

if sys.platform == "linux":  # the harness imports fcntl; every test here uses c_library, so is linux_only anyway
    SPEC = importlib.util.spec_from_file_location("game_harness",
                                                  Path(__file__).resolve().parents[1] / "tools/game_harness.py")
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


def test_soldiers_take_only_the_ring_spots_the_path_grid_has_cells_for(c_library):
    """Bugs: the spawner places a soldier off the path grid (the 0x4A96B9 crash's likely trigger), the map test
    ignored or inverted; a refused spot tried again for the next soldier, or an accepted one handed out twice; spots
    past TROOP_KEEP, where the spawner would despawn the squad; the refused count off by one; the spot's height not
    the player's (a multi-level map's cell picks its floor by it). Differential: the spots returned against
    troop_spot's rings filtered by the same synthetic map edge (x >= 0)."""
    lib = c_library("next_spot", r'''
#include "troop_ring.h"
static int east(const float *at) { return at[0] >= 0 && at[1] == 50; }
int next(unsigned int *spot, float inner, const float *me, float *at, unsigned int *refused)
{ return troop_next_spot(spot, inner, me, east, at, refused); }
void spot(unsigned int j, float inner, float *xz) { troop_spot(j, inner, &xz[0], &xz[1]); }
float keep(void) { return TROOP_KEEP; }
''')
    lib.keep.restype = ctypes.c_float
    inner, me = 600, (ctypes.c_float * 3)(-1000, 50, 0)
    expected, refused_expected, j = [], 0, 0
    while True:
        xz = (ctypes.c_float * 2)()
        lib.spot(j, ctypes.c_float(inner), xz)
        if max(abs(xz[0]), abs(xz[1])) >= inner + lib.keep():
            break
        if me[0] + xz[0] >= 0:
            expected.append((me[0] + xz[0], me[1], me[2] + xz[1]))
        else:
            refused_expected += 1
        j += 1
    spot, refused, at, got = ctypes.c_uint(0), ctypes.c_uint(0), (ctypes.c_float * 3)(), []
    while lib.next(ctypes.byref(spot), ctypes.c_float(inner), me, at, ctypes.byref(refused)):
        got.append(tuple(at))
        assert len(got) <= len(expected)
    assert expected and refused_expected  # the edge splits the rings, so both branches run
    assert (got, refused.value, spot.value) == (expected, refused_expected, j)
    assert lib.next(ctypes.byref(spot), ctypes.c_float(inner), me, at, ctypes.byref(refused)) == 0  # stays spent
