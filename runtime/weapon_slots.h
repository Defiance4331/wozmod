#ifndef WOZMOD_WEAPON_SLOTS_H
#define WOZMOD_WEAPON_SLOTS_H

/* The battle's weapon texture cache, moved into the DLL and sized for every battle (issue 18). Stock keeps 36 slots of
   20 bytes at 0x8C07A0: +0 a weapon texture bundle's LINKDATA1 key (0xFFFF: free), +8 the loaded bundle, +0x10 its
   handle. 0x4B6940 returns the slot holding a key, else the first free one, else null, and 0x4B8680 writes through
   it unchecked, so a battle that asks for a 37th texture crashes at 0x4B86DF. Battle start claims the stage officers'
   weapons, then both teams' (0x4B1470: 0x46DC80, then 0x4F00F0), and nothing frees a slot in between: a two-player
   Dream battle on stage 143 asks for 37.
   The size is the most any battle can ask for. A claimed slot's key is unique in the table (each claim looks the key
   up first), and every key is field +0 of a record of the weapon model table at 0x793930 (580 records, read through
   0x4BDA80), which names 413 distinct keys besides 0xFFFF.
   Each instruction that names the table, or the end of a scan over it, holds the address as a 32-bit immediate
   (weapon_slot_sites in english_runtime.c). The DLL rewrites each to the same offset in its own table, and an end,
   which lies past the 36th slot, to the same distance past the last slot. */
#define WEAPON_SLOTS_STOCK 0x8C07A0u
#define WEAPON_SLOTS_STOCK_COUNT 36u
#define WEAPON_SLOT_BYTES 20u
#define WEAPON_SLOT_COUNT 413u

/* The address a stock immediate into the 36-slot table names, in a table of WEAPON_SLOT_COUNT slots at table. */
static unsigned int weapon_slot_moved(unsigned int stock, unsigned int table)
{
    unsigned int offset = stock - WEAPON_SLOTS_STOCK;
    if (offset >= WEAPON_SLOTS_STOCK_COUNT * WEAPON_SLOT_BYTES)
        offset += (WEAPON_SLOT_COUNT - WEAPON_SLOTS_STOCK_COUNT) * WEAPON_SLOT_BYTES;
    return table + offset;
}

#endif
