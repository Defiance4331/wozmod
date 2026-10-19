#ifndef WOZMOD_TROOP_RING_H
#define WOZMOD_TROOP_RING_H

/* Where the test spawner puts troop j around the player: on square rings, the first `inner` units out (in x and z,
   the larger of the two), each next ring TROOP_SPACING farther, with TROOP_SPACING between neighbours along a ring.
   No trigonometry, so the DLL needs no maths library. tools/game_harness.py ring_reach() follows the same rings. */
#define TROOP_SPACING 120.0f
/* Squads despawned to make room stand farther than this past the nearest ring: beyond every ring a full pool fills,
   and beyond where the game scatters the troops it was given, so the harness's near count never loses one. */
#define TROOP_KEEP 3000.0f

static inline void troop_spot(unsigned int j, float inner, float *dx, float *dz)
{
    const float spacing = TROOP_SPACING;
    float half = inner, t;
    unsigned int n;
    for (;;) {
        n = (unsigned int)(8 * half / spacing);  /* points along this ring's perimeter, 8 half-sides long */
        if (j < n)
            break;
        j -= n, half += spacing;
    }
    t = j * (8 * half / n);  /* distance along the perimeter, from the corner (-half, -half), counter-clockwise */
    if (t < 2 * half)
        *dx = -half + t, *dz = -half;
    else if (t < 4 * half)
        *dx = half, *dz = -half + (t - 2 * half);
    else if (t < 6 * half)
        *dx = half - (t - 4 * half), *dz = half;
    else
        *dx = -half, *dz = half - (t - 6 * half);
}

#endif
