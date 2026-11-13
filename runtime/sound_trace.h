#ifndef WOZMOD_SOUND_TRACE_H
#define WOZMOD_SOUND_TRACE_H
/* Test builds only (ENRT_TEST): a runtime.log line for every sound the game asks for and every effect it starts, so
   a trace gives each 3D effect's parameters and the event that asked for it (issue 11,
   work/handbacks/issue11-3d-audio.md). One line: "snd TAG ms ret ...", values in hex, floats as their bits.

   req  0x510840  sound request: emitter, flag, id (eax: row << 8 | entry), then the first six words on the stack
                  above the call that fall in .text (the request's callers, unfiltered: check each offline)
   fx   0x512EB0  direct play by id: id, then four arguments
   2d   0x5D5C40  2D effect start: group, bank, entry, slot, volume (of 16383); the slot's emitter and id
   3d   0x5D5CC0  3D effect start: group, bank, entry, slot; the slot's emitter and id; the source x, y, z, min and
                  max distance (the request at arg 4, from 0x512850); the listener x, y, z (0xBFD9C0, set once a tick
                  by 0x512100); the start gain (float 0x72D6A0)
   buf  0x67A960  DirectSound buffer creation (thiscall): its five arguments, the second the DSBCAPS flags
   tick 0x6770B0  the Ksl voice pool's play step (ecx the pool), once a Ksl tick before it plays the new voices: free
                  records; voices and buffers live (states 1, 2, 4) 2D, live 3D, new (state 3) 2D, new 3D; the tick
   v    the same, every SND_EVERY ticks: per voice of the pool's active list four words: record index | state << 8
        | 3D << 11 | mute << 12 | DSBSTATUS_PLAYING << 13 | buffers << 14 | priority << 16; entry | bank << 8 |
        (the head buffer's GetVolume + 10000) << 16; distance from the listener in units | sub entry << 16; min |
        max distance << 16. Six voices a line.
   rms  the same, once per buffer a v line first shows: record index, its group | bank << 8 | entry << 16 (+0x6C),
        the mean square of its samples over the whole buffer (float bits, full scale 1.0), its length in frames, and
        its WAVEFORMATEX channels | bits << 16 (the clip's loudness, for levels computed from the trace)
   snd_trace.on = 0 stops the lines. */

#define SND_SLOTS 0xC0CC48u     /* 100 sound slots of 0x3C bytes: emitter, id, lifetime, started; slot n at n - 1 */
#define SND_LISTENER 0xBFD9C0u  /* the first listener: position x, y, z */

static struct { char magic[16]; volatile u32 on, lines; } snd_trace = {"ENRT-SND-TRACE", 1};

static void snd_line(const char *tag, struct frame *f, const u32 *v, u32 n);
#define SND_EVERY 6u
#define SND_V(r, off) (*(u32 *)((u8 *)(r) + (off)))
#define SND_COM(obj, slot) ((*(void ***)(obj))[(slot) / 4])

/* The mean square of a buffer's 16-bit samples (full scale 1.0) and its frame count: the rms line. */
static void snd_rms(struct frame *f, u8 *r, u32 index)
{
    static void *seen[0x80];
    void *buf = *(void **)(r + 0xC), *p1 = 0, *p2 = 0;
    u32 n1 = 0, n2 = 0, fmt[5] = {0}, written = 0, v[5], i, frames;
    long hr;
    double sum = 0;
    union { float f; u32 u; } ms;
    if (index >= 0x80 || seen[index] == buf)
        return;
    seen[index] = buf;
    ((long (__stdcall *)(void *, void *, u32, u32 *))SND_COM(buf, 0x14))(buf, fmt, sizeof fmt, &written);
    if ((fmt[3] >> 16) != 16)  /* wBitsPerSample */
        return;
    hr = ((long (__stdcall *)(void *, u32, u32, void **, u32 *, void **, u32 *, u32))SND_COM(buf, 0x2C))(
        buf, 0, 0, &p1, &n1, &p2, &n2, 2);  /* DSBLOCK_ENTIREBUFFER */
    if (hr < 0)
        return;
    for (i = 0; i < n1 / 2; i++)
        sum += (double)((short *)p1)[i] * ((short *)p1)[i];
    ((long (__stdcall *)(void *, void *, u32, void *, u32))SND_COM(buf, 0x4C))(buf, p1, n1, p2, n2);
    frames = n1 / 2 / (fmt[0] >> 16 ? fmt[0] >> 16 : 1);
    ms.f = n1 ? (float)(sum / (n1 / 2) / (32768.0 * 32768.0)) : 0;
    v[0] = index, v[1] = r[0x6C] | r[0x6D] << 8 | r[0x6E] << 16, v[2] = ms.u, v[3] = frames;
    v[4] = (fmt[0] >> 16) | (fmt[3] >> 16) << 16;
    snd_line("rms", f, v, 5);
}

