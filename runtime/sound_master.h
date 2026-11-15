#ifndef WOZMOD_SOUND_MASTER_H
#define WOZMOD_SOUND_MASTER_H

/* The master stage on the finished mix: a gain that brings normal play to -16 LUFS, then a lookahead true-peak limiter
   that holds the output at or under -1 dBTP. The 13 dB trim on every sound (sound_headroom.h) stays the mix's headroom,
   so the sum reaching this stage stays under full scale; the gain lifts it after the sum, where nothing clips.
   Measured before this stage: normal play -21.6 LUFS integrated and -2.6 dBTP (stages 82 and 35 joined).
   The runtime runs it on each output stream at the point the stream leaves the process (english_runtime.c,
   sound_attach). Pure functions over interleaved frames, so the host tests compile it as it is. */
#define MASTER_GAIN_DB 5.6
#define MASTER_CEILING_DBTP (-1.0)

#define MASTER_LOOKAHEAD_US 2000  /* the attack: the gain falls across this much, ahead of the peak */
#define MASTER_RELEASE_MS 120     /* the time constant the gain recovers with after a peak */
#define MASTER_TAPS 32            /* interpolation taps per phase of the 4x true-peak detector */
#define MASTER_SPAN 8             /* frames either side of a peak held at its gain or under: the detector's main lobe */
#define MASTER_CHANNELS 32        /* channels a frame holds at most */
#define MASTER_WINDOW 512         /* lookahead frames at most: 2 ms at 256 kHz */
#define MASTER_POOL 8192          /* delay line samples (frames x channels) */

/* The 4x true-peak detector's three in-between phases (ITU-R BS.1770-4 Annex 2 measures true peak at 4x): the value at
   a quarter, a half and three quarters of the way from frame t-16 to t-15, from frames t-31..t. A Kaiser-windowed sinc
   (beta 5, 32 taps a phase), each phase scaled so its gain is at least 1 up to 0.45 of the sample rate (21.6 kHz at
   48 kHz): in that band it never reads a peak low, and reads it at most 0.07 dB high. Above it, it reads low: white
   noise to 24 kHz passes the ceiling by up to 0.35 dB, where game sound, with a few millionths of its energy above
   21.6 kHz (stages 82 and 35), does not. The fourth phase is frame t-16 itself. */
static const float master_phase[3][MASTER_TAPS] = {
    {-0.000901135447f, 0.00161270465f, -0.00260391755f, 0.00393821262f, -0.00569035004f, 0.00795184851f,
     -0.010840826f, 0.0145199738f, -0.01923043f, 0.025359169f, -0.03358428f, 0.0452259456f, -0.0632454671f,
     0.0959074518f, -0.178017073f, 0.901826509f, 0.299299676f, -0.125495362f, 0.0767584818f, -0.0531291977f,
     0.038851758f, -0.0291488336f, 0.0220845452f, -0.0167266715f, 0.0125689571f, -0.00930907053f, 0.00675082827f,
     -0.00475681127f, 0.00322382082f, -0.00206961251f, 0.00122557971f, -0.000632727687f},
    {-0.00107758111f, 0.00199958002f, -0.00329739334f, 0.00505767204f, -0.00738180846f, 0.010392571f, -0.0142461286f,
     0.0191539184f, -0.0254234491f, 0.0335382058f, -0.0443260196f, 0.0593529018f, -0.0819923735f, 0.121060358f,
     -0.208986106f, 0.638017792f, 0.638017792f, -0.208986106f, 0.121060358f, -0.0819923735f, 0.0593529018f,
     -0.0443260196f, 0.0335382058f, -0.0254234491f, 0.0191539184f, -0.0142461286f, 0.010392571f, -0.00738180846f,
     0.00505767204f, -0.00329739334f, 0.00199958002f, -0.00107758111f},
    {-0.000632727687f, 0.00122557971f, -0.00206961251f, 0.00322382082f, -0.00475681127f, 0.00675082827f,
     -0.00930907053f, 0.0125689571f, -0.0167266715f, 0.0220845452f, -0.0291488336f, 0.038851758f, -0.0531291977f,
     0.0767584818f, -0.125495362f, 0.299299676f, 0.901826509f, -0.178017073f, 0.0959074518f, -0.0632454671f,
     0.0452259456f, -0.03358428f, 0.025359169f, -0.01923043f, 0.0145199738f, -0.010840826f, 0.00795184851f,
     -0.00569035004f, 0.00393821262f, -0.00260391755f, 0.00161270465f, -0.000901135447f},
};

