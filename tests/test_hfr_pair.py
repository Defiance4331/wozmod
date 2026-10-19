"""The 120 fps matcher (runtime/hfr_pair.h): the bone upload each in-between upload is blended with."""

from __future__ import annotations

import ctypes
import math
import random

import pytest

GLUE = r'''
#include "hfr_pair.h"
#define MAX 2048
static float floats[MAX][PAIR_FLOATS];
static unsigned int keys[MAX], loose[MAX];
static struct pair_entry by_x[MAX];
static struct pair_prev prev;

void load(unsigned int n, const unsigned int *k, const float *f)
{
    unsigned int i, j;
    for (i = 0; i < n; i++) {
        keys[i] = k[i];
        for (j = 0; j < PAIR_FLOATS; j++)
            floats[i][j] = f[i * PAIR_FLOATS + j];
    }
    prev.count = n, prev.key = keys, prev.floats = (const float (*)[PAIR_FLOATS])floats;
    prev.by_x = by_x, prev.loose = loose;
    pair_index(&prev);
}

unsigned int find(unsigned int n, unsigned int key, const float *data, unsigned int count, unsigned int *gaps)
{
    return pair_find(&prev, n, key, data, count, gaps);
}

/* The matcher as shipped before hfr_pair.h (english_runtime.c pair() and palette_gap()): the oracle. */
static float palette_gap(const float *a, const float *b, unsigned int count, float least)
{
    float gap = 0, g;
    unsigned int i;
    for (i = 0; i < count * 4 && gap < least; i++)
        if ((g = a[i] > b[i] ? a[i] - b[i] : b[i] - a[i]) > gap)
            gap = g;
    return gap;
}

unsigned int scan(unsigned int n, unsigned int reg_count, const float *data, unsigned int count, unsigned int *gaps)
{
    unsigned int d, k, m, key = reg_count, best = prev.count;
    float gap, least = 200.0f;
    for (d = 0; d < prev.count && least > 0; d++)
        for (k = 0; k <= (d != 0); k++) {
            m = k ? n - d : n + d;
            if (m < prev.count && keys[m] == key) {
                ++*gaps;
                if ((gap = palette_gap(floats[m], data, count, least)) < least)
                    best = m, least = gap;
            }
        }
    return best;
}
'''

FLOATS = 256 * 4
REGS = (0, 4, 7, 8, 11)  # the single-matrix keys a crowd's soldiers upload most (work/crowdperf snapshot)


@pytest.fixture(scope="module")
def matcher(c_library):
    lib = c_library("hfr_pair", GLUE)
    for fn in (lib.find, lib.scan):
        fn.restype = ctypes.c_uint
    return lib


SHARED = 18 << 16 | 3  # a key whose x is 0 in every upload and whose values repeat across soldiers (register 4's)


def soldier(rng, x, z, bones):
    """One soldier's uploads: (key, floats) per bone, single 4x3 matrices with the bone's translation in floats 3,
    7 and 11, as the game uploads them, then two of the shared key: one of a few values in float 0, x 0."""
    out = []
    for b in range(bones):
        m = [rng.uniform(-1, 1) for _ in range(12)]
        m[3], m[7], m[11] = x + rng.uniform(-60, 60), rng.uniform(0, 180), z + rng.uniform(-60, 60)
        out.append((REGS[b % len(REGS)] << 16 | 3, m))
    for _ in range(2):
        out.append((SHARED, [float(rng.randrange(-50, 50, 25))] + [0.0] * 11))
    return out


def moved(rng, upload, step):
    key, m = upload
    if key == SHARED:
        return key, list(m)
    m = [v + rng.uniform(-0.05, 0.05) for v in m]
    for i in (3, 7, 11):
        m[i] += rng.uniform(-step, step)
    return key, m


