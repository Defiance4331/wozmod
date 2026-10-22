#ifndef WOZMOD_CRASH_LOG_H
#define WOZMOD_CRASH_LOG_H

#include <windows.h>

#define EXIT_LINE "process exit"  /* the log's last line when the game closes normally */

/* A run whose log does not end with EXIT_LINE crashed, or was ended from outside (a freeze closed from Task Manager).
   Keep its log as `crash`, replacing an older one, before the next run overwrites `log`: a player who starts the
   game again after a crash still has the log to report. */
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

#endif
