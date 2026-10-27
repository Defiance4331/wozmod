#ifndef WOZMOD_TRUE_RATE_H
#define WOZMOD_TRUE_RATE_H
/* True frame rate: the battle ticks N times a second and every per-tick step is scaled to real time.

   Pure parts, testable on the host (tests/test_true_rate.py): the 60 Hz phase, the constant transforms and the
   thunk builder. The runtime owns the game memory writes.

   The site catalogue (data/true_rate.toml, packed by wozmod.runtime.true_rate) has three kinds of entry:
   - operand: an instruction's 4-byte absolute address of a stock constant is pointed at a slot holding the constant
     transformed for the rate (scaled by 60/N, raised to the power 60/N, ...);
   - patch: the instruction bytes run as replacement code in a thunk, whenever the rate is not 60;
   - gate: the instruction bytes run as they are on the ticks that complete a 60 Hz period, and an alternative
     (often the same step by zero) on the others: a count of ticks keeps its stock meaning at any rate.
   At 60 every site holds its stock bytes. */

#ifndef TR_U8
typedef unsigned char tr_u8;
typedef unsigned int tr_u32;
#else
typedef u8 tr_u8;
typedef u32 tr_u32;
#endif

/* The rate and its 60 Hz phase: tick60 is 1 on exactly 60 of every N ticks, spread evenly (the phase gains 60 a tick
   and a period ends each time it passes N), so a count of ticks advances 60 times a second at any N. */
struct tr_clock {
    tr_u32 rate;    /* N, 60 at stock */
    tr_u32 phase;   /* 0 .. N - 1 */
    tr_u32 tick60;  /* this tick completes a 60 Hz period */
    tr_u32 ticks;   /* ticks since the rate was set */
    float step;     /* 60 / N: the share of a stock tick one tick covers */
    double step_d;
    tr_u32 periods;    /* 60 Hz periods completed since the rate was set */
    tr_u32 ring[8];    /* periods at the end of each of the last 8 ticks */
    /* The window of the call being made: a character the game updates every k ticks (k = 6 far away, else 1) covers
       the last k ticks. n: the 60 Hz periods in it (what a stock count of int(dt) ticks takes); once: 1 when a stock
       call every k ticks would have happened in it (what a count of calls takes). nf is n as a float. */
    tr_u32 n, once;
    float nf, lod;     /* lod: the call's far-update factor, 1 or 6 */
    /* The frame clock, for what the game steps once per drawn frame (the draw, the frame end, the menus over a
       battle) or once per skipped frame (the catch-up's stand-in steps): at stock a frame is 1/60 s, so each of
       those steps counts 1/60 s, also in a pause or a pass without a tick. A pass covers the real time since the
       previous one, which at N need not be 1/N s: the machine may present fewer passes than N, and frame skip makes
       each pass's ticks pay for its time. fphase: the 60 Hz periods of that time not yet whole; frame60: 1 when
       the pass completes one (and, inside the stand-ins, when a stand-in takes one); owed: the pass's further
       periods, for its stand-ins. */
    double fphase;
    tr_u32 frame60, owed;
    tr_u32 limit;      /* the present limiter's passes a second: the rate, or none when VSYNC already paces */
    /* The frame time: the ticks run since the last present, and the seconds of game time they cover. What the game
       steps once a drawn frame by the time the frame shows (particle effects) advances by it: k ticks after a
       catch-up of k, and nothing on a frame that repeats because its pass ran no tick. */
    tr_u32 frame_ticks;
    float frame_dt;
};

static void tr_set_rate(struct tr_clock *c, tr_u32 rate)
{
    tr_u32 i;
    c->rate = rate < 60 ? 60 : rate;
    c->phase = 0;
    c->tick60 = 1;
    c->ticks = 0;
    c->periods = 0;
    for (i = 0; i < 8; i++)
        c->ring[i] = 0;
    c->n = c->once = 1;
    c->nf = c->lod = 1.0f;
    c->fphase = 0;
    c->frame60 = 1;
    c->owed = 0;
    c->frame_ticks = 0;
    c->frame_dt = 0.0f;
    c->limit = c->rate;
    c->step_d = 60.0 / c->rate;
    c->step = (float)c->step_d;
}

