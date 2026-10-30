#ifndef WOZMOD_CRASH_LOG_H
#define WOZMOD_CRASH_LOG_H

#include <windows.h>

#define EXIT_LINE "process exit"  /* the log's last line when the game closes normally */

/* A run whose log does not end with EXIT_LINE crashed, or was ended from outside (a freeze closed from Task Manager).
   Keep its log as `crash`, replacing an older one, before the next run overwrites `log`: a player who starts the
   game again after a crash still has the log to report. A crash the DLL sees has already written `crash` as it
   happened (crash_keep_now); this catches the runs that ended where no code of ours could run. */
static inline void keep_crash_log(const WCHAR *log, const WCHAR *crash)
{
    static const char mark[] = EXIT_LINE;
    char tail[64];  /* the exit line and its value fit with room to spare */
    DWORD size, got = 0, i, j;
    HANDLE file = CreateFileW(log, GENERIC_READ, FILE_SHARE_READ, 0, OPEN_EXISTING, 0, 0);
    if (file == INVALID_HANDLE_VALUE)
        return;
    size = GetFileSize(file, 0);
    SetFilePointer(file, size > sizeof tail ? size - sizeof tail : 0, 0, FILE_BEGIN);
    ReadFile(file, tail, sizeof tail, &got, 0);
    CloseHandle(file);
    for (i = 0; i + sizeof mark - 1 <= got; i++) {
        for (j = 0; mark[j] && tail[i + j] == mark[j]; j++)
            ;
        if (!mark[j])
            return;
    }
    if (got)
        MoveFileExW(log, crash, MOVEFILE_REPLACE_EXISTING);
}

/* An unhandled exception's code, place and the code addresses on its stack, written through line before Windows ends
   the game: what the crash was and which module (OROCHI_Z.exe, d3d9.dll, a driver) it was in, without the player
   reading it out of Event Viewer. Offsets are from the module's base, so the game's own addresses read as its VAs
   less 0x400000. A value of 0 prints bare. */
typedef void (*crash_line_fn)(const char *text, unsigned int value);
static crash_line_fn crash_line;
static LPTOP_LEVEL_EXCEPTION_FILTER crash_before;
static LPTOP_LEVEL_EXCEPTION_FILTER (WINAPI *crash_set_real)(LPTOP_LEVEL_EXCEPTION_FILTER) = SetUnhandledExceptionFilter;
static WCHAR crash_log_path[MAX_PATH], crash_copy_path[MAX_PATH];

#define CRASH_STACK_DWORDS 1024  /* how far up the stack to look: a few frames of a deep call, not the whole thread */
#define CRASH_STACK_HITS 16

/* "<what> <module>, offset" and the offset, for an address in a loaded module's code; 0 when it is in none. A module
   outside the game's folder is named with its folder: Windows' own d3d9.dll (system32\d3d9.dll) and the game's
   (d3d9.dll) share a name. */
static int crash_where(const char *what, const void *at)
{
    MEMORY_BASIC_INFORMATION page;
    HMODULE module;
    char line[160], file[MAX_PATH], game[MAX_PATH], *name = file, *folder = file;
    int n = 0, i, g = 0;
    if (!VirtualQuery(at, &page, sizeof page) || page.State != MEM_COMMIT
        || !(page.Protect & (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY)))
        return 0;
    if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            (const char *)at, &module) || !GetModuleFileNameA(module, file, sizeof file))
        return 0;
    for (i = 0; file[i]; i++)
        if (file[i] == '\\' || file[i] == '/')
            folder = name, name = file + i + 1;
    if (GetModuleFileNameA(0, game, sizeof game))
        for (i = 0; game[i]; i++)
            if (game[i] == '\\' || game[i] == '/')
                g = i + 1;
    if (name - file != g || CompareStringA(LOCALE_INVARIANT, NORM_IGNORECASE, file, g, game, g) != CSTR_EQUAL)
        name = folder;  /* not the game's folder: keep the folder's name */
    while (*what && n < 60)
        line[n++] = *what++;
    while (*name && n < 140)
        line[n++] = *name++;
    lstrcpyA(line + n, ", offset");
    crash_line(line, (unsigned int)((const char *)at - (const char *)module));
    return 1;
}

/* 1 when the code before at is a call: E8 rel32, or FF /2 (call through a register or memory, 2 to 7 bytes). A return
   address passes; a function pointer or text that happens to fall in a module's code (a message's UTF-16 on the
   stack) does not. Only bytes from the start of at's region are read. */
static int crash_after_call(const unsigned char *at)
{
    MEMORY_BASIC_INFORMATION page;
    const unsigned char *base;
    int n;
    if (!VirtualQuery(at, &page, sizeof page) || page.State != MEM_COMMIT
        || !(page.Protect & (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY)))
        return 0;
    base = (const unsigned char *)page.BaseAddress;
    if (at - 5 >= base && at[-5] == 0xE8)
        return 1;
    for (n = 2; n <= 7; n++) {
        const unsigned char *c = at - n;
        unsigned int mod, rm;
        if (c < base || c[0] != 0xFF || (c[1] & 0x38) != 0x10)
            continue;
        mod = c[1] >> 6, rm = c[1] & 7;
        if (2 + (mod != 3 && rm == 4) + (mod == 1 ? 1 : mod == 2 || (mod == 0 && rm == 5)
                                                        || (mod == 0 && rm == 4 && n >= 3 && (c[2] & 7) == 5) ? 4 : 0)
            == n)
            return 1;
    }
    return 0;
}

