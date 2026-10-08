/* Guest diagnostic logs live on the CF image. Once a log reaches
   M90_GUEST_LOG_LIMIT bytes it is emptied and reused from the start.
   Writers open their logs with FILE_APPEND_DATA, so other open handles
   simply continue at the new end. Kernel32 only; no CRT. */
#ifndef M90_GUEST_LOG_LIMIT_H
#define M90_GUEST_LOG_LIMIT_H

#include <windows.h>

#define M90_GUEST_LOG_LIMIT (10UL * 1024UL * 1024UL)
/* Check the size only after this many bytes written by this process. */
#define M90_GUEST_LOG_CHECK_BYTES (64UL * 1024UL)

static void m90_limit_log(const char *path)
{
    WIN32_FILE_ATTRIBUTE_DATA info;
    HANDLE file;
    if (!GetFileAttributesExA(path, GetFileExInfoStandard, &info)) return;
    if (!info.nFileSizeHigh && info.nFileSizeLow < M90_GUEST_LOG_LIMIT) return;
    file = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                       TRUNCATE_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
}

/* Call before writing length bytes; checks the first time and then every
   M90_GUEST_LOG_CHECK_BYTES. Not synchronized: a missed or extra check only
   moves the reset point slightly. */
static void m90_limit_log_before_write(const char *path, DWORD length)
{
    static DWORD written = M90_GUEST_LOG_CHECK_BYTES;
    if (written >= M90_GUEST_LOG_CHECK_BYTES) {
        written = 0;
        m90_limit_log(path);
    }
    written += length;
}

#endif
