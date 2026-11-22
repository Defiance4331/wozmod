#ifndef WOZMOD_STAGE_DUCK_H
#define WOZMOD_STAGE_DUCK_H

/* While a battle stage line speaks, music and sound effects play lower, by a fixed depth at every slider position:
   the stock duck at full sliders (music to 11/15, effects to 12/15) and the owner's 4 dB more, so music -6.69 dB and
   effects -5.94 dB. The voice is never raised.
   Stock ducks in start_stage_line_voice 0x4F4F80 by passing its two level setters a lower whole level: music 3L/4
   (fade_bgm_level 0x514200, a 15-tick fade) and effects 4L/5 (set_se_level 0x513070, all 16 channels at once). Whole
   levels round down, so the depth moved with the slider (-1.9 to -6.0 dB) and level 1 muted both; and it ducked
   every spoken line, voiced or not. runtime_edits.STAGE_DUCK makes both calls pass the full level, and the volume
   hooks (volume_curve.h) apply the depth here to those two calls alone, when the line has a voice: its load
   succeeded and buffer 0 holds a take (stage 40's lines 15143-15146 and 15148 load a take of size 0). As in stock,
   any later setter call sets its own level: a pause and its resume, an event scene.

   The duck is held by the spoken window the line opened, and ends with it. The window ends one of three ways:
   - it closes (0x4F5108): stock then calls both setters with the full level (0x4F511B);
   - a speak with word 5 = 1 releases it (0x4F5146) with no restore, because the next line follows in the same tick
     and ducks again; when none does by the end of that tick's window commands (0x4ED6D0; 9 stock events end that
     way), the duck is undone as a close does;
   - the battle is decided (a commander falls: 0x435DAB sets mode 0x2000000 and result 1 or 2): the stage script tick
     0x4EF4F0 stops stepping its window, so the window never closes and nothing restores; stock kept the duck through
     the results, the menus and into the next battle until a spoken window there closed. The duck is undone as a
     close does at the first such tick.

   Gains are 1/65536: 65536 * 11/15 * 10^(-4/20) and 65536 * 12/15 * 10^(-4/20), rounded (within 0.001 dB). */
#define STAGE_DUCK_MUSIC 30324u
#define STAGE_DUCK_EFFECTS 33080u

enum stage_duck_state {
    STAGE_DUCK_NONE,      /* no spoken window holds a duck */
    STAGE_DUCK_HELD,      /* a voiced line's window is open: its two setter calls duck */
    STAGE_DUCK_RELEASED,  /* that window was let go this tick with no restore, and no line has taken over yet */
};

struct stage_duck {
    unsigned char state;  /* enum stage_duck_state */
};

/* A spoken line starts (0x4F4FD5, before its two setter calls): whether it has a voice. Its calls set the levels, so
   a release before it needs no restore. */
static inline void stage_duck_line(struct stage_duck *d, int voiced)
{
    d->state = voiced ? STAGE_DUCK_HELD : STAGE_DUCK_NONE;
}

/* The gain the line's setter call sets: ``gain`` (at most 0x3FFF) at ``depth``, when the line has a voice. */
static inline unsigned stage_duck_gain(const struct stage_duck *d, unsigned gain, unsigned depth)
{
    return d->state == STAGE_DUCK_HELD ? (gain * depth + 0x8000) >> 16 : gain;
}

/* The window closes (0x4F5108): stock restores both levels itself. */
static inline void stage_duck_close(struct stage_duck *d)
{
    d->state = STAGE_DUCK_NONE;
}

/* A window released with no restore (0x4F5146): the spoken one (word 5 = 1), or the notice box, which never ducks. */
static inline void stage_duck_release(struct stage_duck *d, int spoken)
{
    if (spoken && d->state == STAGE_DUCK_HELD)
        d->state = STAGE_DUCK_RELEASED;
}

/* The tick's window commands are done (0x4ED6D0): 1 when a released window's duck must be undone, as a close does. */
static inline int stage_duck_tick_end(struct stage_duck *d)
{
    if (d->state != STAGE_DUCK_RELEASED)
        return 0;
    d->state = STAGE_DUCK_NONE;
    return 1;
}

/* A tick of a decided battle (0x4EF4FC), whose window the stage no longer steps: 1 when its duck must be undone, as
   a close does; once, since the window is then gone. */
static inline int stage_duck_decided(struct stage_duck *d)
{
    if (d->state == STAGE_DUCK_NONE)
        return 0;
    d->state = STAGE_DUCK_NONE;
    return 1;
}

#endif