enum master_kind { MASTER_NONE, MASTER_F32, MASTER_S16, MASTER_S24, MASTER_S32 };

/* A stream's sample format, read from its WAVEFORMATEX. kind MASTER_NONE: the stage cannot run on it. */
struct master_format {
    enum master_kind kind;
    unsigned int tag, bits, channels, rate, block;
};

static inline unsigned int master_u16(const unsigned char *p) { return p[0] | p[1] << 8; }
static inline unsigned int master_u32(const unsigned char *p)
{
    return master_u16(p) | (unsigned int)master_u16(p + 2) << 16;
}

/* The format of a WAVEFORMATEX (or WAVEFORMATEXTENSIBLE, whose SubFormat GUID carries the tag) at w: 32-bit float, or
   integer PCM in 16, 24 or 32 bits (24 valid bits in a 32-bit container are the container's high bits, so it runs as
   32), 1 to MASTER_CHANNELS channels, the block one frame. Anything else is MASTER_NONE. */
static inline struct master_format master_format_of(const unsigned char *w)
{
    static const unsigned char ks_tail[12] = {0x00, 0x00, 0x10, 0x00, 0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71};
    struct master_format f;
    unsigned int i;
    f.kind = MASTER_NONE, f.tag = master_u16(w), f.channels = master_u16(w + 2), f.rate = master_u32(w + 4);
    f.block = master_u16(w + 12), f.bits = master_u16(w + 14);
    if (f.tag == 0xFFFE) {  /* WAVE_FORMAT_EXTENSIBLE: cbSize 22, the valid bits, the channel mask, the SubFormat */
        if (master_u16(w + 16) < 22)
            return f;
        for (i = 0; i < 12; i++)
            if (w[28 + i] != ks_tail[i])
                return f;
        f.tag = master_u32(w + 24);  /* KSDATAFORMAT_SUBTYPE_PCM and _IEEE_FLOAT are their tag in this GUID form */
    }
    if (!f.channels || f.channels > MASTER_CHANNELS || !f.rate || f.block != f.channels * (f.bits / 8) || f.bits % 8)
        return f;
    if (f.tag == 3 && f.bits == 32)
        f.kind = MASTER_F32;
    else if (f.tag == 1)
        f.kind = f.bits == 16 ? MASTER_S16 : f.bits == 24 ? MASTER_S24 : f.bits == 32 ? MASTER_S32 : MASTER_NONE;
    return f;
}

/* e^x: 2^k e^r with |r| <= ln 2 / 2, r by its series (no libm in the runtime). */
static inline double master_exp(double x)
{
    const double ln2 = 0.69314718055994531;
    int k = (int)(x / ln2 + (x < 0 ? -0.5 : 0.5)), i;
    double r = x - k * ln2, term = 1, sum = 1;
    for (i = 1; i < 20; i++)
        term *= r / i, sum += term;
    for (; k > 0; k--)
        sum *= 2;
    for (; k < 0; k++)
        sum *= 0.5;
    return sum;
}

/* The amplitude of a level in dB. */
static inline double master_amplitude(double db)
{
    return master_exp(db * 0.11512925464970229);  /* ln 10 / 20 */
}

