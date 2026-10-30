#ifndef WOZMOD_VB_PROBE_H
#define WOZMOD_VB_PROBE_H
/* Test builds only (ENRT_TEST): what the engine does with locked vertex and index buffers, for the Resizable BAR
   study (work/rebar). With ReBAR on, AMD's driver can put DEFAULT-pool buffers in CPU-visible video memory, where CPU
   reads are slow; a Lock with flags 0 must also keep the buffer's old contents.

   The host sets vbp.frames and vbp.command: 1 shadows every Lock for that many presents, 3 only times the Locks. A
   shadowed Lock hands the game a no-access copy of the locked range (the real contents copied in); every access then
   faults, is counted by kind and instruction, and is let through by one single step with the page open. Unlock copies
   the copy back into the real buffer. A read of a 16-byte granule this Lock has not written reads the buffer's old
   contents, which only a Lock without DISCARD keeps. Per call site (the Lock's return address): locks, flags,
   reads and writes, the range written, and the time the real Lock took. state 2 when the frames are done; command 0
   resets. Slow while shadowing: every access faults. Game thread only. */

#define VBP_SITES 32
#define VBP_EIPS 64
#define VBP_ACTIVE 32
#define VBP_GRAIN 16
typedef long (__stdcall *vbp_lock_fn)(void *, u32, u32, void **, u32);
typedef long (__stdcall *vbp_unlock_fn)(void *);
typedef long (__stdcall *vbp_desc_fn)(void *, u32 *);
typedef long (__stdcall *vbp_create_fn)(void *, u32, u32, u32, u32, void **, void *);

struct vbp_site {
    u32 site, locks, shadowed, flags, usage, pool, bytes, writes, own_reads, stale_reads, write_lo, write_hi,
        stale_eip, pad;
    long long ticks, max_ticks;
};
static struct {
    char magic[16];
    volatile u32 command, state, frames, presents, faults, foreign, after_unlock, overflow;
    long long freq;
    struct vbp_site site[VBP_SITES];
    struct { u32 eip, reads, stale, site; } eip[VBP_EIPS];
} vbp = {"ENRT-VB-PROBE"};

/* One shadow per buffer, kept for the run: cap bytes from base, a written bit per granule. */
static struct vbp_lock {
    void *buffer;
    u8 *base, *real, *written;
    u32 cap, n, site, active;
} vbp_lock[VBP_ACTIVE];
static u32 vbp_page[2], vbp_pending, vbp_thread;  /* vbp_thread: the one that presents */

static struct vbp_site *vbp_site_at(u32 site)
{
    u32 i;
    for (i = 0; i < VBP_SITES && vbp.site[i].site && vbp.site[i].site != site; i++)
        ;
    if (i == VBP_SITES)
        return vbp.overflow++, (struct vbp_site *)0;
    vbp.site[i].site = site;
    return &vbp.site[i];
}

