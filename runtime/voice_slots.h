#ifndef WOZMOD_VOICE_SLOTS_H
#define WOZMOD_VOICE_SLOTS_H

/* Stage lines that share a voice id but not a take (decisions ledger, text row 23). Two stage lines can carry one
   stock string, the same text and the same @NNNN suffix, while the donor plays a different take at each line's
   trigger. Neither the voice id the suffix gives nor the text key can tell them apart, so the line that would hear
   its partner's take gets a voice id of its own past the stock range, and a row keyed with its stage and line.

   voice/Slots.bin (wozmod.voices.maps.slot_table): u32 row count, then per row u16 stage (the LINKDATA2 entry the
   stage loads, 0x4F06D0), u16 dialog line, u16 the stock voice id its speak plays, u16 the id in Voice.bin that
   voices it instead. A line spoken with both bank flags has a row per id. */
static inline unsigned int voice_slot(const unsigned char *table, unsigned int size, unsigned int stage,
                                      unsigned int line, unsigned int voice)
{
    unsigned int n, i;
    if (!table || size < 4)
        return voice;
    n = table[0] | table[1] << 8 | table[2] << 16 | (unsigned int)table[3] << 24;
    if (n > (size - 4) / 8)
        return voice;
    for (i = 0; i < n; i++) {
        const unsigned char *r = table + 4 + 8 * i;
        if ((r[0] | r[1] << 8) == stage && (r[2] | r[3] << 8) == line && (r[4] | r[5] << 8) == voice)
            return r[6] | r[7] << 8;
    }
    return voice;
}

static inline char *put_decimal(char *p, unsigned int v)
{
    char digits[10];
    unsigned int n = 0;
    do
        digits[n++] = (char)('0' + v % 10);
    while (v /= 10);
    while (n)
        *p++ = digits[--n];
    return p;
}

/* A spoken stage line's key with its stage and line: the text with its suffix, "@L", the stage, ".", the line, in
   decimal (wozmod.text.stock.stage_line_form). Returns key, or 0 when it would not fit in size bytes. */
static inline char *stage_line_key(const char *s, unsigned int stage, unsigned int line, char *key, unsigned int size)
{
    unsigned int n = 0;
    char *p;
    while (s[n])
        n++;
    if (n + 2 + 10 + 1 + 10 + 1 > size)
        return 0;
    for (p = key; *s; )
        *p++ = *s++;
    *p++ = '@', *p++ = 'L';
    p = put_decimal(p, stage);
    *p++ = '.';
    *put_decimal(p, line) = 0;
    return key;
}

#endif