/* One stream's limiter. Each frame goes in times the gain and comes out D = L + S + 15 frames later (L the lookahead in
   frames, S the span, 15 the detector's own delay), times the limiter's gain g. g is 1 unless a true peak near that
   frame, read on the gained signal, would pass the ceiling; then g is at most ceiling / peak:
   - need(n): ceiling / the highest true peak within a frame of frame n, over every channel (linked), or 1.
   - hold(n): the least need over the last L + 2S frames.
   - envelope(n): drops to hold at once, rises back to it with the release time constant; never above hold.
   - g(n): the mean of the last L envelopes. Each of those holds covers the frame leaving now and S frames either side
     of it, so g is at most every one of their needs: the gain falls in a straight line across the lookahead and is at
     its lowest over a peak's whole main lobe, so the detector's in-between values hold for the output too (with S = 0,
     noise to 20 kHz with 13 dB of reduction came out 0.01 dB over). With no reduction in the window g is exactly 1,
     and the output exactly the input times the gain. */
struct master {
    unsigned int channels, lookahead, line_frames, line_at, ramp_at, now;
    unsigned int head, count;        /* the hold queue: frames whose need may still be the least, in order */
    float gain, ceiling, half_last;  /* half_last: the peak from frame n-1 to n, for the next frame's need */
    double release, envelope, reduced;  /* reduced: the sum of 1 - envelope over the ramp */
    unsigned int when[MASTER_WINDOW];
    float need[MASTER_WINDOW];
    float ramp[MASTER_WINDOW];
    float line[MASTER_POOL];  /* the gained frames, the last line_frames of them */
};

/* 0 when the stream does not fit (a delay line shorter than the detector's taps, a hold over MASTER_WINDOW, or a delay
   line over MASTER_POOL): it then passes through untouched. */
static inline int master_init(struct master *m, unsigned int rate, unsigned int channels)
{
    unsigned int i, lookahead = (unsigned int)(((unsigned long long)rate * MASTER_LOOKAHEAD_US + 500000) / 1000000);
    if (!channels || channels > MASTER_CHANNELS || lookahead + MASTER_SPAN < MASTER_TAPS / 2
        || lookahead + 2 * MASTER_SPAN > MASTER_WINDOW
        || (lookahead + MASTER_SPAN + MASTER_TAPS / 2) * channels > MASTER_POOL)
        return 0;
    m->channels = channels, m->lookahead = lookahead, m->line_frames = lookahead + MASTER_SPAN + MASTER_TAPS / 2;
    m->line_at = m->ramp_at = m->now = m->head = m->count = 0;
    m->gain = (float)master_amplitude(MASTER_GAIN_DB), m->ceiling = (float)master_amplitude(MASTER_CEILING_DBTP);
    m->half_last = 0, m->envelope = 1, m->reduced = 0;
    m->release = 1 - master_exp(-1000.0 / ((double)MASTER_RELEASE_MS * rate));
    for (i = 0; i < lookahead; i++)
        m->ramp[i] = 1;
    for (i = 0; i < m->line_frames * channels; i++)
        m->line[i] = 0;
    return 1;
}

static inline float master_abs(float v) { return v < 0 ? -v : v; }