/* The pool at its play step: the tick line, and every SND_EVERY ticks the v lines. */
static void snd_pool(struct frame *f)
{
    static u32 ticks;
    u8 *pool = (u8 *)f->ecx, *r, *b;
    u32 count[8] = {0}, words[24], n = 0, kind, buffers, state, status, i;
    int vol;
    float pos[3], lo, hi, dx, dy, dz, d2, d;
    const float *ear = (const float *)SND_LISTENER;
    int snapshot = ticks++ % SND_EVERY == 0;
    for (r = *(u8 **)(pool + 0x5C); r; r = *(u8 **)r) {
        state = SND_V(r, 0x74);
        kind = SND_V(r, 0x18) >> 4 & 1;  /* DSBCAPS_CTRL3D */
        for (buffers = 0, b = r; b; b = *(u8 **)(b + 8))
            buffers++;
        if (state == 3)
            count[4 + 2 * kind]++, count[5 + 2 * kind] += buffers;
        else if (state)
            count[2 * kind]++, count[1 + 2 * kind] += buffers;
        if (!snapshot)
            continue;
        vol = -10000, status = 0, d = 0, lo = hi = 0;
        ((long (__stdcall *)(void *, int *))SND_COM(*(void **)(r + 0xC), 0x18))(*(void **)(r + 0xC), &vol);
        ((long (__stdcall *)(void *, u32 *))SND_COM(*(void **)(r + 0xC), 0x24))(*(void **)(r + 0xC), &status);
        if (kind && *(void **)(r + 0x10)) {
            void *b3 = *(void **)(r + 0x10);
            ((long (__stdcall *)(void *, float *))SND_COM(b3, 0x28))(b3, pos);
            ((long (__stdcall *)(void *, float *))SND_COM(b3, 0x20))(b3, &lo);
            ((long (__stdcall *)(void *, float *))SND_COM(b3, 0x1C))(b3, &hi);
            dx = pos[0] - ear[0], dy = pos[1] - ear[1], dz = pos[2] + ear[2];  /* DirectSound z is the game's negated */
            d2 = dx * dx + dy * dy + dz * dz;
            for (d = d2 > 1 ? d2 / 2 : 1, i = 0; i < 24; i++)  /* Newton's square root: no libm in the runtime */
                d = (d + d2 / d) / 2;
        }
        if (state == 4)
            snd_rms(f, r, (u32)(r - *(u8 **)(pool + 4)) / 0x88);
        words[n++] = (u32)(r - *(u8 **)(pool + 4)) / 0x88 | state << 8 | kind << 11 | (SND_V(r, 0x18) >> 17 & 1) << 12
                     | (status & 1) << 13 | (buffers < 3 ? buffers : 3) << 14 | (u32)*(u16 *)(r + 0x84) << 16;
        words[n++] = r[0x6D] | r[0x6C] << 8 | (u32)(vol + 10000) << 16;
        words[n++] = (d < 65535 ? (u32)(int)d : 65535) | (u32)r[0x6E] << 16;
        words[n++] = (lo < 65535 ? (u32)(int)lo : 65535) | (hi < 65535 ? (u32)(int)hi : 65535) << 16;
        if (n == 24)
            snd_line("v", f, words, n), n = 0;
    }
    if (n)
        snd_line("v", f, words, n);
    {
        u32 line[10] = {SND_V(pool, 0x58), count[0], count[1], count[2], count[3], count[4], count[5], count[6],
                        count[7], ticks};
        snd_line("tick", f, line, 10);
    }
}

static void snd_line(const char *tag, struct frame *f, const u32 *v, u32 n)
{
    char line[256], *p = line;
    u32 i;
    DWORD written;
    if (!snd_trace.on || runtime_log == INVALID_HANDLE_VALUE)
        return;
    *p++ = 's', *p++ = 'n', *p++ = 'd', *p++ = ' ';
    while (*tag)
        *p++ = *tag++;
    *p++ = ' ', p = put_hex(p, GetTickCount(), 8);
    *p++ = ' ', p = put_hex(p, f->ret, 8);
    for (i = 0; i < n; i++)
        *p++ = ' ', p = put_hex(p, v[i], 8);
    *p++ = '\r', *p++ = '\n';
    WriteFile(runtime_log, line, p - line, &written, 0);
    snd_trace.lines++;
}

static const u32 *snd_slot(u32 slot)
{
    static const u32 none[2] = {0xFFFFFFFF, 0xFFFFFFFF};
    return slot - 1 < 100 ? (const u32 *)(SND_SLOTS + (slot - 1) * 0x3C) : none;
}

static void snd_hook(u32 id, struct frame *f)
{
    u32 v[16], n = 0, i;
    switch (id) {
    case 128:
        v[n++] = f->arg[0], v[n++] = f->arg[1], v[n++] = f->eax;
        for (i = 2; i < 48 && n < 9; i++)
            if (f->arg[i] - 0x401000u < 0x2CAE8Bu)
                v[n++] = f->arg[i];
        snd_line("req", f, v, n);
        break;
    case 129:
        snd_line("fx", f, f->arg, 5);
        break;
    case 132:
        snd_line("buf", f, f->arg, 5);
        break;
    case 134:
        snd_pool(f);
        break;
    case 130: {
        const u32 *s = snd_slot(f->arg[3]);
        for (i = 0; i < 5; i++)
            v[n++] = f->arg[i];
        v[n++] = s[0], v[n++] = s[1];
        snd_line("2d", f, v, n);
        break;
    }
    case 131: {
        const u32 *s = snd_slot(f->arg[3]), *req = (const u32 *)f->arg[4], *ear = (const u32 *)SND_LISTENER;
        for (i = 0; i < 4; i++)
            v[n++] = f->arg[i];
        v[n++] = s[0], v[n++] = s[1];
        v[n++] = req[0], v[n++] = req[1], v[n++] = req[2], v[n++] = req[8], v[n++] = req[9];
        v[n++] = ear[0], v[n++] = ear[1], v[n++] = ear[2];
        v[n++] = *(const u32 *)0x72D6A0;  /* the start gain it is about to read */
        snd_line("3d", f, v, n);
        break;
    }
    }
}
#endif
