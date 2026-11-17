/* wozmod-setup.exe: opens the setup window by starting the wozmod.exe beside it as `wozmod.exe --setup-window`.

   wozmod.exe is a console program, so Command Prompt waits for it and it can ask questions. Started from Explorer it
   would open a console window; this launcher is a windowed program that starts it with CREATE_NO_WINDOW instead: its
   console exists but is hidden, so no window flashes, and the build workers it starts share that hidden console.

   It stays running until wozmod.exe exits, and exits with its exit code. wozmod.exe writes to a temporary file
   rather than the hidden console. When it fails to start, or exits with an error, its last output goes into a
   message box, as the windowed PyInstaller program before this one showed an unhandled error.

   packaging/build_windows.sh builds it with MinGW, with PyInstaller's windowed icon and manifest, as before. */
#include <windows.h>
#include <wchar.h>

#define TITLE L"wozmod setup"
#define SHOWN 4000  /* the last bytes of the output a message box shows */

static void tell(const wchar_t *text)
{
    MessageBoxW(NULL, text, TITLE, MB_ICONERROR | MB_OK);
}

/* The end of what wozmod.exe wrote (UTF-8), after `opening`, in a message box. */
static void show_output(HANDLE output, const wchar_t *opening)
{
    static char bytes[SHOWN + 1];
    static wchar_t text[SHOWN + 512];
    LARGE_INTEGER size = {0}, start = {0};
    DWORD got = 0;
    GetFileSizeEx(output, &size);
    start.QuadPart = size.QuadPart > SHOWN ? size.QuadPart - SHOWN : 0;
    if (SetFilePointerEx(output, start, NULL, FILE_BEGIN))
        ReadFile(output, bytes, SHOWN, &got, NULL);
    int written = swprintf(text, 512, L"%ls\n\n", opening);
    if (written < 0)
        written = 0;
    MultiByteToWideChar(CP_UTF8, 0, bytes, (int)got, text + written, SHOWN + 512 - written - 1);
    tell(text);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show)
{
    (void)instance; (void)previous; (void)arguments; (void)show;
    static wchar_t program[32768], line[32768 + 32], folder[MAX_PATH + 1], log[MAX_PATH + 1];
    DWORD length = GetModuleFileNameW(NULL, program, 32768);
    wchar_t *name = wcsrchr(program, L'\\');
    if (length == 0 || length >= 32768 || name == NULL || (size_t)(name + 1 - program) + 11 >= 32768) {
        tell(L"wozmod-setup could not find its own folder.");
        return 1;
    }
    wcscpy(name + 1, L"wozmod.exe");
    swprintf(line, sizeof line / sizeof line[0], L"\"%ls\" --setup-window", program);

    /* Its output: a temporary file that Windows deletes once both programs have closed it. */
    SECURITY_ATTRIBUTES inherit = {sizeof inherit, NULL, TRUE};
    HANDLE output = INVALID_HANDLE_VALUE;
    if (GetTempPathW(MAX_PATH + 1, folder) && GetTempFileNameW(folder, L"woz", 0, log))
        output = CreateFileW(log, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                             &inherit, CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_DELETE_ON_CLOSE, NULL);

    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION started = {0};
    startup.cb = sizeof startup;
    if (output != INVALID_HANDLE_VALUE) {
        startup.dwFlags = STARTF_USESTDHANDLES;
        startup.hStdInput = NULL;
        startup.hStdOutput = startup.hStdError = output;
    }
    if (!CreateProcessW(program, line, NULL, NULL, output != INVALID_HANDLE_VALUE, CREATE_NO_WINDOW, NULL, NULL,
                        &startup, &started)) {
        tell(L"wozmod-setup could not start wozmod.exe. Keep wozmod.exe, wozmod-setup.exe and the _internal "
             L"folder together, as they came in the zip.");
        return 1;
    }
    WaitForSingleObject(started.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(started.hProcess, &code);
    CloseHandle(started.hThread);
    CloseHandle(started.hProcess);
    if (code != 0) {
        if (output != INVALID_HANDLE_VALUE)
            show_output(output, L"wozmod stopped with an error:");
        else
            tell(L"wozmod stopped with an error.");
    }
    if (output != INVALID_HANDLE_VALUE)
        CloseHandle(output);
    return (int)code;
}
