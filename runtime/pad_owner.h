#ifndef WOZMOD_PAD_OWNER_H
#define WOZMOD_PAD_OWNER_H

#include "pad_map.h"

/* Which actions of a player's map a removal parked: bit n is action n.
   Why this and not the fields: the stock map construction never writes the direction or the button index of a
   binding it does not bind. 0x5E4300 allocates the array through 0x69D130 (malloc, not cleared) and runs 0x5E42A0 on
   each action, which writes the device and two flag bytes of each binding and nothing at +0x10, +0x14, +0x20 or
   +0x24. The default maps (0x5E2520, 0x5E27C0, 0x5E2A80) fill a 0x28-byte stack temporary the same way (0x5E42D0
   and the setters at 0x5E3DD0 to 0x5E3E70) and copy it into the array with 0x5E3CF0, so an action the pad
   auto-setting (0x5E2D30) does not bind keeps stack residue there. The run 2026-10-27_2255 read a direction that is
   the address of the fopen mode string. A device-10 binding therefore says nothing about itself: no value in its
   fields can tell a layout from residue. What tells is where it came from, and this mask is that: the only
   bindings put back on a new pad are the ones a removal took off the old one. */
struct pad_park {
    unsigned int mask;  /* bit n: action n was parked */
    unsigned int pad;   /* the pad index those actions were on, which the saved file keeps for them */
};

/* A pad leaves the game's vector when DirectInput no longer enumerates it. Bindings that named it become device 10
   and keep their button, axis and direction, and `park` gains their actions and the index they were on. Bindings and a player's joy that
   named a later pad move down with that pad, so a player who is still connected keeps the same device. The player
   whose pad left is joy 10 until they use another one. Returns 1 when this joy was the pad that left. */
static inline int pad_index_removed(unsigned char *map, unsigned int *joy, unsigned int index, struct pad_park *park)
{
    unsigned int i, device, gone, first;
    if (index >= 8)
        return 0;
    gone = *joy == index;
    first = !park->mask;
    for (i = 0; i < PAD_MAP_ACTIONS; i++) {
        device = PAD_MAP_DEVICE(map, i);
        if (device == index)
            PAD_MAP_DEVICE(map, i) = 10, park->mask |= 1u << i;
        else if (device > index && device < 8)
            PAD_MAP_DEVICE(map, i) = device - 1;
    }
    if (park->mask && (gone || first))
        park->pad = index;
    if (*joy == index)
        *joy = 10;
    else if (*joy > index && *joy < 8)
        *joy -= 1;
    return gone;
}

/* Move every binding that names `from` onto `to`. The button, axis and direction stay. Returns how many moved. */
static inline unsigned int pad_bindings_move(unsigned char *map, unsigned int from, unsigned int to)
{
    unsigned int i, moved = 0;
    if (from >= 8 || to >= 8 || from == to)
        return 0;
    for (i = 0; i < PAD_MAP_ACTIONS; i++)
        if (PAD_MAP_DEVICE(map, i) == from)
            PAD_MAP_DEVICE(map, i) = to, moved++;
    return moved;
}

/* Put exactly the parked actions on `to`, and forget them. An action the player has since bound to a pad is not
   parked any more (its device is not 10) and stays. Returns how many went back. */
static inline unsigned int pad_parked_back(unsigned char *map, struct pad_park *park, unsigned int to)
{
    unsigned int i, back = 0;
    if (to >= 8)
        return 0;
    for (i = 0; i < PAD_MAP_ACTIONS; i++)
        if ((park->mask >> i & 1) && PAD_MAP_DEVICE(map, i) == 10)
            PAD_MAP_DEVICE(map, i) = to, back++;
    park->mask = 0, park->pad = 0;
    return back;
}

static inline int pad_map_on(const unsigned char *map, unsigned int joy)
{
    unsigned int i;
    if (joy >= 8)
        return 0;
    for (i = 0; i < PAD_MAP_ACTIONS; i++)
        if (PAD_MAP_DEVICE(map, i) == joy)
            return 1;
    return 0;
}

