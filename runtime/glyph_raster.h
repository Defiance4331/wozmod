#ifndef WOZMOD_GLYPH_RASTER_H
#define WOZMOD_GLYPH_RASTER_H

/* Glyphs drawn from their outlines at the exact size on screen. Before filling, the outline is
   bent so its stems and its heights land on whole pixels: each stem's left side on a pixel edge and its width a
   whole number of pixels, the baseline, x-height and cap height on pixel rows, and every other height rounded to
   whole pixels. Then the outline is filled with exact area coverage. Pure (no libc), so the runtime and the tests both include it.
   Font units: 1200 to the game's 24-unit cell, y up from the baseline. */

#define GR_KNOTS 16

/* A piecewise-linear bend of one axis: font units in, pixels out, `scale` px per unit beyond the knots. */
struct gr_warp {
    int n;
    float from[GR_KNOTS], to[GR_KNOTS], scale;
};

/* Heights in font units (see wozmod.fonts.glyph_outlines.ZONES). */
struct gr_zones {
    short desc, base_over, xh, xh_over, cap, cap_over, asc;
};

/* wozmod/glyphs.bin's outline blob (wozmod.fonts.glyph_outlines.encode). */
struct gr_font {
    const unsigned char *blob;
    unsigned int size, base, count;  /* base: font units from the cell top down to the baseline */
    struct gr_zones zones;
};

struct gr_outline {
    short offset;  /* font units from the cell's left to the outline's origin */
    unsigned int stems, bars, contours, points;
    const short *stem, *bar, *point;  /* left, right pairs; bottom, top pairs; x, y pairs */
    const unsigned short *end;  /* cumulative point count of each contour */
};

static inline unsigned int gr_u16(const unsigned char *p)
{
    return p[0] | (unsigned int)p[1] << 8;
}

static inline unsigned int gr_u32(const unsigned char *p)
{
    return gr_u16(p) | gr_u16(p + 2) << 16;
}

static inline int gr_open(struct gr_font *f, const unsigned char *blob, unsigned int size)
{
    const short *z = (const short *)(blob + 8);
    if (size < 26 || blob[0] != 'W' || blob[1] != 'Z' || blob[2] != 'G' || blob[3] != 'O' || gr_u32(blob + 4) != 2)
        return 0;
    f->blob = blob, f->size = size, f->base = gr_u16(blob + 22), f->count = gr_u16(blob + 24);
    f->zones.desc = z[0], f->zones.base_over = z[1], f->zones.xh = z[2], f->zones.xh_over = z[3];
    f->zones.cap = z[4], f->zones.cap_over = z[5], f->zones.asc = z[6];
    return 26 + 14 * f->count <= size;
}

/* The outline of glyph `number` (0 = the first Latin cell); 0 when the font has none or it runs past the blob. */
static inline int gr_outline(const struct gr_font *f, unsigned int number, struct gr_outline *o)
{
    unsigned int i, at;
    for (i = 0; i < f->count; i++) {
        const unsigned char *row = f->blob + 26 + 14 * i;
        unsigned int lines;
        if (gr_u16(row) != number)
            continue;
        o->offset = (short)gr_u16(row + 2), o->stems = gr_u16(row + 4), o->bars = gr_u16(row + 6);
        o->contours = gr_u16(row + 8);
        at = gr_u32(row + 10);
        lines = 4 * (o->stems + o->bars);
        if (at + lines + 2 * o->contours > f->size)
            return 0;
        o->stem = (const short *)(f->blob + at);
        o->bar = o->stem + 2 * o->stems;
        o->end = (const unsigned short *)(f->blob + at + lines);
        o->points = o->contours ? o->end[o->contours - 1] : 0;
        o->point = (const short *)(f->blob + at + lines + 2 * o->contours);
        return at + lines + 2 * o->contours + 4 * o->points <= f->size;
    }
    return 0;
}

static inline int gr_floor(float v)
{
    int i = (int)v;
    return i - (v < (float)i);
}

static inline int gr_round(float v)
{
    return gr_floor(v + 0.5f);
}

static inline float gr_map(const struct gr_warp *w, float v)
{
    int i;
    if (w->n == 0)
        return v * w->scale;
    if (v <= w->from[0])
        return w->to[0] + (v - w->from[0]) * w->scale;
    for (i = 1; i < w->n; i++)
        if (v <= w->from[i])
            return w->to[i - 1] + (v - w->from[i - 1]) * (w->to[i] - w->to[i - 1]) / (w->from[i] - w->from[i - 1]);
    return w->to[w->n - 1] + (v - w->from[w->n - 1]) * w->scale;
}

static inline void gr_knot(struct gr_warp *w, float from, float to)
{
    if (w->n < GR_KNOTS && (w->n == 0 || (from > w->from[w->n - 1] && to >= w->to[w->n - 1])))
        w->from[w->n] = from, w->to[w->n] = to, w->n++;
}

