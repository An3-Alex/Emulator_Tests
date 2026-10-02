#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <mmsystem.h>

namespace irrklang {
class ISoundEngine;
enum E_SOUND_OUTPUT_DRIVER {
    ESOD_AUTO_DETECT = 0,
    ESOD_DIRECT_SOUND_8,
    ESOD_DIRECT_SOUND,
    ESOD_WIN_MM,
    ESOD_ALSA,
    ESOD_CORE_AUDIO,
    ESOD_NULL
};
}

typedef irrklang::ISoundEngine *(__cdecl *PFN_CREATE_IRRKLANG_DEVICE)(
    irrklang::E_SOUND_OUTPUT_DRIVER, int, const char *, const char *);

static PFN_CREATE_IRRKLANG_DEVICE real_create;
static BOOL bridge_enabled;
static BOOL bridge_installed;

static void log_line(const char *text)
{
    HANDLE file;
    DWORD written;
    const char *path = "C:\\NVRAM\\irrklang_proxy.log";
#ifdef IRRKLANG_OFFLINE_PROBE
    // Only the separately compiled native probe accepts a test log path.
    char test_path[MAX_PATH];
    DWORD length = GetEnvironmentVariableA("M90_TEST_AUDIO_LOG", test_path, MAX_PATH);
    if (length && length < MAX_PATH) path = test_path;
#endif
    file = CreateFileA(path, FILE_APPEND_DATA,
                       FILE_SHARE_READ | FILE_SHARE_WRITE, 0, OPEN_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL, 0);
    if (file == INVALID_HANDLE_VALUE) return;
    WriteFile(file, text, lstrlenA(text), &written, 0);
    CloseHandle(file);
}

static void log_hex(const char *label, DWORD value)
{
    char line[160];
    const char *digits = "0123456789ABCDEF";
    int offset = 0;
    while (label[offset] && offset < 145) { line[offset] = label[offset]; ++offset; }
    line[offset++] = '0'; line[offset++] = 'x';
    for (int shift = 28; shift >= 0; shift -= 4) line[offset++] = digits[(value >> shift) & 15];
    line[offset++] = '\r'; line[offset++] = '\n'; line[offset] = 0;
    log_line(line);
}

#include "pcm_wave_bridge.h"

static PFN_CREATE_IRRKLANG_DEVICE resolve_create(void)
{
    HMODULE module;
    if (real_create) return real_create;
#ifdef IRRKLANG_OFFLINE_PROBE
    char test_path[MAX_PATH];
    DWORD length = GetEnvironmentVariableA("M90_TEST_IRRKLANG_DLL", test_path, MAX_PATH);
    if (!length || length >= MAX_PATH) return 0;
    module = LoadLibraryA(test_path);
#else
    module = LoadLibraryW(L"C:\\WINDOWS\\system32\\irrKlang.dll");
#endif
    if (!module) {
        log_line("LoadLibraryW(system32\\irrKlang.dll) failed\r\n");
        return 0;
    }
    real_create = (PFN_CREATE_IRRKLANG_DEVICE)GetProcAddress(
        module,
        "?createIrrKlangDevice@irrklang@@YAPAVISoundEngine@1@W4E_SOUND_OUTPUT_DRIVER@1@HPBD1@Z");
    if (!real_create) log_line("GetProcAddress(createIrrKlangDevice) failed\r\n");
    bridge_enabled = GetFileAttributesA("C:\\NVRAM\\m90_audio_bridge.enabled") != INVALID_FILE_ATTRIBUTES;
    if (bridge_enabled && real_create) bridge_installed = pcm_install(module);
    return real_create;
}

namespace irrklang {
__declspec(dllexport) ISoundEngine *__cdecl createIrrKlangDevice(
    E_SOUND_OUTPUT_DRIVER requested_driver, int options,
    const char *device_id, const char *sdk_version)
{
    PFN_CREATE_IRRKLANG_DEVICE create = resolve_create();
    ISoundEngine *engine;
    (void)requested_driver;
    if (!create) return 0;
    if (bridge_enabled) {
        if (!bridge_installed) {
            log_line("AUDIO_BRIDGE_INIT_FAILED: unsupported irrKlang imports; no silent fallback\r\n");
            return 0;
        }
        engine = create(ESOD_WIN_MM, options, device_id, sdk_version);
        log_hex("AUDIO_BRIDGE_ENGINE=", (DWORD)(ULONG_PTR)engine);
        return engine;
    }
    // irrKlang 1.1.3's NULL driver returns no ISound even for tracked
    // playback. The original game treats that as fatal (sound 237 / exit).
    // QEMU now supplies AC97, supported by the owner's installed XP driver.
    // Allow PnP a bounded startup interval before constructing the engine.
    DWORD started = GetTickCount();
    UINT outputs = waveOutGetNumDevs();
    if (!outputs) log_line("AUDIO_WAITING_FOR_XP_OUTPUT timeout_ms=120000\r\n");
    while (!outputs && (DWORD)(GetTickCount() - started) < 120000) {
        Sleep(250);
        outputs = waveOutGetNumDevs();
    }
    log_hex("AUDIO_XP_OUTPUT_DEVICES=", outputs);
    if (!outputs) {
        log_line("AUDIO_INIT_FAILED: XP has no waveOut device; check AC97/Realtek driver. No NULL fallback.\r\n");
        return 0;
    }
    log_line("AUDIO_DRIVER: using ESOD_WIN_MM (3), real sound objects\r\n");
    engine = create(ESOD_WIN_MM, options, device_id, sdk_version);
    log_hex("AUDIO_ENGINE=", (DWORD)(ULONG_PTR)engine);
    if (!engine) log_line("AUDIO_INIT_FAILED: original irrKlang WinMM constructor returned NULL\r\n");
    return engine;
}
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)instance;
    (void)reason;
    (void)reserved;
    return TRUE;
}
