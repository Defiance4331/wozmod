#ifndef WOZMOD_LATENCY_PROBE_H
#define WOZMOD_LATENCY_PROBE_H
/* Test builds only (ENRT_TEST): input latency, for work/true-rate/latency.py. Each key press the window procedure
   sees is stamped with the time Windows queued it (GetMessageTime, moved onto the performance counter), and each
   present with the time Present returned and the player's motion (chr 0 +0x18CC, 0xFFFF outside a battle). The host
   pairs a press with the first present whose motion changed after it. Under Wine the window procedure sees no key
   messages (DirectInput takes them), so the harness stamps its presses instead (evidence/key-presses.tsv, on
   CLOCK_MONOTONIC_RAW, the clock Wine's performance counter reads). */
#define LAT_KEYS 256
#define LAT_PRESENTS 8192
static struct {
    char magic[16];
    volatile u32 keys, presents;
    long long freq;
    long long key_at[LAT_KEYS];
    u32 key_vk[LAT_KEYS];
    struct { long long at; u32 motion, pad; } present[LAT_PRESENTS];
} lat = {"ENRT-LATENCY-BK"};

static void lat_key(u32 message, u32 wparam, u32 lparam)
{
    LARGE_INTEGER now, rate;
    DWORD ms, queued;
    if (message != WM_KEYDOWN || lparam & 0x40000000)  /* a first press, not a repeat */
        return;
    QueryPerformanceCounter(&now), QueryPerformanceFrequency(&rate);
    ms = GetTickCount(), queued = (DWORD)GetMessageTime();
    lat.freq = rate.QuadPart;
    lat.key_at[lat.keys % LAT_KEYS] = now.QuadPart - (long long)(DWORD)(ms - queued) * rate.QuadPart / 1000;
    lat.key_vk[lat.keys % LAT_KEYS] = wparam;
    lat.keys++;
}

static void lat_present(void)
{
    LARGE_INTEGER now, rate;
    u32 p, motion = 0xFFFF, i = lat.presents % LAT_PRESENTS;
    QueryPerformanceCounter(&now);
    if (!lat.freq)
        QueryPerformanceFrequency(&rate), lat.freq = rate.QuadPart;
    if (*(volatile u32 *)0x7C8518 == 2 && (p = *(volatile u32 *)(0x9DE420 + 0x18CC)) >= 0x400000 && p < 0x7F000000)
        motion = *(volatile unsigned short *)p;
    lat.present[i].at = now.QuadPart, lat.present[i].motion = motion;
    lat.presents++;
}
#endif
