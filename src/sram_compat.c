/*
 * XP/x86 compatibility proxy for the M90 SRAM device.
 *
 * Game and service discover the SRAM through SetupAPI and use two IOCTLs:
 *   0x9C402004 - read  (6-byte 24-bit offset/length request)
 *   0x9C402000 - write (same header followed by payload)
 *
 * The game loads the app-local FBWFLIB.dll; the verified service PE imports its
 * initialization export. ANSI and Unicode discovery share one backing file.
 * This proxy forwards the
 * two FBWF exports used by game.exe to the untouched system DLL and redirects
 * only the verified SRAM GUID/path/IOCTL sequence to a persistent image file.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <setupapi.h>
#include "guest_log_limit.h"

#define GAME_IAT_CREATEFILEW_RVA                    0x002FE11CUL
#define GAME_IAT_DEVICEIOCONTROL_RVA                0x002FE120UL
#define GAME_IAT_SETUPDIGETCLASSDEVSW_RVA           0x002FE2B0UL
#define GAME_IAT_SETUPDIGETDEVICEINTERFACEDETAILW_RVA 0x002FE2B4UL
#define GAME_IAT_SETUPDIDESTROYDEVICEINFOLIST_RVA   0x002FE2B8UL
#define GAME_IAT_SETUPDIENUMDEVICEINTERFACES_RVA    0x002FE2BCUL

#define SRAM_IOCTL_WRITE 0x9C402000UL
#define SRAM_IOCTL_READ  0x9C402004UL
#define SRAM_SIZE        0x01000000UL

static const GUID g_sram_guid = {
    0xBDE06013UL, 0x8122, 0x11DB,
    {0x96, 0xA5, 0x00, 0xE0, 0x81, 0x61, 0x16, 0x5F}
};
static const WCHAR g_fake_device_path[] = L"\\\\?\\M90SRAM";
#ifndef SRAM_BACKING_PATH
#define SRAM_BACKING_PATH L"C:\\NVRAM\\m90_sram.bin"
#endif
#ifndef SRAM_LOG_PATH
#define SRAM_LOG_PATH "C:\\NVRAM\\sram_compat.log"
#endif
static const WCHAR g_backing_path[] = SRAM_BACKING_PATH;
static const WCHAR g_real_fbwf_path[] = L"C:\\WINDOWS\\system32\\FBWFLIB.dll";
static const HANDLE g_fake_devinfo = (HANDLE)(ULONG_PTR)0x4D393053UL;

typedef HDEVINFO (WINAPI *PFN_GET_CLASS_DEVS_W)(const GUID *, PCWSTR, HWND, DWORD);
typedef BOOL (WINAPI *PFN_ENUM_DEVICE_INTERFACES)(HDEVINFO, PSP_DEVINFO_DATA,
                                                  const GUID *, DWORD,
                                                  PSP_DEVICE_INTERFACE_DATA);
typedef BOOL (WINAPI *PFN_GET_DEVICE_INTERFACE_DETAIL_W)(
    HDEVINFO, PSP_DEVICE_INTERFACE_DATA, PSP_DEVICE_INTERFACE_DETAIL_DATA_W,
    DWORD, PDWORD, PSP_DEVINFO_DATA);
typedef BOOL (WINAPI *PFN_DESTROY_DEVICE_INFO_LIST)(HDEVINFO);
typedef DWORD (WINAPI *PFN_FBWF_ENABLE_FILTER)(void);
typedef DWORD (WINAPI *PFN_FBWF_IS_FILTER_ENABLED)(PBOOL, PBOOL);

static PFN_GET_CLASS_DEVS_W g_real_get_class_devs;
static HDEVINFO (WINAPI *g_real_get_class_devs_a)(const GUID *, PCSTR, HWND, DWORD);
static PFN_ENUM_DEVICE_INTERFACES g_real_enum_interfaces;
static PFN_GET_DEVICE_INTERFACE_DETAIL_W g_real_get_detail;
static BOOL (WINAPI *g_real_get_detail_a)(HDEVINFO, PSP_DEVICE_INTERFACE_DATA,
    PSP_DEVICE_INTERFACE_DETAIL_DATA_A, DWORD, PDWORD, PSP_DEVINFO_DATA);
static PFN_DESTROY_DEVICE_INFO_LIST g_real_destroy_list;
#define SRAM_HANDLE_COUNT 64
static HANDLE g_sram_files[SRAM_HANDLE_COUNT];
static CRITICAL_SECTION g_sram_lock;
static HMODULE g_real_fbwf;
static HANDLE g_log = INVALID_HANDLE_VALUE;

static unsigned int guid_equal(const GUID *left, const GUID *right) {
    const unsigned char *a = (const unsigned char *)left;
    const unsigned char *b = (const unsigned char *)right;
    unsigned int index;
    if (!left || !right) return 0;
    for (index = 0; index < sizeof(GUID); ++index) {
        if (a[index] != b[index]) return 0;
    }
    return 1;
}

static DWORD wide_length(const WCHAR *text) {
    DWORD length = 0;
    while (text[length]) ++length;
    return length;
}

static void log_text(const char *text) {
    DWORD length = 0;
    DWORD written;
    while (text[length]) ++length;
    m90_limit_log_before_write(SRAM_LOG_PATH, length);
    if (g_log == INVALID_HANDLE_VALUE) {
        g_log = CreateFileA(SRAM_LOG_PATH, FILE_APPEND_DATA,
                            FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS,
                            FILE_ATTRIBUTE_NORMAL, NULL);
    }
    if (g_log == INVALID_HANDLE_VALUE) return;
    WriteFile(g_log, text, length, &written, NULL);
}

static void log_wide_path(const WCHAR *text) {
    char line[300];
    DWORD index = 0;
    DWORD written;
    static const char prefix[] = "CreateFileW path: ";
    if (g_log == INVALID_HANDLE_VALUE) log_text("");
    if (g_log == INVALID_HANDLE_VALUE) return;
    while (index < sizeof(prefix) - 1) {
        line[index] = prefix[index];
        ++index;
    }
    if (!text) {
        line[index++] = '('; line[index++] = 'n'; line[index++] = 'u';
        line[index++] = 'l'; line[index++] = 'l'; line[index++] = ')';
    } else {
        DWORD source = 0;
        while (text[source] && index < sizeof(line) - 3) {
            WCHAR value = text[source++];
            line[index++] = (value >= 0x20 && value <= 0x7E) ? (char)value : '?';
        }
    }
    line[index++] = '\r'; line[index++] = '\n';
    m90_limit_log_before_write(SRAM_LOG_PATH, index);
    WriteFile(g_log, line, index, &written, NULL);
}

static DWORD decode_u24(const unsigned char *bytes) {
    return ((DWORD)bytes[0]) | ((DWORD)bytes[1] << 8) | ((DWORD)bytes[2] << 16);
}

static void clear_bytes(void *buffer, DWORD count) {
    volatile unsigned char *bytes = (volatile unsigned char *)buffer;
    while (count--) *bytes++ = 0;
}

static BOOL patch_iat(void **slot, void *replacement, void **original) {
    DWORD old_protect;
    DWORD restored;
    if (!slot || !*slot || !replacement) return FALSE;
    *original = *slot;
    if (!VirtualProtect(slot, sizeof(void *), PAGE_READWRITE, &old_protect)) return FALSE;
    *slot = replacement;
    VirtualProtect(slot, sizeof(void *), old_protect, &restored);
    FlushInstructionCache(GetCurrentProcess(), slot, sizeof(void *));
    return TRUE;
}

static HDEVINFO WINAPI hook_get_class_devs_w(const GUID *class_guid,
                                              PCWSTR enumerator, HWND parent,
                                              DWORD flags) {
    if (guid_equal(class_guid, &g_sram_guid)) {
        log_text("SetupAPI: SRAM class requested\r\n");
        SetLastError(ERROR_SUCCESS);
        return (HDEVINFO)g_fake_devinfo;
    }
    return g_real_get_class_devs(class_guid, enumerator, parent, flags);
}

static HDEVINFO WINAPI hook_get_class_devs_a(const GUID *guid, PCSTR enumerator,
                                             HWND parent, DWORD flags) {
    if (guid_equal(guid, &g_sram_guid)) {
        log_text("SetupAPI: service SRAM class requested\r\n");
        SetLastError(ERROR_SUCCESS);
        return (HDEVINFO)g_fake_devinfo;
    }
    return g_real_get_class_devs_a(guid, enumerator, parent, flags);
}

static BOOL WINAPI hook_get_device_interface_detail_a(HDEVINFO set,
    PSP_DEVICE_INTERFACE_DATA interface_data, PSP_DEVICE_INTERFACE_DETAIL_DATA_A detail,
    DWORD detail_size, PDWORD required_size, PSP_DEVINFO_DATA device_data) {
    static const char path[] = "\\\\?\\M90SRAM";
    DWORD index, needed = sizeof(DWORD) + sizeof(path);
    if ((HANDLE)set != g_fake_devinfo)
        return g_real_get_detail_a(set, interface_data, detail, detail_size,
                                  required_size, device_data);
    if (required_size) *required_size = needed;
    if (!detail || detail_size < needed) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return FALSE;
    }
    for (index = 0; index < sizeof(path); ++index)
        ((char *)detail)[sizeof(DWORD) + index] = path[index];
    log_text("SetupAPI: service SRAM path returned\r\n");
    SetLastError(ERROR_SUCCESS);
    return TRUE;
}

static BOOL WINAPI hook_enum_device_interfaces(HDEVINFO set,
                                                PSP_DEVINFO_DATA device,
                                                const GUID *class_guid,
                                                DWORD index,
                                                PSP_DEVICE_INTERFACE_DATA data) {
    if ((HANDLE)set == g_fake_devinfo) {
        log_text("SetupAPI: enum called for SRAM set\r\n");
        if (!guid_equal(class_guid, &g_sram_guid)) {
            log_text("SetupAPI: enum GUID mismatch\r\n");
            SetLastError(ERROR_INVALID_PARAMETER);
            return FALSE;
        }
        if (index >= 2 || !data || data->cbSize < sizeof(SP_DEVICE_INTERFACE_DATA)) {
            log_text("SetupAPI: enum rejected parameters\r\n");
            SetLastError(index >= 2 ? ERROR_NO_MORE_ITEMS : ERROR_INVALID_PARAMETER);
            return FALSE;
        }
        data->InterfaceClassGuid = g_sram_guid;
        data->Flags = SPINT_ACTIVE;
        data->Reserved = index + 1;
        log_text("SetupAPI: enum returned interface\r\n");
        SetLastError(ERROR_SUCCESS);
        return TRUE;
    }
    return g_real_enum_interfaces(set, device, class_guid, index, data);
}

static BOOL WINAPI hook_get_device_interface_detail_w(
    HDEVINFO set, PSP_DEVICE_INTERFACE_DATA interface_data,
    PSP_DEVICE_INTERFACE_DETAIL_DATA_W detail, DWORD detail_size,
    PDWORD required_size, PSP_DEVINFO_DATA device_data) {
    DWORD chars;
    DWORD needed;
    DWORD index;
    WCHAR *path;
    (void)interface_data;
    (void)device_data;
    if ((HANDLE)set != g_fake_devinfo) {
        return g_real_get_detail(set, interface_data, detail, detail_size,
                                 required_size, device_data);
    }
    log_text("SetupAPI: detail requested\r\n");
    chars = wide_length(g_fake_device_path) + 1;
    needed = sizeof(DWORD) + chars * sizeof(WCHAR);
    if (required_size) *required_size = needed;
    if (!detail || detail_size < needed) {
        log_text("SetupAPI: detail size query\r\n");
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return FALSE;
    }
    path = (WCHAR *)((unsigned char *)detail + sizeof(DWORD));
    for (index = 0; index < chars; ++index) path[index] = g_fake_device_path[index];
    log_text("SetupAPI: detail returned fake path\r\n");
    SetLastError(ERROR_SUCCESS);
    return TRUE;
}

static BOOL WINAPI hook_destroy_device_info_list(HDEVINFO set) {
    if ((HANDLE)set == g_fake_devinfo) {
        log_text("SetupAPI: fake set destroyed\r\n");
        SetLastError(ERROR_SUCCESS);
        return TRUE;
    }
    return g_real_destroy_list(set);
}

static HANDLE open_sram(void) {
    HANDLE file;
    DWORD size_high = 0, size_low, index, error;
    OVERLAPPED range;
    file = CreateFileW(g_backing_path, GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return file;
    EnterCriticalSection(&g_sram_lock);
    for (index = 0; index < SRAM_HANDLE_COUNT && g_sram_files[index]; ++index) {}
    if (index == SRAM_HANDLE_COUNT) {
        LeaveCriticalSection(&g_sram_lock);
        CloseHandle(file);
        SetLastError(ERROR_TOO_MANY_OPEN_FILES);
        return INVALID_HANDLE_VALUE;
    }
    /* Serialize size initialization and IOCTL operations across processes. */
    clear_bytes(&range, sizeof(range));
    if (!LockFileEx(file, LOCKFILE_EXCLUSIVE_LOCK, 0, SRAM_SIZE, 0, &range)) {
        error = GetLastError();
        LeaveCriticalSection(&g_sram_lock); CloseHandle(file);
        SetLastError(error); return INVALID_HANDLE_VALUE;
    }
    size_low = GetFileSize(file, &size_high);
    error = ERROR_SUCCESS;
    if (size_low == INVALID_FILE_SIZE && GetLastError() != ERROR_SUCCESS)
        error = GetLastError();
    else if (size_high == 0 && size_low < SRAM_SIZE) {
        if (SetFilePointer(file, SRAM_SIZE, NULL, FILE_BEGIN) == INVALID_SET_FILE_POINTER ||
            !SetEndOfFile(file) || !FlushFileBuffers(file)) error = GetLastError();
    }
    UnlockFileEx(file, 0, SRAM_SIZE, 0, &range);
    if (error == ERROR_SUCCESS) g_sram_files[index] = file;
    LeaveCriticalSection(&g_sram_lock);
    if (error != ERROR_SUCCESS) { CloseHandle(file); SetLastError(error); return INVALID_HANDLE_VALUE; }
    log_text("SRAM backing file opened\r\n");
    SetLastError(ERROR_SUCCESS);
    return file;
}

