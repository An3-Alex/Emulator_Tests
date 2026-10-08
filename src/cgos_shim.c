#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include "cgos_abi.h"
#include "guest_log_limit.h"

/*
 * Phase 1 is a transparent logging forwarder. Phase 2 adds only the verified
 * board identity and conservative no-device behavior for the imported API.
 * The original DLL remains C:\\WINDOWS\\system32\\Cgos_original.dll.
 */

typedef unsigned int (CGOS_CALL *PFN_0)(void);
typedef unsigned int (CGOS_CALL *PFN_1)(unsigned int);
typedef unsigned int (CGOS_CALL *PFN_CLOSE)(HCGOS);
typedef unsigned int (CGOS_CALL *PFN_OPEN)(unsigned int, unsigned int, unsigned int, HCGOS *);
typedef unsigned int (CGOS_CALL *PFN_BOARDINFO)(HCGOS, CGOSBOARDINFOW *);
typedef unsigned int (CGOS_CALL *PFN_COUNT)(HCGOS);
typedef unsigned int (CGOS_CALL *PFN_UNIT)(HCGOS, unsigned int);
typedef unsigned int (CGOS_CALL *PFN_I2C)(HCGOS, unsigned int, unsigned char, unsigned char *, unsigned int);
typedef unsigned int (CGOS_CALL *PFN_GETSETTING)(HCGOS, unsigned int, unsigned int *);
typedef unsigned int (CGOS_CALL *PFN_SETSETTING)(HCGOS, unsigned int, unsigned int);
typedef unsigned int (CGOS_CALL *PFN_WDOGCFG)(HCGOS, unsigned int, unsigned int, unsigned int, unsigned int);
typedef unsigned int (CGOS_CALL *PFN_INFO)(HCGOS, unsigned int, void *);
typedef unsigned int (CGOS_CALL *PFN_CURRENT)(HCGOS, unsigned int, unsigned int *, unsigned int *);

typedef struct LOGBUF_TAG {
    char data[768];
    unsigned int len;
} LOGBUF;

static HINSTANCE g_instance;
static HMODULE g_original;
static volatile LONG g_load_lock;
static volatile LONG g_log_lock;
static volatile LONG g_vga_backlight = 100;
static volatile LONG g_vga_contrast = 100;

#ifndef CGOS_EMULATE_BOARD
#define CGOS_EMULATE_BOARD 0
#endif

#define EMULATED_HANDLE_VALUE 0x43474F53u /* "CGOS" */

static int is_emulated_handle(HCGOS handle) {
    return (unsigned int)(ULONG_PTR)handle == EMULATED_HANDLE_VALUE;
}

static void zero_bytes(void *ptr, unsigned int count) {
    volatile unsigned char *p = (volatile unsigned char *)ptr;
    while (count--) *p++ = 0;
}

static void copy_wascii(WCHAR *dst, unsigned int dst_count, const char *src) {
    unsigned int i = 0;
    if (!dst_count) return;
    while (i + 1 < dst_count && src[i]) {
        dst[i] = (WCHAR)(unsigned char)src[i];
        ++i;
    }
    dst[i] = 0;
}

static void b_ch(LOGBUF *b, char c) {
    if (b->len + 1 < sizeof(b->data)) b->data[b->len++] = c;
}

static void b_str(LOGBUF *b, const char *s) {
    if (!s) s = "<null>";
    while (*s && b->len + 1 < sizeof(b->data)) b->data[b->len++] = *s++;
}

static void b_dec_fixed(LOGBUF *b, unsigned int v, unsigned int digits) {
    char tmp[16];
    unsigned int i;
    if (digits > sizeof(tmp)) digits = sizeof(tmp);
    for (i = 0; i < digits; ++i) {
        tmp[digits - i - 1] = (char)('0' + (v % 10));
        v /= 10;
    }
    for (i = 0; i < digits; ++i) b_ch(b, tmp[i]);
}

static void b_hex(LOGBUF *b, unsigned int v) {
    static const char h[] = "0123456789ABCDEF";
    int i;
    b_str(b, "0x");
    for (i = 7; i >= 0; --i) b_ch(b, h[(v >> (i * 4)) & 15]);
}

