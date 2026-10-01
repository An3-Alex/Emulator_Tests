/* Same XP SetupAPI installation path as QXL, targeting only QEMU's AC'97. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <setupapi.h>
static BOOL prepare_software_audio(void);
#define INSTALLER_PREPARE prepare_software_audio
#define INSTALLER_TITLE "M90 SigmaTel XP audio installer\r\n"
#define INSTALLER_INF_PATH "C:\\NVRAM\\m90-audio-driver\\stac97.inf"
#define INSTALLER_HARDWARE_ID "PCI\\VEN_8086&DEV_2415&CC_0401"
#define INSTALLER_LOG_PATH "C:\\NVRAM\\m90_audio_install.log"
#define INSTALLER_EXPECTED_DEVICES 1
#define INSTALLER_REBOOT 0
#define INSTALLER_REGISTER_INTERFACES 1
#define INSTALLER_EARLY_DIALOG_HELPER 1
#define INSTALLER_OK_SIGNAL "M90-AUDIO-SETUP-OK\n"
#define INSTALLER_FAILED_SIGNAL "M90-AUDIO-SETUP-FAILED\n"
#include "qxl_installer.c"

static BOOL prepare_software_audio(void) {
    GUID system_class;
    HDEVINFO devices;
    SP_DEVINFO_DATA device;
    SP_DEVINSTALL_PARAMS_A params;
    SP_DRVINFO_DATA_A driver;
    char class_name[128];
    DWORD index;
    BOOL found = FALSE, ok = FALSE;
    static const char hardware_id[] = "ROOT\\SWENUM\0";
    static const char *commands[] = {
        "C:\\WINDOWS\\system32\\rundll32.exe streamci.dll,StreamingDeviceSetup {A7C7A5B0-5AF3-11D1-9CED-00A024BF0407},{9B365890-165F-11D0-A195-0020AFD156E4},{A7C7A5B1-5AF3-11D1-9CED-00A024BF0407},C:\\WINDOWS\\INF\\wdmaudio.inf,WDM_SYSAUDIO.Interface.Install",
        "C:\\WINDOWS\\system32\\rundll32.exe streamci.dll,StreamingDeviceSetup {B7EAFDC0-A680-11D0-96D8-00AA0051E51D},{9B365890-165F-11D0-A195-0020AFD156E4},{AD809C00-7B88-11D0-A5D6-28DB04C10000},C:\\WINDOWS\\INF\\wdmaudio.inf,WDM_KMIXER.Interface.Install",
        "C:\\WINDOWS\\system32\\rundll32.exe streamci.dll,StreamingDeviceSetup {CD171DE3-69E5-11D2-B56D-0000F8754380},{9B365890-165F-11D0-A195-0020AFD156E4},{3E227E76-690D-11D2-8161-0000F8775BF1},C:\\WINDOWS\\INF\\wdmaudio.inf,WDM_WDMAUD.Interface.Install"
    };
    if (!SetupDiGetINFClassA("C:\\WINDOWS\\INF\\machine.inf", &system_class, class_name, sizeof(class_name), NULL)) return FALSE;
    devices = SetupDiGetClassDevsA(&system_class, "ROOT", NULL, DIGCF_PRESENT);
    if (devices == INVALID_HANDLE_VALUE) return FALSE;
    for (index = 0;; ++index) {
        char ids[1024];
        zero_memory(&device, sizeof(device)); device.cbSize = sizeof(device);
        if (!SetupDiEnumDeviceInfo(devices, index, &device)) break;
        zero_memory(ids, sizeof(ids));
        if (SetupDiGetDeviceRegistryPropertyA(devices, &device, SPDRP_HARDWAREID, NULL, (PBYTE)ids, sizeof(ids), NULL)
                && multistring_contains(ids, "ROOT\\SWENUM")) { found = TRUE; break; }
    }
    if (!found) {
        zero_memory(&device, sizeof(device)); device.cbSize = sizeof(device);
        if (!SetupDiCreateDeviceInfoA(devices, "SWENUM", &system_class, "Plug and Play Software Device Enumerator", NULL, DICD_GENERATE_ID, &device)) goto done;
        if (!SetupDiSetDeviceRegistryPropertyA(devices, &device, SPDRP_HARDWAREID, (const BYTE *)hardware_id, sizeof(hardware_id))) goto done;
        if (!SetupDiCallClassInstaller(DIF_REGISTERDEVICE, devices, &device)) goto done;
    }
    zero_memory(&params, sizeof(params)); params.cbSize = sizeof(params);
    if (!SetupDiGetDeviceInstallParamsA(devices, &device, &params)) goto done;
    params.Flags |= DI_ENUMSINGLEINF;
    copy_text(params.DriverPath, "C:\\WINDOWS\\INF\\machine.inf", MAX_PATH);
    if (!SetupDiSetDeviceInstallParamsA(devices, &device, &params)) goto done;
    if (!SetupDiBuildDriverInfoList(devices, &device, SPDIT_COMPATDRIVER)) goto done;
    zero_memory(&driver, sizeof(driver)); driver.cbSize = sizeof(driver);
    if (!SetupDiEnumDriverInfoA(devices, &device, SPDIT_COMPATDRIVER, 0, &driver)) goto done;
    if (!SetupDiSetSelectedDriverA(devices, &device, &driver)) goto done;
    ok = SetupDiCallClassInstaller(DIF_INSTALLDEVICE, devices, &device);
done:
    write_text("Software bus setup: "); write_hex(ok); write_hex(GetLastError());
    SetupDiDestroyDeviceInfoList(devices);
    if (!ok) return FALSE;
    for (index = 0; index < sizeof(commands) / sizeof(commands[0]); ++index) {
        STARTUPINFOA startup;
        PROCESS_INFORMATION process;
        char command[1024];
        DWORD code = 1;
        zero_memory(&startup, sizeof(startup)); startup.cb = sizeof(startup);
        zero_memory(&process, sizeof(process));
        copy_text(command, commands[index], sizeof(command));
        if (!CreateProcessA("C:\\WINDOWS\\system32\\rundll32.exe", command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, "C:\\WINDOWS\\system32", &startup, &process)) return FALSE;
        if (WaitForSingleObject(process.hProcess, 60000) != WAIT_OBJECT_0) TerminateProcess(process.hProcess, 1);
        GetExitCodeProcess(process.hProcess, &code);
        CloseHandle(process.hThread); CloseHandle(process.hProcess);
        write_text("WDM registration result: "); write_hex(code);
        if (code) return FALSE;
    }
    return TRUE;
}
