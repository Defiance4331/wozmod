#ifndef WOZMOD_PAD_MAP_H
#define WOZMOD_PAD_MAP_H

/* A player's input map (0x140C48C + 16 * player): 21 actions of 0x28 bytes. Binding 1 of an action is its pad binding:
   device at +0x18 (a pad index 0 to 7, or 10 for none), then the isAxis byte at +0x1C, the direction at +0x20 and the
   button or axis index at +0x24. */
#define PAD_MAP_ACTIONS 21
#define PAD_MAP_DEVICE(map, action) (*(unsigned int *)((map) + (action) * 0x28 + 0x18))

/* Before wozmod skipped it (SAVED_PAD), the stock input map load set the device of every binding whose pad was
   absent at boot to 10, and the next save wrote that to disk: the map kept the player's pad (PLAYER_JOY, joy) and
   every button choice, but no binding pointed at the pad, so the pad gave no input. A player cannot leave a map so:
   clearing a binding on Button Settings (F12) also clears joy once no binding is left on the pad. So a map with no
   binding on joy is that loss, and each cleared binding goes back to joy. The stock defaults leave only Camera Up,
   Down, Left and Right (actions 17 to 20) unbound, as device 10 with isAxis, direction and index all 0; those stay
   unbound. Returns the number of bindings put back. */
static inline unsigned int pad_map_relink(unsigned char *map, unsigned int joy)
{
    unsigned int i, relinked = 0;
    const unsigned char *b;
    if (joy >= 8)
        return 0;
    for (i = 0; i < PAD_MAP_ACTIONS; i++)
        if (PAD_MAP_DEVICE(map, i) == joy)
            return 0;
    for (i = 0; i < PAD_MAP_ACTIONS; i++) {
        b = map + i * 0x28 + 0x18;
        if (PAD_MAP_DEVICE(map, i) == 10
            && (i < 17 || b[4] || *(const unsigned int *)(b + 8) || *(const unsigned int *)(b + 12)))
            PAD_MAP_DEVICE(map, i) = joy, relinked++;
    }
    return relinked;
}

#endif
