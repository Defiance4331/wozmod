#ifndef WOZMOD_VOICE_PLAY_H
#define WOZMOD_VOICE_PLAY_H

/* The game's voice manager ([0xE00404]): two clip buffers of 0x40 bytes from +0xC (take size at +4, voice id at +0x10,
   flags at +0x1C), and at +0xB0 the buffer a load waits for while that buffer still plays (2 for none), its voice id at
   +0xB4. A line's clip is loaded while the line before it plays, into the buffer the line before that played: each
   line's start (0x513720, with the line to play and the line to load next) plays the buffer last loaded, switches
   buffers and loads the next line's clip into the other. A load sets the buffer's bit 3, and play (0x5D7BF0) starts
   only such a buffer, doing nothing otherwise. A load that has to wait is made by the voice frame (0x5D8590) once its
   buffer falls quiet. Every load is a read on the one archive reader, which takes no new read until the last one is
   reaped.

   Two stock gaps lose a line when the reader is busy, and this file closes both by waiting for it:
   - A start that finds the reader busy (0x5D9AE0(0) false) does nothing and returns 0. An event speak (0x4A6BC0) then
     ends the voice of the rest of its sub-scene, and a scene start (0x4B2AA0, 0x4B29B0) leaves the first clip
     unloaded. So such a start waits, and the voice frame makes it once the reader is free, exactly as the stock start
     would have run then (its own free check passes). A newer start while one waits takes over its play, as that
     line's subtitle is already gone, and keeps its load, which its own play needs.
   - An English line longer than the Japanese timing can keep its buffer from falling quiet until the line after next
     has begun (stage 93's ending: line 137, then line 139). That line's play then finds nothing loaded and is lost,
     and its load, made after the line start loaded the next line, finds the reader unreaped and is dropped by it. So
     at each voice frame's start a waiting load is held back while the reader is busy, as a line start would not load
     then, and a play that came before its load is made once the load is: a frame after it, as a stock play, which the
     frame then prepares once read.
   The voice stop (0x5D82A0: both buffers stopped, at a scene's end among others) ends what still waits to play: had it
   played on time, the stop would have ended it. */
#define VOICE_SIZE(m, slot) (*(volatile unsigned int *)((m) + 0xC + 0x40 * (slot) + 4))
#define VOICE_ID(m, slot) (*(volatile unsigned int *)((m) + 0xC + 0x40 * (slot) + 0x10))
#define VOICE_FLAGS(m, slot) (*(volatile unsigned int *)((m) + 0xC + 0x40 * (slot) + 0x1C))
#define VOICE_WAITING(m) (*(volatile unsigned int *)((m) + 0xB0))
#define VOICE_WAITING_ID(m) (*(volatile unsigned int *)((m) + 0xB4))
#define VOICE_LOADED 0x8u
#define VOICE_STARTED 0x4u  /* handed to the sound engine; the voice frame clears it, and the id, once its channel is quiet */
#define VOICE_NONE 2u

/* A voice start's lines: the one it plays and the one it loads next, each at or above the voice mode's end marker for
   none (the start compares them signed), and the voice frame it first waited at. */
struct voice_start {
    unsigned int play, next, since;
};

struct voice_play {
    unsigned int slot, id, arg[3];  /* slot VOICE_NONE: nothing kept */
    unsigned int held, held_id;     /* a waiting load held back from this frame (held VOICE_NONE: none) */
    struct voice_start wait[2];     /* starts waiting for the reader, oldest first: at most a load, then the newest */
    unsigned int waits, frame;      /* how many wait; voice frames so far */
};

/* The stock routines the rule calls, and the runtime log: idle is 0x5D0D20 (reaps a finished read; true when the
   reader is free), start is 0x513720 as stock (0, and nothing done, while the reader is busy). */
struct voice_stock {
    int (*idle)(void);
    int (*start)(unsigned int play, unsigned int next);
    void (*note)(const char *what, const struct voice_start *s, unsigned int frames);
};

/* At a voice start (0x513720) of play and next, end the voice mode's end marker: start it, or have it wait. The game
   is told it started either way (0x513720 returns 1). */
static inline void voice_start(struct voice_play *v, unsigned int play, unsigned int next, unsigned int end,
                               const struct voice_stock *stock)
{
    if (!v->waits) {
        if (stock->start(play, next))
            return;
    } else {
        const struct voice_start *last = &v->wait[v->waits - 1];
        if (last->play < end)
            stock->note("voice start dropped for a newer one while waiting for the archive reader,", last,
                        v->frame - last->since);
        v->wait[0].play = end, v->wait[0].next = last->next, v->wait[0].since = last->since, v->waits = 1;
    }
    v->wait[v->waits].play = play, v->wait[v->waits].next = next, v->wait[v->waits].since = v->frame;
    v->waits++;
}

