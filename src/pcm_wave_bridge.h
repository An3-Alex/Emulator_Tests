/* Optional XP/WinMM PCM backend for the original irrKlang DLL.
   CALLBACK_NULL only: the original mixer polls WHDR_DONE. Completion follows
   the host's playback ACK, never an instant-success/NULL sound replacement. */
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <mmsystem.h>
#if defined(PCM_BRIDGE_NATIVE_PROBE)
#define PCM_USE_TCP
#endif

struct PcmBlock {
    PcmBlock *next;
    WAVEHDR *header;
    DWORD size, wire_size;
    BOOL silence;
    DWORD sequence;
    BYTE data[1];
};
struct PcmOutput {
    DWORD magic;
    WAVEFORMATEX format;
    CRITICAL_SECTION lock;
    HANDLE wake, worker;
    SOCKET socket;
    HANDLE serial;
    volatile LONG stop, failed;
    PcmBlock *first, *last;
    DWORD queued_bytes, queued_count, sequence;
};
static const DWORD PCM_MAGIC = 0x394D5742;
static void pcm_socket_cleanup(void) {
#ifdef PCM_USE_TCP
    WSACleanup();
#endif
}

static BOOL pcm_transfer(PcmOutput *output, void *memory, DWORD size, BOOL writing) {
    char *bytes = (char *)memory;
    while (size) {
#ifdef PCM_USE_TCP
        int amount = writing ? send(output->socket, bytes, (int)size, 0) : recv(output->socket, bytes, (int)size, 0);
#else
        DWORD count = 0;
        BOOL okay = writing ? WriteFile(output->serial, bytes, size, &count, NULL) : ReadFile(output->serial, bytes, size, &count, NULL);
        int amount = okay ? (int)count : 0;
#endif
        if (amount <= 0) return FALSE;
        bytes += amount;
        size -= amount;
    }
    return TRUE;
}

static void pcm_complete(PcmBlock *block) {
    block->header->dwFlags = (block->header->dwFlags & ~WHDR_INQUEUE) | WHDR_DONE;
    HeapFree(GetProcessHeap(), 0, block);
}
static BOOL pcm_connect(PcmOutput *output);

static DWORD WINAPI pcm_worker(void *parameter) {
    PcmOutput *output = (PcmOutput *)parameter;
    DWORD inflight = 0;
    BOOL silence_active = FALSE;
    DWORD silence_due = 0, silence_fraction = 0;
    while (!InterlockedCompareExchange(&output->stop, 0, 0)) {
        PcmBlock *block;
        BOOL okay = TRUE;
        EnterCriticalSection(&output->lock);
        block = output->first;
        LeaveCriticalSection(&output->lock);
        if (!inflight && block && block->silence) {
            /* No UART traffic, no SDL stream and no fake immediate completion.
               Keep fractional milliseconds across adjacent silent buffers. */
            if (!silence_active) {
                silence_due = GetTickCount(); silence_fraction = 0;
                silence_active = TRUE;
            }
            DWORD duration = block->size * 1000 + silence_fraction;
            silence_due += duration / output->format.nAvgBytesPerSec;
            silence_fraction = duration % output->format.nAvgBytesPerSec;
            while (!InterlockedCompareExchange(&output->stop, 0, 0) &&
                   (LONG)(GetTickCount() - silence_due) < 0) {
                WaitForSingleObject(output->wake, 2);
            }
            if (InterlockedCompareExchange(&output->stop, 0, 0)) break;
            EnterCriticalSection(&output->lock);
            output->first = block->next;
            if (!output->first) output->last = NULL;
            output->queued_bytes -= block->size;
            --output->queued_count;
            pcm_complete(block);
            LeaveCriticalSection(&output->lock);
            continue;
        }
        silence_active = FALSE;
        // Feed ahead instead of inserting a round-trip gap after every tiny
        // original mixer buffer. Only the playback ACK frees its WAVEHDR.
        while (inflight < 32 && !InterlockedCompareExchange(&output->stop, 0, 0)) {
            EnterCriticalSection(&output->lock);
            block = output->first;
            DWORD bytes = 0;
            for (DWORD i = 0; i < inflight && block; ++i) { bytes += block->size; block = block->next; }
            LeaveCriticalSection(&output->lock);
            if (!block || block->silence || bytes >= output->format.nAvgBytesPerSec) break;
            if (output->serial == INVALID_HANDLE_VALUE && output->socket == INVALID_SOCKET && !pcm_connect(output)) {
                okay = FALSE; break;
            }
            block->sequence = ++output->sequence;
            DWORD packet[3] = {0x41544144u, block->sequence, block->wire_size}; /* DATA */
            okay = pcm_transfer(output, packet, sizeof(packet), TRUE) &&
                pcm_transfer(output, block->data, block->wire_size, TRUE);
            if (!okay) break;
            ++inflight;
        }
        if (okay && !inflight) { WaitForSingleObject(output->wake, 25); continue; }
        EnterCriticalSection(&output->lock);
        block = output->first;
        LeaveCriticalSection(&output->lock);
        DWORD ack[2] = {0, 0};
        okay = okay && block && pcm_transfer(output, ack, sizeof(ack), FALSE) &&
            ack[0] == 0x454E4F44 && ack[1] == block->sequence; /* DONE */
        if (!okay) {
            if (!InterlockedCompareExchange(&output->stop, 0, 0)) {
                InterlockedExchange(&output->failed, 1);
                log_line("AUDIO_BRIDGE_PLAYBACK_FAILED: connection/ACK lost\r\n");
            }
            break;
        }
        EnterCriticalSection(&output->lock);
        output->first = block->next;
        if (!output->first) output->last = NULL;
        output->queued_bytes -= block->size;
        --output->queued_count;
        --inflight;
        pcm_complete(block);
        LeaveCriticalSection(&output->lock);
    }
    /* Cancel all queued headers when resetting or the receiver disappears. */
    EnterCriticalSection(&output->lock);
    while (output->first) {
        PcmBlock *block = output->first;
        output->first = block->next;
        pcm_complete(block);
    }
    output->last = NULL;
    output->queued_bytes = 0;
    output->queued_count = 0;
    LeaveCriticalSection(&output->lock);
    return 0;
}

