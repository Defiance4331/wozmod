/* Files the DLL checks at start, before it patches anything (wozmod is all or nothing): table.bin, the install record,
 * Lips.bin, Slots.bin and the pad prompt packages. Pure, so a native test reads the files the Python side writes. */
#ifndef WOZMOD_STARTUP_FILES_H
#define WOZMOD_STARTUP_FILES_H

static inline unsigned startup_u32(const unsigned char *p)
{
    return (unsigned)p[0] | (unsigned)p[1] << 8 | (unsigned)p[2] << 16 | (unsigned)p[3] << 24;
}

static inline const char *record_space(const char *p)
{
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')
        p++;
    return p;
}

/* table.bin (wozmod.runtime.english_runtime.table_blob), as u32 words t of size bytes: 1 when its slot mask is one
 * less than a power of two and its slots fit, and its name table's offset and count stay inside it. */
static inline int table_ok(const unsigned *t, unsigned size)
{
    return size >= 12 && !(size % 4) && !(t[0] & (t[0] + 1)) && 20 + 12ull * t[0] <= size && !(t[1] % 4)
        && t[1] <= size - 4 && t[t[1] / 4] <= (size - t[1]) / 4 - 1;
}

/* The install record, wozmod/install.json (wozmod.install._write_manifest): {"format": 1, "version": "...",
 * "files": {"<path from the game folder>": "<SHA-256>", ...}}. Later formats add sections that may hold "files" keys
 * of their own, before or after it. Where the top-level "files" object's first key starts, or 0. */
static inline const char *record_files(const char *json)
{
    static const char key[] = "\"files\"";
    const char *p = record_space(json), *end;
    unsigned depth = 0, i;
    if (*p != '{')
        return 0;
    while (*p) {
        if (*p == '{' || *p == '[')
            depth++;
        else if ((*p == '}' || *p == ']') && !--depth)
            return 0;  /* the record ended without one */
        if (*p != '"') {
            p++;
            continue;
        }
        for (end = p + 1; *end != '"'; end++)  /* past the string, escapes included, so its braces do not count */
            if (!*end || (*end == '\\' && !*++end))
                return 0;
        end++;
        for (i = 0; key[i] && p[i] == key[i]; i++)
            ;
        i = !key[i] && p + i == end;  /* this string is "files" */
        p = record_space(end);
        if (i && depth == 1 && *p == ':') {  /* a key of the record itself, not of a section inside it */
            p = record_space(p + 1);
            return *p == '{' ? p + 1 : 0;
        }
    }
    return 0;
}

/* The next file at *at, into out (size bytes, NUL-ended, '/' as written): 1, and *at moves past it; 0 at the object's
 * end; -1 where the record cannot be read (an escape, a name longer than out, a value that is not a string). */
static inline int record_next(const char **at, char *out, unsigned size)
{
    const char *p = record_space(*at);
    unsigned n = 0;
    if (*p == ',')
        p = record_space(p + 1);
    if (*p == '}')
        return 0;
    if (*p++ != '"')
        return -1;
    for (; *p != '"'; p++) {
        if (!*p || *p == '\\' || (unsigned char)*p < 0x20 || n + 1 >= size)
            return -1;
        out[n++] = *p;
    }
    out[n] = 0;
    p = record_space(p + 1);
    if (!n || *p != ':')
        return -1;
    p = record_space(p + 1);
    if (*p++ != '"')
        return -1;
    while (*p != '"')
        if (!*p || *p++ == '\\')
            return -1;
    p = record_space(p + 1);
    if (*p != ',' && *p != '}')
        return -1;
    *at = p;
    return 1;
}

/* voice/Lips.bin (wozmod.voices.build): u32 id count, then (u32 offset, u32 size) per id: 1 when the list fits. */
static inline int lips_ok(const unsigned char *data, unsigned size)
{
    return data && size >= 4 && startup_u32(data) <= (size - 4) / 8;
}

/* voice/Slots.bin (wozmod.voices.maps.slot_table): u32 row count, then 8 bytes per row: 1 when the rows fill it. */
static inline int slots_ok(const unsigned char *data, unsigned size)
{
    return data && size >= 4 && !((size - 4) % 8) && startup_u32(data) == (size - 4) / 8;
}

/* prompts-<family>.bin (wozmod.art.build.pad_prompts): entry 4079's texture package: u32 magic 0x33B59, item count,
 * alignment (16), 0; per item u32 offset / alignment and size; the items. 1 when each of the n slots is an item that
 * holds a whole G1T inside the data, as the game finds it (0x5AD170: offset word times the alignment). */
#define PACKAGE_MAGIC 0x33B59u
#define G1T_HEAD 28
static inline int package_items_ok(const unsigned char *data, unsigned size, const unsigned char *slots, unsigned n)
{
    unsigned count, i;
    if (!data || size < 16 || startup_u32(data) != PACKAGE_MAGIC || startup_u32(data + 8) != 16)
        return 0;
    count = startup_u32(data + 4);
    if (count > (size - 16) / 8)
        return 0;
    for (i = 0; i < n; i++) {
        unsigned unit, length, at;
        if (slots[i] >= count)
            return 0;
        unit = startup_u32(data + 16 + 8 * slots[i]), length = startup_u32(data + 20 + 8 * slots[i]), at = unit * 16;
        if (unit > size / 16 || length > size - at || length < G1T_HEAD)
            return 0;
        if (data[at] != 'G' || data[at + 1] != 'T' || data[at + 2] != '1' || data[at + 3] != 'G'
            || startup_u32(data + at + 8) > length)
            return 0;
    }
    return 1;
}

#endif
