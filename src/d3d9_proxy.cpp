#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#define Direct3DCreate9 SDK_Direct3DCreate9_Declaration
#include <d3d9.h>
#undef Direct3DCreate9
#include <stddef.h>
#include "display_policy.h"
#include "guest_log_limit.h"
#ifdef M90_QEMU3DFX
#include <winsvc.h>
#endif

static HMODULE g_self;
#ifndef D3D9_PROXY_LOG
#define D3D9_PROXY_LOG "C:\\NVRAM\\d3d9_proxy.log"
#endif
/* Required by MSVC's float-forwarding methods; this DLL uses no CRT. */
extern "C" { int _fltused = 0; }
typedef IDirect3D9 *(WINAPI *PFN_Direct3DCreate9)(UINT);

static void zero_memory(void *memory, size_t size)
{
    volatile unsigned char *bytes = (volatile unsigned char *)memory;
    while (size--) *bytes++ = 0;
}

static void log_text(const char *text)
{
    DWORD length = 0;
    while (text[length]) ++length;
    m90_limit_log_before_write(D3D9_PROXY_LOG, length);
    HANDLE file = CreateFileA(D3D9_PROXY_LOG, FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL, NULL);
    if (file != INVALID_HANDLE_VALUE) {
        DWORD written;
        WriteFile(file, text, length, &written, NULL);
        CloseHandle(file);
    }
}

static void log_hex(const char *prefix, DWORD value, const char *suffix)
{
    static const char digits[] = "0123456789ABCDEF";
    char line[96];
    DWORD pos = 0, i;
    while (*prefix && pos < sizeof(line) - 1) line[pos++] = *prefix++;
    line[pos++] = '0'; line[pos++] = 'x';
    for (i = 0; i != 8; ++i)
        line[pos++] = digits[(value >> ((7 - i) * 4)) & 15];
    while (*suffix && pos < sizeof(line) - 1) line[pos++] = *suffix++;
    line[pos] = 0;
    log_text(line);
}

#ifdef M90_QEMU3DFX
/* Called by Direct3DCreate9, never from DllMain / under the loader lock.
   This only starts the already installed guest service; it installs nothing. */
static BOOL ensure_gpu_mapper()
{
    SC_HANDLE manager = OpenSCManagerA(NULL, NULL, SC_MANAGER_CONNECT);
    if (!manager) { log_hex("MAPMEM manager error=", GetLastError(), "\r\n"); return FALSE; }
    SC_HANDLE service = OpenServiceA(manager, "MAPMEM", SERVICE_QUERY_STATUS | SERVICE_START);
    if (!service) {
        DWORD error = GetLastError(); CloseServiceHandle(manager);
        log_hex("MAPMEM service error=", error, "\r\n"); return FALSE;
    }
    SERVICE_STATUS status;
    zero_memory(&status, sizeof(status));
    BOOL ready = QueryServiceStatus(service, &status);
    DWORD error = ready ? ERROR_SUCCESS : GetLastError();
    if (ready && status.dwServiceType != SERVICE_KERNEL_DRIVER) {
        ready = FALSE; error = ERROR_INVALID_DATA;
    }
    if (ready && status.dwCurrentState == SERVICE_STOPPED) {
        ready = StartServiceA(service, 0, NULL);
        error = ready ? ERROR_SUCCESS : GetLastError();
        if (!ready && error == ERROR_SERVICE_ALREADY_RUNNING) ready = TRUE;
    }
    if (ready) {
        DWORD started = GetTickCount();
        do {
            ready = QueryServiceStatus(service, &status);
            if (!ready) { error = GetLastError(); break; }
            if (status.dwCurrentState != SERVICE_START_PENDING) break;
            Sleep(50);
        } while ((DWORD)(GetTickCount() - started) < 5000);
        ready = ready && status.dwCurrentState == SERVICE_RUNNING;
        if (!ready && !error) error = status.dwWin32ExitCode ? status.dwWin32ExitCode : ERROR_SERVICE_NOT_ACTIVE;
    }
    CloseServiceHandle(service); CloseServiceHandle(manager);
    log_hex("MAPMEM ready=", ready, "\r\n");
    if (!ready) log_hex("MAPMEM start error=", error, "\r\n");
    return ready;
}
#endif