/* The return addresses on the stack from sp up, each once: the callers of whatever failed. A repeat (a return address
   left in an older frame, the same caller twice) would take the room of a caller further up. */
static void crash_stack(const DWORD *sp)
{
    const DWORD *top = (const DWORD *)((NT_TIB *)NtCurrentTeb())->StackBase;
    DWORD seen[CRASH_STACK_HITS];
    int i, j, hits = 0;
    for (i = 0; i < CRASH_STACK_DWORDS && sp + i < top && hits < CRASH_STACK_HITS; i++) {
        for (j = 0; j < hits && seen[j] != sp[i]; j++)
            ;
        if (j == hits && crash_after_call((const unsigned char *)sp[i])
            && crash_where("crash: stack holds ", (const void *)sp[i]))
            seen[hits++] = sp[i];
    }
}

/* Copy the log as it stands to the crash log, replacing an older crash's, so the file is whole the moment the run
   fails rather than at the next start. The log is open for writing with FILE_SHARE_READ, which CopyFile's own open
   refuses; a second handle that shares writing reads it. The buffer is static: a stack overflow leaves little stack. */
static void crash_keep_now(void)
{
    static LONG busy;
    static char buffer[4096];
    DWORD got, wrote;
    HANDLE from, to;
    if (InterlockedExchange(&busy, 1))
        return;
    from = CreateFileW(crash_log_path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, 0, OPEN_EXISTING, 0, 0);
    if (from != INVALID_HANDLE_VALUE) {
        to = CreateFileW(crash_copy_path, GENERIC_WRITE, FILE_SHARE_READ, 0, CREATE_ALWAYS, 0, 0);
        if (to != INVALID_HANDLE_VALUE) {
            while (ReadFile(from, buffer, sizeof buffer, &got, 0) && got && WriteFile(to, buffer, got, &wrote, 0))
                ;
            CloseHandle(to);
        }
        CloseHandle(from);
    }
    InterlockedExchange(&busy, 0);
}

static LONG WINAPI crash_filter(EXCEPTION_POINTERS *e)
{
    static LONG once;
    const EXCEPTION_RECORD *r = e->ExceptionRecord;
    if (!InterlockedExchange(&once, 1)) {
        crash_line("crash: exception", (unsigned int)r->ExceptionCode);
        crash_line("crash: address", (unsigned int)r->ExceptionAddress);
        crash_where("crash: in ", r->ExceptionAddress);
        if (r->ExceptionCode == EXCEPTION_ACCESS_VIOLATION && r->NumberParameters >= 2)
            crash_line("crash: access (0 read, 1 write, 8 run)", (unsigned int)r->ExceptionInformation[0]),
                crash_line("crash: of address", (unsigned int)r->ExceptionInformation[1]);
        crash_stack((const DWORD *)e->ContextRecord->Esp);
        crash_keep_now();
    }
    return crash_before ? crash_before(e) : EXCEPTION_CONTINUE_SEARCH;
}

/* In place of the game's SetUnhandledExceptionFilter (its import slot points here). The game's C runtime sets its own
   filter at start-up, after the DLL's, and that one does not chain: through the plain call it would replace ours and
   no crash would be logged. A filter the game sets here runs after ours instead. A NULL filter is the C runtime ending
   the run itself (an invalid parameter, a failed buffer check, abort), with TerminateProcess or _exit next and no
   exception for crash_filter to see: log where it was called from and keep the log, then pass the call on. */
static inline LPTOP_LEVEL_EXCEPTION_FILTER WINAPI crash_set_filter(LPTOP_LEVEL_EXCEPTION_FILTER filter)
{
    LPTOP_LEVEL_EXCEPTION_FILTER was = crash_before;
    if (filter) {
        crash_before = filter;
        crash_set_real(crash_filter);
        return was;
    }
    crash_line("crash: the C runtime is ending the run", 0);
    crash_where("crash: called from ", __builtin_return_address(0));
    crash_stack((const DWORD *)__builtin_frame_address(0));
    crash_keep_now();
    return crash_set_real(0);
}

/* At load, before the game's own start-up: line writes each line to the log at log_path, which crash_keep_now copies
   to crash_path. */
static inline void crash_watch(crash_line_fn line, const WCHAR *log_path, const WCHAR *crash_path)
{
    crash_line = line;
    lstrcpynW(crash_log_path, log_path, MAX_PATH);
    lstrcpynW(crash_copy_path, crash_path, MAX_PATH);
    crash_before = crash_set_real(crash_filter);
}

#endif