/* Across: x in font units (plus the glyph's offset in its cell) to px right of the pen. stems: left, right
   pairs in font units, left to right. A stem keeps at least one pixel and a pixel of gap to the one before. */
static inline struct gr_warp gr_across(const short *stems, int count, float offset, float scale)
{
    struct gr_warp w = {0, {0}, {0}, scale};
    int i;
    for (i = 0; i < count; i++) {
        float left = stems[2 * i] + offset, right = stems[2 * i + 1] + offset;
        int at = gr_round(left * scale), width = gr_round((right - left) * scale);
        if (width < 1)
            width = 1;
        if (w.n && at < w.to[w.n - 1] + 1)
            at = (int)w.to[w.n - 1] + 1;
        gr_knot(&w, left, (float)at);
        gr_knot(&w, right, (float)(at + width));
    }
    return w;
}

/* Up: y in font units to px above the baseline, for a glyph with these horizontal bars (bottom, top pairs).
   The baseline, x-height and cap height land on whole rows, and a round letter's overshoot past them rounds to
   whole rows like any other length (none at small sizes, where it is under half a pixel). A bar on one of those
   lines is placed against it whole; any other bar (e's crossbar) starts on its nearest row. Each bar is a whole
   number of pixels thick. The rest of the outline stretches between. */
static inline struct gr_warp gr_up(const struct gr_zones *z, const short *bars, int count, float scale)
{
    struct gr_warp w = {0, {0}, {0}, scale};
    float from[2 * GR_KNOTS + 8], to[2 * GR_KNOTS + 8], xh = (float)gr_round(z->xh * scale);
    float cap = (float)gr_round(z->cap * scale);
    int n = 0, i, j, on_base = 0, on_xh = 0, on_cap = 0;
    for (i = 0; i < count && n < 2 * GR_KNOTS; i++) {
        float lo = bars[2 * i], hi = bars[2 * i + 1], thick = (float)gr_round((hi - lo) * scale), at;
        if (thick < 1)
            thick = 1;
        if (lo >= z->base_over - 4 && lo <= 12) {
            at = lo < 0 ? -(float)gr_round(-lo * scale) : 0.0f;
            on_base = 1;
        } else if (hi >= z->xh - 12 && hi <= z->xh_over + 4) {
            at = xh + (hi > z->xh ? (float)gr_round((hi - z->xh) * scale) : 0.0f) - thick;
            on_xh = 1;
        } else if (hi >= z->cap - 12 && hi <= z->cap_over + 4) {
            at = cap + (hi > z->cap ? (float)gr_round((hi - z->cap) * scale) : 0.0f) - thick;
            on_cap = 1;
        } else {
            at = (float)gr_round(lo * scale);
        }
        from[n] = lo, to[n++] = at;
        from[n] = hi, to[n++] = at + thick;
    }
    from[n] = z->desc, to[n++] = (float)gr_round(z->desc * scale);
    from[n] = z->asc, to[n++] = (float)gr_round(z->asc * scale);
    /* Heights no bar holds: the line and its overshoot zone, as for a glyph without bars there. */
    if (!on_base) {
        from[n] = z->base_over, to[n++] = -(float)gr_round(-z->base_over * scale);
        from[n] = 0, to[n++] = 0;
    }
    if (!on_xh) {
        from[n] = z->xh, to[n++] = xh;
        from[n] = z->xh_over, to[n++] = xh + (float)gr_round((z->xh_over - z->xh) * scale);
    }
    if (!on_cap) {
        from[n] = z->cap, to[n++] = cap;
        from[n] = z->cap_over, to[n++] = cap + (float)gr_round((z->cap_over - z->cap) * scale);
    }
    for (i = 1; i < n; i++)  /* by height */
        for (j = i; j > 0 && from[j - 1] > from[j]; j--) {
            float f = from[j], t = to[j];
            from[j] = from[j - 1], to[j] = to[j - 1], from[j - 1] = f, to[j - 1] = t;
        }
    for (i = 0; i < n; i++)
        gr_knot(&w, from[i], to[i]);
    return w;
}

/* Signed area of the line from (x0, y0) to (x1, y1) into acc, rows of `stride` cells (at least w + 2), h rows;
   rows outside 0..h are clipped. Filled with gr_fill. */