static UINT map_adapter(UINT adapter)
{
    if (adapter == 1) {
        log_text("adapter remap 1 -> 0\r\n");
        return 0;
    }
    return adapter;
}

struct CabinetMonitors {
    HMONITOR handles[2];
    MONITORINFOEXA info[2];
};

static BOOL CALLBACK collect_monitor(HMONITOR monitor, HDC, LPRECT, LPARAM context)
{
    CabinetMonitors *monitors = (CabinetMonitors *)context;
    MONITORINFOEXA info;
    zero_memory(&info, sizeof(info));
    info.cbSize = sizeof(info);
    if (!GetMonitorInfoA(monitor, &info)) return TRUE;
    UINT slot = (info.dwFlags & MONITORINFOF_PRIMARY) ? 0 : 1;
    if (!monitors->handles[slot]) {
        monitors->handles[slot] = monitor;
        monitors->info[slot] = info;
    }
    return TRUE;
}

static BOOL cabinet_monitor(UINT adapter, HMONITOR *handle, MONITORINFOEXA *info)
{
    if (adapter > 1) return FALSE;
    CabinetMonitors monitors;
    zero_memory(&monitors, sizeof(monitors));
    if (!EnumDisplayMonitors(NULL, NULL, collect_monitor, (LPARAM)&monitors) ||
        !monitors.handles[adapter]) return FALSE;
    if (handle) *handle = monitors.handles[adapter];
    if (info) *info = monitors.info[adapter];
    return TRUE;
}

static BOOL position_cabinet_window(UINT adapter, HWND window, const MONITORINFOEXA &info,
    const D3DPRESENT_PARAMETERS &pp)
{
    M90DisplayPlacement placement;
    if (!window || !m90_display_placement(adapter, info.rcMonitor.left, info.rcMonitor.top,
        info.rcMonitor.right, info.rcMonitor.bottom, pp.BackBufferWidth, pp.BackBufferHeight,
        &placement)) return FALSE;
    /* A borderless popup is still non-exclusive. It does not move the desktop
     * or force both GPU contexts into a single physical fullscreen monitor.
     */
    LONG style = GetWindowLongA(window, GWL_STYLE);
    style = (style & ~(WS_CHILD | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)) | WS_POPUP;
    SetWindowLongA(window, GWL_STYLE, style);
    BOOL result = SetWindowPos(window, HWND_NOTOPMOST, placement.x, placement.y,
        placement.width, placement.height,
        SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW);
    log_hex("Presentation adapter=", adapter, "\r\n");
    log_text("Presentation Windows device="); log_text(info.szDevice); log_text("\r\n");
    log_hex("Presentation x=", (DWORD)placement.x, "\r\n");
    log_hex("Presentation y=", (DWORD)placement.y, "\r\n");
    log_hex("Presentation position result=", result, "\r\n");
    return result;
}

/* The game requests exclusive fullscreen at its own resolution; the proxy
 * keeps a non-exclusive window instead. Exclusive fullscreen would also set
 * that monitor's mode, and that is how the game leaves the 1280x1024 desktop
 * it switched to for the service program. Apply the requested mode the same
 * way: for this session only (CDS_FULLSCREEN), never into the registry.
 */