static struct vbp_lock *vbp_shadow(void *buffer, u32 size)
{
    u32 i, cap = (size + 0xFFF) & ~0xFFFu;
    for (i = 0; i < VBP_ACTIVE && vbp_lock[i].buffer && vbp_lock[i].buffer != buffer; i++)
        ;
    if (i == VBP_ACTIVE)
        return vbp.overflow++, (struct vbp_lock *)0;
    if (vbp_lock[i].buffer && vbp_lock[i].cap >= size)
        return &vbp_lock[i];
    if (vbp_lock[i].base)
        VirtualFree(vbp_lock[i].base, 0, MEM_RELEASE), HeapFree(GetProcessHeap(), 0, vbp_lock[i].written);
    vbp_lock[i].buffer = buffer, vbp_lock[i].cap = cap;
    vbp_lock[i].base = VirtualAlloc(0, cap, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    vbp_lock[i].written = HeapAlloc(GetProcessHeap(), 0, cap / VBP_GRAIN / 8);
    if (!vbp_lock[i].base || !vbp_lock[i].written)
        return vbp_lock[i].buffer = 0, vbp.overflow++, (struct vbp_lock *)0;
    return &vbp_lock[i];
}

static LONG CALLBACK vbp_trap(EXCEPTION_POINTERS *e)
{
    DWORD code = e->ExceptionRecord->ExceptionCode, old;
    u32 i, a, ip, g, kind;
    if (code == EXCEPTION_SINGLE_STEP && vbp_pending) {
        for (i = 0; i < vbp_pending; i++)
            VirtualProtect((void *)vbp_page[i], 4096, PAGE_NOACCESS, &old);
        vbp_pending = 0;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (code != EXCEPTION_ACCESS_VIOLATION || e->ExceptionRecord->NumberParameters < 2)
        return EXCEPTION_CONTINUE_SEARCH;
    a = e->ExceptionRecord->ExceptionInformation[1], kind = e->ExceptionRecord->ExceptionInformation[0];
    for (i = 0; i < VBP_ACTIVE; i++)
        if (vbp_lock[i].base && a - (u32)vbp_lock[i].base < vbp_lock[i].cap)
            break;
    if (i == VBP_ACTIVE || vbp_pending == 2)
        return EXCEPTION_CONTINUE_SEARCH;
    {
        struct vbp_lock *l = &vbp_lock[i];
        struct vbp_site *s = &vbp.site[l->site];
        ip = e->ContextRecord->Eip, g = (a - (u32)l->base) / VBP_GRAIN;
        vbp.faults++;
        if (GetCurrentThreadId() != vbp_thread)
            vbp.foreign++;
        if (!l->active)
            vbp.after_unlock++;
        else if (kind == 1) {
            s->writes++, l->written[g / 8] |= (u8)(1 << g % 8);
            if (a - (u32)l->base < s->write_lo)
                s->write_lo = a - (u32)l->base;
            if (a - (u32)l->base + 1 > s->write_hi)
                s->write_hi = a - (u32)l->base + 1;
        } else if (kind == 0) {
            u32 stale = !(l->written[g / 8] >> g % 8 & 1), j;
            if (stale)
                s->stale_reads++, s->stale_eip = s->stale_eip ? s->stale_eip : ip;
            else
                s->own_reads++;
            for (j = 0; j < VBP_EIPS && vbp.eip[j].eip && (vbp.eip[j].eip != ip || vbp.eip[j].site != s->site); j++)
                ;
            if (j < VBP_EIPS)
                vbp.eip[j].eip = ip, vbp.eip[j].site = s->site, vbp.eip[j].reads++, vbp.eip[j].stale += stale;
        }
    }
    vbp_page[vbp_pending++] = a & ~0xFFFu;
    VirtualProtect((void *)(a & ~0xFFFu), 4096, PAGE_READWRITE, &old);
    e->ContextRecord->EFlags |= 0x100;
    return EXCEPTION_CONTINUE_EXECUTION;
}

static long __stdcall vbp_lock_hook(void *buffer, u32 offset, u32 size, void **out, u32 flags)
{
    u32 desc[6] = {0}, n, i;
    LARGE_INTEGER t0, t1;
    struct vbp_site *s = 0;
    struct vbp_lock *l;
    DWORD old;
    long r;
    if (vbp.state == 1 || vbp.state == 3)
        s = vbp_site_at((u32)__builtin_return_address(0));
    QueryPerformanceCounter(&t0);
    r = REAL(vbp_lock_fn, buffer, 11)(buffer, offset, size, out, flags);
    QueryPerformanceCounter(&t1);
    if (!s || r < 0 || !*out)
        return r;
    ((vbp_desc_fn)(*(void ***)buffer)[13])(buffer, desc);  /* Format, Type, Usage, Pool, Size (both buffer kinds) */
    n = size ? size : desc[4] - offset;
    s->locks++, s->flags |= flags, s->usage = desc[2], s->pool = desc[3], s->bytes = n;
    s->ticks += t1.QuadPart - t0.QuadPart;
    if (t1.QuadPart - t0.QuadPart > s->max_ticks)
        s->max_ticks = t1.QuadPart - t0.QuadPart;
    if (vbp.state != 1 || !n || !(l = vbp_shadow(buffer, n)))
        return r;
    if (l->active)  /* locked twice without an Unlock: leave the second as it is */
        return vbp.overflow++, r;
    VirtualProtect(l->base, l->cap, PAGE_READWRITE, &old);
    CopyMemory(l->base, *out, n);
    for (i = 0; i < l->cap / VBP_GRAIN / 8; i++)
        l->written[i] = 0;
    l->real = *out, l->n = n, l->site = (u32)(s - vbp.site), l->active = 1;
    s->shadowed++;
    VirtualProtect(l->base, l->cap, PAGE_NOACCESS, &old);
    *out = l->base;
    return r;
}

static long __stdcall vbp_unlock_hook(void *buffer)
{
    u32 i;
    DWORD old;
    for (i = 0; i < VBP_ACTIVE; i++)
        if (vbp_lock[i].buffer == buffer && vbp_lock[i].active) {
            struct vbp_lock *l = &vbp_lock[i];
            VirtualProtect(l->base, l->cap, PAGE_READWRITE, &old);
            CopyMemory(l->real, l->base, l->n);
            VirtualProtect(l->base, l->cap, PAGE_NOACCESS, &old);
            l->active = 0;
        }
    return REAL(vbp_unlock_fn, buffer, 12)(buffer);
}

/* CreateVertexBuffer (26) and CreateIndexBuffer (27): Length, Usage, FVF or Format, Pool, out, shared. */
static long __stdcall vbp_create_vb(void *d, u32 len, u32 usage, u32 fvf, u32 pool, void **out, void *share)
{
    long r = REAL(vbp_create_fn, d, 26)(d, len, usage, fvf, pool, out, share);
    if (r >= 0 && *out)
        hook_slot(*out, 11, (void *)vbp_lock_hook), hook_slot(*out, 12, (void *)vbp_unlock_hook);
    return r;
}

static long __stdcall vbp_create_ib(void *d, u32 len, u32 usage, u32 format, u32 pool, void **out, void *share)
{
    long r = REAL(vbp_create_fn, d, 27)(d, len, usage, format, pool, out, share);
    if (r >= 0 && *out)
        hook_slot(*out, 11, (void *)vbp_lock_hook), hook_slot(*out, 12, (void *)vbp_unlock_hook);
    return r;
}

/* Each Present: start, count and end the host's frames. */
static void vbp_present(void)
{
    static int handler;
    LARGE_INTEGER hz;
    u32 i;
    if (!vbp.command) {
        vbp.state = 0;
        return;
    }
    if (vbp.state == 0 && (vbp.command == 1 || vbp.command == 3)) {
        if (!handler)
            AddVectoredExceptionHandler(1, vbp_trap), handler = 1;
        ZeroMemory(vbp.site, sizeof vbp.site), ZeroMemory(vbp.eip, sizeof vbp.eip);
        for (i = 0; i < VBP_SITES; i++)
            vbp.site[i].write_lo = ~0u;
        vbp.presents = vbp.faults = vbp.foreign = vbp.after_unlock = vbp.overflow = 0;
        QueryPerformanceFrequency(&hz), vbp.freq = hz.QuadPart, vbp_thread = GetCurrentThreadId();
        vbp.state = vbp.command;
    } else if ((vbp.state == 1 || vbp.state == 3) && ++vbp.presents >= vbp.frames)
        vbp.state = 2;
}
#endif
