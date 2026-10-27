#ifndef WOZMOD_TRUE_RATE_PROBE_H
#define WOZMOD_TRUE_RATE_PROBE_H
/* Test builds only (ENRT_TEST): which game functions run, for the true frame rate study (work/true-rate).

   Coverage: the host drops wozmod/cover.bin (u32 count, then that many function entry VAs, ascending) into the
   run's game folder and sets cover.command to 1. At the next message pump every listed entry gets an int3; the first
   time one runs, the handler puts the byte back and records the hit: its order, the scene, the caller's return
   address and whether the battle was ticking (cover.phase: 1 from the battle tick until the message pump).
   command 2 writes wozmod/cover-hits.bin (per entry: u32 order, u32 caller, u8 scene, u8 phase, 2 spare) and puts
   back the bytes of every entry that never ran. */

#define COVER_MAX 12000
static struct {
    char magic[16];
    volatile u32 command, state, count, hits, phase;
} cover = {"ENRT-COVER-BLK"};
static u32 cover_va[COVER_MAX];
static u8 cover_byte[COVER_MAX];
static struct { u32 order, caller; u8 scene, phase, spare[2]; } cover_hit[COVER_MAX];

static int cover_find(u32 at)
{
    int lo = 0, hi = (int)cover.count - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (cover_va[mid] == at)
            return mid;
        if (cover_va[mid] < at)
            lo = mid + 1;
        else
            hi = mid - 1;
    }
    return -1;
}

static LONG CALLBACK cover_trap(EXCEPTION_POINTERS *e)
{
    u32 at = (u32)e->ExceptionRecord->ExceptionAddress;
    int i;
    if (e->ExceptionRecord->ExceptionCode != EXCEPTION_BREAKPOINT || cover.state != 1)
        return EXCEPTION_CONTINUE_SEARCH;
    if ((i = cover_find(at)) < 0 && (i = cover_find(at - 1)) >= 0)
        at -= 1;  /* a handler that reports the address after the int3 */
    if (i < 0 || *(volatile u8 *)at != 0xCC)
        return EXCEPTION_CONTINUE_SEARCH;
    *(volatile u8 *)at = cover_byte[i];
    FlushInstructionCache(GetCurrentProcess(), (void *)at, 1);
    cover_hit[i].order = ++cover.hits;
    cover_hit[i].caller = *(const u32 *)e->ContextRecord->Esp;
    cover_hit[i].scene = (u8)SCENE;
    cover_hit[i].phase = (u8)cover.phase;
    e->ContextRecord->Eip = at;
    return EXCEPTION_CONTINUE_EXECUTION;
}

static void cover_pump(void)
{
    WCHAR name[MAX_PATH];
    HANDLE file;
    DWORD got, old;
    u32 i, n;
    if (cover.command == 1 && cover.state == 0) {
        cover.state = 3;  /* failed unless armed below */
        file = CreateFileW(path(name, L"cover.bin"), GENERIC_READ, FILE_SHARE_READ, 0, OPEN_EXISTING, 0, 0);
        if (file == INVALID_HANDLE_VALUE)
            return;
        if (ReadFile(file, &n, 4, &got, 0) && got == 4 && n <= COVER_MAX
            && ReadFile(file, cover_va, 4 * n, &got, 0) && got == 4 * n) {
            cover.count = n;
            VirtualProtect((void *)0x401000, 0x2CB000, PAGE_EXECUTE_READWRITE, &old);
            AddVectoredExceptionHandler(1, cover_trap);
            for (i = 0; i < n; i++)
                cover_byte[i] = *(const u8 *)cover_va[i];
            cover.state = 1;
            for (i = 0; i < n; i++)
                *(volatile u8 *)cover_va[i] = 0xCC;
            FlushInstructionCache(GetCurrentProcess(), 0, 0);
        }
        CloseHandle(file);
    } else if (cover.command == 2 && cover.state == 1) {
        for (i = 0; i < cover.count; i++)
            if (!cover_hit[i].order && *(volatile u8 *)cover_va[i] == 0xCC)
                *(volatile u8 *)cover_va[i] = cover_byte[i];
        FlushInstructionCache(GetCurrentProcess(), 0, 0);
        cover.state = 2;
        file = CreateFileW(path(name, L"cover-hits.bin"), GENERIC_WRITE, 0, 0, CREATE_ALWAYS, 0, 0);
        if (file != INVALID_HANDLE_VALUE) {
            WriteFile(file, cover_hit, sizeof *cover_hit * cover.count, &got, 0);
            CloseHandle(file);
        }
    }
}
#endif

