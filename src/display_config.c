#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#ifdef DISPLAY_VERIFY
#include <setupapi.h>
#include <cfgmgr32.h>
#endif

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

#ifdef DISPLAY_VERIFY
static void signal_host(BOOL success) {
    const char *message = success ? "M90-QXL-VERIFY-OK\n" : "M90-QXL-VERIFY-FAILED\n";
    HANDLE serial = CreateFileA("\\\\.\\COM1", GENERIC_WRITE, 0, NULL,
        OPEN_EXISTING, 0, NULL);
    DWORD written;
    if (serial == INVALID_HANDLE_VALUE) {
        write_text("COM1 verification signal unavailable: "); write_hex(GetLastError());
        return;
    }
    WriteFile(serial, message, text_length(message), &written, NULL);
    CloseHandle(serial);
}

static BOOL is_qxl(const char *description) {
    static const char wanted[] = "Red Hat QXL GPU";
    DWORD index = 0;
    while (wanted[index] != '\0') {
        if (description[index] != wanted[index]) return FALSE;
        ++index;
    }
    return description[index] == '\0';
}

static void log_qxl_pnp_status(void) {
    HMODULE setupapi = LoadLibraryA("setupapi.dll");
    HMODULE cfgmgr = LoadLibraryA("cfgmgr32.dll");
    HDEVINFO (WINAPI *get_devices)(const GUID *, PCSTR, HWND, DWORD);
    BOOL (WINAPI *enum_device)(HDEVINFO, DWORD, PSP_DEVINFO_DATA);
    BOOL (WINAPI *get_property)(HDEVINFO, PSP_DEVINFO_DATA, DWORD, PDWORD, PBYTE, DWORD, PDWORD);
    BOOL (WINAPI *destroy_devices)(HDEVINFO);
    CONFIGRET (WINAPI *get_status)(PULONG, PULONG, DEVINST, ULONG);
    HDEVINFO devices;
    DWORD index;
    if (setupapi == NULL || cfgmgr == NULL) {
        write_text("PnP API unavailable\r\n");
        return;
    }
    get_devices = (void *)GetProcAddress(setupapi, "SetupDiGetClassDevsA");
    enum_device = (void *)GetProcAddress(setupapi, "SetupDiEnumDeviceInfo");
    get_property = (void *)GetProcAddress(setupapi, "SetupDiGetDeviceRegistryPropertyA");
    destroy_devices = (void *)GetProcAddress(setupapi, "SetupDiDestroyDeviceInfoList");
    get_status = (void *)GetProcAddress(cfgmgr, "CM_Get_DevNode_Status");
    if (!get_devices || !enum_device || !get_property || !destroy_devices || !get_status) {
        write_text("PnP export unavailable\r\n");
        return;
    }
    devices = get_devices(NULL, "PCI", NULL, DIGCF_ALLCLASSES | DIGCF_PRESENT);
    if (devices == INVALID_HANDLE_VALUE) {
        write_text("PnP enumeration unavailable: "); write_hex(GetLastError());
        return;
    }
    for (index = 0;; ++index) {
        SP_DEVINFO_DATA device;
        char hardware_ids[1024];
        DWORD value_type = 0, required = 0;
        ULONG status = 0, problem = 0;
        CONFIGRET result;
        zero_memory(&device, sizeof(device));
        device.cbSize = sizeof(device);
        if (!enum_device(devices, index, &device)) break;
        zero_memory(hardware_ids, sizeof(hardware_ids));
        if (!get_property(devices, &device, SPDRP_HARDWAREID, &value_type,
                          (PBYTE)hardware_ids, sizeof(hardware_ids), &required)) continue;
        if (text_length(hardware_ids) < 16 ||
            hardware_ids[0] != 'P' || hardware_ids[4] != 'V' ||
            hardware_ids[8] != '1' || hardware_ids[9] != 'B' ||
            hardware_ids[10] != '3' || hardware_ids[11] != '6') continue;
        write_text("QXL PnP device index: "); write_hex(index);
        write_text("QXL hardware ID: "); write_text(hardware_ids); write_text("\r\n");
        result = get_status(&status, &problem, device.DevInst, 0);
        write_text("QXL CM result: "); write_hex(result);
        write_text("QXL CM status: "); write_hex(status);
        write_text("QXL CM problem: "); write_hex(problem);
    }
    destroy_devices(devices);
}
#endif

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
#ifdef DISPLAY_VERIFY
    DWORD qxl_count = 0;
    DWORD active_primary = 0;
#endif
    LONG apply_result;

#ifdef DISPLAY_VERIFY
    g_log = CreateFileA("C:\\NVRAM\\display_verify.log", GENERIC_WRITE,
#elif defined(DISPLAY_BOOTSTRAP)
    g_log = CreateFileA("C:\\NVRAM\\display_bootstrap.log", GENERIC_WRITE,
#else
    g_log = CreateFileA("C:\\NVRAM\\display_config.log", GENERIC_WRITE,
#endif
        FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
#ifdef DISPLAY_VERIFY
    write_text("M90 XP QXL display verification\r\n");
#elif defined(DISPLAY_BOOTSTRAP)
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
#ifdef DISPLAY_VERIFY
        if (is_qxl(device.DeviceString)) {
            ++qxl_count;
            if ((device.StateFlags & (DISPLAY_DEVICE_PRIMARY_DEVICE | DISPLAY_DEVICE_ATTACHED_TO_DESKTOP)) ==
                (DISPLAY_DEVICE_PRIMARY_DEVICE | DISPLAY_DEVICE_ATTACHED_TO_DESKTOP)) ++active_primary;
        }
#endif

        zero_memory(&mode, sizeof(mode));
        mode.dmSize = sizeof(mode);
        have_mode = enum_settings(device.DeviceName, ENUM_CURRENT_SETTINGS, &mode);
        if (have_mode && (device.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE)) {
            primary_width = mode.dmPelsWidth;
            primary_height = mode.dmPelsHeight;
            /* The service UI assumes true-color surfaces on both outputs. */
            mode.dmFields = DM_POSITION | DM_PELSWIDTH | DM_PELSHEIGHT | DM_BITSPERPEL;
            mode.dmPosition.x = 0;
            mode.dmPosition.y = 0;
            mode.dmBitsPerPel = 32;
            result = change_settings(device.DeviceName, &mode, NULL,
                CDS_UPDATEREGISTRY | CDS_NORESET, NULL);
            write_text("Primary true-color result: "); write_hex((DWORD)result);
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
#ifdef DISPLAY_VERIFY
    write_text("Recognized QXL displays: "); write_hex(qxl_count);
    write_text("Active QXL primary: "); write_hex(active_primary);
    log_qxl_pnp_status();
    signal_host(qxl_count == 2 && active_primary == 1 && attached == 1 &&
        apply_result == DISP_CHANGE_SUCCESSFUL);
    Sleep(120000);
    ExitProcess(qxl_count == 2 && active_primary == 1 && attached == 1 &&
        apply_result == DISP_CHANGE_SUCCESSFUL ? 0 : 12);
#elif defined(DISPLAY_BOOTSTRAP)
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