/* What one poll decides for one player. `acting` is the pad whose press or stick this poll read. `own_live` is
   whether this player's joy still names a pad in the vector. `other` is the other player's joy, or 10.
   moved: bindings that named the old joy (a pad index that is no longer in the vector) now name acting.
   restored: the actions a removal parked are back on acting.
   defaults: nothing was moved or parked and nothing already names acting, so the caller asks the game for its
   defaults. That covers a map the game built itself and a map the player cleared.
   A live pad is left alone. The other player's pad is never taken. Restoring uses up the parked actions. */
struct pad_claim {
    unsigned int joy;
    unsigned int moved;
    unsigned int restored;
    int defaults;
};

static inline struct pad_claim pad_claim(unsigned char *map, unsigned int joy, unsigned int other,
                                         unsigned int acting, int own_live, struct pad_park *park)
{
    struct pad_claim c;
    c.joy = joy;
    c.moved = 0;
    c.restored = 0;
    c.defaults = 0;
    if (own_live || acting >= 8 || acting == other)
        return c;
    if (joy < 8 && joy != acting)
        c.moved = pad_bindings_move(map, joy, acting);
    if (!c.moved)
        c.restored = pad_parked_back(map, park, acting);
    c.defaults = !c.moved && !c.restored && !pad_map_on(map, acting);
    c.joy = acting;
    return c;
}

/* What the game saves (the input map writer, 0x5E2290, called for player 0 then 1 by save job state 0xE at
   0x5AC839 and 0x5AC840) is the map in memory and both joys. A parked player is joy 10 with every parked action at
   device 10 in memory, and written so, the file would hold a layout no pad owns: the next boot has nothing to put
   back and the first pad gets the defaults. Unplugging never touched the saved map before, so the file must hold
   what stock would have written had the pad stayed: the player's joy and their parked actions on the pad index
   they were on. pad_file_begin puts that in memory just before the writer; pad_file_end puts the park back just
   after. Both are exact state (the park record), never a field value. */
struct pad_undo {
    int active;
    unsigned int joy[2];      /* the live joys, to put back */
    unsigned int changed[2];  /* per player, the actions begin moved off device 10 */
};

static inline int pad_parked_now(const struct pad_park *park, unsigned int joy)
{
    return joy == 10 && park->mask && park->pad < 8;
}

/* The joy player p's file holds: the pad they had when parked. Two players never hold one pad in a file: a parked
   player gives way to a live pad, and player 2 to a parked player 1, by taking the lowest other index. */
static inline unsigned int pad_file_joy(const struct pad_park park[2], const unsigned int joy[2], unsigned int p)
{
    int mine_parked = pad_parked_now(&park[p], joy[p]), theirs_parked = pad_parked_now(&park[p ^ 1], joy[p ^ 1]);
    unsigned int mine = mine_parked ? park[p].pad : joy[p];
    unsigned int theirs = theirs_parked ? park[p ^ 1].pad : joy[p ^ 1];
    if (mine_parked && mine == theirs && (!theirs_parked || p == 1))
        for (mine = 0; mine == theirs; mine++)
            ;
    return mine;
}

static inline void pad_file_begin(unsigned char *const maps[2], unsigned int joy[2], const struct pad_park park[2],
                                  struct pad_undo *undo)
{
    unsigned int p, i, file[2];
    undo->active = 1;
    undo->changed[0] = undo->changed[1] = 0;
    for (p = 0; p < 2; p++)
        undo->joy[p] = joy[p], file[p] = pad_file_joy(park, joy, p);
    for (p = 0; p < 2; p++) {
        if (!maps[p] || !pad_parked_now(&park[p], joy[p]))
            continue;
        for (i = 0; i < PAD_MAP_ACTIONS; i++)
            if ((park[p].mask >> i & 1) && PAD_MAP_DEVICE(maps[p], i) == 10)
                PAD_MAP_DEVICE(maps[p], i) = file[p], undo->changed[p] |= 1u << i;
    }
    joy[0] = file[0], joy[1] = file[1];
}

static inline void pad_file_end(unsigned char *const maps[2], unsigned int joy[2], struct pad_undo *undo)
{
    unsigned int p, i;
    if (!undo->active)
        return;
    undo->active = 0;
    for (p = 0; p < 2; p++) {
        for (i = 0; i < PAD_MAP_ACTIONS; i++)
            if (maps[p] && (undo->changed[p] >> i & 1))
                PAD_MAP_DEVICE(maps[p], i) = 10;
        joy[p] = undo->joy[p];
    }
}

#endif