static void apply_fullscreen_mode(const MONITORINFOEXA &monitor, const D3DPRESENT_PARAMETERS &pp)
{
    if (pp.Windowed || !pp.BackBufferWidth || !pp.BackBufferHeight) return;
    DEVMODEA mode;
    zero_memory(&mode, sizeof(mode));
    mode.dmSize = sizeof(mode);
    if (!EnumDisplaySettingsA(monitor.szDevice, ENUM_CURRENT_SETTINGS, &mode) ||
        (mode.dmPelsWidth == pp.BackBufferWidth && mode.dmPelsHeight == pp.BackBufferHeight))
        return;
    DEVMODEA wanted;
    zero_memory(&wanted, sizeof(wanted));
    wanted.dmSize = sizeof(wanted);
    wanted.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT;
    wanted.dmPelsWidth = pp.BackBufferWidth;
    wanted.dmPelsHeight = pp.BackBufferHeight;
    LONG result = ChangeDisplaySettingsExA(monitor.szDevice, &wanted, NULL, CDS_FULLSCREEN, NULL);
    log_text("Fullscreen mode for "); log_text(monitor.szDevice); log_text("\r\n");
    log_hex("Fullscreen mode previous width=", mode.dmPelsWidth, "\r\n");
    log_hex("Fullscreen mode width=", pp.BackBufferWidth, "\r\n");
    log_hex("Fullscreen mode height=", pp.BackBufferHeight, "\r\n");
    log_hex("Fullscreen mode result=", (DWORD)result, "\r\n");
}

static BOOL same_guid(REFIID a, const IID &b)
{
    return a.Data1 == b.Data1 && a.Data2 == b.Data2 && a.Data3 == b.Data3 &&
        a.Data4[0] == b.Data4[0] && a.Data4[1] == b.Data4[1] &&
        a.Data4[2] == b.Data4[2] && a.Data4[3] == b.Data4[3] &&
        a.Data4[4] == b.Data4[4] && a.Data4[5] == b.Data4[5] &&
        a.Data4[6] == b.Data4[6] && a.Data4[7] == b.Data4[7];
}

/* Retain the logical monitor across Reset (the game's video reinitialization).
 * Only rendering uses adapter zero. COM ownership and presentation stay here.
 */
class Direct3DDevice9Proxy : public IDirect3DDevice9 {
    LONG refs_;
    IDirect3DDevice9 *inner_;
    IDirect3D9 *parent_;
    UINT adapter_;
    HWND window_;

