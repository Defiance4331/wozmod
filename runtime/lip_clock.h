#ifndef WOZMOD_LIP_CLOCK_H
#define WOZMOD_LIP_CLOCK_H
/* An event line's mouths follow its voice as heard, not the event clock.

   Stock, a speak hands its lip block to the speaker's slot (0x4A1710) and the slot's lip time (0x89AA04) steps by half
   the frame step (0x4A1C40): 30 lip frames a second of event clock. The voice starts later (the voice frame 0x5D8590
   hands it to the sound engine, whose channel thread opens and prefills the stream before it plays), and a slow pass
   slows the event clock while the sound engine's stream thread keeps playing. So the mouths start 17 to 51 ms early
   and fall behind for the rest of a line after any slow pass.

   Here a slot handed a block by a speak that started a voice follows that voice, found by its id in the voice
   manager's two buffers (voice_play.h). Each pass, after the timeline step and before the poses read the time
   (hook 117, the tick chain's first call): while the voice is not heard yet the lip time holds at 0; once its stream
   plays, the lip time is 30 lip frames a second of the samples heard, up to the block's last frame; once the buffer
   holds another clip, or the slot another block, the stock stepper carries on from the last time written. Blocks
   with no voice (the face command, scene-start blocks) keep the event clock.

   Each line also measures, while heard, the lip time posed against a clock of its own: the voice's time as the wall
   clock has run while its stream played, from the samples played at its first heard pass. So a rate read wrong, a
   play position that does not move, or a slot the clock does not drive shows as drift, where the samples the lip
   time is made from would agree with it whatever they are. The gap is taken at the first heard pass (start gap)
   and the last while the voice still plays (end drift); a line where either is more than a lip frame is reported
   when it ends.

   Pure, so a host test drives it (tests/test_runtime_lip_clock.py); the runtime reads the game through lip_game. */
#include "voice_play.h"

#define LIP_FRAMES_A_SECOND 30.0
#define LIP_NO_BUFFER 2u
#define LIP_NO_VOICE VOICE_NO_ID
#define LIP_VOICE_PENDING 0x2u  /* a buffer's flags: loaded, then played (bit 3 cleared): the voice frame starts it */
#define LIP_STREAM_PLAYING 1u   /* the stream player's state (0x678780): 0 stopped, 1 playing, 2 paused, 3 failed */
#define LIP_STREAM_PAUSED 2u

/* What a voice buffer shows of a voice id. */
enum lip_buffer {
    LIP_GONE,       /* another clip, or none: its channel fell quiet (id -1), or it loaded another line */
    LIP_PLAY_WAITS, /* its play waits (voice_waits: for its load, or its start for the reader) or for the voice frame
                       to start it */
    LIP_STARTED     /* handed to the sound engine, until its channel falls quiet */
};

/* One voice buffer's line: the slot it drives. */
enum lip_phase {
    LIP_NONE,     /* no line on this buffer */
    LIP_WAITING,  /* not heard yet: its play waits, or its stream has not played */
    LIP_HEARD     /* its stream has played: the lip time follows the position, which holds at the end once it stops */
};

struct lip_line {
    enum lip_phase phase;
    unsigned int slot, id;   /* the lip slot, the voice id it follows */
    unsigned int scene;      /* the lip time array of the scene the slot is in (each scene allocates its own) */
    unsigned int rate;       /* the clip's sample rate, once known (0 before) */
    float start_gap, end_drift;  /* lip frames, posed less the voice's own clock: at the first heard pass, the latest */
    double voice, since;     /* that clock in lip frames; the wall clock at its last pass, or -1 while not playing */
};

/* What a pass reads from the game. */
struct lip_game {
    /* The state of the stream playing buffer's clip, and in *played the samples played since the clip's start. *rate
       is the line's rate so far: when 0, set it to the clip's, or leave it 0 while unknown. */
    unsigned int (*stream)(unsigned int buffer, unsigned int *played, unsigned int *rate);
    /* The running scene's lip time of slot, or 0 when it has no such slot; *length its block's frame count, or 0 when
       the stock stepper does not step it (the time is then only read). */
    float *(*slot)(unsigned int slot, unsigned int *length);
    void (*report)(const struct lip_line *);  /* a line that ended more than a lip frame off its voice */
    double (*now)(void);  /* the wall clock, in seconds */
};

struct lip_clock {
    struct lip_line line[2];  /* by voice buffer: a buffer plays one clip at a time */
};

/* What buffer b of the voice manager m shows of voice id, given the ids whose play waits on each buffer
   (voice_waits). A loaded buffer holds a clip not played yet, whatever its
   other bits say (it keeps a stale started bit). */
static inline enum lip_buffer lip_buffer(const unsigned char *m, unsigned int b, unsigned int id,
                                         const unsigned int waits[2])
{
    unsigned int flags = VOICE_FLAGS(m, b);
    if (waits[b] == id)
        return LIP_PLAY_WAITS;
    if (VOICE_ID(m, b) != id || flags & VOICE_LOADED)
        return LIP_GONE;
    return flags & LIP_VOICE_PENDING ? LIP_PLAY_WAITS : flags & VOICE_STARTED ? LIP_STARTED : LIP_GONE;
}

