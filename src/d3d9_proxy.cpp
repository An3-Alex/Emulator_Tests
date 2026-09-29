#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#define Direct3DCreate9 SDK_Direct3DCreate9_Declaration
#include <d3d9.h>
#undef Direct3DCreate9
#include <stddef.h>

static HMODULE g_self;
typedef IDirect3D9 *(WINAPI *PFN_Direct3DCreate9)(UINT);

static void log_text(const char *text)
{
    HANDLE file = CreateFileA("C:\\NVRAM\\d3d9_proxy.log", FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL, NULL);
    if (file != INVALID_HANDLE_VALUE) {
        DWORD length = 0, written;
        while (text[length]) ++length;
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

static UINT map_adapter(UINT adapter)
{
    if (adapter == 1) {
        log_text("adapter remap 1 -> 0\r\n");
        return 0;
    }
    return adapter;
}

static BOOL same_guid(REFIID a, const IID &b)
{
    return a.Data1 == b.Data1 && a.Data2 == b.Data2 && a.Data3 == b.Data3 &&
        a.Data4[0] == b.Data4[0] && a.Data4[1] == b.Data4[1] &&
        a.Data4[2] == b.Data4[2] && a.Data4[3] == b.Data4[3] &&
        a.Data4[4] == b.Data4[4] && a.Data4[5] == b.Data4[5] &&
        a.Data4[6] == b.Data4[6] && a.Data4[7] == b.Data4[7];
}

class Direct3D9Proxy : public IDirect3D9 {
    LONG refs_;
    IDirect3D9 *inner_;
public:
    static void *operator new(size_t size) { return HeapAlloc(GetProcessHeap(), 0, size); }
    static void operator delete(void *p) { if (p) HeapFree(GetProcessHeap(), 0, p); }
    Direct3D9Proxy(IDirect3D9 *inner) : refs_(1), inner_(inner) {}
    ~Direct3D9Proxy() { if (inner_) inner_->Release(); }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **object)
    {
        if (!object) return E_POINTER;
        if (same_guid(riid, IID_IUnknown) || same_guid(riid, IID_IDirect3D9)) {
            *object = static_cast<IDirect3D9 *>(this); AddRef(); return S_OK;
        }
        return inner_->QueryInterface(riid, object);
    }
    ULONG STDMETHODCALLTYPE AddRef() { return (ULONG)InterlockedIncrement(&refs_); }
    ULONG STDMETHODCALLTYPE Release()
    {
        ULONG refs = (ULONG)InterlockedDecrement(&refs_);
        if (!refs) delete this;
        return refs;
    }
    HRESULT STDMETHODCALLTYPE RegisterSoftwareDevice(void *init) { return inner_->RegisterSoftwareDevice(init); }
    UINT STDMETHODCALLTYPE GetAdapterCount()
    {
        UINT count = inner_->GetAdapterCount();
        log_hex("GetAdapterCount -> ", count, "\r\n");
        return count;
    }
    HRESULT STDMETHODCALLTYPE GetAdapterIdentifier(UINT a, DWORD f, D3DADAPTER_IDENTIFIER9 *id)
      { return inner_->GetAdapterIdentifier(map_adapter(a), f, id); }
    UINT STDMETHODCALLTYPE GetAdapterModeCount(UINT a, D3DFORMAT f)
      { return inner_->GetAdapterModeCount(map_adapter(a), f); }
    HRESULT STDMETHODCALLTYPE EnumAdapterModes(UINT a, D3DFORMAT f, UINT m, D3DDISPLAYMODE *o)
      { return inner_->EnumAdapterModes(map_adapter(a), f, m, o); }
    HRESULT STDMETHODCALLTYPE GetAdapterDisplayMode(UINT a, D3DDISPLAYMODE *m)
      { return inner_->GetAdapterDisplayMode(map_adapter(a), m); }
    HRESULT STDMETHODCALLTYPE CheckDeviceType(UINT a, D3DDEVTYPE t, D3DFORMAT ad, D3DFORMAT bb, BOOL w)
      { return inner_->CheckDeviceType(map_adapter(a), t, ad, bb, w); }
    HRESULT STDMETHODCALLTYPE CheckDeviceFormat(UINT a, D3DDEVTYPE t, D3DFORMAT af, DWORD u, D3DRESOURCETYPE r, D3DFORMAT cf)
      { return inner_->CheckDeviceFormat(map_adapter(a), t, af, u, r, cf); }
    HRESULT STDMETHODCALLTYPE CheckDeviceMultiSampleType(UINT a, D3DDEVTYPE t, D3DFORMAT sf, BOOL w, D3DMULTISAMPLE_TYPE ms, DWORD *q)
      { return inner_->CheckDeviceMultiSampleType(map_adapter(a), t, sf, w, ms, q); }
    HRESULT STDMETHODCALLTYPE CheckDepthStencilMatch(UINT a, D3DDEVTYPE t, D3DFORMAT af, D3DFORMAT rf, D3DFORMAT ds)
      { return inner_->CheckDepthStencilMatch(map_adapter(a), t, af, rf, ds); }
    HRESULT STDMETHODCALLTYPE CheckDeviceFormatConversion(UINT a, D3DDEVTYPE t, D3DFORMAT s, D3DFORMAT d)
      { return inner_->CheckDeviceFormatConversion(map_adapter(a), t, s, d); }
    HRESULT STDMETHODCALLTYPE GetDeviceCaps(UINT a, D3DDEVTYPE t, D3DCAPS9 *c)
      { return inner_->GetDeviceCaps(map_adapter(a), t, c); }
    HMONITOR STDMETHODCALLTYPE GetAdapterMonitor(UINT a)
      { return inner_->GetAdapterMonitor(map_adapter(a)); }
    HRESULT STDMETHODCALLTYPE CreateDevice(UINT a, D3DDEVTYPE t, HWND w, DWORD flags,
        D3DPRESENT_PARAMETERS *pp, IDirect3DDevice9 **device)
    {
        D3DPRESENT_PARAMETERS adjusted;
        D3DPRESENT_PARAMETERS *effective = pp;
        log_hex("CreateDevice adapter=", a, "\r\n");
        log_hex("CreateDevice flags=", flags, "\r\n");
        if (pp) {
            log_hex("CreateDevice width=", pp->BackBufferWidth, "\r\n");
            log_hex("CreateDevice height=", pp->BackBufferHeight, "\r\n");
            log_hex("CreateDevice windowed=", pp->Windowed, "\r\n");
        }
        if (a == 1 && pp) {
            adjusted = *pp;
            adjusted.Windowed = TRUE;
            adjusted.FullScreen_RefreshRateInHz = 0;
            effective = &adjusted;
            log_text("CreateDevice adapter 1: forcing non-exclusive windowed mode\r\n");
        }
        HRESULT hr = inner_->CreateDevice(map_adapter(a), t, w, flags, effective, device);
        log_hex("CreateDevice result=", (DWORD)hr, "\r\n");
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
    Direct3D9Proxy *proxy = new Direct3D9Proxy(inner);
    if (!proxy) { inner->Release(); log_text("proxy allocation failed\r\n"); }
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