static BOOL WINAPI hook_close_handle(HANDLE file) {
    DWORD index, error;
    BOOL result;
    EnterCriticalSection(&g_sram_lock);
    result = CloseHandle(file);
    error = GetLastError();
    if (result) for (index = 0; index < SRAM_HANDLE_COUNT; ++index)
        if (g_sram_files[index] == file) g_sram_files[index] = NULL;
    LeaveCriticalSection(&g_sram_lock);
    SetLastError(error);
    return result;
}

static HANDLE WINAPI hook_create_file_a(LPCSTR name, DWORD access, DWORD share,
    LPSECURITY_ATTRIBUTES security, DWORD creation, DWORD attributes, HANDLE template_file) {
    if (name && lstrcmpA(name, "\\\\?\\M90SRAM") == 0) return open_sram();
    return CreateFileA(name, access, share, security, creation, attributes, template_file);
}

static HANDLE WINAPI hook_create_file_w(LPCWSTR name, DWORD access, DWORD share,
                                         LPSECURITY_ATTRIBUTES security,
                                         DWORD creation, DWORD attributes,
                                         HANDLE template_file) {
    (void)access;
    (void)share;
    (void)security;
    (void)creation;
    (void)attributes;
    (void)template_file;
    /* Every guest log line is a write to the emulated CF card: record only
     * the SRAM device, not each game file. */
    if (!name || lstrcmpW(name, g_fake_device_path) != 0) {
        return CreateFileW(name, access, share, security, creation, attributes,
                           template_file);
    }
    log_wide_path(name);
    return open_sram();
}