def crowd(seed, soldiers=110, bones=11, spread=1500.0, step=20.0):
    """A frame and the next: soldiers move, the draw order shifts by whole blocks, some leave view and some come in,
    two soldiers are identical twins, and a few uploads have no usable position (NaN or infinite x)."""
    rng = random.Random(seed)
    people = [soldier(rng, rng.uniform(0, spread), rng.uniform(0, spread), bones) for _ in range(soldiers)]
    people[1] = [(k, list(m)) for k, m in people[0]]  # twins: the same floats
    for p in rng.sample(people, 3):
        p[0][1][3] = math.nan
    people[5][0][1][3] = math.inf
    before = [u for p in people for u in p]
    after_people = [[moved(rng, u, step) for u in p] for p in people]
    del after_people[7:10]  # left the view
    after_people += [soldier(rng, rng.uniform(0, spread), rng.uniform(0, spread), bones) for _ in range(4)]
    blocks = [after_people[i:i + 9] for i in range(0, len(after_people), 9)]
    for i in range(0, len(blocks), 2):
        blocks[i].reverse()
    rng.shuffle(blocks[:4])
    after = [u for b in blocks for p in b for u in p]
    return before, after


def load(matcher, uploads, cap=None):
    stored = uploads[:cap] if cap else uploads
    keys = (ctypes.c_uint * len(stored))(*(k for k, _ in stored))
    flat = (ctypes.c_float * (len(stored) * FLOATS))()
    for i, (_, m) in enumerate(stored):
        flat[i * FLOATS:i * FLOATS + len(m)] = m
    matcher.load(len(stored), keys, flat)


def picks(matcher, uploads):
    """(new pick, today's pick, gaps measured by each) for every upload of the in-between frame."""
    out, new_gaps, old_gaps = [], ctypes.c_uint(0), ctypes.c_uint(0)
    for n, (key, m) in enumerate(uploads):
        data = (ctypes.c_float * FLOATS)(*m)
        out.append((matcher.find(n, key, data, 3, ctypes.byref(new_gaps)),
                    matcher.scan(n, key, data, 3, ctypes.byref(old_gaps))))
    return out, new_gaps.value, old_gaps.value


@pytest.mark.parametrize("seed, cap", [(1, None), (2, None), (3, 1024), (4, 600)])
def test_pairs_every_upload_with_the_partner_the_full_scan_picks(matcher, seed, cap):
    """Bug: a search bound one step too narrow, a tie to the wrong position, NaN-x uploads never searched, or a
    partner taken from beyond the full scan's reach when the frame outgrows the store."""
    before, after = crowd(seed)
    load(matcher, before, cap)
    got, _, _ = picks(matcher, after)
    wrong = [(n, new, old) for n, (new, old) in enumerate(got) if new != old]
    assert not wrong, f"{len(wrong)} of {len(got)} picks differ from the full scan: {wrong[:10]}"
    assert sum(old < len(before[:cap] if cap else before) for _, old in got) > len(got) // 2  # the case pairs most


def test_a_dense_crowd_measures_a_few_gaps_per_upload_not_the_whole_frame(matcher):
    """Bug: the search falls back to every same-key upload (a bound from the step, not the gap found; x sorted for a
    key whose x never changes; every twin of an exact match measured), the cost that made 120 fps late in crowds."""
    before, after = crowd(5, soldiers=120, spread=800.0)
    load(matcher, before)
    cost = {False: [0, 0], True: [0, 0]}  # per kind of key (shared or not): gaps, uploads
    for n, (key, m) in enumerate(after):
        gaps = ctypes.c_uint(0)
        matcher.find(n, key, (ctypes.c_float * FLOATS)(*m), 3, ctypes.byref(gaps))
        cost[key == SHARED][0] += gaps.value
        cost[key == SHARED][1] += 1
    per = {("shared" if k else "bones"): g / u for k, (g, u) in cost.items()}
    assert per["bones"] < 30 and per["shared"] < 25, f"gaps per upload {per}"