/* One frame through the stage, in place: x[channels] in, the frame D frames before it out. */
static inline void master_frame(struct master *m, float *x)
{
    unsigned int n = m->channels, frames = m->line_frames, at = m->line_at, out = at + 1 == frames ? 0 : at + 1;
    unsigned int c, j, p, tap[MASTER_TAPS], back;
    float *line = m->line, peak = 0, both, need = 1, ramp_old, ramp_new, g;
    double hold;
    for (j = 0, p = at + frames - (MASTER_TAPS - 1); j < MASTER_TAPS; j++, p++)  /* frames t-31..t, oldest first */
        tap[j] = (p < frames ? p : p - frames) * n;
    for (c = 0; c < n; c++)
        line[at * n + c] = x[c] * m->gain;
    for (c = 0; c < n; c++) {
        float v = master_abs(line[tap[MASTER_TAPS / 2 - 1] + c]);  /* frame t-16 */
        peak = v > peak ? v : peak;
        for (p = 0; p < 3; p++) {
            float sum = 0;
            for (j = 0; j < MASTER_TAPS; j++)
                sum += master_phase[p][j] * line[tap[j] + c];
            v = master_abs(sum);
            peak = v > peak ? v : peak;
        }
    }
    both = peak > m->half_last ? peak : m->half_last;  /* frame t-16: the peaks on either side of it */
    m->half_last = peak;
    if (both > m->ceiling)
        need = m->ceiling / both;

    while (m->count && m->need[back = (m->head + m->count - 1) % MASTER_WINDOW] >= need)
        m->count--;
    back = (m->head + m->count++) % MASTER_WINDOW;
    m->when[back] = m->now, m->need[back] = need;
    if (m->now - m->when[m->head] >= m->lookahead + 2 * MASTER_SPAN)
        m->head = (m->head + 1) % MASTER_WINDOW, m->count--;
    m->now++;
    hold = m->need[m->head];

    /* double: in float the release would stall short of 1 once its step rounds away; in double the ramp's float
       reaches exactly 1 */
    m->envelope = hold < m->envelope ? hold : m->envelope + (hold - m->envelope) * m->release;

    ramp_old = m->ramp[m->ramp_at], ramp_new = (float)m->envelope;
    m->ramp[m->ramp_at] = ramp_new;
    m->ramp_at = m->ramp_at + 1 == m->lookahead ? 0 : m->ramp_at + 1;
    m->reduced += (1.0 - ramp_new) - (1.0 - ramp_old);
    g = (float)(1 - m->reduced / m->lookahead);  /* its float is exactly 1 while every envelope is */

    for (c = 0; c < n; c++)
        x[c] = line[out * n + c] * g;
    m->line_at = out;
}

/* frames frames of a stream's buffer through the stage, in place, in the stream's format: little-endian float32, or
   integers of 2, 3 or 4 bytes, read as their top bits of a 32-bit integer (so the sign comes with them) and written
   back rounded to their own step. */
static inline void master_run(struct master *m, const struct master_format *f, unsigned char *data, unsigned int frames)
{
    float x[MASTER_CHANNELS];
    unsigned int i, c, b, n = f->channels, bytes = f->bits / 8;
    double step = (double)(1u << (f->bits - 1));  /* full scale in the format's integers */
    for (i = 0; i < frames; i++, data += f->block) {
        for (c = 0; c < n; c++) {
            unsigned int u = 0;
            for (b = 0; b < bytes; b++)
                u |= (unsigned int)data[bytes * c + b] << (8 * b + 32 - f->bits);
            if (f->kind == MASTER_F32)
                __builtin_memcpy(&x[c], &u, 4);
            else
                x[c] = (float)((int)u / 2147483648.0);
        }
        master_frame(m, x);
        for (c = 0; c < n; c++) {
            unsigned int u;
            if (f->kind == MASTER_F32)
                __builtin_memcpy(&u, &x[c], 4);
            else {
                double v = x[c] * step;
                v = v < 0 ? -(double)(long long)(0.5 - v) : (double)(long long)(v + 0.5);
                u = (unsigned int)(int)(v > step - 1 ? step - 1 : v < -step ? -step : v);
            }
            for (b = 0; b < bytes; b++)
                data[bytes * c + b] = (unsigned char)(u >> 8 * b);
        }
    }
}

/* A stream's ReleaseBuffer: frames frames written at data, and the call's flags. A block flagged silent
   (AUDCLNT_BUFFERFLAGS_SILENT, 2: the stream ignores what the buffer holds) still has to carry the delay line's
   tail, so it becomes zeros run through the stage and goes on without the flag. Returns the flags to pass on. */
#define MASTER_SILENT 2u
static inline unsigned int master_release(struct master *m, const struct master_format *f, unsigned char *data,
                                          unsigned int frames, unsigned int flags)
{
    if (flags & MASTER_SILENT)
        __builtin_memset(data, 0, (unsigned long)frames * f->block), flags &= ~MASTER_SILENT;
    master_run(m, f, data, frames);
    return flags;
}

#endif