static BOOL WINAPI hook_device_io_control(HANDLE device, DWORD code,
                                           LPVOID in_buffer, DWORD in_size,
                                           LPVOID out_buffer, DWORD out_size,
                                           LPDWORD bytes_returned,
                                           LPOVERLAPPED overlapped) {
    const unsigned char *request = (const unsigned char *)in_buffer;
    DWORD offset;
    DWORD length;
    DWORD transferred = 0;
    DWORD index, error;
    OVERLAPPED range;
    BOOL result;
    EnterCriticalSection(&g_sram_lock);
    for (index = 0; index < SRAM_HANDLE_COUNT && g_sram_files[index] != device; ++index) {}
    if (index == SRAM_HANDLE_COUNT || (code != SRAM_IOCTL_READ && code != SRAM_IOCTL_WRITE)) {
        LeaveCriticalSection(&g_sram_lock);
        return DeviceIoControl(device, code, in_buffer, in_size, out_buffer,
                               out_size, bytes_returned, overlapped);
    }
    if (overlapped || !request || in_size < 6) {
        LeaveCriticalSection(&g_sram_lock);
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    offset = decode_u24(request);
    length = decode_u24(request + 3);
    if (offset >= SRAM_SIZE || length > SRAM_SIZE - offset) {
        LeaveCriticalSection(&g_sram_lock);
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    if (bytes_returned) *bytes_returned = 0;
    if ((code == SRAM_IOCTL_READ && (!out_buffer || out_size < length)) ||
        (code == SRAM_IOCTL_WRITE && in_size - 6 < length)) {
        LeaveCriticalSection(&g_sram_lock);
        SetLastError(code == SRAM_IOCTL_READ ? ERROR_INSUFFICIENT_BUFFER : ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    clear_bytes(&range, sizeof(range));
    result = LockFileEx(device, LOCKFILE_EXCLUSIVE_LOCK, 0, SRAM_SIZE, 0, &range);
    if (!result) {
        error = GetLastError(); LeaveCriticalSection(&g_sram_lock); SetLastError(error); return FALSE;
    }
    result = SetFilePointer(device, offset, NULL, FILE_BEGIN) != INVALID_SET_FILE_POINTER;
    if (result) {
        if (code == SRAM_IOCTL_READ) result = ReadFile(device, out_buffer, length, &transferred, NULL);
        else result = WriteFile(device, request + 6, length, &transferred, NULL);
    }
    error = result ? ERROR_SUCCESS : GetLastError();
    if (result && transferred != length) { result = FALSE; error = ERROR_HANDLE_EOF; }
    if (result && code == SRAM_IOCTL_WRITE && !FlushFileBuffers(device)) {
        result = FALSE; error = GetLastError();
    }
    UnlockFileEx(device, 0, SRAM_SIZE, 0, &range);
    LeaveCriticalSection(&g_sram_lock);
    if (result && bytes_returned) *bytes_returned = code == SRAM_IOCTL_READ ? length : 0;
    if (result) {
        /* Log the first access of each kind per process only. */
        static LONG logged_read, logged_write;
        LONG *logged = code == SRAM_IOCTL_READ ? &logged_read : &logged_write;
        if (!InterlockedExchange(logged, 1))
            log_text(code == SRAM_IOCTL_READ ? "DeviceIoControl: first SRAM read\r\n"
                                             : "DeviceIoControl: first SRAM write\r\n");
    }
    SetLastError(error);
    return result;
}

static void install_game_hooks(void) {
    unsigned char *base = (unsigned char *)GetModuleHandleA(NULL);
    void *original;
    log_text("DllMain: installing game hooks\r\n");
    if (!base) {
        log_text("DllMain: no main module\r\n");
        return;
    }
    if (!patch_iat((void **)(base + GAME_IAT_CREATEFILEW_RVA),
                   hook_create_file_w, &original)) {
        log_text("DllMain: CreateFileW hook failed\r\n"); return;
    }
    log_text("DllMain: CreateFileW hook installed\r\n");
    if (!patch_iat((void **)(base + GAME_IAT_DEVICEIOCONTROL_RVA),
                   hook_device_io_control, &original)) {
        log_text("DllMain: DeviceIoControl hook failed\r\n"); return;
    }
    log_text("DllMain: DeviceIoControl hook installed\r\n");
    if (!patch_iat((void **)(base + GAME_IAT_SETUPDIGETCLASSDEVSW_RVA),
                   hook_get_class_devs_w, (void **)&g_real_get_class_devs)) {
        log_text("DllMain: GetClassDevs hook failed\r\n"); return;
    }
    log_text("DllMain: GetClassDevs hook installed\r\n");
    if (!patch_iat((void **)(base + GAME_IAT_SETUPDIGETDEVICEINTERFACEDETAILW_RVA),
                   hook_get_device_interface_detail_w, (void **)&g_real_get_detail)) {
        log_text("DllMain: GetDetail hook failed\r\n"); return;
    }
    log_text("DllMain: GetDetail hook installed\r\n");
    if (!patch_iat((void **)(base + GAME_IAT_SETUPDIDESTROYDEVICEINFOLIST_RVA),
                   hook_destroy_device_info_list, (void **)&g_real_destroy_list)) {
        log_text("DllMain: DestroyList hook failed\r\n"); return;
    }
    log_text("DllMain: DestroyList hook installed\r\n");
    if (!patch_iat((void **)(base + GAME_IAT_SETUPDIENUMDEVICEINTERFACES_RVA),
                   hook_enum_device_interfaces, (void **)&g_real_enum_interfaces)) {
        log_text("DllMain: EnumInterfaces hook failed\r\n"); return;
    }
    log_text("DllMain: all hooks installed\r\n");
}

/* Match resolved imports, including Borland images with no separate ILT.
 * Only imports owned by the main executable are changed; the proxy's own
 * kernel32 calls and unrelated DLLs remain untouched. */
static BOOL install_named_hook(unsigned char *base, const char *dll,
                               const char *name, void *replacement, void **original) {
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS32 *nt = (IMAGE_NT_HEADERS32 *)(base + dos->e_lfanew);
    DWORD rva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
    IMAGE_IMPORT_DESCRIPTOR *descriptor;
    FARPROC target = GetProcAddress(GetModuleHandleA(dll), name);
    void *unused;
    if (!target || !rva) return FALSE;
    for (descriptor = (IMAGE_IMPORT_DESCRIPTOR *)(base + rva); descriptor->Name; ++descriptor) {
        IMAGE_THUNK_DATA32 *slot;
        if (lstrcmpiA((const char *)(base + descriptor->Name), dll)) continue;
        for (slot = (IMAGE_THUNK_DATA32 *)(base + descriptor->FirstThunk); slot->u1.Function; ++slot) {
            if ((FARPROC)(ULONG_PTR)slot->u1.Function == target)
                return patch_iat((void **)&slot->u1.Function, replacement, original ? original : &unused);
        }
    }
    return FALSE;
}

static BOOL install_service_hooks(unsigned char *base) {
    BOOL ok = TRUE;
    ok &= install_named_hook(base, "SETUPAPI.DLL", "SetupDiGetClassDevsA",
        hook_get_class_devs_a, (void **)&g_real_get_class_devs_a);
    ok &= install_named_hook(base, "SETUPAPI.DLL", "SetupDiGetDeviceInterfaceDetailA",
        hook_get_device_interface_detail_a, (void **)&g_real_get_detail_a);
    ok &= install_named_hook(base, "SETUPAPI.DLL", "SetupDiEnumDeviceInterfaces",
        hook_enum_device_interfaces, (void **)&g_real_enum_interfaces);
    ok &= install_named_hook(base, "SETUPAPI.DLL", "SetupDiDestroyDeviceInfoList",
        hook_destroy_device_info_list, (void **)&g_real_destroy_list);
    ok &= install_named_hook(base, "KERNEL32.DLL", "CreateFileA", hook_create_file_a, NULL);
    ok &= install_named_hook(base, "KERNEL32.DLL", "DeviceIoControl", hook_device_io_control, NULL);
    ok &= install_named_hook(base, "KERNEL32.DLL", "CloseHandle", hook_close_handle, NULL);
    log_text(ok ? "Service: all SRAM hooks installed\r\n" : "Service: SRAM hooks incomplete\r\n");
    return ok;
}

__declspec(dllexport) DWORD WINAPI SramCompatInitialize(void) { return ERROR_SUCCESS; }

/* The patched service imports SramCompatInitialize; game.exe does not. Its
 * imports are bound before this DllMain runs, so compare resolved addresses:
 * independent of the file name spelling, name/ordinal imports and Borland
 * images without a separate name table. */
static BOOL imports_sram_initialize(unsigned char *base) {
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS32 *nt = (IMAGE_NT_HEADERS32 *)(base + dos->e_lfanew);
    DWORD rva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
    IMAGE_IMPORT_DESCRIPTOR *descriptor;
    if (!rva) return FALSE;
    for (descriptor = (IMAGE_IMPORT_DESCRIPTOR *)(base + rva); descriptor->Name; ++descriptor) {
        IMAGE_THUNK_DATA32 *slot;
        if (lstrcmpiA((const char *)(base + descriptor->Name), "FBWFLIB.dll")) continue;
        for (slot = (IMAGE_THUNK_DATA32 *)(base + descriptor->FirstThunk); slot->u1.Function; ++slot) {
            if ((FARPROC)(ULONG_PTR)slot->u1.Function == (FARPROC)SramCompatInitialize) return TRUE;
        }
    }
    return FALSE;
}

static FARPROC real_fbwf_export(const char *name) {
    if (!g_real_fbwf) g_real_fbwf = LoadLibraryW(g_real_fbwf_path);
    if (!g_real_fbwf) return NULL;
    return GetProcAddress(g_real_fbwf, name);
}

__declspec(dllexport) DWORD WINAPI FbwfEnableFilter(void) {
    PFN_FBWF_ENABLE_FILTER function =
        (PFN_FBWF_ENABLE_FILTER)real_fbwf_export("FbwfEnableFilter");
    if (!function) return ERROR_PROC_NOT_FOUND;
    return function();
}

__declspec(dllexport) DWORD WINAPI FbwfIsFilterEnabled(PBOOL current, PBOOL next) {
    PFN_FBWF_IS_FILTER_ENABLED function =
        (PFN_FBWF_IS_FILTER_ENABLED)real_fbwf_export("FbwfIsFilterEnabled");
    if (!function) return ERROR_PROC_NOT_FOUND;
    return function(current, next);
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        char path[MAX_PATH];
        char *name = path;
        DWORD index;
        unsigned char *base = (unsigned char *)GetModuleHandleA(NULL);
        DisableThreadLibraryCalls(instance);
        InitializeCriticalSection(&g_sram_lock);
        if (!GetModuleFileNameA(NULL, path, sizeof(path))) return FALSE;
        /* XP may start a program through its 8.3 path (GGSG_S~1.EXE). An
         * unrecognized service got no SRAM device and crashed on a nil object. */
        GetLongPathNameA(path, path, sizeof(path));
        for (index = 0; path[index]; ++index) if (path[index] == '\\' || path[index] == '/') name = path + index + 1;
        /* One line per loading process: shows whether the service loaded this DLL at all. */
        log_text("DllMain: loaded by ");
        log_text(path);
        log_text("\r\n");
        if (lstrcmpiA(name, "GGSG_Servic.exe") == 0 || imports_sram_initialize(base)) {
            if (!install_service_hooks(base)) return FALSE;
        } else if (lstrcmpiA(name, "game.exe") == 0) {
            install_game_hooks();
            if (!install_named_hook(base, "KERNEL32.DLL", "CloseHandle", hook_close_handle, NULL)) return FALSE;
        } else {
            log_text("DllMain: SRAM hooks not installed for process ");
            log_text(name);
            log_text("\r\n");
        }
    } else if (reason == DLL_PROCESS_DETACH && g_log != INVALID_HANDLE_VALUE) {
        CloseHandle(g_log);
        g_log = INVALID_HANDLE_VALUE;
    }
    return TRUE;
}