    bool prepare(D3DPRESENT_PARAMETERS &pp, MONITORINFOEXA &monitor)
    {
        if (!cabinet_monitor(adapter_, NULL, &monitor)) return false;
        if (!pp.Windowed) {
            apply_fullscreen_mode(monitor, pp);
            if (!cabinet_monitor(adapter_, NULL, &monitor)) return false;
        }
        pp.Windowed = TRUE;
        pp.FullScreen_RefreshRateInHz = 0;
        /* Reset must not move a device onto another display's focus window. */
        pp.hDeviceWindow = window_;
        return position_cabinet_window(adapter_, window_, monitor, pp);
    }
public:
    static void *operator new(size_t size) { return HeapAlloc(GetProcessHeap(), 0, size); }
    static void operator delete(void *p) { if (p) HeapFree(GetProcessHeap(), 0, p); }
    Direct3DDevice9Proxy(IDirect3DDevice9 *inner, IDirect3D9 *parent,
        UINT adapter, HWND window) : refs_(1), inner_(inner), parent_(parent),
        adapter_(adapter), window_(window) { parent_->AddRef(); }
    ~Direct3DDevice9Proxy() { inner_->Release(); parent_->Release(); }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **object) override
    {
        if (!object) return E_POINTER;
        *object = NULL;
        if (!same_guid(riid, IID_IUnknown) && !same_guid(riid, IID_IDirect3DDevice9))
            return E_NOINTERFACE;
        *object = static_cast<IDirect3DDevice9 *>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)InterlockedIncrement(&refs_); }
    ULONG STDMETHODCALLTYPE Release() override
    {
        ULONG refs = (ULONG)InterlockedDecrement(&refs_);
        if (!refs) delete this;
        return refs;
    }
    HRESULT STDMETHODCALLTYPE GetDirect3D(IDirect3D9 **parent) override
    {
        if (!parent) return D3DERR_INVALIDCALL;
        *parent = parent_; parent_->AddRef(); return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetCreationParameters(D3DDEVICE_CREATION_PARAMETERS *p) override
    {
        HRESULT hr = inner_->GetCreationParameters(p);
        if (SUCCEEDED(hr)) p->AdapterOrdinal = adapter_;
        return hr;
    }
    HRESULT STDMETHODCALLTYPE Reset(D3DPRESENT_PARAMETERS *pp) override
    {
        if (!pp) return D3DERR_INVALIDCALL;
        D3DPRESENT_PARAMETERS adjusted = *pp;
        MONITORINFOEXA monitor;
        zero_memory(&monitor, sizeof(monitor));
        log_hex("Reset logical adapter=", adapter_, "\r\n");
        if (!prepare(adjusted, monitor)) return D3DERR_NOTAVAILABLE;
        HRESULT hr = inner_->Reset(&adjusted);
        if (SUCCEEDED(hr)) {
            *pp = adjusted;
            position_cabinet_window(adapter_, window_, monitor, adjusted);
        }
        log_hex("Reset result=", (DWORD)hr, "\r\n");
        return hr;
    }
    HRESULT STDMETHODCALLTYPE CreateAdditionalSwapChain(D3DPRESENT_PARAMETERS *pp,
        IDirect3DSwapChain9 **chain) override
    {
        if (!pp) return D3DERR_INVALIDCALL;
        D3DPRESENT_PARAMETERS adjusted = *pp;
        MONITORINFOEXA monitor;
        zero_memory(&monitor, sizeof(monitor));
        if (!prepare(adjusted, monitor)) return D3DERR_NOTAVAILABLE;
        HRESULT hr = inner_->CreateAdditionalSwapChain(&adjusted, chain);
        if (SUCCEEDED(hr)) *pp = adjusted;
        return hr;
    }
    HRESULT STDMETHODCALLTYPE Present(const RECT *source, const RECT *destination,
        HWND override_window, const RGNDATA *dirty) override
    {
        /* A shared focus window must not redirect output to the other head. */
        return inner_->Present(source, destination, override_window ? window_ : NULL, dirty);
    }
#include "d3d9_device_forwarders.inc"
};

