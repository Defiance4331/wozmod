#ifndef WOZMOD_DEVICE_HOOKS_H
#define WOZMOD_DEVICE_HOOKS_H

/* COM method hooks kept per vtable. Native d3d9 gives devices made with different behaviour flags their own vtables:
   on Windows 10 the game's first device (0x80) and the one it keeps (0x84, multithreaded) differ, where DXVK and Wine
   share one. So every object's vtable is hooked, each once, and each call goes on to its own vtable's original. */
#define HOOK_VTABLES 16
#define HOOK_SLOTS 120

struct hooked_vtable {
    void **vtable;
    void *real[HOOK_SLOTS];
};

struct hooks {
    unsigned int count;
    struct hooked_vtable table[HOOK_VTABLES];
};

static inline struct hooked_vtable *hook_vtable(struct hooks *h, void **vtable)
{
    unsigned int i;
    for (i = 0; i < h->count; i++)
        if (h->table[i].vtable == vtable)
            return &h->table[i];
    return 0;
}

/* The original of a hooked slot in the object's own vtable. */
static inline void *hook_real(struct hooks *h, const void *object, unsigned int slot)
{
    struct hooked_vtable *e = hook_vtable(h, *(void **const *)object);
    return e ? e->real[slot] : 0;
}

/* Put fn in the slot of the object's vtable, keeping that vtable's original; a slot already holding fn keeps the
   original it had. write stores a pointer into the (read-only) vtable. 0 when it could not be hooked. */
static inline int hook_put(struct hooks *h, void *object, unsigned int slot, void *fn, int (*write)(void **at, void *value))
{
    void **vtable = *(void ***)object;
    struct hooked_vtable *e = hook_vtable(h, vtable);
    if (slot >= HOOK_SLOTS)
        return 0;
    if (!e) {
        if (h->count == HOOK_VTABLES)
            return 0;
        e = &h->table[h->count++], e->vtable = vtable;
    }
    if (vtable[slot] == fn)
        return 1;
    e->real[slot] = vtable[slot];
    return write(vtable + slot, fn);
}

#endif
