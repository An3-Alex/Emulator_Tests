#define M90_QEMU3DFX 1
#define D3D9_PROXY_LOG "d3d9-backend-harness.log"
#include "../src/d3d9_proxy.cpp"

class Fake9 : public IDirect3D9 {
public:
    ULONG refs;
    DWORD marker;
    Fake9(DWORD m) : refs(1), marker(m) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID,void **p) { *p = NULL; return E_NOINTERFACE; }
    ULONG STDMETHODCALLTYPE AddRef() { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() { return --refs; }
    HRESULT STDMETHODCALLTYPE RegisterSoftwareDevice(void *) { return S_OK; }
    UINT STDMETHODCALLTYPE GetAdapterCount() { return 1; }
    HRESULT STDMETHODCALLTYPE GetAdapterIdentifier(UINT a,DWORD,D3DADAPTER_IDENTIFIER9 *p)
      { if (a) return D3DERR_INVALIDCALL; zero_memory(p,sizeof(*p)); p->VendorId=marker; return S_OK; }
    UINT STDMETHODCALLTYPE GetAdapterModeCount(UINT a,D3DFORMAT) { return a ? 0 : marker; }
    HRESULT STDMETHODCALLTYPE EnumAdapterModes(UINT,D3DFORMAT,UINT,D3DDISPLAYMODE *) { return S_OK; }
    HRESULT STDMETHODCALLTYPE GetAdapterDisplayMode(UINT,D3DDISPLAYMODE *) { return S_OK; }
    HRESULT STDMETHODCALLTYPE CheckDeviceType(UINT,D3DDEVTYPE,D3DFORMAT,D3DFORMAT,BOOL) { return S_OK; }
    HRESULT STDMETHODCALLTYPE CheckDeviceFormat(UINT,D3DDEVTYPE,D3DFORMAT,DWORD,D3DRESOURCETYPE,D3DFORMAT) { return S_OK; }
    HRESULT STDMETHODCALLTYPE CheckDeviceMultiSampleType(UINT,D3DDEVTYPE,D3DFORMAT,BOOL,D3DMULTISAMPLE_TYPE,DWORD *) { return S_OK; }
    HRESULT STDMETHODCALLTYPE CheckDepthStencilMatch(UINT,D3DDEVTYPE,D3DFORMAT,D3DFORMAT,D3DFORMAT) { return S_OK; }
    HRESULT STDMETHODCALLTYPE CheckDeviceFormatConversion(UINT,D3DDEVTYPE,D3DFORMAT,D3DFORMAT) { return S_OK; }
    HRESULT STDMETHODCALLTYPE GetDeviceCaps(UINT a,D3DDEVTYPE,D3DCAPS9 *p)
      { if (a) return D3DERR_INVALIDCALL; zero_memory(p,sizeof(*p)); p->Caps=marker; return S_OK; }
    HMONITOR STDMETHODCALLTYPE GetAdapterMonitor(UINT) { return NULL; }
    HRESULT STDMETHODCALLTYPE CreateDevice(UINT,D3DDEVTYPE,HWND,DWORD,D3DPRESENT_PARAMETERS *,IDirect3DDevice9 **p)
      { *p=NULL; return D3DERR_NOTAVAILABLE; }
};

static void check(bool condition, UINT code) { if (!condition) ExitProcess(code); }
extern "C" void __stdcall mainCRTStartup(void)
{
    Fake9 cpu(0x1111), gpu(0x2222);
    Direct3D9Proxy *proxy = new Direct3D9Proxy(&cpu,&gpu);
    check(proxy != NULL,1);
    D3DCAPS9 caps;
    check(SUCCEEDED(proxy->GetDeviceCaps(0,D3DDEVTYPE_HAL,&caps)) && caps.Caps==gpu.marker,2);
    check(SUCCEEDED(proxy->GetDeviceCaps(1,D3DDEVTYPE_HAL,&caps)) && caps.Caps==cpu.marker,3);
    D3DADAPTER_IDENTIFIER9 id;
    check(SUCCEEDED(proxy->GetAdapterIdentifier(0,0,&id)) && id.VendorId==gpu.marker,4);
    check(SUCCEEDED(proxy->GetAdapterIdentifier(1,0,&id)) && id.VendorId==cpu.marker,5);
    check(proxy->GetAdapterModeCount(0,D3DFMT_X8R8G8B8)==gpu.marker,6);
    check(proxy->GetAdapterModeCount(1,D3DFMT_X8R8G8B8)==cpu.marker,7);
    void *query=NULL;
    check(SUCCEEDED(proxy->QueryInterface(IID_IDirect3D9,&query)) && query==proxy,8);
    check(proxy->Release()==1,9);
    const IID unsupported = {0xffffffff,0,0,{0}};
    check(proxy->QueryInterface(unsupported,&query)==E_NOINTERFACE && query==NULL,10);
    check(proxy->Release()==0 && cpu.refs==0 && gpu.refs==0,11);
    const char message[]="D3D9_BACKEND_PASS primary GPU, secondary CPU, adapter remapping and COM lifetime\r\n";
    DWORD written;
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE),message,sizeof(message)-1,&written,NULL);
    ExitProcess(0);
}