static inline void gr_line(float *acc, int stride, int h, float x0, float y0, float x1, float y1)
{
    float dir, dxdy, x, t;
    int y, yend;
    if (y0 == y1)
        return;
    dir = 1.0f;
    if (y0 > y1) {
        dir = -1.0f;
        t = x0, x0 = x1, x1 = t;
        t = y0, y0 = y1, y1 = t;
    }
    dxdy = (x1 - x0) / (y1 - y0);
    x = x0;
    if (y0 < 0) {
        x -= y0 * dxdy;
        y0 = 0;
    }
    if (y1 > (float)h)
        y1 = (float)h;
    yend = -gr_floor(-y1);
    for (y = gr_floor(y0); y < yend; y++) {
        float top = (float)y > y0 ? (float)y : y0, bottom = (float)(y + 1) < y1 ? (float)(y + 1) : y1;
        float dy = bottom - top, xnext = x + dxdy * dy, d = dy * dir, a, b;
        float *row = acc + y * stride;
        int ai, bi;
        if (dy <= 0)
            continue;
        a = x < xnext ? x : xnext, b = x < xnext ? xnext : x;
        ai = gr_floor(a), bi = -gr_floor(-b);
        if (ai < 0)
            ai = 0;
        if (bi <= ai + 1) {
            float mid = 0.5f * (x + xnext) - (float)ai;
            row[ai] += d - d * mid;
            row[ai + 1] += d * mid;
        } else {
            float s = 1.0f / (b - a), af = a - (float)ai, bf = b - (float)bi + 1.0f;
            float a0 = 0.5f * s * (1.0f - af) * (1.0f - af), am = 0.5f * s * bf * bf;
            int i;
            row[ai] += d * a0;
            if (bi == ai + 2) {
                row[ai + 1] += d * (1.0f - a0 - am);
            } else {
                float a1 = s * (1.5f - af);
                row[ai + 1] += d * (a1 - a0);
                for (i = ai + 2; i < bi - 1; i++)
                    row[i] += d * s;
                row[bi - 1] += d * (1.0f - (a1 + (float)(bi - ai - 3) * s) - am);
            }
            row[bi] += d * am;
        }
        x = xnext;
    }
}

/* Coverage 0..255 of each pixel from the accumulated areas (nonzero fill of non-overlapping contours). */
static inline void gr_fill(const float *acc, int stride, int w, int h, unsigned char *out, int out_stride)
{
    int x, y;
    for (y = 0; y < h; y++) {
        float sum = 0;
        for (x = 0; x < w; x++) {
            float v;
            sum += acc[y * stride + x];
            v = sum < 0 ? -sum : sum;
            out[y * out_stride + x] = (unsigned char)(v >= 1.0f ? 255 : (int)(v * 255.0f + 0.5f));
        }
    }
}

/* A letter's step to the next, in whole screen px: the game's own advance (whole layout px) at this scale, a half
   rounded to the even neighbour, so the same letter at the same size always steps the same and halves round both
   ways along a line, which keeps its length. */
static inline int gr_step(int advance, float scale)
{
    float v = advance * scale;
    int i = gr_floor(v);
    float f = v - (float)i;
    return f > 0.5f ? i + 1 : f < 0.5f ? i : i + (i & 1);
}

/* The squish factor, at most `factor`, at which a line of these advances (whole layout px, spaces included) fits
   `box` layout px when every letter steps by gr_step at `scale` screen px per layout px. The steps round, so a line
   can come out wider than its exact width: the fit is on the steps the renderer draws, not on the exact width. */
static inline float gr_fit(const int *advance, int count, float box, float scale, float factor)
{
    int i, k, px;
    for (k = 0; k < 16; k++) {
        for (px = 0, i = 0; i < count; i++)
            px += gr_step(advance[i], scale * factor);
        if ((float)px <= box * scale)
            break;
        factor *= box * scale / (float)px * 0.999f;
    }
    return factor;
}

/* Softening of a drawn glyph, the same for every glyph so stems, baseline and spacing stay even: each pixel takes
   `side` (0..256 of 256) of its ink from each of its four neighbours' share, a tent blur across then down.
   0 leaves it sharp. The ink a glyph holds is kept (a pixel of padding round the glyph takes what spreads out). */
static inline void gr_soften(unsigned char *px, int w, int h, int stride, int side)
{
    int x, y, a, b, c;
    if (side <= 0)
        return;
    for (y = 0; y < h; y++) {
        unsigned char *row = px + y * stride;
        for (a = 0, b = row[0], x = 0; x < w; x++) {
            c = x + 1 < w ? row[x + 1] : 0;
            row[x] = (unsigned char)((a * side + b * (256 - 2 * side) + c * side + 128) >> 8);
            a = b, b = c;
        }
    }
    for (x = 0; x < w; x++)
        for (a = 0, b = px[x], y = 0; y < h; y++) {
            c = y + 1 < h ? px[(y + 1) * stride + x] : 0;
            px[y * stride + x] = (unsigned char)((a * side + b * (256 - 2 * side) + c * side + 128) >> 8);
            a = b, b = c;
        }
}

#endif
