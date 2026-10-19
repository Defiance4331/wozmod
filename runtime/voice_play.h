#ifndef WOZMOD_VOICE_PLAY_H
#define WOZMOD_VOICE_PLAY_H

/* The game's voice manager ([0xE00404]): two clip buffers of 0x40 bytes from +0xC (voice id at +0x10, flags at +0x1C),
   and at +0xB0 the buffer a load waits for while that buffer still plays (2 for none), its voice id at +0xB4. A line's
   clip is loaded while the line before it plays, into the buffer the line before that played: each line's start
   (0x513720) reaps the archive reader (0x5D0D20), plays its own buffer and loads the next line's. A load sets the
   buffer's bit 3, and play (0x5D7BF0) starts only such a buffer, doing nothing otherwise. A load that has to wait is
   made by the voice frame (0x5D8590) once its buffer falls quiet.
   An English line longer than the Japanese timing can keep its buffer from falling quiet until the line after next
   has begun (stage 93's ending: line 137, then line 139). That line's play then finds nothing loaded and is lost, and
   its load, made after the line start loaded the next line, finds the reader unreaped and is dropped by it. So at each
   voice frame's start a waiting load is held back while the reader is busy, as a line start would not load then, and
   a play that came before its load is made once the load is: a frame after it, as a stock play, which the frame
   then prepares once read. */
#define VOICE_ID(m, slot) (*(volatile unsigned int *)((m) + 0xC + 0x40 * (slot) + 0x10))
#define VOICE_FLAGS(m, slot) (*(volatile unsigned int *)((m) + 0xC + 0x40 * (slot) + 0x1C))
#define VOICE_WAITING(m) (*(volatile unsigned int *)((m) + 0xB0))
#define VOICE_WAITING_ID(m) (*(volatile unsigned int *)((m) + 0xB4))
#define VOICE_LOADED 0x8u
#define VOICE_NONE 2u

struct voice_play {
    unsigned int slot, id, arg[3];  /* slot VOICE_NONE: nothing kept */
    unsigned int held, held_id;     /* a waiting load held back from this frame (held VOICE_NONE: none) */
};

/* At play on slot (arguments arg): keep it when its clip still waits to be loaded, or is held back. */
static inline void voice_keep(struct voice_play *kept, unsigned char *m, unsigned int slot, const unsigned int *arg)
{
    if (slot <= 1 && !(VOICE_FLAGS(m, slot) & VOICE_LOADED) && (VOICE_WAITING(m) == slot || kept->held == slot)) {
        kept->slot = slot, kept->id = VOICE_WAITING(m) == slot ? VOICE_WAITING_ID(m) : kept->held_id;
        kept->arg[0] = arg[0], kept->arg[1] = arg[1], kept->arg[2] = arg[2];
    }
}

/* At a voice frame's start, with idle reaping the reader (true when it is free): whether the kept play is to be made
   now. A kept play goes either way once its load no longer waits: a failed load leaves nothing to play, another load
   another clip. A waiting load held back last frame waits again unless the game has made another wait since. */
static inline int voice_frame(struct voice_play *kept, unsigned char *m, int (*idle)(void))
{
    int due = 0;
    if (kept->held != VOICE_NONE && VOICE_WAITING(m) == VOICE_NONE)
        VOICE_WAITING(m) = kept->held, VOICE_WAITING_ID(m) = kept->held_id;
    kept->held = VOICE_NONE;
    if (kept->slot != VOICE_NONE && VOICE_WAITING(m) != kept->slot) {
        due = VOICE_FLAGS(m, kept->slot) & VOICE_LOADED && VOICE_ID(m, kept->slot) == kept->id;
        if (!due)
            kept->slot = VOICE_NONE;
    }
    if (VOICE_WAITING(m) != VOICE_NONE && !idle()) {
        kept->held = VOICE_WAITING(m), kept->held_id = VOICE_WAITING_ID(m);
        VOICE_WAITING(m) = VOICE_NONE;
    }
    return due;
}

#endif
