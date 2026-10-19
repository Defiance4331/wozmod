#ifndef WOZMOD_HFR_PAIR_H
#define WOZMOD_HFR_PAIR_H

#include <stdlib.h>

/* Pairs a bone upload of the in-between frame with its upload in the frame before: the upload with the same key
   (register << 16 | count) whose floats differ least, under PAIR_STEP in every float, ties to the nearest draw
   position (later before earlier). A partner that beats the best so far differs from the upload by less than that
   best gap in every float, so only the uploads within the gap in one float are searched, from a list sorted by key
   then that float: per key, the float of the first matrix that spreads widest (a world x for the skinned bones; some
   keys hold one value in x for every upload). The draw position's neighbours, almost always the partner, set the
   first gap. Once a match is exact, only an earlier draw position can still win, and nothing else is measured. */
#define PAIR_STEP 200.0f
#define PAIR_FLOATS (256 * 4)
#define PAIR_SEEDS 4  /* draw positions tried first on each side */

struct pair_entry {
    unsigned int key;
    float x;            /* the key's sorting float */
    unsigned int m, axis;
};

struct pair_prev {
    unsigned int count;                   /* uploads stored */
    const unsigned int *key;
    const float (*floats)[PAIR_FLOATS];
    struct pair_entry *by_x;              /* the stored uploads whose sorting float is a number, by key then it */
    unsigned int *loose;                  /* the others, searched every time: a NaN never counts towards a gap */
    unsigned int by_x_count, loose_count;
};

static float pair_gap(const float *a, const float *b, unsigned int count, float least)
{
    float gap = 0, g;
    unsigned int i;
    for (i = 0; i < count * 4 && gap < least; i++)
        if ((g = a[i] > b[i] ? a[i] - b[i] : b[i] - a[i]) > gap)
            gap = g;
    return gap;
}

static int pair_order(const void *a, const void *b)
{
    const struct pair_entry *p = a, *q = b;
    if (p->key != q->key)
        return p->key < q->key ? -1 : 1;
    if (p->x != q->x)
        return p->x < q->x ? -1 : 1;
    return p->m < q->m ? -1 : p->m > q->m;
}

/* The float of the first matrix (or fewer, for a shorter upload) whose finite values spread widest over uploads
   s to e of the list. */
static unsigned int pair_axis(const struct pair_prev *p, unsigned int s, unsigned int e)
{
    unsigned int floats = (p->by_x[s].key & 0xFFFF) * 4, i, j, axis = 0;
    float widest = -1;
    for (i = 0; i < floats && i < 12; i++) {
        float lo = 0, hi = 0;
        int any = 0;
        for (j = s; j < e; j++) {
            float v = p->floats[p->by_x[j].m][i];
            if (v - v != 0)  /* NaN or infinite */
                continue;
            if (!any || v < lo)
                lo = v;
            if (!any || v > hi)
                hi = v;
            any = 1;
        }
        if (any && hi - lo > widest)
            widest = hi - lo, axis = i;
    }
    return axis;
}

/* Index the frame just drawn, for the next in-between frame. */
static void pair_index(struct pair_prev *p)
{
    unsigned int m, s, e, w = 0;
    p->loose_count = 0;
    for (m = 0; m < p->count; m++)
        p->by_x[m].key = p->key[m], p->by_x[m].x = 0, p->by_x[m].m = m;
    qsort(p->by_x, p->count, sizeof *p->by_x, pair_order);  /* by key, then draw position */
    for (s = 0; s < p->count; s = e) {
        unsigned int axis, from = w;
        for (e = s; e < p->count && p->by_x[e].key == p->by_x[s].key; e++)
            ;
        axis = pair_axis(p, s, e);
        for (m = s; m < e; m++) {
            struct pair_entry entry = p->by_x[m];
            entry.x = p->floats[entry.m][axis], entry.axis = axis;
            if (entry.x != entry.x)
                p->loose[p->loose_count++] = entry.m;
            else
                p->by_x[w++] = entry;  /* w <= m: this group's entries are all read before any is overwritten */
        }
        qsort(p->by_x + from, w - from, sizeof *p->by_x, pair_order);
    }
    p->by_x_count = w;
}

