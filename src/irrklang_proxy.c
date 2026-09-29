#define WIN32_LEAN_AND_MEAN
#include <windows.h>

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

static void log_line(const char *text)
{
    HANDLE file;
    DWORD written;
    file = CreateFileA("C:\\NVRAM\\irrklang_proxy.log", FILE_APPEND_DATA,
                       FILE_SHARE_READ | FILE_SHARE_WRITE, 0, OPEN_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL, 0);
    if (file == INVALID_HANDLE_VALUE) return;
    WriteFile(file, text, lstrlenA(text), &written, 0);
    CloseHandle(file);
}

static PFN_CREATE_IRRKLANG_DEVICE resolve_create(void)
{
    HMODULE module;
    if (real_create) return real_create;
    module = LoadLibraryW(L"C:\\WINDOWS\\system32\\irrKlang.dll");
    if (!module) {
        log_line("LoadLibraryW(system32\\irrKlang.dll) failed\r\n");
        return 0;
    }
    real_create = (PFN_CREATE_IRRKLANG_DEVICE)GetProcAddress(
        module,
        "?createIrrKlangDevice@irrklang@@YAPAVISoundEngine@1@W4E_SOUND_OUTPUT_DRIVER@1@HPBD1@Z");
    if (!real_create) log_line("GetProcAddress(createIrrKlangDevice) failed\r\n");
    return real_create;
}

namespace irrklang {
__declspec(dllexport) ISoundEngine *__cdecl createIrrKlangDevice(
    E_SOUND_OUTPUT_DRIVER requested_driver, int options,
    const char *device_id, const char *sdk_version)
{
    PFN_CREATE_IRRKLANG_DEVICE create = resolve_create();
    (void)requested_driver;
    if (!create) return 0;
    log_line("createIrrKlangDevice: forcing ESOD_NULL (6)\r\n");
    return create(ESOD_NULL, options, device_id, sdk_version);
}
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)instance;
    (void)reason;
    (void)reserved;
    return TRUE;
}