static BOOL pcm_connect(PcmOutput *output) {
#ifdef PCM_USE_TCP
    sockaddr_in address = {0};
    u_long nonblocking = 1;
    DWORD timeout = 5000;
    int enabled = 1;
#else
    DCB settings = {0};
    COMMTIMEOUTS times = {MAXDWORD, 0, 5000, 0, 5000};
    output->serial = CreateFileA("\\\\.\\COM1", GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    if (output->serial == INVALID_HANDLE_VALUE) return FALSE;
    settings.DCBlength = sizeof(settings);
    if (!GetCommState(output->serial, &settings)) goto serial_failed;
    settings.BaudRate = CBR_115200;
    settings.ByteSize = 8; settings.Parity = NOPARITY; settings.StopBits = ONESTOPBIT;
    settings.fBinary = TRUE; settings.fParity = FALSE;
    settings.fOutxCtsFlow = FALSE; settings.fOutxDsrFlow = FALSE;
    settings.fDtrControl = DTR_CONTROL_ENABLE; settings.fDsrSensitivity = FALSE;
    settings.fOutX = FALSE; settings.fInX = FALSE;
    settings.fRtsControl = RTS_CONTROL_ENABLE; settings.fAbortOnError = FALSE;
    if (!SetCommState(output->serial, &settings) || !SetCommTimeouts(output->serial, &times) ||
        !SetupComm(output->serial, 32768, 32768) || !PurgeComm(output->serial, PURGE_RXCLEAR | PURGE_TXCLEAR)) goto serial_failed;
#endif
    /* Original cabinet: one speaker. Mix before the UART rather than sending
       stereo and discarding half of it on the host. */
    DWORD hello[4] = {0x3141394D, output->format.nSamplesPerSec,
        1, output->format.wBitsPerSample}; /* M9A1 */
    DWORD ack = 0;
#ifdef PCM_USE_TCP
    output->socket = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (output->socket == INVALID_SOCKET) return FALSE;
    setsockopt(output->socket, IPPROTO_TCP, TCP_NODELAY, (char *)&enabled, sizeof(enabled));
    setsockopt(output->socket, SOL_SOCKET, SO_SNDTIMEO, (char *)&timeout, sizeof(timeout));
    setsockopt(output->socket, SOL_SOCKET, SO_RCVTIMEO, (char *)&timeout, sizeof(timeout));
    address.sin_family = AF_INET;
    address.sin_port = htons(4765);
    address.sin_addr.s_addr = htonl(0x7F000001);
    ioctlsocket(output->socket, FIONBIO, &nonblocking);
    if (connect(output->socket, (sockaddr *)&address, sizeof(address)) == SOCKET_ERROR) {
        fd_set ready;
        timeval wait = {2, 0};
        int error = 0, length = sizeof(error);
        if (WSAGetLastError() != WSAEWOULDBLOCK) goto failed;
        FD_ZERO(&ready); FD_SET(output->socket, &ready);
        if (select(0, NULL, &ready, NULL, &wait) != 1 ||
            getsockopt(output->socket, SOL_SOCKET, SO_ERROR, (char *)&error, &length) || error) goto failed;
    }
    nonblocking = 0;
    ioctlsocket(output->socket, FIONBIO, &nonblocking);
#endif
    if (!pcm_transfer(output, hello, sizeof(hello), TRUE) ||
        !pcm_transfer(output, &ack, sizeof(ack), FALSE) || ack != 0x4E45504F) goto failed; /* OPEN */
    log_line("AUDIO_BRIDGE_STREAM_OPEN\r\n");
    return TRUE;
failed:
#ifdef PCM_USE_TCP
    closesocket(output->socket);
    output->socket = INVALID_SOCKET;
#else
serial_failed:
    CloseHandle(output->serial);
    output->serial = INVALID_HANDLE_VALUE;
#endif
    return FALSE;
}

static BOOL pcm_start(PcmOutput *output) {
    InterlockedExchange(&output->stop, 0);
    InterlockedExchange(&output->failed, 0);
    output->worker = CreateThread(NULL, 0, pcm_worker, output, 0, NULL);
    return output->worker != NULL;
}

static BOOL pcm_stop(PcmOutput *output) {
    InterlockedExchange(&output->stop, 1);
    SetEvent(output->wake);
#ifdef PCM_USE_TCP
    if (output->socket != INVALID_SOCKET) shutdown(output->socket, SD_BOTH);
#endif
    if (output->worker) {
        if (WaitForSingleObject(output->worker, 6000) != WAIT_OBJECT_0) {
            log_line("AUDIO_BRIDGE_STOP_FAILED: worker still owns buffers\r\n");
            return FALSE; /* Do not free live thread/headers. */
        }
        CloseHandle(output->worker);
        output->worker = NULL;
    }
#ifdef PCM_USE_TCP
    if (output->socket != INVALID_SOCKET) closesocket(output->socket);
#else
    if (output->serial != INVALID_HANDLE_VALUE) {
        DWORD end[3] = {0x21444E45, 0, 0}; /* END! */
        DWORD ack = 0;
        BOOL okay = pcm_transfer(output, end, sizeof(end), TRUE);
        // A reset can leave already played DONE/sequence acknowledgements in
        // the UART. Consume those before the host's session-close SHUT marker.
        for (DWORD i = 0; okay && i <= 32; ++i) {
            okay = pcm_transfer(output, &ack, sizeof(ack), FALSE);
            if (!okay || ack == 0x54554853) break; /* SHUT */
            DWORD discarded;
            okay = ack == 0x454E4F44 && pcm_transfer(output, &discarded, sizeof(discarded), FALSE);
        }
        okay = okay && ack == 0x54554853;
        CloseHandle(output->serial);
        output->serial = INVALID_HANDLE_VALUE;
        if (!okay) return FALSE;
    }
#endif
    output->socket = INVALID_SOCKET;
    return TRUE;
}

static PcmOutput *pcm_output(HWAVEOUT handle) {
    PcmOutput *output = (PcmOutput *)handle;
    return output && output->magic == PCM_MAGIC ? output : NULL;
}

static MMRESULT WINAPI pcm_open(LPHWAVEOUT handle, UINT device, LPCWAVEFORMATEX format,
    DWORD_PTR callback, DWORD_PTR instance, DWORD flags) {
    (void)callback; (void)instance;
    if (device != WAVE_MAPPER && device != 0) return MMSYSERR_BADDEVICEID;
    if (!format || format->wFormatTag != WAVE_FORMAT_PCM ||
        (format->nChannels != 1 && format->nChannels != 2) || format->wBitsPerSample != 16 ||
        format->nSamplesPerSec < 8000 || format->nSamplesPerSec > 96000 ||
        format->nBlockAlign != format->nChannels * 2 ||
        format->nAvgBytesPerSec != format->nSamplesPerSec * format->nBlockAlign) return WAVERR_BADFORMAT;
    if (flags & ~(WAVE_FORMAT_QUERY | WAVE_ALLOWSYNC)) return MMSYSERR_NOTSUPPORTED;
    if (flags & WAVE_FORMAT_QUERY) return MMSYSERR_NOERROR;
    if (!handle) return MMSYSERR_INVALPARAM;
    *handle = NULL;
#ifdef PCM_USE_TCP
    WSADATA data;
    if (WSAStartup(MAKEWORD(2, 2), &data)) return MMSYSERR_ERROR;
#endif
    PcmOutput *output = (PcmOutput *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(PcmOutput));
    if (!output) { pcm_socket_cleanup(); return MMSYSERR_NOMEM; }
    output->magic = PCM_MAGIC;
    output->format = *format;
    output->socket = INVALID_SOCKET;
    output->serial = INVALID_HANDLE_VALUE;
    InitializeCriticalSection(&output->lock);
    output->wake = CreateEventA(NULL, FALSE, FALSE, NULL);
    /* The output worker starts locally. Transport/device open happens lazily
       at the first non-silent buffer, never during loader initialization. */
    BOOL started = output->wake && pcm_start(output);
    if (!started) {
        log_line("AUDIO_BRIDGE_OPEN_FAILED: local worker unavailable\r\n");
        if (output->wake) CloseHandle(output->wake);
        DeleteCriticalSection(&output->lock);
        HeapFree(GetProcessHeap(), 0, output);
        pcm_socket_cleanup();
        return MMSYSERR_ERROR;
    }
    *handle = (HWAVEOUT)output;
    return MMSYSERR_NOERROR;
}

static MMRESULT WINAPI pcm_prepare(HWAVEOUT handle, LPWAVEHDR header, UINT size) {
    if (!pcm_output(handle)) return MMSYSERR_INVALHANDLE;
    if (!header || size < sizeof(WAVEHDR) || !header->lpData) return MMSYSERR_INVALPARAM;
    header->dwFlags |= WHDR_PREPARED;
    return MMSYSERR_NOERROR;
}
static MMRESULT WINAPI pcm_unprepare(HWAVEOUT handle, LPWAVEHDR header, UINT size) {
    if (!pcm_output(handle)) return MMSYSERR_INVALHANDLE;
    if (!header || size < sizeof(WAVEHDR)) return MMSYSERR_INVALPARAM;
    if (header->dwFlags & WHDR_INQUEUE) return WAVERR_STILLPLAYING;
    header->dwFlags &= ~WHDR_PREPARED;
    return MMSYSERR_NOERROR;
}
static MMRESULT WINAPI pcm_write(HWAVEOUT handle, LPWAVEHDR header, UINT size) {
    PcmOutput *output = pcm_output(handle);
    if (!output) return MMSYSERR_INVALHANDLE;
    if (!header || size < sizeof(WAVEHDR) || !header->lpData || !header->dwBufferLength ||
        header->dwBufferLength % output->format.nBlockAlign ||
        header->dwBufferLength > output->format.nAvgBytesPerSec * 2) return MMSYSERR_INVALPARAM;
    if (!(header->dwFlags & WHDR_PREPARED)) return WAVERR_UNPREPARED;
    if (header->dwFlags & WHDR_INQUEUE) return WAVERR_STILLPLAYING;
    if (header->dwFlags & (WHDR_BEGINLOOP | WHDR_ENDLOOP)) return MMSYSERR_NOTSUPPORTED;
    if (InterlockedCompareExchange(&output->failed, 0, 0) ||
        InterlockedCompareExchange(&output->stop, 0, 0)) return MMSYSERR_ERROR;
    DWORD wire_size = header->dwBufferLength / output->format.nChannels;
    PcmBlock *block = (PcmBlock *)HeapAlloc(GetProcessHeap(), 0, sizeof(PcmBlock) + wire_size);
    if (!block) return MMSYSERR_NOMEM;
    block->next = NULL; block->header = header; block->size = header->dwBufferLength;
    block->wire_size = wire_size;
    block->silence = TRUE;
    for (DWORD i = 0; i < wire_size / 2; ++i) {
        /* Explicit little endian reads also accept unaligned WAVEHDR data. */
        DWORD offset = i * output->format.nBlockAlign;
        const BYTE *input = (const BYTE *)header->lpData + offset;
        int value = (short)(input[0] | (input[1] << 8));
        if (output->format.nChannels == 2) {
            int sum = value + (short)(input[2] | (input[3] << 8));
            value = sum >= 0 ? sum / 2 : (sum - 1) / 2;
        }
        block->data[i*2] = (BYTE)value;
        block->data[i*2+1] = (BYTE)(value >> 8);
        if (value) block->silence = FALSE;
    }
    EnterCriticalSection(&output->lock);
    if (InterlockedCompareExchange(&output->stop, 0, 0) ||
        InterlockedCompareExchange(&output->failed, 0, 0) || output->queued_count >= 32 ||
        output->queued_bytes + block->size > 4*1024*1024) {
        LeaveCriticalSection(&output->lock);
        HeapFree(GetProcessHeap(), 0, block);
        return MMSYSERR_ERROR;
    }
    header->dwFlags = (header->dwFlags & ~WHDR_DONE) | WHDR_INQUEUE;
    if (output->last) output->last->next = block; else output->first = block;
    output->last = block;
    output->queued_bytes += block->size;
    ++output->queued_count;
    LeaveCriticalSection(&output->lock);
    SetEvent(output->wake);
    return MMSYSERR_NOERROR;
}
static MMRESULT WINAPI pcm_reset(HWAVEOUT handle) {
    PcmOutput *output = pcm_output(handle);
    if (!output) return MMSYSERR_INVALHANDLE;
    if (!pcm_stop(output) || !pcm_start(output)) return MMSYSERR_ERROR;
    return MMSYSERR_NOERROR;
}
static MMRESULT WINAPI pcm_close(HWAVEOUT handle) {
    PcmOutput *output = pcm_output(handle);
    if (!output) return MMSYSERR_INVALHANDLE;
    EnterCriticalSection(&output->lock);
    BOOL busy = output->first != NULL;
    LeaveCriticalSection(&output->lock);
    if (busy) return WAVERR_STILLPLAYING;
    if (!pcm_stop(output)) return MMSYSERR_ERROR;
    output->magic = 0;
    CloseHandle(output->wake);
    DeleteCriticalSection(&output->lock);
    HeapFree(GetProcessHeap(), 0, output);
    pcm_socket_cleanup();
    return MMSYSERR_NOERROR;
}

static BOOL pcm_install(HMODULE module) {
    const char *required[] = {"waveOutOpen", "waveOutClose", "waveOutPrepareHeader",
        "waveOutUnprepareHeader", "waveOutWrite", "waveOutReset"};
    FARPROC replacements[] = {(FARPROC)pcm_open, (FARPROC)pcm_close, (FARPROC)pcm_prepare,
        (FARPROC)pcm_unprepare, (FARPROC)pcm_write, (FARPROC)pcm_reset};
    ULONG_PTR *slots[6] = {0}, originals[6] = {0};
    BYTE *base = (BYTE *)module;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    if (!nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress) return FALSE;
    IMAGE_IMPORT_DESCRIPTOR *imports = (IMAGE_IMPORT_DESCRIPTOR *)(base + nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress);
    for (; imports->Name; ++imports) {
        if (lstrcmpiA((char *)(base + imports->Name), "winmm.dll") || !imports->OriginalFirstThunk) continue;
        IMAGE_THUNK_DATA *names = (IMAGE_THUNK_DATA *)(base + imports->OriginalFirstThunk);
        IMAGE_THUNK_DATA *table = (IMAGE_THUNK_DATA *)(base + imports->FirstThunk);
        for (; names->u1.AddressOfData; ++names, ++table) {
            if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
            char *name = (char *)((IMAGE_IMPORT_BY_NAME *)(base + names->u1.AddressOfData))->Name;
            for (DWORD i = 0; i < 6; ++i) if (!lstrcmpA(name, required[i])) slots[i] = &table->u1.Function;
        }
    }
    for (DWORD i = 0; i < 6; ++i) if (!slots[i]) return FALSE;
    DWORD changed = 0;
    for (; changed < 6; ++changed) {
        DWORD old, restored;
        if (!VirtualProtect(slots[changed], sizeof(ULONG_PTR), PAGE_READWRITE, &old)) break;
        originals[changed] = *slots[changed];
        *slots[changed] = (ULONG_PTR)replacements[changed];
        VirtualProtect(slots[changed], sizeof(ULONG_PTR), old, &restored);
    }
    if (changed != 6) {
        for (DWORD i = 0; i < changed; ++i) {
            DWORD old, restored;
            if (VirtualProtect(slots[i], sizeof(ULONG_PTR), PAGE_READWRITE, &old)) {
                *slots[i] = originals[i];
                VirtualProtect(slots[i], sizeof(ULONG_PTR), old, &restored);
            }
        }
        return FALSE;
    }
    log_line("AUDIO_BRIDGE_INSTALLED: six WinMM imports; host-paced PCM\r\n");
    return TRUE;
}
