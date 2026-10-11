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

/* The factor for names that share a lane with parts of fixed width (gaps, icons, names that cannot be
   condensed): only the names shrink, so the whole line ends inside room. */
static inline double text_fit_names(unsigned int names, unsigned int fixed, unsigned int room)
{
    if (!names || names + fixed <= room || fixed >= room)
        return 1.0;
    return (double)(room - fixed) / names;
}

/* The packed 16-bit X unwrapped around the glyph's pen (both biased by 0x800 pixels). */
static inline double text_fit_unwrap(unsigned int packed, double pen)
{
    double original = packed & 0xffffu;
    while (original < pen - 32768)
        original += 65536;
    while (original >= pen + 32768)
        original -= 65536;
    return original;
}

static inline unsigned int text_fit_x(unsigned int packed, double anchor, struct text_fit fit, double pixels, double pen)
{
    double original = text_fit_unwrap(packed, pen), x;
    if (fit.factor == 1.0)
        return packed;
    x = anchor + (original - anchor) * fit.factor + fit.shift * pixels;
    unsigned int fixed = (unsigned int)(int)(x + 0.5);
    return (packed & 0xffff0000u) | (fixed & 0xffffu);
}

/* A corner the game placed at 2048 physical px or more carried into the word's high half (depth);
   the squish brings it back on screen, so that half is restored from a corner that did not carry,
   in this quad or an earlier one of the same draw (*clean: its high half | 1, or 0 for none). */
static inline void text_fit_quad(unsigned int *quad, int four, double anchor, struct text_fit fit, double pixels, double pen,
                                 unsigned int *clean)
{
    unsigned int i, n = four ? 4 : 2, stride = four ? 6 : 4, *x;
    if (fit.factor == 1.0)
        return;
    for (i = 0; i < n; i++)
        if (text_fit_unwrap(quad[12 + i * stride], pen) < 65536) {
            *clean = (quad[12 + i * stride] & 0xffff0000u) | 1;
            break;
        }
    for (i = 0; i < n; i++) {
        x = &quad[12 + i * stride];
        if (*clean && text_fit_unwrap(*x, pen) >= 65536)
            *x = (*x & 0xffffu) | (*clean & 0xffff0000u);
        *x = text_fit_x(*x, anchor, fit, pixels, pen);
    }
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