struct pair_best {
    unsigned int n, key, count, m, rank, gaps;
    const float *data;
    float least;
};

/* The draw-order rank of the full scan this search stands for: n, n+1, n-1, n+2, n-2, ... */
static unsigned int pair_rank(unsigned int n, unsigned int m)
{
    return m >= n ? 2 * (m - n) : 2 * (n - m) + 1;
}

static void pair_try(const struct pair_prev *p, struct pair_best *b, unsigned int m)
{
    unsigned int rank;
    float gap;
    if (m >= p->count || p->key[m] != b->key || (m > b->n ? m - b->n : b->n - m) >= p->count)
        return;  /* the full scan's reach: positions less than count away */
    rank = pair_rank(b->n, m);
    if (b->least == 0 && rank >= b->rank)  /* nothing beats an exact match but one earlier in draw order */
        return;
    b->gaps++;
    gap = pair_gap(p->floats[m], b->data, b->count, b->least);
    if (gap < b->least)
        b->m = m, b->rank = rank, b->least = gap;
    else if (gap == b->least && b->m < p->count && rank < b->rank  /* a tie: the earlier position, if the gap is exact */
             && pair_gap(p->floats[m], b->data, b->count, PAIR_STEP * 2) == b->least)
        b->m = m, b->rank = rank;
}

/* The partner of upload n, or p->count for none; *gaps counts the gaps measured. */
static unsigned int pair_find(const struct pair_prev *p, unsigned int n, unsigned int key, const float *data,
                              unsigned int count, unsigned int *gaps)
{
    struct pair_best b;
    unsigned int d, lo, hi, i;
    float x;
    b.n = n, b.key = key, b.count = count, b.data = data, b.m = p->count, b.rank = ~0u, b.gaps = 0;
    b.least = PAIR_STEP;
    for (lo = 0, hi = p->by_x_count; lo < hi;) {  /* this key's first entry */
        unsigned int mid = lo + (hi - lo) / 2;
        if (p->by_x[mid].key < key)
            lo = mid + 1;
        else
            hi = mid;
    }
    x = lo < p->by_x_count && p->by_x[lo].key == key ? data[p->by_x[lo].axis] : 0;
    if (x != x) {  /* nothing to search by: every upload, as the full scan */
        for (d = 0; d < p->count; d++)
            pair_try(p, &b, d);
    } else {
        for (d = 0; d <= PAIR_SEEDS; d++) {
            pair_try(p, &b, n + d);
            if (d && d <= n)
                pair_try(p, &b, n - d);
        }
        for (hi = p->by_x_count; lo < hi;) {  /* the first entry of this key at x or more */
            unsigned int mid = lo + (hi - lo) / 2;
            const struct pair_entry *e = &p->by_x[mid];
            if (e->key < key || (e->key == key && e->x < x))
                lo = mid + 1;
            else
                hi = mid;
        }
        /* outwards from x, the nearer side first, so the gap shrinks early; each side ends past the gap */
        for (hi = lo; ;) {
            /* equal x is inside the gap, infinite x included (its difference is NaN, as in pair_gap) */
            int up = hi < p->by_x_count && p->by_x[hi].key == key
                     && (p->by_x[hi].x == x || p->by_x[hi].x - x <= b.least);
            int down = lo > 0 && p->by_x[lo - 1].key == key && (p->by_x[lo - 1].x == x || x - p->by_x[lo - 1].x <= b.least);
            if (!up && !down)
                break;
            if (up && (!down || p->by_x[hi].x - x <= x - p->by_x[lo - 1].x))
                pair_try(p, &b, p->by_x[hi++].m);
            else
                pair_try(p, &b, p->by_x[--lo].m);
        }
        for (i = 0; i < p->loose_count; i++)
            pair_try(p, &b, p->loose[i]);
    }
    *gaps += b.gaps;
    return b.m;
}

#endif