#ifndef WOZMOD_TRUE_RATE_WATCH
#define WOZMOD_TRUE_RATE_WATCH
/* Write watch: which instructions write [watch.at, watch.at + watch.len), and how often. The host sets at, len and
   command 1; the page goes read-only, each write faults, its EIP is counted (watch.eip/count, 512 at most) and the
   write is let through by one single step with the page writable. command 2 restores the page. Slow: every write to
   the page faults, watched or not. Game thread only (other threads' writes to the page are let through too). */
#define WATCH_EIPS 512
static struct {
    char magic[16];
    volatile u32 command, state, at, len, faults, hits;
    u32 eip[WATCH_EIPS], count[WATCH_EIPS];
    volatile u32 hardware;  /* 1: a debug register (len 1, 2 or 4, aligned) instead of the page; EIP is then the
                               instruction after the write */
} watch = {"ENRT-WATCH-BLK"};
static u32 watch_page;
static DWORD watch_old;
static volatile LONG watch_stepping;

static LONG CALLBACK watch_trap(EXCEPTION_POINTERS *e)
{
    DWORD code = e->ExceptionRecord->ExceptionCode, old;
    if (code == EXCEPTION_SINGLE_STEP && watch.hardware && watch.state == 1 && (e->ContextRecord->Dr6 & 1)) {
        u32 ip = e->ContextRecord->Eip, i;
        watch.hits++;
        for (i = 0; i < WATCH_EIPS && watch.eip[i] && watch.eip[i] != ip; i++)
            ;
        if (i < WATCH_EIPS)
            watch.eip[i] = ip, watch.count[i]++;
        e->ContextRecord->Dr6 = 0;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (code == EXCEPTION_SINGLE_STEP && watch_stepping) {
        watch_stepping = 0;
        if (watch.state == 1)
            VirtualProtect((void *)watch_page, 4096, PAGE_READONLY, &old);
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (code == EXCEPTION_ACCESS_VIOLATION && watch.state == 1 && e->ExceptionRecord->NumberParameters >= 2
        && e->ExceptionRecord->ExceptionInformation[0] == 1
        && (e->ExceptionRecord->ExceptionInformation[1] & ~0xFFFu) == watch_page) {
        u32 a = e->ExceptionRecord->ExceptionInformation[1], ip = e->ContextRecord->Eip, i;
        watch.faults++;
        if (a + 4 > watch.at && a < watch.at + watch.len) {
            watch.hits++;
            for (i = 0; i < WATCH_EIPS && watch.eip[i] && watch.eip[i] != ip; i++)
                ;
            if (i < WATCH_EIPS)
                watch.eip[i] = ip, watch.count[i]++;
        }
        VirtualProtect((void *)watch_page, 4096, PAGE_READWRITE, &old);
        watch_stepping = 1;
        e->ContextRecord->EFlags |= 0x100;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

static void watch_pump(void)
{
    static int handler;
    DWORD old;
    if (watch.command == 1 && watch.state != 1) {
        if (!handler)
            AddVectoredExceptionHandler(1, watch_trap), handler = 1;
        ZeroMemory(watch.eip, sizeof watch.eip), ZeroMemory(watch.count, sizeof watch.count);
        watch.faults = watch.hits = 0;
        watch_page = watch.at & ~0xFFFu;
        watch.state = 1;
        if (watch.hardware) {
            CONTEXT c;
            ZeroMemory(&c, sizeof c);
            c.ContextFlags = CONTEXT_DEBUG_REGISTERS;
            c.Dr0 = watch.at;
            c.Dr7 = 1 | 1u << 16 | (watch.len == 4 ? 3u : watch.len == 2 ? 1u : 0u) << 18;
            if (!SetThreadContext(GetCurrentThread(), &c))
                watch.state = 3;
        } else
            VirtualProtect((void *)watch_page, 4096, PAGE_READONLY, &watch_old);
    } else if (watch.command == 2 && watch.state == 1) {
        watch.state = 2;
        if (watch.hardware) {
            CONTEXT c;
            ZeroMemory(&c, sizeof c);
            c.ContextFlags = CONTEXT_DEBUG_REGISTERS;
            SetThreadContext(GetCurrentThread(), &c);
        } else
            VirtualProtect((void *)watch_page, 4096, PAGE_READWRITE, &old);
    }
}
#endif

#ifndef WOZMOD_TRUE_RATE_TRACE
#define WOZMOD_TRUE_RATE_TRACE
/* Call trace: up to 16 code addresses (the host writes va[] and count, then command 1); every time one runs, its time
   (performance counter), address, return address, the first four stack arguments and ecx go to a ring of 4096.
   The int3 is put back after a single step. command 2 removes them all. Game thread or any thread. */
#define TRACE_VAS 16
#define TRACE_RING 4096
static struct {
    char magic[16];
    volatile u32 command, state, count, next;
    u32 va[TRACE_VAS];
    struct { long long at; u32 va, ret, arg[4], ecx, pad; } rec[TRACE_RING];
} calls = {"ENRT-CALLS-BLK"};
static u8 calls_byte[TRACE_VAS];
static volatile u32 calls_rearm;  /* index + 1 of the int3 to put back after the single step */

static LONG CALLBACK calls_trap(EXCEPTION_POINTERS *e)
{
    DWORD code = e->ExceptionRecord->ExceptionCode;
    u32 at = (u32)e->ExceptionRecord->ExceptionAddress, i, n;
    LARGE_INTEGER now;
    if (code == EXCEPTION_SINGLE_STEP && calls_rearm) {
        i = calls_rearm - 1, calls_rearm = 0;
        if (calls.state == 1)
            *(volatile u8 *)calls.va[i] = 0xCC, FlushInstructionCache(GetCurrentProcess(), (void *)calls.va[i], 1);
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (code != EXCEPTION_BREAKPOINT || calls.state != 1)
        return EXCEPTION_CONTINUE_SEARCH;
    for (i = 0; i < calls.count && calls.va[i] != at && calls.va[i] != at - 1; i++)
        ;
    if (i == calls.count)
        return EXCEPTION_CONTINUE_SEARCH;
    at = calls.va[i];
    QueryPerformanceCounter(&now);
    n = calls.next++ % TRACE_RING;
    calls.rec[n].at = now.QuadPart, calls.rec[n].va = at, calls.rec[n].ecx = e->ContextRecord->Ecx;
    calls.rec[n].ret = ((const u32 *)e->ContextRecord->Esp)[0];
    calls.rec[n].arg[0] = ((const u32 *)e->ContextRecord->Esp)[1], calls.rec[n].arg[1] = ((const u32 *)e->ContextRecord->Esp)[2];
    calls.rec[n].arg[2] = ((const u32 *)e->ContextRecord->Esp)[3], calls.rec[n].arg[3] = ((const u32 *)e->ContextRecord->Esp)[4];
    *(volatile u8 *)at = calls_byte[i];
    FlushInstructionCache(GetCurrentProcess(), (void *)at, 1);
    calls_rearm = i + 1;
    e->ContextRecord->Eip = at;
    e->ContextRecord->EFlags |= 0x100;
    return EXCEPTION_CONTINUE_EXECUTION;
}

static void calls_pump(void)
{
    static int handler;
    DWORD old;
    u32 i;
    if (calls.command == 1 && calls.state != 1 && calls.count <= TRACE_VAS) {
        if (!handler)
            AddVectoredExceptionHandler(1, calls_trap), handler = 1;
        VirtualProtect((void *)0x401000, 0x2CB000, PAGE_EXECUTE_READWRITE, &old);
        for (i = 0; i < calls.count; i++)
            calls_byte[i] = *(const u8 *)calls.va[i];
        calls.state = 1;
        for (i = 0; i < calls.count; i++)
            *(volatile u8 *)calls.va[i] = 0xCC;
        FlushInstructionCache(GetCurrentProcess(), 0, 0);
    } else if (calls.command == 2 && calls.state == 1) {
        calls.state = 2;
        for (i = 0; i < calls.count; i++)
            *(volatile u8 *)calls.va[i] = calls_byte[i];
        FlushInstructionCache(GetCurrentProcess(), 0, 0);
    }
}
#endif