static inline float lip_abs(float x)
{
    return x < 0 ? -x : x;
}

static inline char *lip_put(char *p, const char *s)
{
    while (*s)
        *p++ = *s++;
    return p;
}

static inline char *lip_put_tenths(char *p, float v)
{
    char digits[12];
    float a = v < 0 ? -v : v;
    unsigned int t = a < 4e8f ? (unsigned int)(a * 10.0f + 0.5f) : 4000000000u, n = 0;
    if (v < 0 && t)
        *p++ = '-';
    do  /* at least two digits: the point goes before the last */
        digits[n++] = (char)('0' + t % 10);
    while ((t /= 10) || n < 2);
    while (n > 1)
        *p++ = digits[--n];
    *p++ = '.', *p++ = digits[0];
    return p;
}

/* A reported line's text, "lips off their voice: id B94, start gap 0.0, end drift -5.1 lip frames", into out (96
   bytes). */
static inline char *lip_text(const struct lip_line *l, char *out)
{
    static const char hex[] = "0123456789ABCDEF";
    char *p = lip_put(out, "lips off their voice: id ");
    int shift = 28;
    while (shift > 0 && !(l->id >> shift & 15))
        shift -= 4;
    for (; shift >= 0; shift -= 4)
        *p++ = hex[l->id >> shift & 15];
    p = lip_put_tenths(lip_put(p, ", start gap "), l->start_gap);
    p = lip_put_tenths(lip_put(p, ", end drift "), l->end_drift);
    *lip_put(p, " lip frames") = 0;
    return out;
}

static inline void lip_end(struct lip_line *l, const struct lip_game *g)
{
    if (l->phase == LIP_HEARD && (lip_abs(l->start_gap) > 1.0f || lip_abs(l->end_drift) > 1.0f) && g->report)
        g->report(l);
    l->phase = LIP_NONE;
}

/* A block handed to slot of scene (hook 71), by a speak that started voice id, or LIP_NO_VOICE. The slot's earlier
   line ends; the slot follows the voice when a buffer's play of it waits (a speak that started no voice, as for a line
   other than the one expected, leaves the event clock). */
static inline void lip_hand(struct lip_clock *c, const struct lip_game *g, const unsigned char *m, unsigned int slot,
                            unsigned int scene, unsigned int id, const unsigned int waits[2])
{
    unsigned int b, found = LIP_NO_BUFFER;
    for (b = 0; b < 2; b++) {
        if (c->line[b].phase != LIP_NONE && c->line[b].slot == slot)
            lip_end(&c->line[b], g);
        if (found == LIP_NO_BUFFER && id != LIP_NO_VOICE && lip_buffer(m, b, id, waits) == LIP_PLAY_WAITS)
            found = b;
    }
    if (found == LIP_NO_BUFFER)
        return;
    lip_end(&c->line[found], g);
    c->line[found] = (struct lip_line){LIP_WAITING, slot, id, scene, 0, 0.0f, 0.0f, 0.0, -1.0};
}

/* One pass of the scene whose lip time array is scene: each line's slot gets its voice's time. */
static inline void lip_pass(struct lip_clock *c, const struct lip_game *g, const unsigned char *m, unsigned int scene,
                            const unsigned int waits[2])
{
    unsigned int b;
    for (b = 0; b < 2; b++) {
        struct lip_line *l = &c->line[b];
        enum lip_buffer seen;
        unsigned int played = 0, length = 0, state = 0, first = 0, playing;
        float *time, heard;
        double now, voice;
        if (l->phase == LIP_NONE)
            continue;
        if ((seen = lip_buffer(m, b, l->id, waits)) == LIP_GONE) {
            lip_end(l, g);
            continue;
        }
        if (l->scene != scene)  /* another context's tick, or a later scene: not this line's slots */
            continue;
        if (seen == LIP_STARTED) {
            state = g->stream(b, &played, &l->rate);  /* stopped before it played: not opened yet; after: its end */
            if (l->phase == LIP_WAITING && (state == LIP_STREAM_PLAYING || state == LIP_STREAM_PAUSED))
                l->phase = LIP_HEARD, first = 1;
        }
        if (!(time = g->slot(l->slot, &length)))
            continue;
        if (l->phase == LIP_WAITING) {
            if (length)
                *time = 0.0f;
            continue;
        }
        if (!l->rate)
            continue;
        heard = (float)(LIP_FRAMES_A_SECOND * played / l->rate);
        if (length && heard > (float)(length - 1))
            heard = (float)(length - 1);
        if (length)
            *time = heard;
        if (state != LIP_STREAM_PLAYING && state != LIP_STREAM_PAUSED)  /* the clip has ended: the drift is its last */
            continue;
        now = g->now(), playing = state == LIP_STREAM_PLAYING;
        if (first)
            l->voice = LIP_FRAMES_A_SECOND * played / l->rate;
        else if (l->since >= 0.0)  /* played since the last pass: paused time does not count */
            l->voice += LIP_FRAMES_A_SECOND * (now - l->since);
        l->since = playing ? now : -1.0;
        voice = length && l->voice > length - 1 ? length - 1 : l->voice;
        l->end_drift = (float)(*time - voice);
        if (first)
            l->start_gap = l->end_drift;
    }
}

#endif