class Direct3D9Proxy : public IDirect3D9 {
    LONG refs_;
    IDirect3D9 *inner_;
#ifdef M90_QEMU3DFX
    IDirect3D9 *gpu_;
    IDirect3D9 *engine(UINT) const
    {
        return gpu_;
#define M90_ENGINE(adapter) engine(adapter)
    }
#else
#define M90_ENGINE(adapter) inner_
#endif
public:
    static void *operator new(size_t size) { return HeapAlloc(GetProcessHeap(), 0, size); }
    static void operator delete(void *p) { if (p) HeapFree(GetProcessHeap(), 0, p); }
#ifdef M90_QEMU3DFX
    Direct3D9Proxy(IDirect3D9 *inner, IDirect3D9 *gpu) : refs_(1), inner_(inner), gpu_(gpu) {}
    ~Direct3D9Proxy() { if (inner_) inner_->Release(); if (gpu_) gpu_->Release(); }
#else
    Direct3D9Proxy(IDirect3D9 *inner) : refs_(1), inner_(inner) {}
    ~Direct3D9Proxy() { if (inner_) inner_->Release(); }
#endif

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **object)
    {
        if (!object) return E_POINTER;
        if (same_guid(riid, IID_IUnknown) || same_guid(riid, IID_IDirect3D9)) {
            *object = static_cast<IDirect3D9 *>(this); AddRef(); return S_OK;
        }
#ifdef M90_QEMU3DFX
        *object = NULL;
        return E_NOINTERFACE;
#else
        return inner_->QueryInterface(riid, object);
#endif
    }
    ULONG STDMETHODCALLTYPE AddRef() { return (ULONG)InterlockedIncrement(&refs_); }
    ULONG STDMETHODCALLTYPE Release()
    {
        ULONG refs = (ULONG)InterlockedDecrement(&refs_);
        if (!refs) delete this;
        return refs;
    }
    HRESULT STDMETHODCALLTYPE RegisterSoftwareDevice(void *init) { return M90_ENGINE(0)->RegisterSoftwareDevice(init); }
    UINT STDMETHODCALLTYPE GetAdapterCount()
    {
        UINT count = cabinet_monitor(1, NULL, NULL) ? 2 :
                     (cabinet_monitor(0, NULL, NULL) ? 1 : 0);
        log_hex("GetAdapterCount -> ", count, "\r\n");
        return count;
    }
    HRESULT STDMETHODCALLTYPE GetAdapterIdentifier(UINT a, DWORD f, D3DADAPTER_IDENTIFIER9 *id)
      { return M90_ENGINE(a)->GetAdapterIdentifier(map_adapter(a), f, id); }
    UINT STDMETHODCALLTYPE GetAdapterModeCount(UINT a, D3DFORMAT f)
      { return M90_ENGINE(a)->GetAdapterModeCount(map_adapter(a), f); }
    HRESULT STDMETHODCALLTYPE EnumAdapterModes(UINT a, D3DFORMAT f, UINT m, D3DDISPLAYMODE *o)
      { return M90_ENGINE(a)->EnumAdapterModes(map_adapter(a), f, m, o); }
    HRESULT STDMETHODCALLTYPE GetAdapterDisplayMode(UINT a, D3DDISPLAYMODE *m)
    {
        if (!m) return D3DERR_INVALIDCALL;
        MONITORINFOEXA monitor;
        DEVMODEA mode;
        zero_memory(&monitor, sizeof(monitor)); zero_memory(&mode, sizeof(mode));
        mode.dmSize = sizeof(mode);
        if (!cabinet_monitor(a, NULL, &monitor) ||
            !EnumDisplaySettingsA(monitor.szDevice, ENUM_CURRENT_SETTINGS, &mode))
            return D3DERR_NOTAVAILABLE;
        m->Width = mode.dmPelsWidth; m->Height = mode.dmPelsHeight;
        m->RefreshRate = mode.dmDisplayFrequency;
        m->Format = mode.dmBitsPerPel == 32 ? D3DFMT_X8R8G8B8 : D3DFMT_R5G6B5;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE CheckDeviceType(UINT a, D3DDEVTYPE t, D3DFORMAT ad, D3DFORMAT bb, BOOL w)
      { return M90_ENGINE(a)->CheckDeviceType(map_adapter(a), t, ad, bb, w); }
    HRESULT STDMETHODCALLTYPE CheckDeviceFormat(UINT a, D3DDEVTYPE t, D3DFORMAT af, DWORD u, D3DRESOURCETYPE r, D3DFORMAT cf)
      { return M90_ENGINE(a)->CheckDeviceFormat(map_adapter(a), t, af, u, r, cf); }
    HRESULT STDMETHODCALLTYPE CheckDeviceMultiSampleType(UINT a, D3DDEVTYPE t, D3DFORMAT sf, BOOL w, D3DMULTISAMPLE_TYPE ms, DWORD *q)
      { return M90_ENGINE(a)->CheckDeviceMultiSampleType(map_adapter(a), t, sf, w, ms, q); }
    HRESULT STDMETHODCALLTYPE CheckDepthStencilMatch(UINT a, D3DDEVTYPE t, D3DFORMAT af, D3DFORMAT rf, D3DFORMAT ds)
      { return M90_ENGINE(a)->CheckDepthStencilMatch(map_adapter(a), t, af, rf, ds); }
    HRESULT STDMETHODCALLTYPE CheckDeviceFormatConversion(UINT a, D3DDEVTYPE t, D3DFORMAT s, D3DFORMAT d)
      { return M90_ENGINE(a)->CheckDeviceFormatConversion(map_adapter(a), t, s, d); }
    HRESULT STDMETHODCALLTYPE GetDeviceCaps(UINT a, D3DDEVTYPE t, D3DCAPS9 *c)
      { return M90_ENGINE(a)->GetDeviceCaps(map_adapter(a), t, c); }
    HMONITOR STDMETHODCALLTYPE GetAdapterMonitor(UINT a)
    {
        HMONITOR monitor = NULL;
        if (!cabinet_monitor(a, &monitor, NULL)) {
            log_hex("Missing presentation monitor for adapter=", a, "\r\n");
            return NULL;
        }
        return monitor;
    }
    HRESULT STDMETHODCALLTYPE CreateDevice(UINT a, D3DDEVTYPE t, HWND w, DWORD flags,
        D3DPRESENT_PARAMETERS *pp, IDirect3DDevice9 **device)
    {
        if (!device || !pp || a > 1) return D3DERR_INVALIDCALL;
        *device = NULL;
        D3DPRESENT_PARAMETERS adjusted;
        D3DPRESENT_PARAMETERS *effective = pp;
        MONITORINFOEXA monitor;
        zero_memory(&monitor, sizeof(monitor));
        HWND presentation_window = pp && pp->hDeviceWindow ? pp->hDeviceWindow : w;
        log_hex("CreateDevice adapter=", a, "\r\n");
        log_hex("CreateDevice flags=", flags, "\r\n");
        log_hex("CreateDevice focus window=", (DWORD)(ULONG_PTR)w, "\r\n");
        log_hex("CreateDevice presentation window=", (DWORD)(ULONG_PTR)presentation_window, "\r\n");
        if (pp) {
            log_hex("CreateDevice width=", pp->BackBufferWidth, "\r\n");
            log_hex("CreateDevice height=", pp->BackBufferHeight, "\r\n");
            log_hex("CreateDevice windowed=", pp->Windowed, "\r\n");
        }
        if (a <= 1 && pp) {
            if (!cabinet_monitor(a, NULL, &monitor) || !presentation_window) {
                log_text("CreateDevice missing cabinet monitor or window\r\n");
                if (device) *device = NULL;
                return D3DERR_NOTAVAILABLE;
            }
            if (!pp->Windowed) {
                apply_fullscreen_mode(monitor, *pp);
                if (!cabinet_monitor(a, NULL, &monitor)) {
                    if (device) *device = NULL;
                    return D3DERR_NOTAVAILABLE;
                }
            }
            adjusted = *pp;
            adjusted.Windowed = TRUE;
            adjusted.FullScreen_RefreshRateInHz = 0;
            adjusted.hDeviceWindow = presentation_window;
            effective = &adjusted;
            if (!position_cabinet_window(a, presentation_window, monitor, adjusted)) {
                if (device) *device = NULL;
                return D3DERR_NOTAVAILABLE;
            }
            log_text("CreateDevice: separate monitor, non-exclusive windowed mode\r\n");
        }
#ifdef M90_QEMU3DFX
        log_text("CreateDevice backend=QEMU3DFX\r\n");
#endif
        HRESULT hr = M90_ENGINE(a)->CreateDevice(map_adapter(a), t, w, flags, effective, device);
        log_hex("CreateDevice result=", (DWORD)hr, "\r\n");
        if (SUCCEEDED(hr) && pp && effective == &adjusted) {
            *pp = adjusted;
            position_cabinet_window(a, presentation_window, monitor, adjusted);
            IDirect3DDevice9 *inner_device = *device;
            Direct3DDevice9Proxy *wrapped = new Direct3DDevice9Proxy(
                inner_device, this, a, presentation_window);
            if (!wrapped) {
                inner_device->Release(); *device = NULL;
                return E_OUTOFMEMORY;
            }
            *device = wrapped;
        }
        return hr;
    }
};

