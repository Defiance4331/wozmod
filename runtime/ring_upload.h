#ifndef WOZMOD_RING_UPLOAD_H
#define WOZMOD_RING_UPLOAD_H
/* The engine's ground rings, built in system memory and uploaded once a pass.

   Each scene pass the engine locks its ground vertex rings (DYNAMIC and WRITEONLY vertex buffers in the DEFAULT pool;
   0x5B3E00, 0x5B4100) with flags 0, writes each ground tile's four vertices at the ring's cursor, reads them straight
   back for the pass's bounding box (0x5B7A00), and unlocks them (0x5B7600, 0x5B77B0). A driver may keep a write-only
   buffer in video memory (AMD with Resizable BAR, DXVK), where each CPU read crosses the bus uncached: battles slow
   down, and collapse once the catch-up ticks pile up. So the engine's Lock gets this ring's copy in system memory,
   where its reads are cheap, and its Unlock copies what the pass wrote into the buffer under one DISCARD lock: the
   buffer is written once, in order, and never read, and the Lock does not wait for the GPU to finish last pass's
   draws, which no longer need the old contents (each pass draws only what it wrote).

   ru_lock and ru_unlock take the buffer's own Lock and Unlock arguments. out is the address of the engine's slot field
   for the ring's base; three fields on is the slot's write cursor, which the engine still holds when it unlocks.
   Every engine site locks from offset 0. A buffer that is not DYNAMIC (the MANAGED rings) goes to its own Lock and
   Unlock, and so does a ring with no slot here or no memory for its copy (counted in full). One thread: the one that
   draws. Pure (no headers), so the runtime and the tests both include it. */

#ifdef __i386__
#define RU_CALL __attribute__((stdcall))
#else
#define RU_CALL
#endif
#define RU_SLOTS 64
#define RU_DYNAMIC 0x200     /* D3DUSAGE_DYNAMIC */
#define RU_DISCARD 0x2000    /* D3DLOCK_DISCARD */
#define RU_LOCK 11           /* IDirect3DVertexBuffer9 vtable slots */
#define RU_UNLOCK 12
#define RU_DESC 13

typedef long (RU_CALL *ru_lock_fn)(void *, unsigned, unsigned, void **, unsigned);
typedef long (RU_CALL *ru_unlock_fn)(void *);
typedef long (RU_CALL *ru_desc_fn)(void *, unsigned *);

struct ru_slot {
    void **field;  /* the engine's slot field for the ring's base (0: free) */
    void *buffer;  /* the vertex buffer it last locked; a Reset makes a new one for the same slot */
    char *copy;
    unsigned size, locked;
};

struct ring_upload {
    void *(*resize)(void *old, unsigned size);  /* the copies' memory: grows old (0 at first), 0 when there is none */
    unsigned full;
    struct ru_slot slot[RU_SLOTS];
};

#define RU_METHOD(type, buffer, slot) ((type)(*(void ***)(buffer))[slot])

static long ru_lock(struct ring_upload *u, void *buffer, unsigned size, void **out, unsigned flags)
{
    unsigned desc[6] = {0}, i;  /* D3DVERTEXBUFFER_DESC: Format, Type, Usage, Pool, Size, FVF */
    struct ru_slot *s = 0, *free = 0;
    void *grown;
    for (i = 0; i < RU_SLOTS && !s; i++)
        if (u->slot[i].field == out)
            s = &u->slot[i];
        else if (!u->slot[i].field && !free)
            free = &u->slot[i];
    if (RU_METHOD(ru_desc_fn, buffer, RU_DESC)(buffer, desc) < 0 || !(desc[2] & RU_DYNAMIC))
        return RU_METHOD(ru_lock_fn, buffer, RU_LOCK)(buffer, 0, size, out, flags);
    if (!s && (s = free))
        s->field = out;
    size = size ? size : desc[4];
    if (s && s->size < size && (grown = u->resize(s->copy, size)))
        s->copy = grown, s->size = size;
    if (!s || s->size < size)
        return u->full++, RU_METHOD(ru_lock_fn, buffer, RU_LOCK)(buffer, 0, size, out, flags);
    s->buffer = buffer, s->locked = 1;
    *out = s->copy;
    return 0;
}

static long ru_unlock(struct ring_upload *u, void *buffer)
{
    struct ru_slot *s = 0;
    unsigned i, used;
    void *to;
    long r;
    for (i = 0; i < RU_SLOTS && !s; i++)
        if (u->slot[i].locked && u->slot[i].buffer == buffer)
            s = &u->slot[i];
    if (!s)
        return RU_METHOD(ru_unlock_fn, buffer, RU_UNLOCK)(buffer);
    s->locked = 0;
    if (!(used = (unsigned)((char *)s->field[3] - s->copy)))
        return 0;
    if ((r = RU_METHOD(ru_lock_fn, buffer, RU_LOCK)(buffer, 0, used, &to, RU_DISCARD)) < 0)
        return r;
    __builtin_memcpy(to, s->copy, used);
    return RU_METHOD(ru_unlock_fn, buffer, RU_UNLOCK)(buffer);
}
#endif
