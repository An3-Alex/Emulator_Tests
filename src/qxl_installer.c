#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <setupapi.h>

#ifndef INSTALLER_TITLE
#define INSTALLER_TITLE "QXL XP one-shot installer v2 (SetupAPI only)\r\n"
#endif
#ifndef INSTALLER_INF_PATH
#define INSTALLER_INF_PATH "C:\\NVRAM\\qxl-driver\\qxl.inf"
#endif
#ifndef INSTALLER_HARDWARE_ID
#define INSTALLER_HARDWARE_ID "PCI\\VEN_1B36&DEV_0100&SUBSYS_11001AF4&REV_02"
#endif
#ifndef INSTALLER_ENUMERATOR
#define INSTALLER_ENUMERATOR "PCI"
#endif
#ifndef INSTALLER_LOG_PATH
#define INSTALLER_LOG_PATH "C:\\NVRAM\\qxl_install.log"
#endif
#ifndef INSTALLER_EXPECTED_DEVICES
#define INSTALLER_EXPECTED_DEVICES 2
#endif
#ifndef INSTALLER_OK_SIGNAL
#define INSTALLER_OK_SIGNAL "M90-QXL-SETUP-OK\n"
#endif
#ifndef INSTALLER_FAILED_SIGNAL
#define INSTALLER_FAILED_SIGNAL "M90-QXL-SETUP-FAILED\n"
#endif
#ifndef INSTALLER_REBOOT
#define INSTALLER_REBOOT 1
#endif

typedef BOOL (WINAPI *PFN_SETUP_COPY_OEM_INF_A)(PCSTR, PCSTR, DWORD, DWORD, PSTR, DWORD, PDWORD, PSTR *);
typedef HDEVINFO (WINAPI *PFN_SETUP_DI_GET_CLASS_DEVS_A)(const GUID *, PCSTR, HWND, DWORD);
typedef BOOL (WINAPI *PFN_SETUP_DI_ENUM_DEVICE_INFO)(HDEVINFO, DWORD, PSP_DEVINFO_DATA);
typedef BOOL (WINAPI *PFN_SETUP_DI_GET_DEVICE_REGISTRY_PROPERTY_A)(HDEVINFO, PSP_DEVINFO_DATA, DWORD, PDWORD, PBYTE, DWORD, PDWORD);
typedef BOOL (WINAPI *PFN_SETUP_DI_GET_DEVICE_INSTALL_PARAMS_A)(HDEVINFO, PSP_DEVINFO_DATA, PSP_DEVINSTALL_PARAMS_A);
typedef BOOL (WINAPI *PFN_SETUP_DI_SET_DEVICE_INSTALL_PARAMS_A)(HDEVINFO, PSP_DEVINFO_DATA, PSP_DEVINSTALL_PARAMS_A);
typedef BOOL (WINAPI *PFN_SETUP_DI_BUILD_DRIVER_INFO_LIST)(HDEVINFO, PSP_DEVINFO_DATA, DWORD);
typedef BOOL (WINAPI *PFN_SETUP_DI_ENUM_DRIVER_INFO_A)(HDEVINFO, PSP_DEVINFO_DATA, DWORD, DWORD, PSP_DRVINFO_DATA_A);
typedef BOOL (WINAPI *PFN_SETUP_DI_SET_SELECTED_DRIVER_A)(HDEVINFO, PSP_DEVINFO_DATA, PSP_DRVINFO_DATA_A);
typedef BOOL (WINAPI *PFN_SETUP_DI_CALL_CLASS_INSTALLER)(DI_FUNCTION, HDEVINFO, PSP_DEVINFO_DATA);
typedef BOOL (WINAPI *PFN_SETUP_DI_DESTROY_DRIVER_INFO_LIST)(HDEVINFO, PSP_DEVINFO_DATA, DWORD);
typedef BOOL (WINAPI *PFN_SETUP_DI_DESTROY_DEVICE_INFO_LIST)(HDEVINFO);

static HANDLE g_log = INVALID_HANDLE_VALUE;
static volatile LONG g_signing_helper_active = 1;

static DWORD text_length(const char *text) {
    DWORD length = 0;
    while (text[length] != '\0') ++length;
    return length;
}

static void zero_memory(void *memory, DWORD size) {
    volatile unsigned char *bytes = (volatile unsigned char *)memory;
    while (size-- != 0) *bytes++ = 0;
}

static void copy_text(char *destination, const char *source, DWORD capacity) {
    DWORD index = 0;
    if (capacity == 0) return;
    while (source[index] != '\0' && index + 1 < capacity) {
        destination[index] = source[index];
        ++index;
    }
    destination[index] = '\0';
}

static char ascii_lower(char value) {
    if (value >= 'A' && value <= 'Z') return (char)(value + ('a' - 'A'));
    return value;
}