static void tr_tick(struct tr_clock *c)
{
    c->phase += 60;
    c->tick60 = c->phase >= c->rate;
    if (c->tick60)
        c->phase -= c->rate, c->periods++;
    c->ticks++;
    c->ring[c->ticks & 7] = c->periods;
    c->n = c->once = c->tick60;
    c->nf = (float)c->n;
    c->frame_ticks++;
    c->frame_dt = (float)c->frame_ticks / c->rate;
}

/* A pass (the message pump, once a present), which covers seconds of real time since the previous one. The pass's
   once-a-frame steps (the draw, the frame end) see frame60 when that time completes a 60 Hz period; the frame end's
   fader takes the first such period. The catch-up's stand-ins for the frames it skips in the next pass take the
   rest (tr_skipped), which the ticks of that pass pay for: the fader steps once a period, as it steps once a stock
   tick, however many passes the machine presents. A pass that covers more periods than it has frames and stand-ins
   (under 60 presents a second) steps once: at stock too a drawn frame steps once. */
static void tr_frame(struct tr_clock *c, double seconds)
{
    int n;
    c->frame_ticks = 0;
    c->frame_dt = 0.0f;
    c->fphase += seconds * 60;
    n = (int)c->fphase;
    c->fphase -= n;
    c->frame60 = n > 0;
    c->owed = n > 0 ? (tr_u32)n - 1 : 0;
}

/* A frame the catch-up skipped: its stand-in steps run on one of the pass's owed periods, if one is left. */
static void tr_skipped(struct tr_clock *c)
{
    c->frame60 = c->owed > 0;
    c->owed -= c->frame60;
}

/* The window of a call covering the last k ticks (1 <= k <= 7). */
static void tr_window(struct tr_clock *c, tr_u32 k)
{
    c->lod = (float)k;
    tr_u32 before = c->ticks >= k ? c->ring[(c->ticks - k) & 7] : 0;
    c->n = c->periods - before;
    c->once = c->periods / k - before / k;
    c->nf = (float)c->n;
}

/* Transforms of a stock constant x for the step s = 60/N. */
enum { TR_SCALE = 1,   /* x s: a step per tick */
       TR_POW = 2,     /* x^s: a factor applied per tick (decay) */
       TR_EASE = 3,    /* 1 - (1 - x)^s: an easing share per tick */
       TR_INVERSE = 4, /* x / s: a count of ticks given as a float */
       TR_DECAY_SUM = 5 /* (1 - x^s) / ((1 - x) s): a speed that decays by x a stock tick moves this many times
                           its step-scaled distance a tick, so the distance at each 60 Hz period is the stock one */ };

static double tr_pow(double x, double y)
{
    /* x^y for x > 0 by exp(y ln x), series only: no libm in the runtime */
    double l = 0, t, term, sum;
    int k, n = 0;
    if (x <= 0)
        return 0;
    while (x > 1.5)
        x /= 2.718281828459045, n++;
    while (x < 0.5)
        x *= 2.718281828459045, n--;
    t = (x - 1) / (x + 1);  /* ln x = 2 atanh t */
    for (k = 1, term = t; k < 200; k += 2, term *= t * t)
        l += term / k;
    l = 2 * l + n;
    t = y * l;
    for (k = 1, term = 1, sum = 1; k < 200 && (term > 1e-18 || term < -1e-18); k++)
        term *= t / k, sum += term;
    return sum;
}

static double tr_transform(int kind, double x, double s)
{
    switch (kind) {
    case TR_SCALE:
        return x * s;
    case TR_POW:
        return x < 0 ? -tr_pow(-x, s) : tr_pow(x, s);
    case TR_EASE:
        return 1 - tr_pow(1 - x, s);
    case TR_INVERSE:
        return x / s;
    case TR_DECAY_SUM:
        return x == 1 ? 1 : (1 - tr_pow(x, s)) / ((1 - x) * s);
    }
    return x;
}

/* Code in the catalogue's pool is a stream of records: TR_BYTES n, then n bytes copied; TR_ABS k, the 4-byte
   address of symbol k; TR_REL va, a rel32 to the absolute VA (a call or jump out of the thunk); TR_END. */
