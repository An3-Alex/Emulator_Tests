#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>

// Native x86 ABI observed in the supported owner game (27c45539...0427d).
// Do not use these slots with a different irrKlang library version.
typedef void *(__cdecl *Create)(int, int, const char *, const char *);
typedef void *(__thiscall *AddFile)(void *, const char *, int, bool);
typedef void *(__thiscall *PlaySource)(void *, void *, bool, bool, bool, bool);
typedef void (__thiscall *StopAll)(void *);
typedef void (__thiscall *SetVolume)(void *, float);
typedef unsigned (__thiscall *Length)(void *);

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    HMODULE dll = LoadLibraryA(argv[1]);
    if (!dll) { printf("load_error=%lu\n", GetLastError()); return 3; }
    Create create = (Create)GetProcAddress(dll,
        "?createIrrKlangDevice@irrklang@@YAPAVISoundEngine@1@W4E_SOUND_OUTPUT_DRIVER@1@HPBD1@Z");
    if (!create) return 4;
    // Request NULL as the old proxy did. The new proxy must choose WinMM.
    void *engine = create(6, 0x3d, 0, "1.1.3");
    printf("engine=%p\n", engine);
    if (!engine) return 5;
    void **vtable = *(void ***)engine;
    ((SetVolume)vtable[17])(engine, 0.0f); // never emit sound during the probe
    void *source = ((AddFile)vtable[10])(engine, argv[2], 1, true);
    printf("source=%p\n", source);
    if (!source) return 6;
    unsigned length = ((Length)(*(void ***)source)[6])(source);
    printf("length_ms=%u\n", length);
    if (!length || length == 0xffffffff) return 8;
    int failures = 0;
    for (int paused = 0; paused < 2; ++paused) {
        for (int track = 0; track < 2; ++track) {
            for (int effects = 0; effects < 2; ++effects) {
                void *sound = ((PlaySource)vtable[1])(engine, source, false,
                    paused != 0, track != 0, effects != 0);
                printf("paused=%d track=%d effects=%d sound=%p\n", paused, track, effects, sound);
                if ((track || paused || effects) && !sound) ++failures;
                ((StopAll)vtable[5])(engine);
            }
        }
    }
    // Process owns the probe's native engine; avoid guessing virtual-base
    // drop slots. This reproduces playback, not long-run lifetime behavior.
    fflush(stdout);
    ExitProcess(failures ? 7 : 0);
}
