#ifndef WOZMOD_SE_CUTOFF_H
#define WOZMOD_SE_CUTOFF_H

/* Version 1.01's falling-rock fix, for version 1.00. The sound manager polls its 240 sound-effect slots once a game
   tick (0x5D5E30, ecx the manager; from 0x5E0E80 through 0x5D6DB0 and 0x5D6BE0). A slot is 0x14 bytes from +0x1E0C:
   the middleware handle, then the four ids both sound-effect starts give it (bank, group, index, channel). The poll
   asks the middleware (0x670730) whether each slot with a handle still plays, and frees the first one that does not
   (0x5D5DC0), returning what the free returns; it returns 1 when every slot still plays.
   On map 115 (the dword at 0x801AA4, from byte 0 of the stage record) the sound at bank 10, index 20 can loop to the
   end of the battle. Version 1.01 (0x5D60F0) counts each poll that finds that sound playing on that map in a 16-bit
   field it adds to the slot, zeroed when the slot is allocated, freed or built, and on the 180th stops the sound by
   its ids (1.00's 0x5D5D80), frees the slot and returns. 1.00's slot has no room for the count, so it is kept here,
   per slot: the poll that frees a slot zeroes it, and a slot whose handle or ids differ from what its last playing
   poll saw (a sound allocated after a free this poll did not make, or a rebuilt manager) starts again from zero. */
#define SE_SLOTS 240u
#define SE_MAP_AT 0x801AA4u  /* the current map (1.01 moved it to 0x801AC4) */
#define SE_SLOT(m, i) ((const unsigned int *)((m) + 0x1E0C + 0x14 * (i)))  /* handle, bank, group, index, channel */
#define SE_CUT_BANK 10u
#define SE_CUT_INDEX 20u
#define SE_CUT_MAP 115u
#define SE_CUT_POLLS 180u

struct se_cutoff {
    unsigned short polls[SE_SLOTS];  /* playing polls on map 115 of the slot's sound, if it is bank 10, index 20 */
    unsigned int seen[SE_SLOTS][5];  /* the slot as its last playing poll saw it */
};

/* The stock routines the poll calls, each given the manager. */
struct se_stock {
    int (*playing)(unsigned char *m, unsigned int handle);  /* 0x670730: bit 0 of the state it writes */
    void (*stop)(unsigned char *m, const unsigned int *slot);  /* 0x5D5D80 (bank, group, index, channel) */
    unsigned int (*release)(unsigned char *m, unsigned int i);  /* 0x5D5DC0: frees slot i */
};

/* A poll that found slot i playing on map `map`: whether its sound is to be stopped now. */
static inline int se_cutoff_due(struct se_cutoff *c, unsigned int i, const unsigned int *slot, unsigned int map)
{
    unsigned int k, same = 1;
    for (k = 0; k < 5; k++)
        same &= c->seen[i][k] == slot[k], c->seen[i][k] = slot[k];
    if (!same)
        c->polls[i] = 0;
    if (slot[3] != SE_CUT_INDEX || slot[1] != SE_CUT_BANK || map != SE_CUT_MAP)
        return 0;
    return ++c->polls[i] >= SE_CUT_POLLS;
}

/* Version 1.01's poll over manager m: its return value, and in *cut the slot whose sound it stopped (SE_SLOTS: none). */
static inline unsigned int se_poll(struct se_cutoff *c, unsigned char *m, unsigned int map, const struct se_stock *s,
                                   unsigned int *cut)
{
    unsigned int i;
    const unsigned int *slot;
    *cut = SE_SLOTS;
    for (i = 0; i < SE_SLOTS; i++) {
        slot = SE_SLOT(m, i);
        if (!slot[0])
            continue;
        if (s->playing(m, slot[0])) {
            if (!se_cutoff_due(c, i, slot, map))
                continue;
            s->stop(m, slot);
            *cut = i;
        }
        c->polls[i] = 0;
        return s->release(m, i);
    }
    return 1;
}

#endif
