#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winsvc.h>

static int scenario, starts, queries;
static DWORD clock_value;
static SC_HANDLE WINAPI fake_manager(LPCSTR,LPCSTR,DWORD) {
    if(scenario==4){SetLastError(ERROR_ACCESS_DENIED);return NULL;}
    return (SC_HANDLE)1;
}
static SC_HANDLE WINAPI fake_service(SC_HANDLE,LPCSTR,DWORD) { return (SC_HANDLE)2; }
static BOOL WINAPI fake_query(SC_HANDLE,SERVICE_STATUS *s) {
    ++queries;
    if(scenario==6){SetLastError(ERROR_ACCESS_DENIED);return FALSE;}
    s->dwServiceType=scenario==2?SERVICE_WIN32_OWN_PROCESS:SERVICE_KERNEL_DRIVER;
    s->dwCurrentState=scenario==5?SERVICE_START_PENDING:(scenario==1||starts?SERVICE_RUNNING:SERVICE_STOPPED);
    s->dwWin32ExitCode=0;return TRUE;
}
static BOOL WINAPI fake_start(SC_HANDLE,DWORD,LPCSTR*) {
    ++starts;if(scenario==3){SetLastError(ERROR_PATH_NOT_FOUND);return FALSE;}return TRUE;
}
static BOOL WINAPI fake_close(SC_HANDLE) { return TRUE; }
static DWORD WINAPI fake_tick() { clock_value+=1000;return clock_value; }
static void WINAPI fake_sleep(DWORD) {}

#define OpenSCManagerA fake_manager
#define OpenServiceA fake_service
#define QueryServiceStatus fake_query
#define StartServiceA fake_start
#define CloseServiceHandle fake_close
#define GetTickCount fake_tick
#define Sleep fake_sleep
#define M90_QEMU3DFX 1
#define D3D9_PROXY_LOG "gpu-mapper-harness.log"
#include "../src/d3d9_proxy.cpp"

extern "C" void __stdcall mainCRTStartup() {
    for(scenario=0;scenario<7;++scenario){
        starts=queries=0;clock_value=0;
        BOOL ready=ensure_gpu_mapper();
        if(ready!=(scenario<=1))ExitProcess(10+scenario);
        if(scenario==0&&starts!=1)ExitProcess(20);
        if(scenario==1&&starts!=0)ExitProcess(21);
        if(scenario==2&&starts!=0)ExitProcess(22);
        if(scenario==5&&queries>8)ExitProcess(23);
    }
    const char message[]="GPU_MAPPER_PASS stopped/running, invalid service, start/query/access failure, bounded pending wait\r\n";
    DWORD written;WriteFile(GetStdHandle(STD_OUTPUT_HANDLE),message,sizeof(message)-1,&written,NULL);
    ExitProcess(0);
}
