#ifndef WOZMOD_EVENT_LINE_H
#define WOZMOD_EVENT_LINE_H

/* A subtitle's key with its voice: the text, "@V" and the four-digit voice id (wozmod.text.stock.event_voice_form).
   A line whose text another line shares, but whose voice says other words, has a row keyed this way; the runtime
   tries it before the text alone. Returns key, or 0 when the id has more than four digits or the key would not fit
   in size bytes. */
static inline char *voice_key(const char *s, unsigned int voice, char *key, unsigned int size)
{
    unsigned int i, n = 0;
    if (voice > 9999)
        return 0;
    while (s[n])
        n++;
    if (n + 7 > size)
        return 0;
    for (i = 0; i < n; i++)
        key[i] = s[i];
    key[n] = '@', key[n + 1] = 'V';
    key[n + 2] = (char)('0' + voice / 1000 % 10), key[n + 3] = (char)('0' + voice / 100 % 10);
    key[n + 4] = (char)('0' + voice / 10 % 10), key[n + 5] = (char)('0' + voice % 10), key[n + 6] = 0;
    return key;
}

/* An event subtitle's key with its voice. The speak command (0x49E560) points the subtitle at the text inside its
   line record (u32 -1, u16 voice index, u16 size, text), and the draw and its measure look the text up there. The
   voice id is the story's base plus the index: 0x8F4 for the WO1 events (voice mode set), else 0xAF3, as the
   voice start (0x513720) adds them. Returns key, or 0 when s is not the text of a line record or the key would not
   fit in size bytes. */
static inline char *event_line_key(const char *s, unsigned int voice_mode, char *key, unsigned int size)
{
    const unsigned char *record = (const unsigned char *)s - 8;
    unsigned int index;
    if (record[0] != 0xFF || record[1] != 0xFF || record[2] != 0xFF || record[3] != 0xFF)
        return 0;
    index = record[4] | (unsigned int)record[5] << 8;
    if (index >= (voice_mode ? 0x1FFu : 0x150u))
        return 0;
    return voice_key(s, (voice_mode ? 0x8F4u : 0xAF3u) + index, key, size);
}

/* The line a WO1 still event (camp talk, LINKDATA1 4105) voices for line `line` of scene `scene`: the voice start
   (0x59CB92 to 0x59CBA3) takes one line less for lines 20 to 22 of scene 0x25, so line 20 repeats line 19's voice.
   Its voice id is 0x59B420(scene) + this. */
static inline unsigned int still_voice_line(unsigned int scene, unsigned int line)
{
    return scene == 0x25 && line - 20 <= 2 ? line - 1 : line;
}

#endif
