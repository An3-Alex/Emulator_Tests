/* Setup runs before all XP PnP ports are necessarily ready. Never claim success
 * to the host unless the real completion message was fully written. */
static BOOL m90_setup_status(const char *message,
    void (*log_text)(const char *), void (*log_number)(DWORD)) {
    DWORD attempt, written = 0, length = 0, error = ERROR_SUCCESS;
    HANDLE serial = INVALID_HANDLE_VALUE;
    COMMTIMEOUTS timeouts;
    while (message[length]) ++length;
    for (attempt = 0; attempt < 120; ++attempt) {
        serial = CreateFileA("\\\\.\\COM1", GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
        if (serial != INVALID_HANDLE_VALUE) break;
        error = GetLastError();
        if (attempt == 0) { log_text("Setup COM1 pending; error: "); log_number(error); }
        if (error != ERROR_FILE_NOT_FOUND && error != ERROR_PATH_NOT_FOUND &&
            error != ERROR_DEV_NOT_EXIST && error != ERROR_NOT_READY &&
            error != ERROR_SHARING_VIOLATION) break;
        Sleep(250);
    }
    if (serial == INVALID_HANDLE_VALUE) {
        log_text("Setup COM1 unavailable after bounded retry; error: "); log_number(error);
        return FALSE;
    }
    timeouts.ReadIntervalTimeout = MAXDWORD;
    timeouts.ReadTotalTimeoutMultiplier = timeouts.ReadTotalTimeoutConstant = 0;
    timeouts.WriteTotalTimeoutMultiplier = 0;
    timeouts.WriteTotalTimeoutConstant = 3000;
    if (!SetCommTimeouts(serial, &timeouts) || !WriteFile(serial, message, length, &written, NULL) || written != length) {
        error = GetLastError(); CloseHandle(serial);
        log_text("Setup COM1 completion write failed; error: "); log_number(error);
        return FALSE;
    }
    CloseHandle(serial);
    log_text("Setup COM1 completion sent.\r\n");
    return TRUE;
}
