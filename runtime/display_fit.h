#ifndef WOZMOD_DISPLAY_FIT_H
#define WOZMOD_DISPLAY_FIT_H

/* The display sizes the Options list offers, each shown only when it fits the monitor the game is on. */
static const unsigned short display_sizes[][2] = {
    {1280, 720}, {1280, 800}, {1366, 768}, {1440, 900}, {1600, 900}, {1680, 1050}, {1920, 1080}, {1920, 1200},
    {2560, 1080}, {2560, 1440}, {2560, 1600}, {3200, 1800}, {3440, 1440}, {3840, 1080}, {3840, 1600},
    {3840, 2160}, {5120, 1440}, {5120, 2160},
};
#define DISPLAY_SIZES (sizeof display_sizes / sizeof *display_sizes)

/* The game packs a 2D y as 16 bits around a 2048 px origin, so its layout space stays under 2048 px high. */
#define DISPLAY_LAYOUT_MAX_H 2047
/* The widest HUD area, "21:9": 12:5, so every 21:9 size in the list (up to 3840x1600) uses its edges. */
#define DISPLAY_HUD_W 12
#define DISPLAY_HUD_H 5

struct display_fit {
    unsigned int layout_w, layout_h;  /* the game's 2D layout space (SCREEN_W/H): a 16:9 area */
    unsigned int scale;               /* back-buffer px per layout px */
    int x, y;                         /* where the layout area's corner sits in the back buffer */
    int hud;                          /* layout px an edge HUD element moves outwards (wider than 16:9 only) */
};

/* The centred 16:9 area of a w x h back buffer, laid out at full size or, past the 16-bit limit, at half. */
static inline struct display_fit display_fit(unsigned int w, unsigned int h)
{
    struct display_fit f;
    unsigned int area_w = w, area_h = h, hud_w;
    if ((unsigned long long)w * 9 >= (unsigned long long)h * 16)
        area_w = h * 16 / 9;
    else
        area_h = w * 9 / 16;
    f.scale = area_h > DISPLAY_LAYOUT_MAX_H ? 2 : 1;
    f.layout_w = area_w / f.scale, f.layout_h = area_h / f.scale;
    f.x = (int)(w - f.layout_w * f.scale) / 2, f.y = (int)(h - f.layout_h * f.scale) / 2;
    hud_w = h * DISPLAY_HUD_W / DISPLAY_HUD_H < w ? h * DISPLAY_HUD_W / DISPLAY_HUD_H : w;
    f.hud = hud_w > f.layout_w * f.scale ? (int)(hud_w - f.layout_w * f.scale) / 2 / (int)f.scale : 0;
    return f;
}

/* A 2D clip c = {x0, x1, y0, y1} in layout px, into output px for an out_w x out_h output: the whole layout means
   the whole output (an edge HUD element moved outward stays visible), anything smaller maps like a position. */
static inline void display_clip(const struct display_fit *f, unsigned int out_w, unsigned int out_h, unsigned int c[4])
{
    if ((int)c[0] <= 0 && (int)c[2] <= 0 && c[1] + 1 >= f->layout_w && c[3] + 1 >= f->layout_h) {
        c[0] = c[2] = 0, c[1] += out_w - f->layout_w, c[3] += out_h - f->layout_h;
        return;
    }
    c[0] = c[0] * f->scale + (unsigned int)f->x, c[1] = c[1] * f->scale + (unsigned int)f->x;
    c[2] = c[2] * f->scale + (unsigned int)f->y, c[3] = c[3] * f->scale + (unsigned int)f->y;
}

/* Add delta (layout px in 12.4) to every vertex X of a GIF packet of qwc quadwords: the XYZ2/XYZ3 entries of its
   reglist tags. Packed data (A+D registers such as clips) never moves. */
static inline void display_packet_shift(unsigned int *p, unsigned int qwc, int delta)
{
    unsigned int *end = p + qwc * 4, *q, lo, hi, nloop, flg, nreg, l, j, r;
    unsigned long long regs;
    while (p + 4 <= end) {
        lo = p[0], hi = p[1], nloop = lo & 0x7FFF, flg = hi >> 26 & 3, nreg = hi >> 28 ? hi >> 28 : 16;
        regs = (unsigned long long)p[3] << 32 | p[2], q = p + 4;
        if (flg == 1) {
            for (l = 0; l < nloop; l++)
                for (j = 0; j < nreg && q + 2 <= end; j++, q += 2)
                    if ((r = (unsigned int)(regs >> 4 * j) & 0xF) == 5 || r == 0xD)
                        q[0] += (unsigned int)delta;
            p = q;
        } else if (flg == 0) {
            p = q + 4 * nloop * nreg;
        } else if (flg == 2) {
            p = q + 4 * nloop;
        } else {
            break;
        }
    }
}

/* Whether w x h fits a monitor whose desktop is mw x mh: the list offers no size larger than the monitor. */
static inline int display_size_fits(unsigned int w, unsigned int h, unsigned int mw, unsigned int mh)
{
    return w <= mw && h <= mh;
}

/* Size i when it fits the monitor, else the listed size with the most pixels that does (the first, when none does). */
static inline unsigned int display_size_clamp(unsigned int i, unsigned int mw, unsigned int mh)
{
    unsigned int k, best = 0, most = 0;
    if (display_size_fits(display_sizes[i][0], display_sizes[i][1], mw, mh))
        return i;
    for (k = 0; k < DISPLAY_SIZES; k++)
        if (display_size_fits(display_sizes[k][0], display_sizes[k][1], mw, mh)
            && (unsigned int)display_sizes[k][0] * display_sizes[k][1] > most)
            best = k, most = (unsigned int)display_sizes[k][0] * display_sizes[k][1];
    return best;
}

/* The list index of w x h, or -1. */
static inline int display_size_index(unsigned int w, unsigned int h)
{
    unsigned int i;
    for (i = 0; i < DISPLAY_SIZES; i++)
        if (display_sizes[i][0] == w && display_sizes[i][1] == h)
            return (int)i;
    return -1;
}

#endif