static BOOL equal_ascii_ci(const char *left, const char *right) {
    DWORD index = 0;
    while (left[index] != '\0' && right[index] != '\0') {
        if (ascii_lower(left[index]) != ascii_lower(right[index])) return FALSE;
        ++index;
    }
    return left[index] == right[index];
}

static BOOL multistring_contains(const char *values, const char *wanted) {
    const char *current = values;
    while (*current != '\0') {
        if (equal_ascii_ci(current, wanted)) return TRUE;
        current += text_length(current) + 1;
    }
    return FALSE;
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

static void signal_host(BOOL success) {
    static const char ok[] = INSTALLER_OK_SIGNAL;
    static const char failed[] = INSTALLER_FAILED_SIGNAL;
    const char *message = success ? ok : failed;
    HANDLE serial = CreateFileA("\\\\.\\COM1", GENERIC_WRITE, 0, NULL,
        OPEN_EXISTING, 0, NULL);
    DWORD written;
    if (serial == INVALID_HANDLE_VALUE) {
        write_text("COM1 status signal unavailable: "); write_hex(GetLastError());
        return;
    }
    WriteFile(serial, message, text_length(message), &written, NULL);
    CloseHandle(serial);
}

static BOOL has_continue_label(const char *text) {
    static const char wanted[] = "continue anyway";
    DWORD i = 0;
    while (*text != '\0' && i < sizeof(wanted) - 1) {
        if (*text != '&' && *text != ' ') {
            char value = ascii_lower(*text);
            if (value != wanted[i]) return FALSE;
            ++i;
        } else if (*text == ' ' && wanted[i] == ' ') {
            ++i;
        }
        ++text;
    }
    return i == sizeof(wanted) - 1;
}

static BOOL CALLBACK continue_button(HWND child, LPARAM unused) {
    char label[128];
    char class_name[32];
    (void)unused;
    zero_memory(label, sizeof(label));
    zero_memory(class_name, sizeof(class_name));
    GetClassNameA(child, class_name, sizeof(class_name));
    GetWindowTextA(child, label, sizeof(label));
    if (equal_ascii_ci(class_name, "Button") && has_continue_label(label)) {
        write_text("Confirming selected unsigned QXL driver in XP guest.\r\n");
        SendMessageA(child, BM_CLICK, 0, 0);
        return FALSE;
    }
    return TRUE;
}

static DWORD WINAPI signing_helper(LPVOID unused) {
    (void)unused;
    while (g_signing_helper_active) {
        HWND dialog = FindWindowA(NULL, "Software Installation");
        if (dialog != NULL) EnumChildWindows(dialog, continue_button, 0);
        dialog = FindWindowA(NULL, "Hardware Installation");
        if (dialog != NULL) EnumChildWindows(dialog, continue_button, 0);
        Sleep(400);
    }
    return 0;
}

#ifdef INSTALLER_EARLY_DIALOG_HELPER
static HANDLE start_signing_helper(void) {
    HANDLE thread = CreateThread(NULL, 0, signing_helper, NULL, 0, NULL);
    if (thread == NULL) {
        write_text("Could not start signing-dialog helper: "); write_hex(GetLastError());
    } else {
        write_text("Signing-dialog helper started.\r\n");
    }
    return thread;
}
#endif

#define RESOLVE(module, variable, type, name) \
    variable = (type)GetProcAddress(module, name); \
    if (variable == NULL) { \
        write_text("Missing SetupAPI export: " name " error="); \
        write_hex(GetLastError()); \
        Sleep(120000); \
        ExitProcess(11); \
    }

void __stdcall mainCRTStartup(void) {
#ifndef INSTALLER_SOFTWARE_ONLY
    static const char inf_path[] = INSTALLER_INF_PATH;
    static const char hardware_id[] = INSTALLER_HARDWARE_ID;
    HMODULE setupapi;
    PFN_SETUP_COPY_OEM_INF_A setup_copy;
    PFN_SETUP_DI_GET_CLASS_DEVS_A get_class_devs;
    PFN_SETUP_DI_ENUM_DEVICE_INFO enum_device;
    PFN_SETUP_DI_GET_DEVICE_REGISTRY_PROPERTY_A get_property;
    PFN_SETUP_DI_GET_DEVICE_INSTALL_PARAMS_A get_params;
    PFN_SETUP_DI_SET_DEVICE_INSTALL_PARAMS_A set_params;
    PFN_SETUP_DI_BUILD_DRIVER_INFO_LIST build_drivers;
    PFN_SETUP_DI_ENUM_DRIVER_INFO_A enum_driver;
    PFN_SETUP_DI_SET_SELECTED_DRIVER_A select_driver;
    PFN_SETUP_DI_CALL_CLASS_INSTALLER call_installer;
    PFN_SETUP_DI_DESTROY_DRIVER_INFO_LIST destroy_drivers;
    PFN_SETUP_DI_DESTROY_DEVICE_INFO_LIST destroy_devices;
    HDEVINFO devices;
    DWORD index;
    DWORD matches = 0;
    DWORD installed = 0;
    BOOL copied;
#endif
    HANDLE signing_thread;

    g_log = CreateFileA(INSTALLER_LOG_PATH, GENERIC_WRITE, FILE_SHARE_READ,
        NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    write_text(INSTALLER_TITLE);
#ifdef INSTALLER_EARLY_DIALOG_HELPER
    /* Audio software-device installation may itself open a signing dialog. */
    signing_thread = start_signing_helper();
#endif
#ifdef INSTALLER_PREPARE
    write_text("Preparing XP audio software devices.\r\n");
    if (!INSTALLER_PREPARE()) {
        write_text("Required audio software device setup failed.\r\n");
        g_signing_helper_active = 0;
        if (signing_thread != NULL) {
            WaitForSingleObject(signing_thread, 2000);
            CloseHandle(signing_thread);
        }
        signal_host(FALSE);
        Sleep(120000);
        ExitProcess(14);
    }
    write_text("XP audio software devices ready.\r\n");
#endif
#ifdef INSTALLER_SOFTWARE_ONLY
    /* No hardware enumeration/installation until the next, separate boot. */
    write_text("Software audio preparation complete.\r\n");
    g_signing_helper_active = 0;
    if (signing_thread != NULL) {
        WaitForSingleObject(signing_thread, 2000);
        CloseHandle(signing_thread);
    }
    signal_host(TRUE);
    Sleep(120000);
    ExitProcess(0);
#else
#ifndef INSTALLER_EARLY_DIALOG_HELPER
    signing_thread = CreateThread(NULL, 0, signing_helper, NULL, 0, NULL);
    if (signing_thread == NULL) {
        write_text("Could not start signing-dialog helper: "); write_hex(GetLastError());
    }
#endif

    setupapi = LoadLibraryA("setupapi.dll");
    if (setupapi == NULL) {
        write_text("LoadLibrary setupapi.dll failed: ");
        write_hex(GetLastError());
        Sleep(120000);
        ExitProcess(10);
    }

    RESOLVE(setupapi, setup_copy, PFN_SETUP_COPY_OEM_INF_A, "SetupCopyOEMInfA");
    RESOLVE(setupapi, get_class_devs, PFN_SETUP_DI_GET_CLASS_DEVS_A, "SetupDiGetClassDevsA");
    RESOLVE(setupapi, enum_device, PFN_SETUP_DI_ENUM_DEVICE_INFO, "SetupDiEnumDeviceInfo");
    RESOLVE(setupapi, get_property, PFN_SETUP_DI_GET_DEVICE_REGISTRY_PROPERTY_A, "SetupDiGetDeviceRegistryPropertyA");
    RESOLVE(setupapi, get_params, PFN_SETUP_DI_GET_DEVICE_INSTALL_PARAMS_A, "SetupDiGetDeviceInstallParamsA");
    RESOLVE(setupapi, set_params, PFN_SETUP_DI_SET_DEVICE_INSTALL_PARAMS_A, "SetupDiSetDeviceInstallParamsA");
    RESOLVE(setupapi, build_drivers, PFN_SETUP_DI_BUILD_DRIVER_INFO_LIST, "SetupDiBuildDriverInfoList");
    RESOLVE(setupapi, enum_driver, PFN_SETUP_DI_ENUM_DRIVER_INFO_A, "SetupDiEnumDriverInfoA");
    RESOLVE(setupapi, select_driver, PFN_SETUP_DI_SET_SELECTED_DRIVER_A, "SetupDiSetSelectedDriverA");
    RESOLVE(setupapi, call_installer, PFN_SETUP_DI_CALL_CLASS_INSTALLER, "SetupDiCallClassInstaller");
    RESOLVE(setupapi, destroy_drivers, PFN_SETUP_DI_DESTROY_DRIVER_INFO_LIST, "SetupDiDestroyDriverInfoList");
    RESOLVE(setupapi, destroy_devices, PFN_SETUP_DI_DESTROY_DEVICE_INFO_LIST, "SetupDiDestroyDeviceInfoList");

    SetLastError(ERROR_SUCCESS);
    copied = setup_copy(inf_path, NULL, SPOST_PATH, 0, NULL, 0, NULL, NULL);
    write_text("SetupCopyOEMInfA result: "); write_hex((DWORD)copied);
    write_text("SetupCopyOEMInfA last error: "); write_hex(GetLastError());

    devices = get_class_devs(NULL, INSTALLER_ENUMERATOR, NULL,
        DIGCF_PRESENT | DIGCF_ALLCLASSES);
    if (devices == INVALID_HANDLE_VALUE) {
        write_text("SetupDiGetClassDevsA failed: "); write_hex(GetLastError());
        Sleep(120000);
        ExitProcess(12);
    }

    for (index = 0;; ++index) {
        SP_DEVINFO_DATA device;
        char ids[1024];
        DWORD value_type = 0;
        DWORD required = 0;
        SP_DEVINSTALL_PARAMS_A params;
        SP_DRVINFO_DATA_A driver;
        BOOL ok;

        zero_memory(&device, sizeof(device));
        device.cbSize = sizeof(device);
        if (!enum_device(devices, index, &device)) {
            if (GetLastError() == ERROR_NO_MORE_ITEMS) break;
            write_text("SetupDiEnumDeviceInfo failed: "); write_hex(GetLastError());
            continue;
        }
        zero_memory(ids, sizeof(ids));
        if (!get_property(devices, &device, SPDRP_HARDWAREID, &value_type,
                (PBYTE)ids, sizeof(ids), &required)) continue;
        if (!multistring_contains(ids, hardware_id)) continue;

        ++matches;
        write_text("Matched target device index: "); write_hex(index);
        zero_memory(&params, sizeof(params));
        params.cbSize = sizeof(params);
        if (!get_params(devices, &device, &params)) {
            write_text("GetDeviceInstallParams failed: "); write_hex(GetLastError());
            continue;
        }
        params.Flags |= DI_ENUMSINGLEINF;
        copy_text(params.DriverPath, inf_path, MAX_PATH);
        if (!set_params(devices, &device, &params)) {
            write_text("SetDeviceInstallParams failed: "); write_hex(GetLastError());
            continue;
        }
        if (!build_drivers(devices, &device, SPDIT_COMPATDRIVER)) {
            write_text("BuildDriverInfoList failed: "); write_hex(GetLastError());
            continue;
        }
        zero_memory(&driver, sizeof(driver));
        driver.cbSize = sizeof(driver);
        ok = enum_driver(devices, &device, SPDIT_COMPATDRIVER, 0, &driver);
        if (!ok) {
            write_text("EnumDriverInfo failed: "); write_hex(GetLastError());
            destroy_drivers(devices, &device, SPDIT_COMPATDRIVER);
            continue;
        }
        write_text("Selected driver: "); write_text(driver.Description); write_text("\r\n");
        if (!select_driver(devices, &device, &driver)) {
            write_text("SetSelectedDriver failed: "); write_hex(GetLastError());
            destroy_drivers(devices, &device, SPDIT_COMPATDRIVER);
            continue;
        }
#ifdef INSTALLER_REGISTER_INTERFACES
        ok = call_installer(DIF_REGISTER_COINSTALLERS, devices, &device);
        write_text("DIF_REGISTERCOINSTALLERS result: "); write_hex((DWORD)ok);
        if (ok) {
            ok = call_installer(DIF_INSTALLINTERFACES, devices, &device);
            write_text("DIF_INSTALLINTERFACES result: "); write_hex((DWORD)ok);
        }
        if (!ok) {
            write_text("Interface registration failed: "); write_hex(GetLastError());
            destroy_drivers(devices, &device, SPDIT_COMPATDRIVER);
            continue;
        }
#endif
        SetLastError(ERROR_SUCCESS);
        ok = call_installer(DIF_INSTALLDEVICE, devices, &device);
        write_text("DIF_INSTALLDEVICE result: "); write_hex((DWORD)ok);
        write_text("DIF_INSTALLDEVICE last error: "); write_hex(GetLastError());
        if (ok) ++installed;
        destroy_drivers(devices, &device, SPDIT_COMPATDRIVER);
    }
    destroy_devices(devices);
    g_signing_helper_active = 0;
    if (signing_thread != NULL) {
        WaitForSingleObject(signing_thread, 2000);
        CloseHandle(signing_thread);
    }
    write_text("Matched devices: "); write_hex(matches);
    write_text("Installed devices: "); write_hex(installed);
    if (matches == INSTALLER_EXPECTED_DEVICES && installed == INSTALLER_EXPECTED_DEVICES) {
        write_text("Installer complete.\r\n");
        signal_host(TRUE);
#if INSTALLER_REBOOT
        Sleep(1000);
        if (!ExitWindowsEx(EWX_REBOOT | EWX_FORCEIFHUNG, 0)) {
            write_text("ExitWindowsEx failed: "); write_hex(GetLastError());
        }
#endif
    } else {
        write_text("Installer incomplete.\r\n");
        signal_host(FALSE);
    }
    Sleep(120000);
    ExitProcess(matches == INSTALLER_EXPECTED_DEVICES && installed == INSTALLER_EXPECTED_DEVICES ? 0 : 13);
#endif
}
