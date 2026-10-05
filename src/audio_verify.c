#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mmsystem.h>
#include <setupapi.h>
#include <cfgmgr32.h>

static HANDLE log_file = INVALID_HANDLE_VALUE;
static void zero_memory(void *data, DWORD count) {
    volatile unsigned char *bytes = (volatile unsigned char *)data;
    while (count--) *bytes++ = 0;
}
static void text(const char *s) {
    DWORD n = 0, written;
    while (s[n]) ++n;
    if (log_file != INVALID_HANDLE_VALUE) {
        WriteFile(log_file, s, n, &written, NULL);
        FlushFileBuffers(log_file);
    }
}
static void number(DWORD n) {
    static const char digits[] = "0123456789ABCDEF";
    char s[13] = "0x00000000\r\n";
    int i;
    for (i = 9; i >= 2; --i) { s[i] = digits[n & 15]; n >>= 4; }
    text(s);
}
static void signal_result(BOOL ok) {
    const char *s = ok ? "M90-AUDIO-VERIFY-OK\n" : "M90-AUDIO-VERIFY-FAILED\n";
    DWORD n = 0, written;
    HANDLE serial = CreateFileA("\\\\.\\COM1", GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    while (s[n]) ++n;
    if (serial != INVALID_HANDLE_VALUE) {
        WriteFile(serial, s, n, &written, NULL);
        CloseHandle(serial);
    }
}
static void pnp_status(void) {
    HDEVINFO devices;
    DWORD index;
    devices = SetupDiGetClassDevsA(NULL, NULL, NULL, DIGCF_ALLCLASSES | DIGCF_PRESENT);
    if (devices == INVALID_HANDLE_VALUE) return;
    for (index = 0;; ++index) {
        SP_DEVINFO_DATA device;
        char description[256];
        char service[256];
        ULONG flags = 0, problem = 0;
        zero_memory(&device, sizeof(device));
        zero_memory(description, sizeof(description));
        zero_memory(service, sizeof(service));
        device.cbSize = sizeof(device);
        if (!SetupDiEnumDeviceInfo(devices, index, &device)) break;
        SetupDiGetDeviceRegistryPropertyA(devices, &device, SPDRP_DEVICEDESC, NULL,
            (PBYTE)description, sizeof(description), NULL);
        SetupDiGetDeviceRegistryPropertyA(devices, &device, SPDRP_SERVICE, NULL,
            (PBYTE)service, sizeof(service), NULL);
        if (lstrcmpiA(service, "STAC97") && lstrcmpiA(service, "ALCXWDM") &&
            lstrcmpiA(service, "swenum") && lstrcmpiA(service, "sysaudio") &&
            lstrcmpiA(service, "wdmaud") && lstrcmpiA(service, "kmixer")) continue;
        text("PnP device: "); text(description); text("\r\nService: "); text(service); text("\r\n");
        number(CM_Get_DevNode_Status(&flags, &problem, device.DevInst, 0));
        text("PnP flags: "); number(flags); text("PnP problem: "); number(problem);
    }
    SetupDiDestroyDeviceInfoList(devices);
}
static BOOL audio_service(BOOL start) {
    SERVICE_STATUS status;
    SC_HANDLE manager = OpenSCManagerA(NULL, NULL, SC_MANAGER_CONNECT);
    SC_HANDLE service;
    BOOL running = FALSE;
    if (!manager) return FALSE;
    service = OpenServiceA(manager, "AudioSrv", SERVICE_QUERY_STATUS | SERVICE_START);
    if (service) {
        zero_memory(&status, sizeof(status));
        if (QueryServiceStatus(service, &status)) {
            text("AudioSrv state: "); number(status.dwCurrentState);
            text("AudioSrv error: "); number(status.dwWin32ExitCode);
            running = status.dwCurrentState == SERVICE_RUNNING;
            if (start && status.dwCurrentState == SERVICE_STOPPED) {
                BOOL started;
                DWORD error;
                SetLastError(0);
                started = StartServiceA(service, 0, NULL); error = GetLastError();
                text("AudioSrv start: "); number(started); number(error);
            }
        }
        CloseServiceHandle(service);
    }
    CloseServiceHandle(manager);
    return running;
}
static void kernel_audio_services(void) {
    static const char *names[] = {"sysaudio", "kmixer", "wdmaud"};
    SC_HANDLE manager = OpenSCManagerA(NULL, NULL, SC_MANAGER_CONNECT);
    DWORD i;
    if (!manager) return;
    for (i = 0; i < 3; ++i) {
        SC_HANDLE service = OpenServiceA(manager, names[i], SERVICE_START | SERVICE_QUERY_STATUS);
        if (service) {
            SERVICE_STATUS status;
            BOOL started;
            DWORD error;
            zero_memory(&status, sizeof(status));
            QueryServiceStatus(service, &status);
            text(names[i]); text(" state: "); number(status.dwCurrentState);
            SetLastError(0);
            started = StartServiceA(service, 0, NULL); error = GetLastError();
            text("Kernel start: "); number(started); number(error);
            CloseServiceHandle(service);
        }
    }
    CloseServiceHandle(manager);
}
void __stdcall mainCRTStartup(void) {
    WAVEFORMATEX format = {WAVE_FORMAT_PCM, 1, 44100, 88200, 2, 16, 0};
    static short samples[44100];
    WAVEHDR header = {0};
    WAVEOUTCAPSA caps;
    HWAVEOUT output = NULL;
    DWORD i, count = 0;
    MMRESULT result;
    BOOL ok = FALSE;
    HMODULE winmm = NULL;
    UINT (WINAPI *get_count)(void);
    MMRESULT (WINAPI *get_caps)(UINT_PTR, LPWAVEOUTCAPSA, UINT);
    MMRESULT (WINAPI *open_output)(LPHWAVEOUT, UINT, LPCWAVEFORMATEX, DWORD_PTR, DWORD_PTR, DWORD);
    MMRESULT (WINAPI *prepare_header)(HWAVEOUT, LPWAVEHDR, UINT);
    MMRESULT (WINAPI *write_output)(HWAVEOUT, LPWAVEHDR, UINT);
    MMRESULT (WINAPI *reset_output)(HWAVEOUT);
    MMRESULT (WINAPI *unprepare_header)(HWAVEOUT, LPWAVEHDR, UINT);
    MMRESULT (WINAPI *close_wave)(HWAVEOUT);
    log_file = CreateFileA("C:\\NVRAM\\m90_audio_verify.log", GENERIC_WRITE,
        FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    text("M90 XP audio verification\r\n");
    pnp_status();
    audio_service(TRUE);
    kernel_audio_services();
    /* WinMM must initialize after Windows Audio, not during shell loading. */
    for (i = 0; i < 120; ++i) {
        if (audio_service(FALSE)) break;
        Sleep(500);
    }
    if (i == 120) goto finished;
    winmm = LoadLibraryA("winmm.dll");
    if (!winmm) { text("LoadLibrary winmm: "); number(GetLastError()); goto finished; }
#define RESOLVE(variable, name) *(FARPROC *)&variable = GetProcAddress(winmm, name); if (!variable) goto finished
    RESOLVE(get_count, "waveOutGetNumDevs");
    RESOLVE(get_caps, "waveOutGetDevCapsA");
    RESOLVE(open_output, "waveOutOpen");
    RESOLVE(prepare_header, "waveOutPrepareHeader");
    RESOLVE(write_output, "waveOutWrite");
    RESOLVE(reset_output, "waveOutReset");
    RESOLVE(unprepare_header, "waveOutUnprepareHeader");
    RESOLVE(close_wave, "waveOutClose");
#undef RESOLVE
    for (i = 0; i < 120; ++i) {
        count = get_count();
        if (count) break;
        Sleep(500);
    }
    text("waveOut devices: "); number(count);
    audio_service(FALSE);
    pnp_status();
    if (!count) goto finished;
    result = get_caps(0, &caps, sizeof(caps));
    text("waveOutGetDevCaps: "); number(result);
    if (result) goto finished;
    text("Device: "); text(caps.szPname); text("\r\n");
    result = open_output(&output, WAVE_MAPPER, &format, 0, 0, CALLBACK_NULL);
    text("waveOutOpen: "); number(result);
    if (result) goto finished;
    /* A quiet triangle; verification normally uses the host's none backend. */
    for (i = 0; i < 44100; ++i) {
        int phase = (int)(i % 100);
        short value = (short)((phase < 50 ? phase : 100 - phase) * 16 - 400);
        samples[i] = value;
    }
    header.lpData = (LPSTR)samples;
    header.dwBufferLength = sizeof(samples);
    result = prepare_header(output, &header, sizeof(header));
    text("waveOutPrepareHeader: "); number(result);
    if (result) goto close_output;
    result = write_output(output, &header, sizeof(header));
    text("waveOutWrite: "); number(result);
    if (!result) {
        for (i = 0; i < 100 && !(header.dwFlags & WHDR_DONE); ++i) Sleep(50);
        ok = (header.dwFlags & WHDR_DONE) != 0;
    }
    text("Playback completed: "); number(ok);
    reset_output(output);
    unprepare_header(output, &header, sizeof(header));
close_output:
    close_wave(output);
finished:
    if (winmm) FreeLibrary(winmm);
    text(ok ? "Audio verified\r\n" : "Audio verification failed\r\n");
    signal_result(ok);
    if (log_file != INVALID_HANDLE_VALUE) CloseHandle(log_file);
    /* Host requests clean ACPI shutdown after receiving the result. */
    Sleep(120000);
    ExitProcess(ok ? 0 : 1);
}