/* The voice id whose play waits on each buffer, or VOICE_NO_ID: the kept play's on its buffer, and a waiting start's
   line (base + its play) on the buffer it will play. Each start plays the current buffer ([0xC0E594]) and then switches
   to the other, so the waiting starts, made in turn, play from current alternately. */
#define VOICE_NO_ID 0xFFFFFFFFu
static inline void voice_waits(const struct voice_play *v, unsigned int current, unsigned int base, unsigned int end,
                               unsigned int ids[2])
{
    unsigned int i;
    ids[0] = ids[1] = VOICE_NO_ID;
    if (v->slot != VOICE_NONE)
        ids[v->slot] = v->id;
    for (i = 0; i < v->waits; i++)
        if (v->wait[i].play < end)
            ids[(current ^ i) & 1] = base + v->wait[i].play;
}

/* At play on slot: a loaded clip is about to be started, so a started bit it carries belongs to the clip it replaced.
   The load (0x5D8160) sets the new id and load bits but keeps that bit, and the voice frame (0x5D8590) ends any buffer
   whose started bit is set while its channel is quiet by clearing the bit and the id. When the new clip's start is put
   off a frame (its channel still busy, or the reader busy with the next line's load), that check wipes the id of the
   clip waiting to start; the clip still plays, but the lip clock (lip_clock.h) can no longer find its voice, and the
   first line of a scene started over a battle voice kept the event clock (work/handbacks/lip-av-offset.md). The bit is
   the replaced clip's alone until the voice frame starts this one, so it goes here; play itself starts only a loaded
   buffer, and the loaded bit keeps the voice frame off the buffer until then. */
static inline void voice_fresh(unsigned char *m, unsigned int slot)
{
    if (slot <= 1 && VOICE_FLAGS(m, slot) & VOICE_LOADED)
        VOICE_FLAGS(m, slot) &= ~VOICE_STARTED;
}

/* At play on slot (arguments arg): keep it when its clip still waits to be loaded, or is held back. */
static inline void voice_keep(struct voice_play *kept, unsigned char *m, unsigned int slot, const unsigned int *arg)
{
    if (slot <= 1 && !(VOICE_FLAGS(m, slot) & VOICE_LOADED) && (VOICE_WAITING(m) == slot || kept->held == slot)) {
        kept->slot = slot, kept->id = VOICE_WAITING(m) == slot ? VOICE_WAITING_ID(m) : kept->held_id;
        kept->arg[0] = arg[0], kept->arg[1] = arg[1], kept->arg[2] = arg[2];
    }
}

/* At a voice frame's start: the oldest waiting start is made if the reader is free; then whether the kept play is to be
   made now. A kept play goes either way once its load no longer waits: a failed load leaves nothing to play, another
   load another clip. A waiting load held back last frame waits again unless the game has made another wait since, and
   a start made now holds it back as its own read is under way. */
static inline int voice_frame(struct voice_play *kept, unsigned char *m, const struct voice_stock *stock)
{
    int due = 0;
    kept->frame++;
    if (kept->waits && stock->start(kept->wait[0].play, kept->wait[0].next)) {
        stock->note("voice start made once the archive reader was free, after", &kept->wait[0],
                    kept->frame - kept->wait[0].since);
        kept->wait[0] = kept->wait[1], kept->waits--;
    }
    if (kept->held != VOICE_NONE && VOICE_WAITING(m) == VOICE_NONE)
        VOICE_WAITING(m) = kept->held, VOICE_WAITING_ID(m) = kept->held_id;
    kept->held = VOICE_NONE;
    if (kept->slot != VOICE_NONE && VOICE_WAITING(m) != kept->slot) {
        due = VOICE_FLAGS(m, kept->slot) & VOICE_LOADED && VOICE_ID(m, kept->slot) == kept->id;
        if (!due)
            kept->slot = VOICE_NONE;
    }
    if (VOICE_WAITING(m) != VOICE_NONE && !stock->idle()) {
        kept->held = VOICE_WAITING(m), kept->held_id = VOICE_WAITING_ID(m);
        VOICE_WAITING(m) = VOICE_NONE;
    }
    return due;
}

/* At the voice stop (0x5D82A0): what waits to play is dropped, as the stop would have ended it. A waiting load stays:
   the stop leaves the game's own wait in place too. */
static inline void voice_stop(struct voice_play *kept, unsigned int end, const struct voice_stock *stock)
{
    unsigned int i;
    for (i = 0; i < kept->waits; i++)
        if (kept->wait[i].play < end)
            stock->note("voice start dropped as the voice stopped while it waited for the archive reader,",
                        &kept->wait[i], kept->frame - kept->wait[i].since);
    kept->waits = 0;
    kept->slot = VOICE_NONE;
}

#endif
