#ifndef WOZMOD_TEXT_FIT_H
#define WOZMOD_TEXT_FIT_H

struct text_fit {
    double factor, shift;
};

/* The widest line of the run sets the factor; this line's own width sets its alignment shift. */
static inline struct text_fit text_fit(unsigned int run, unsigned int own, unsigned int box, unsigned int align, int enabled)
{
    struct text_fit fit = {1.0, 0.0};
    if (enabled && box && run > box) {
        fit.factor = (double)box / run;
        fit.shift = own * (1.0 - fit.factor) * (align == 1 ? 0.5 : align == 2 ? 1.0 : 0.0);
    }
    return fit;
}

/* Whether a glyph carries on the pen of the glyph before it: in the same text draw, on its row at its cell, at or past
   its end and no more than four cells on (a gap the game left for spaces). A glyph of another draw starts where the
   game put it, even on the same row: carried on, it would start where the last draw's (perhaps condensed) text ended. */
static inline int text_fit_carries(unsigned int draw, unsigned int last_draw, unsigned int y, unsigned int last_y,
                                   unsigned int w, unsigned int last_w, unsigned int h, unsigned int last_h,
                                   unsigned int x, unsigned int last_end)
{
    return draw == last_draw && y == last_y && w == last_w && h == last_h && x >= last_end && x - last_end <= 4 * w;
}

/* Where to put a pen so the ink it draws sits centred in a box: the ink runs from first to last (from the pen, along
   one axis); past room it condenses by *factor about the pen until it fills room. */
static inline double text_fit_centre(double box, double size, double room, double first, double last, double *factor)
{
    double ink = last - first;
    *factor = ink > room ? room / ink : 1.0;
    return box + (size - ink * *factor) / 2 - first * *factor;
}

/* The factor for names that share a lane with parts of fixed width (gaps, icons, names that cannot be
   condensed): only the names shrink, so the whole line ends inside room. */
static inline double text_fit_names(unsigned int names, unsigned int fixed, unsigned int room)
{
    if (!names || names + fixed <= room || fixed >= room)
        return 1.0;
    return (double)(room - fixed) / names;
}

/* A corner's X is a whole dword, (x + 2048) * 16 (stock 0x54A5A4): past 2048 px it is just larger. Y and depth
   share the next word. */
static inline unsigned int text_fit_x(unsigned int packed, double anchor, struct text_fit fit, double pixels)
{
    if (fit.factor == 1.0)
        return packed;
    return (unsigned int)(int)(anchor + ((int)packed - anchor) * fit.factor + fit.shift * pixels + 0.5);
}

static inline void text_fit_quad(unsigned int *quad, int four, double anchor, struct text_fit fit, double pixels)
{
    unsigned int i, n = four ? 4 : 2, stride = four ? 6 : 4;
    for (i = 0; i < n; i++)
        quad[12 + i * stride] = text_fit_x(quad[12 + i * stride], anchor, fit, pixels);
}

static inline int text_fit_latin(const char *s)
{
    const unsigned char *p = (const unsigned char *)s;
    if (!p)
        return 0;
    while (*p) {
        if (*p == 0xf0) {
            if (p[1] < 0x40 || p[1] > 0x59 || p[1] == 0x48)
                return 0;
            p++;
        } else if (*p >= 0x80) {
            if (p[0] != 0x84 || p[1] < 0x40 || p[1] > 0x44)
                return 0;
            p++;
        }
        p++;
    }
    return 1;
}

/* Icon codes the stock text draw (jump table at 0x549F48) handles; any other code writes through
   an uninitialised quad pointer. F0 48 takes two more bytes as its argument. */
static inline int text_icon_drawable(unsigned char code)
{
    return code >= 0x40 && code <= 0x59 && code != 0x49 && code != 0x4b && code != 0x56;
}

static inline unsigned int text_token_length(const unsigned char *p)
{
    if (*p == 0xf0 && p[1] == 0x48)
        return p[2] ? (p[3] ? 4 : 3) : 2;
    if (((*p >= 0x81 && *p <= 0x9f) || *p >= 0xe0) && p[1])
        return 2;
    return 1;
}

static inline int text_bad_icon(const unsigned char *p)
{
    return *p == 0xf0 && p[1] != 0x48 && !text_icon_drawable(p[1]);
}

/* s without the icons the stock draw cannot handle, copied to out; s itself when it has none. */
static inline const char *text_drop_bad_icons(const char *s, char *out, unsigned int size)
{
    const unsigned char *p = (const unsigned char *)s;
    unsigned int n = 0, k, bad = 0;
    if (!p || !size)
        return s;
    for (; *p; p += text_token_length(p))
        bad |= text_bad_icon(p);
    if (!bad)
        return s;
    for (p = (const unsigned char *)s; *p; p += k) {
        k = text_token_length(p);
        if (text_bad_icon(p))
            continue;
        if (n + k >= size)
            break;
        for (unsigned int i = 0; i < k; i++)
            out[n++] = (char)p[i];
    }
    out[n] = 0;
    return out;
}

#endif