static void b_bytes(LOGBUF *b, const unsigned char *bytes, unsigned int count) {
    static const char h[] = "0123456789ABCDEF";
    unsigned int i;
    unsigned int shown = count > 16 ? 16 : count;
    if (!bytes) {
        b_str(b, "<null>");
        return;
    }
    for (i = 0; i < shown; ++i) {
        if (i) b_ch(b, ' ');
        b_ch(b, h[(bytes[i] >> 4) & 15]);
        b_ch(b, h[bytes[i] & 15]);
    }
    if (shown < count) b_str(b, " ...");
}

static void b_ptr(LOGBUF *b, const void *p) {
    b_hex(b, (unsigned int)(ULONG_PTR)p);
}

static void b_wascii(LOGBUF *b, const WCHAR *s, unsigned int max_chars) {
    unsigned int i;
    b_ch(b, '"');
    if (!s) {
        b_str(b, "<null>");
    } else {
        for (i = 0; i < max_chars && s[i]; ++i) {
            WCHAR wc = s[i];
            b_ch(b, (wc >= 0x20 && wc <= 0x7E) ? (char)wc : '?');
        }
    }
    b_ch(b, '"');
}

static void log_begin(LOGBUF *b, const char *name) {
    SYSTEMTIME st;
    b->len = 0;
    GetLocalTime(&st);
    b_ch(b, '[');
    b_dec_fixed(b, st.wYear, 4); b_ch(b, '-');
    b_dec_fixed(b, st.wMonth, 2); b_ch(b, '-');
    b_dec_fixed(b, st.wDay, 2); b_ch(b, ' ');
    b_dec_fixed(b, st.wHour, 2); b_ch(b, ':');
    b_dec_fixed(b, st.wMinute, 2); b_ch(b, ':');
    b_dec_fixed(b, st.wSecond, 2); b_ch(b, '.');
    b_dec_fixed(b, st.wMilliseconds, 3); b_str(b, "] tid=");
    b_hex(b, GetCurrentThreadId()); b_ch(b, ' ');
    b_str(b, name); b_ch(b, '(');
}

static void log_result(LOGBUF *b, unsigned int result) {
    b_str(b, ") -> "); b_hex(b, result); b_str(b, "\r\n");
}