enum { TR_END = 0, TR_BYTES = 1, TR_ABS = 2, TR_REL = 3 };
enum { TR_SYM_STEP, TR_SYM_TICK60, TR_SYM_STEP_D, TR_SYM_N, TR_SYM_ONCE, TR_SYM_NF, TR_SYM_CHARACTER,
       TR_SYM_RATE, TR_SYM_FRAME60, TR_SYM_LOD, TR_SYM_FRAME, TR_SYM_LIMIT, TR_SYM_FRAME_DT, TR_SYM_SLOT };
/* TR_SYM_FRAME: a pointer to tr_frame_step (cdecl: 1 before the catch-up's stand-ins for a skipped frame, 0 after) */  /* TR_SYM_CHARACTER: a pointer to tr_character (cdecl: float lod) */

static tr_u32 tr_get32(const tr_u8 *p)
{
    return p[0] | p[1] << 8 | p[2] << 16 | (tr_u32)p[3] << 24;
}

static void tr_put32(tr_u8 *p, tr_u32 v)
{
    p[0] = (tr_u8)v, p[1] = (tr_u8)(v >> 8), p[2] = (tr_u8)(v >> 16), p[3] = (tr_u8)(v >> 24);
}

/* Emits code records at out (whose address once installed is at): returns the bytes written, or 0 for a bad record.
   sym[k] is the address of symbol k. */
static tr_u32 tr_emit(tr_u8 *out, tr_u32 at, const tr_u8 *code, const tr_u32 *sym, tr_u32 syms)
{
    tr_u32 n = 0, i, len;
    for (;;) {
        switch (*code++) {
        case TR_END:
            return n;
        case TR_BYTES:
            len = *code++;
            for (i = 0; i < len; i++)
                out[n++] = *code++;
            break;
        case TR_ABS:
            if (*code >= syms)
                return 0;
            tr_put32(out + n, sym[*code++]);
            n += 4;
            break;
        case TR_REL:
            tr_put32(out + n, tr_get32(code) - (at + n + 4));
            code += 4, n += 4;
            break;
        default:
            return 0;
        }
    }
}

/* A gate's thunk at out (address at): run code when the symbol cond (tick60 or once) is set, alt otherwise, then
   jump back to va + len.
     cmp byte ptr [cond], 0 ; je alt ; code ; jmp back ; alt: alt ; jmp back
   Returns its length, 0 when a record is bad. */
static tr_u32 tr_gate(tr_u8 *out, tr_u32 at, tr_u32 cond, const tr_u8 *code, const tr_u8 *alt, tr_u32 back,
                      const tr_u32 *sym, tr_u32 syms)
{
    tr_u32 n = 0, k, m;
    if (cond >= syms)
        return 0;
    out[n++] = 0x80, out[n++] = 0x3D;
    tr_put32(out + n, sym[cond]), n += 4;
    out[n++] = 0x00;
    out[n++] = 0x0F, out[n++] = 0x84, n += 4;  /* je alt, filled below */
    k = n;
    if (!(m = tr_emit(out + n, at + n, code, sym, syms)) && *code != TR_END)
        return 0;
    n += m;
    out[n++] = 0xE9, tr_put32(out + n, back - (at + n + 4)), n += 4;
    tr_put32(out + k - 4, n - k);
    if (!(m = tr_emit(out + n, at + n, alt, sym, syms)) && *alt != TR_END)
        return 0;
    n += m;
    out[n++] = 0xE9, tr_put32(out + n, back - (at + n + 4)), n += 4;
    return n;
}

/* A patch's thunk: code, then jump back. */
static tr_u32 tr_patch(tr_u8 *out, tr_u32 at, const tr_u8 *code, tr_u32 back, const tr_u32 *sym, tr_u32 syms)
{
    tr_u32 n = tr_emit(out, at, code, sym, syms);
    if (!n && *code != TR_END)
        return 0;
    out[n++] = 0xE9, tr_put32(out + n, back - (at + n + 4)), n += 4;
    return n;
}
#endif