extern "C" __declspec(dllexport) IDirect3D9 *WINAPI Direct3DCreate9(UINT sdk)
{
    char path[MAX_PATH];
    DWORD length;
    log_hex("Direct3DCreate9 sdk=", sdk, "\r\n");
    length = GetModuleFileNameA(g_self, path, MAX_PATH);
    if (!length || length >= MAX_PATH) { log_text("proxy path failed\r\n"); return NULL; }
    while (length && path[length - 1] != '\\') --length;
#ifdef M90_QEMU3DFX
    if (!ensure_gpu_mapper()) return NULL;
    const char gl_name[] = "opengl32.dll";
    DWORD gl_index = 0;
    while (gl_name[gl_index] && length + gl_index + 1 < MAX_PATH) { path[length + gl_index] = gl_name[gl_index]; ++gl_index; }
    path[length + gl_index] = 0;
    HMODULE gl_module = LoadLibraryExA(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!gl_module) { log_hex("QEMU3DFX OpenGL load error=", GetLastError(), "\r\n"); return NULL; }
    char actual_gl[MAX_PATH];
    DWORD gl_length = GetModuleFileNameA(gl_module, actual_gl, MAX_PATH);
    if (!gl_length || gl_length >= MAX_PATH || lstrcmpiA(actual_gl, path) != 0) {
        log_text("QEMU3DFX refuses system OpenGL fallback\r\n");
        FreeLibrary(gl_module); return NULL;
    }
    log_text("QEMU3DFX OpenGL ready\r\n");
#endif
#ifndef M90_QEMU3DFX
    const char real_name[] = "swiftshader_d3d9.dll";
    DWORD i = 0;
    while (real_name[i] && length + i + 1 < MAX_PATH) { path[length + i] = real_name[i]; ++i; }
    path[length + i] = 0;
    HMODULE real = LoadLibraryA(path);
    if (!real) { log_hex("LoadLibrary failed=", GetLastError(), "\r\n"); return NULL; }
    PFN_Direct3DCreate9 create = (PFN_Direct3DCreate9)GetProcAddress(real, "Direct3DCreate9");
    if (!create) { log_hex("GetProcAddress failed=", GetLastError(), "\r\n"); return NULL; }
    IDirect3D9 *inner = create(sdk);
    log_hex("real Direct3DCreate9 -> ", (DWORD)(ULONG_PTR)inner, "\r\n");
    if (!inner) return NULL;
#endif
#ifdef M90_QEMU3DFX
    const char gpu_name[] = "wined3d_d3d9.dll";
    DWORD i = 0;
    while (gpu_name[i] && length + i + 1 < MAX_PATH) { path[length + i] = gpu_name[i]; ++i; }
    path[length + i] = 0;
    HMODULE gpu_module = LoadLibraryExA(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!gpu_module) {
        DWORD error = GetLastError();
        log_text("QEMU3DFX backend missing\r\n");
        log_text("QEMU3DFX DLL path="); log_text(path); log_text("\r\n");
        log_hex("QEMU3DFX LoadLibrary error=", error, "\r\n");
        return NULL;
    }
    PFN_Direct3DCreate9 gpu_create = (PFN_Direct3DCreate9)GetProcAddress(gpu_module, "Direct3DCreate9");
    IDirect3D9 *gpu = gpu_create ? gpu_create(sdk) : NULL;
    if (!gpu) { log_text("QEMU3DFX creation failed\r\n"); return NULL; }
    Direct3D9Proxy *proxy = new Direct3D9Proxy(NULL, gpu);
    if (!proxy) gpu->Release();
#else
    Direct3D9Proxy *proxy = new Direct3D9Proxy(inner);
    if (!proxy) { inner->Release(); log_text("proxy allocation failed\r\n"); }
#endif
    return proxy;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = instance;
        DisableThreadLibraryCalls(instance);
        log_text("d3d9 proxy attach\r\n");
    }
    return TRUE;
}
