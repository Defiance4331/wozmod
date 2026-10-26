/* The guard list (wozmod.runtime.guards, exported as wozmod_guards): which stock bytes each feature needs unchanged.
 * Pure, so a native test runs it over a file image; the DLL runs it over the game as loaded, before any patch. wozmod
 * is all or nothing: one differing guard of any feature and the DLL patches nothing. The features only name what
 * another mod changed.
 *
 * Blob: "WOZGUARD", u16 version, u16 feature count, u32 TimeDateStamp and SizeOfImage of version 1.00, u32 guard
 * count; per feature u8 length + name; per guard u8 feature, u32 VA, u16 length and the stock bytes. All little
 * endian, unaligned. */
#ifndef FEATURE_GUARDS_H
#define FEATURE_GUARDS_H

#define GUARD_FEATURES_MAX 32

static unsigned guard_u16(const unsigned char *p)
{
    return (unsigned)p[0] | (unsigned)p[1] << 8;
}

static unsigned guard_u32(const unsigned char *p)
{
    return (unsigned)p[0] | (unsigned)p[1] << 8 | (unsigned)p[2] << 16 | (unsigned)p[3] << 24;
}

/* The running game's bytes at a VA. */
typedef const unsigned char *(*guard_at)(unsigned va);

static int guard_blob_ok(const unsigned char *blob)
{
    static const char magic[8] = {'W', 'O', 'Z', 'G', 'U', 'A', 'R', 'D'};
    unsigned i;
    for (i = 0; i < 8; i++)
        if (blob[i] != (unsigned char)magic[i])
            return 0;
    return guard_u16(blob + 8) == 3 && guard_u16(blob + 10) <= GUARD_FEATURES_MAX;
}

/* 1 when the image's PE header (at base) has version 1.00's build stamp and image size; *stamp gets its stamp. */
static int guard_build(const unsigned char *blob, const unsigned char *base, unsigned *stamp)
{
    const unsigned char *nt = base + guard_u32(base + 0x3C);
    *stamp = guard_u32(nt + 8);
    return nt[0] == 'P' && nt[1] == 'E' && !nt[2] && !nt[3]
        && *stamp == guard_u32(blob + 12) && guard_u32(nt + 24 + 56) == guard_u32(blob + 16);
}

/* Copy feature f's name, NUL-ended, into out (size bytes). */
static void guard_name(const unsigned char *blob, unsigned f, char *out, unsigned size)
{
    const unsigned char *p = blob + 24;
    unsigned i, n;
    for (i = 0; i < f; i++)
        p += 1 + p[0];
    for (n = 0; n < p[0] && n + 1 < size; n++)
        out[n] = (char)p[1 + n];
    out[n] = 0;
}

/* What another mod changed: one call per guard whose bytes differ from the game, with its feature and VA. */
typedef void (*guard_refuse)(unsigned feature, unsigned va);

/* Compare every guard with the game; return how many differ (0: every one is stock), calling refuse for each. */
static unsigned guards_differ(const unsigned char *blob, guard_at at, guard_refuse refuse)
{
    unsigned features = guard_u16(blob + 10), count = guard_u32(blob + 20), differ = 0, f, i, j;
    const unsigned char *p = blob + 24;
    for (f = 0; f < features; f++)
        p += 1 + p[0];
    for (i = 0; i < count; i++) {
        unsigned feature = p[0], va = guard_u32(p + 1), n = guard_u16(p + 5), same = 1;
        const unsigned char *stock = p + 7, *live = at(va);
        p = stock + n;
        for (j = 0; j < n && same; j++)
            same = live[j] == stock[j];
        if (!same)
            differ++, refuse(feature, va);
    }
    return differ;
}

#endif