static void write_log(LOGBUF *b) {
    HANDLE file;
    DWORD wrote;
    while (InterlockedCompareExchange(&g_log_lock, 1, 0) != 0) Sleep(0);
    m90_limit_log_before_write("C:\\NVRAM\\cgos_shim.log", b->len);
    file = CreateFileA("C:\\NVRAM\\cgos_shim.log", FILE_APPEND_DATA,
                       FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        file = CreateFileA("C:\\cgos_shim.log", FILE_APPEND_DATA,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    }
    if (file != INVALID_HANDLE_VALUE) {
        WriteFile(file, b->data, b->len, &wrote, NULL);
        CloseHandle(file);
    }
    InterlockedExchange(&g_log_lock, 0);
}

static HMODULE original_module(void) {
    char path[MAX_PATH];
    char *p;
    if (g_original) return g_original;
    while (InterlockedCompareExchange(&g_load_lock, 1, 0) != 0) Sleep(0);
    if (!g_original) {
        DWORD n = GetModuleFileNameA(g_instance, path, MAX_PATH);
        if (n && n < MAX_PATH) {
            p = path + n;
            while (p > path && p[-1] != '\\' && p[-1] != '/') --p;
            lstrcpyA(p, "Cgos_original.dll");
            g_original = LoadLibraryA(path);
        }
        if (!g_original) g_original = LoadLibraryA("Cgos_original.dll");
    }
    InterlockedExchange(&g_load_lock, 0);
    return g_original;
}

static FARPROC resolve(const char *name) {
    HMODULE module = original_module();
    return module ? GetProcAddress(module, name) : NULL;
}

static void log_noargs(const char *name, unsigned int result) {
    LOGBUF b; log_begin(&b, name); log_result(&b, result); write_log(&b);
}

__declspec(dllexport) unsigned int CGOS_CALL CgosLibInitialize(void) {
    PFN_0 fn = (PFN_0)resolve("CgosLibInitialize");
    unsigned int r = fn ? fn() : (CGOS_EMULATE_BOARD ? 1u : 0u);
    log_noargs("CgosLibInitialize", r); return r;
}

__declspec(dllexport) unsigned int CGOS_CALL CgosLibUninitialize(void) {
    PFN_0 fn = (PFN_0)resolve("CgosLibUninitialize");
    unsigned int r = fn ? fn() : 0;
    log_noargs("CgosLibUninitialize", r); return r;
}

__declspec(dllexport) unsigned int CGOS_CALL CgosLibIsAvailable(void) {
    PFN_0 fn = (PFN_0)resolve("CgosLibIsAvailable");
    unsigned int r = fn ? fn() : (CGOS_EMULATE_BOARD ? 1u : 0u);
    log_noargs("CgosLibIsAvailable", r); return r;
}

__declspec(dllexport) unsigned int CGOS_CALL CgosLibInstall(unsigned int install) {
    PFN_1 fn = (PFN_1)resolve("CgosLibInstall");
    unsigned int r = fn ? fn(install) : 0;
    LOGBUF b; log_begin(&b, "CgosLibInstall"); b_str(&b, "install="); b_hex(&b, install);
    log_result(&b, r); write_log(&b); return r;
}

__declspec(dllexport) unsigned int CGOS_CALL CgosLibGetVersion(void) {
    PFN_0 fn = (PFN_0)resolve("CgosLibGetVersion");
    unsigned int r = fn ? fn() : 0;
    log_noargs("CgosLibGetVersion", r); return r;
}

__declspec(dllexport) unsigned int CGOS_CALL CgosLibGetLastError(void) {
    PFN_0 fn = (PFN_0)resolve("CgosLibGetLastError");
    unsigned int r = fn ? fn() : 0xFFFFFFFFu;
    log_noargs("CgosLibGetLastError", r); return r;
}

__declspec(dllexport) unsigned int CGOS_CALL CgosBoardOpen(unsigned int cls, unsigned int num,
                                                           unsigned int flags, HCGOS *out_handle) {
    PFN_OPEN fn = (PFN_OPEN)resolve("CgosBoardOpen");
    unsigned int r = fn ? fn(cls, num, flags, out_handle) : 0;
#if CGOS_EMULATE_BOARD
    if (!r && out_handle && num == 0) {
        *out_handle = (HCGOS)(ULONG_PTR)EMULATED_HANDLE_VALUE;
        r = 1;
    }
#endif
    LOGBUF b; log_begin(&b, "CgosBoardOpen");
    b_str(&b, "class="); b_hex(&b, cls); b_str(&b, ",num="); b_hex(&b, num);
    b_str(&b, ",flags="); b_hex(&b, flags); b_str(&b, ",out="); b_ptr(&b, out_handle);
    b_str(&b, ",handle="); b_ptr(&b, (r && out_handle) ? *out_handle : NULL);
    log_result(&b, r); write_log(&b); return r;
}

__declspec(dllexport) unsigned int CGOS_CALL CgosBoardClose(HCGOS handle) {
    PFN_CLOSE fn = (PFN_CLOSE)resolve("CgosBoardClose");
    unsigned int r = is_emulated_handle(handle) ? 1u : (fn ? fn(handle) : 0u);
    LOGBUF b; log_begin(&b, "CgosBoardClose"); b_str(&b, "handle="); b_ptr(&b, handle);
    log_result(&b, r); write_log(&b); return r;
}

__declspec(dllexport) unsigned int CGOS_CALL CgosBoardGetInfoW(HCGOS handle, CGOSBOARDINFOW *info) {
    unsigned int requested = info ? info->dwSize : 0;
    PFN_BOARDINFO fn = (PFN_BOARDINFO)resolve("CgosBoardGetInfoW");
    unsigned int r;
    if (is_emulated_handle(handle)) {
        if (info && requested >= sizeof(CGOSBOARDINFOW)) {
            zero_bytes(info, sizeof(CGOSBOARDINFOW));
            info->dwSize = sizeof(CGOSBOARDINFOW);
            copy_wascii(info->szBoard, CGOS_BOARD_MAX_SIZE_ID_STRING, "B945");
            copy_wascii(info->szManufacturer, CGOS_BOARD_MAX_SIZE_ID_STRING, "congatec");
            copy_wascii(info->szSerialNumber, CGOS_BOARD_MAX_SIZE_SERIAL_STRING, "000000533731");
            info->dwClasses = 0x00000001u;      /* CGOS_BOARD_CLASS_CPU */
            info->dwPrimaryClass = 0x00000001u;
            r = 1;
        } else {
            r = 0;
        }
    } else {
        r = fn ? fn(handle, info) : 0;
    }
    LOGBUF b; log_begin(&b, "CgosBoardGetInfoW"); b_str(&b, "handle="); b_ptr(&b, handle);
    b_str(&b, ",info="); b_ptr(&b, info); b_str(&b, ",requestedSize="); b_hex(&b, requested);
    if (info) {
        b_str(&b, ",returnedSize="); b_hex(&b, info->dwSize);
        b_str(&b, ",board="); b_wascii(&b, info->szBoard, CGOS_BOARD_MAX_SIZE_ID_STRING);
        b_str(&b, ",manufacturer="); b_wascii(&b, info->szManufacturer, CGOS_BOARD_MAX_SIZE_ID_STRING);
        b_str(&b, ",serial="); b_wascii(&b, info->szSerialNumber, CGOS_BOARD_MAX_SIZE_SERIAL_STRING);
        b_str(&b, ",classes="); b_hex(&b, info->dwClasses);
        b_str(&b, ",primaryClass="); b_hex(&b, info->dwPrimaryClass);
    }
    log_result(&b, r); write_log(&b); return r;
}

#define DEFINE_COUNT(name) \
__declspec(dllexport) unsigned int CGOS_CALL name(HCGOS handle) { \
    PFN_COUNT fn = (PFN_COUNT)resolve(#name); \
    unsigned int r = is_emulated_handle(handle) ? 0u : (fn ? fn(handle) : 0u); \
    LOGBUF b; log_begin(&b, #name); b_str(&b, "handle="); b_ptr(&b, handle); \
    log_result(&b, r); write_log(&b); return r; \
}

#define DEFINE_UNIT(name, emulated_result) \
__declspec(dllexport) unsigned int CGOS_CALL name(HCGOS handle, unsigned int unit) { \
    PFN_UNIT fn = (PFN_UNIT)resolve(#name); \
    unsigned int r = is_emulated_handle(handle) ? (emulated_result) : (fn ? fn(handle, unit) : 0u); \
    LOGBUF b; log_begin(&b, #name); b_str(&b, "handle="); b_ptr(&b, handle); \
    b_str(&b, ",unit="); b_hex(&b, unit); log_result(&b, r); write_log(&b); return r; \
}

DEFINE_COUNT(CgosI2CCount)
DEFINE_UNIT(CgosI2CIsAvailable, 0u)
DEFINE_UNIT(CgosWDogDisable, 1u)
DEFINE_UNIT(CgosWDogTrigger, 1u)

__declspec(dllexport) unsigned int CGOS_CALL CgosTemperatureCount(HCGOS handle) {
    PFN_COUNT fn = (PFN_COUNT)resolve("CgosTemperatureCount");
    unsigned int r = is_emulated_handle(handle) ? 2u : (fn ? fn(handle) : 0u);
    LOGBUF b; log_begin(&b, "CgosTemperatureCount"); b_str(&b, "handle="); b_ptr(&b, handle);
    log_result(&b, r); write_log(&b); return r;
}

__declspec(dllexport) unsigned int CGOS_CALL CgosVgaCount(HCGOS handle) {
    PFN_COUNT fn = (PFN_COUNT)resolve("CgosVgaCount");
    unsigned int r = is_emulated_handle(handle) ? 1u : (fn ? fn(handle) : 0u);
    LOGBUF b; log_begin(&b, "CgosVgaCount"); b_str(&b, "handle="); b_ptr(&b, handle);
    log_result(&b, r); write_log(&b); return r;
}

static void log_i2c(const char *name, HCGOS handle, unsigned int unit,
                    unsigned char addr, unsigned char *bytes, unsigned int len,
                    unsigned int result) {
    LOGBUF b; log_begin(&b, name); b_str(&b, "handle="); b_ptr(&b, handle);
    b_str(&b, ",unit="); b_hex(&b, unit); b_str(&b, ",addr="); b_hex(&b, addr);
    b_str(&b, ",buffer="); b_ptr(&b, bytes); b_str(&b, ",len="); b_hex(&b, len);
    b_str(&b, ",bytes="); b_bytes(&b, bytes, len); log_result(&b, result); write_log(&b);
}

__declspec(dllexport) unsigned int CGOS_CALL CgosI2CRead(HCGOS handle, unsigned int unit,
                                                         unsigned char addr, unsigned char *bytes,
                                                         unsigned int len) {
    PFN_I2C fn = (PFN_I2C)resolve("CgosI2CRead");
    unsigned int r = is_emulated_handle(handle) ? 0u : (fn ? fn(handle, unit, addr, bytes, len) : 0u);
    log_i2c("CgosI2CRead", handle, unit, addr, bytes, len, r); return r;
}

__declspec(dllexport) unsigned int CGOS_CALL CgosI2CWrite(HCGOS handle, unsigned int unit,
                                                          unsigned char addr, unsigned char *bytes,
                                                          unsigned int len) {
    PFN_I2C fn = (PFN_I2C)resolve("CgosI2CWrite");
    unsigned int r;
    if (is_emulated_handle(handle)) {
        /* Observed HD-configuration endpoint. Unknown endpoints and large frames remain rejected. */
        r = (unit == 0u && addr == 0x36u && bytes && len > 0u && len <= 64u) ? 1u : 0u;
    } else {
        r = fn ? fn(handle, unit, addr, bytes, len) : 0u;
    }
    log_i2c("CgosI2CWrite", handle, unit, addr, bytes, len, r); return r;
}

#define DEFINE_GETSETTING(name) \
__declspec(dllexport) unsigned int CGOS_CALL name(HCGOS handle, unsigned int unit, unsigned int *setting) { \
    PFN_GETSETTING fn = (PFN_GETSETTING)resolve(#name); \
    unsigned int r = is_emulated_handle(handle) ? 0u : (fn ? fn(handle, unit, setting) : 0u); \
    LOGBUF b; log_begin(&b, #name); b_str(&b, "handle="); b_ptr(&b, handle); b_str(&b, ",unit="); \
    b_hex(&b, unit); b_str(&b, ",settingPtr="); b_ptr(&b, setting); b_str(&b, ",setting="); \
    b_hex(&b, (r && setting) ? *setting : 0); log_result(&b, r); write_log(&b); return r; \
}

#define DEFINE_SETSETTING(name) \
__declspec(dllexport) unsigned int CGOS_CALL name(HCGOS handle, unsigned int unit, unsigned int setting) { \
    PFN_SETSETTING fn = (PFN_SETSETTING)resolve(#name); \
    unsigned int r = is_emulated_handle(handle) ? 0u : (fn ? fn(handle, unit, setting) : 0u); \
    LOGBUF b; log_begin(&b, #name); b_str(&b, "handle="); b_ptr(&b, handle); b_str(&b, ",unit="); \
    b_hex(&b, unit); b_str(&b, ",setting="); b_hex(&b, setting); log_result(&b, r); write_log(&b); return r; \
}

static unsigned int vga_get_setting(const char *name, HCGOS handle, unsigned int unit,
                                    unsigned int *setting, volatile LONG *value,
                                    PFN_GETSETTING fn) {
    unsigned int r;
    if (is_emulated_handle(handle)) {
        r = (unit == 0u && setting != 0) ? 1u : 0u;
        if (r) *setting = (unsigned int)InterlockedCompareExchange(value, 0, 0);
    } else r = fn ? fn(handle, unit, setting) : 0u;
    { LOGBUF b; log_begin(&b, name); b_str(&b, "handle="); b_ptr(&b, handle);
      b_str(&b, ",unit="); b_hex(&b, unit); b_str(&b, ",settingPtr="); b_ptr(&b, setting);
      b_str(&b, ",setting="); b_hex(&b, (r && setting) ? *setting : 0u);
      log_result(&b, r); write_log(&b); }
    return r;
}

static unsigned int vga_set_setting(const char *name, HCGOS handle, unsigned int unit,
                                    unsigned int setting, volatile LONG *value,
                                    PFN_SETSETTING fn) {
    unsigned int r;
    if (is_emulated_handle(handle)) {
        r = (unit == 0u && setting <= 100u) ? 1u : 0u;
        if (r) InterlockedExchange(value, (LONG)setting);
    } else r = fn ? fn(handle, unit, setting) : 0u;
    { LOGBUF b; log_begin(&b, name); b_str(&b, "handle="); b_ptr(&b, handle);
      b_str(&b, ",unit="); b_hex(&b, unit); b_str(&b, ",setting="); b_hex(&b, setting);
      log_result(&b, r); write_log(&b); }
    return r;
}

#define DEFINE_VGA_GET(name, state) \
__declspec(dllexport) unsigned int CGOS_CALL name(HCGOS h, unsigned int u, unsigned int *s) { \
    return vga_get_setting(#name, h, u, s, &(state), (PFN_GETSETTING)resolve(#name)); \
}
#define DEFINE_VGA_SET(name, state) \
__declspec(dllexport) unsigned int CGOS_CALL name(HCGOS h, unsigned int u, unsigned int s) { \
    return vga_set_setting(#name, h, u, s, &(state), (PFN_SETSETTING)resolve(#name)); \
}

DEFINE_VGA_GET(CgosVgaGetContrast, g_vga_contrast)
DEFINE_VGA_SET(CgosVgaSetContrast, g_vga_contrast)
DEFINE_VGA_GET(CgosVgaGetBacklight, g_vga_backlight)
DEFINE_VGA_SET(CgosVgaSetBacklight, g_vga_backlight)

__declspec(dllexport) unsigned int CGOS_CALL CgosWDogSetConfig(HCGOS handle, unsigned int unit,
                                                               unsigned int timeout, unsigned int delay,
                                                               unsigned int mode) {
    PFN_WDOGCFG fn = (PFN_WDOGCFG)resolve("CgosWDogSetConfig");
    unsigned int r = is_emulated_handle(handle) ? 1u : (fn ? fn(handle, unit, timeout, delay, mode) : 0u);
    LOGBUF b; log_begin(&b, "CgosWDogSetConfig"); b_str(&b, "handle="); b_ptr(&b, handle);
    b_str(&b, ",unit="); b_hex(&b, unit); b_str(&b, ",timeout="); b_hex(&b, timeout);
    b_str(&b, ",delay="); b_hex(&b, delay); b_str(&b, ",mode="); b_hex(&b, mode);
    log_result(&b, r); write_log(&b); return r;
}

__declspec(dllexport) unsigned int CGOS_CALL CgosTemperatureGetInfo(HCGOS handle, unsigned int unit,
                                                                    void *info) {
    PFN_INFO fn = (PFN_INFO)resolve("CgosTemperatureGetInfo");
    CGOSTEMPERATUREINFO *temperature = (CGOSTEMPERATUREINFO *)info;
    unsigned int requested = temperature ? temperature->dwSize : 0u;
    unsigned int r;
    if (is_emulated_handle(handle)) {
        r = (unit < 2u && temperature && requested >= sizeof(*temperature)) ? 1u : 0u;
        if (r) {
            zero_bytes(temperature, sizeof(*temperature));
            temperature->dwSize = sizeof(*temperature);
            temperature->dwType = unit == 0u ? CGOS_TEMP_CPU : CGOS_TEMP_BOARD;
            temperature->dwFlags = CGOS_SENSOR_ACTIVE;
            temperature->dwRes = 1000u;
            temperature->dwMin = 0u;
            temperature->dwMax = 100000u;
            temperature->dwAlarmHi = 85000u;
            temperature->dwHystHi = 5000u;
        }
    } else r = fn ? fn(handle, unit, info) : 0u;
    LOGBUF b; log_begin(&b, "CgosTemperatureGetInfo"); b_str(&b, "handle="); b_ptr(&b, handle);
    b_str(&b, ",unit="); b_hex(&b, unit); b_str(&b, ",info="); b_ptr(&b, info);
    b_str(&b, ",requestedSize="); b_hex(&b, requested);
    if (r && temperature) {
        b_str(&b, ",returnedSize="); b_hex(&b, temperature->dwSize);
        b_str(&b, ",type="); b_hex(&b, temperature->dwType);
        b_str(&b, ",flags="); b_hex(&b, temperature->dwFlags);
    }
    log_result(&b, r); write_log(&b); return r;
}

__declspec(dllexport) unsigned int CGOS_CALL CgosTemperatureGetCurrent(HCGOS handle, unsigned int unit,
                                                                       unsigned int *setting,
                                                                       unsigned int *status) {
    PFN_CURRENT fn = (PFN_CURRENT)resolve("CgosTemperatureGetCurrent");
    unsigned int r;
    if (is_emulated_handle(handle)) {
        r = (unit < 2u && setting && status) ? 1u : 0u;
        if (r) {
            *setting = unit == 0u ? 45000u : 40000u;
            *status = CGOS_SENSOR_ACTIVE;
        }
    } else r = fn ? fn(handle, unit, setting, status) : 0u;
    LOGBUF b; log_begin(&b, "CgosTemperatureGetCurrent"); b_str(&b, "handle="); b_ptr(&b, handle);
    b_str(&b, ",unit="); b_hex(&b, unit); b_str(&b, ",setting="); b_hex(&b, (r && setting) ? *setting : 0);
    b_str(&b, ",status="); b_hex(&b, (r && status) ? *status : 0);
    log_result(&b, r); write_log(&b); return r;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        g_instance = instance;
        DisableThreadLibraryCalls(instance);
    }
    return TRUE;
}
