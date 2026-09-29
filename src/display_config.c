#define WIN32_LEAN_AND_MEAN
#include <windows.h>

typedef BOOL (WINAPI *PFN_ENUM_DISPLAY_DEVICES_A)(LPCSTR, DWORD, PDISPLAY_DEVICEA, DWORD);
typedef BOOL (WINAPI *PFN_ENUM_DISPLAY_SETTINGS_A)(LPCSTR, DWORD, PDEVMODEA);
typedef LONG (WINAPI *PFN_CHANGE_DISPLAY_SETTINGS_EX_A)(LPCSTR, PDEVMODEA, HWND, DWORD, LPVOID);

static HANDLE g_log = INVALID_HANDLE_VALUE;

static DWORD text_length(const char *text) {
    DWORD length = 0;
    while (text[length] != '\0') ++length;
    return length;
}

static void zero_memory(void *memory, DWORD size) {
    volatile unsigned char *bytes = (volatile unsigned char *)memory;
    while (size-- != 0) *bytes++ = 0;
}

static void write_text(const char *text) {
    DWORD written;
    DWORD length = text_length(text);
    HANDLE console = GetStdHandle(STD_OUTPUT_HANDLE);
    if (console != NULL && console != INVALID_HANDLE_VALUE)
        WriteFile(console, text, length, &written, NULL);
    if (g_log != INVALID_HANDLE_VALUE) {
        WriteFile(g_log, text, length, &written, NULL);
        FlushFileBuffers(g_log);
    }
}

static void write_hex(DWORD value) {
    static const char digits[] = "0123456789ABCDEF";
    char buffer[13] = "0x00000000\r\n";
    int index;
    for (index = 0; index < 8; ++index) {
        buffer[9 - index] = digits[value & 0x0Fu];
        value >>= 4;
    }
    write_text(buffer);
}

void __stdcall mainCRTStartup(void) {
    HMODULE user32;
    PFN_ENUM_DISPLAY_DEVICES_A enum_devices;
    PFN_ENUM_DISPLAY_SETTINGS_A enum_settings;
    PFN_CHANGE_DISPLAY_SETTINGS_EX_A change_settings;
    DWORD index;
    DWORD primary_width = 640;
    DWORD primary_height = 480;
    DWORD found = 0;
    DWORD attached = 0;
    LONG apply_result;

#ifdef DISPLAY_BOOTSTRAP
    g_log = CreateFileA("C:\\NVRAM\\display_bootstrap.log", GENERIC_WRITE,
#else
    g_log = CreateFileA("C:\\NVRAM\\display_config.log", GENERIC_WRITE,
#endif
        FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
#ifdef DISPLAY_BOOTSTRAP
    write_text("M90 XP dual-display bootstrap\r\n");
#else
    write_text("M90 XP dual-display configurator\r\n");
#endif
    user32 = LoadLibraryA("user32.dll");
    if (user32 == NULL) {
        write_text("LoadLibrary user32.dll failed: "); write_hex(GetLastError());
        Sleep(120000); ExitProcess(10);
    }
    enum_devices = (PFN_ENUM_DISPLAY_DEVICES_A)GetProcAddress(user32, "EnumDisplayDevicesA");
    enum_settings = (PFN_ENUM_DISPLAY_SETTINGS_A)GetProcAddress(user32, "EnumDisplaySettingsA");
    change_settings = (PFN_CHANGE_DISPLAY_SETTINGS_EX_A)GetProcAddress(user32, "ChangeDisplaySettingsExA");
    if (enum_devices == NULL || enum_settings == NULL || change_settings == NULL) {
        write_text("Missing user32 display export: "); write_hex(GetLastError());
        Sleep(120000); ExitProcess(11);
    }

    for (index = 0;; ++index) {
        DISPLAY_DEVICEA device;
        DEVMODEA mode;
        BOOL have_mode;
        LONG result;
        zero_memory(&device, sizeof(device));
        device.cb = sizeof(device);
        if (!enum_devices(NULL, index, &device, 0)) break;
        ++found;
        write_text("Device "); write_hex(index);
        write_text("Name: "); write_text(device.DeviceName); write_text("\r\n");
        write_text("Description: "); write_text(device.DeviceString); write_text("\r\n");
        write_text("StateFlags: "); write_hex(device.StateFlags);

        zero_memory(&mode, sizeof(mode));
        mode.dmSize = sizeof(mode);
        have_mode = enum_settings(device.DeviceName, ENUM_CURRENT_SETTINGS, &mode);
        if (have_mode && (device.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE)) {
            primary_width = mode.dmPelsWidth;
            primary_height = mode.dmPelsHeight;
            continue;
        }
        if ((device.StateFlags & DISPLAY_DEVICE_MIRRORING_DRIVER) != 0) continue;
        if ((device.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE) != 0) continue;

        zero_memory(&mode, sizeof(mode));
        mode.dmSize = sizeof(mode);
        if (!enum_settings(device.DeviceName, ENUM_REGISTRY_SETTINGS, &mode)) {
            if (!enum_settings(device.DeviceName, 0, &mode)) {
                write_text("No usable mode for secondary, error: ");
                write_hex(GetLastError());
                continue;
            }
        }
        mode.dmFields = DM_POSITION | DM_PELSWIDTH | DM_PELSHEIGHT | DM_BITSPERPEL;
        mode.dmPosition.x = (LONG)primary_width;
        mode.dmPosition.y = 0;
        mode.dmPelsWidth = primary_width;
        mode.dmPelsHeight = primary_height;
        mode.dmBitsPerPel = 32;
        result = change_settings(device.DeviceName, &mode, NULL,
            CDS_UPDATEREGISTRY | CDS_NORESET, NULL);
        write_text("Attach result: "); write_hex((DWORD)result);
        if (result == DISP_CHANGE_SUCCESSFUL) ++attached;
    }

    apply_result = change_settings(NULL, NULL, NULL, 0, NULL);
    write_text("Enumerated displays: "); write_hex(found);
    write_text("Attached secondary displays: "); write_hex(attached);
    write_text("Global apply result: "); write_hex((DWORD)apply_result);
#ifdef DISPLAY_BOOTSTRAP
    {
        STARTUPINFOA startup;
        PROCESS_INFORMATION process;
        char command_line[] = "C:\\WINDOWS\\explorer_adp_before_qxl.exe";
        BOOL launched;
        DWORD exit_code = 0;
        zero_memory(&startup, sizeof(startup));
        zero_memory(&process, sizeof(process));
        startup.cb = sizeof(startup);
        launched = CreateProcessA(
            command_line, command_line, NULL, NULL, FALSE, 0, NULL,
            "C:\\WINDOWS", &startup, &process);
        write_text("ADP loader launch result: "); write_hex((DWORD)launched);
        write_text("ADP loader launch last error: "); write_hex(GetLastError());
        if (!launched) ExitProcess(14);
        CloseHandle(process.hThread);
        WaitForSingleObject(process.hProcess, INFINITE);
        GetExitCodeProcess(process.hProcess, &exit_code);
        CloseHandle(process.hProcess);
        ExitProcess(exit_code);
    }
#else
    write_text("Configurator halted for host-side verification.\r\n");
    Sleep(120000);
    ExitProcess(attached != 0 && apply_result == DISP_CHANGE_SUCCESSFUL ? 0 : 12);
#endif
}
