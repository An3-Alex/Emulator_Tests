/* A separate process with the original service's ANSI import API surface. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <setupapi.h>
__declspec(dllimport) DWORD WINAPI SramCompatInitialize(void);
#define CHECK(condition, code) do { if (!(condition)) ExitProcess(code); } while (0)

void mainCRTStartup(void) {
    GUID guid = {0xBDE06013, 0x8122, 0x11DB, {0x96,0xA5,0x00,0xE0,0x81,0x61,0x16,0x5F}};
    SP_DEVICE_INTERFACE_DATA item;
    BYTE details[64], request[9] = {0x30,0,0,3,0,0,'A','B','C'}, output[3];
    HDEVINFO set;
    HANDLE a, b;
    DWORD required, bytes;
    CHECK(SramCompatInitialize() == ERROR_SUCCESS, 10);
    item.cbSize = sizeof(item);
    set = SetupDiGetClassDevsA(&guid, NULL, NULL, DIGCF_DEVICEINTERFACE);
    CHECK(set != INVALID_HANDLE_VALUE, 11);
    CHECK(SetupDiEnumDeviceInterfaces(set, NULL, &guid, 0, &item), 12);
    CHECK(!SetupDiGetDeviceInterfaceDetailA(set, &item, NULL, 0, &required, NULL) && required <= sizeof(details), 13);
    ((SP_DEVICE_INTERFACE_DETAIL_DATA_A *)details)->cbSize = sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_A);
    CHECK(SetupDiGetDeviceInterfaceDetailA(set, &item,
        (SP_DEVICE_INTERFACE_DETAIL_DATA_A *)details, sizeof(details), &required, NULL), 14);
    a = CreateFileA((char *)details + 4, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(a != INVALID_HANDLE_VALUE, 15);
    CHECK(SetupDiEnumDeviceInterfaces(set, NULL, &guid, 1, &item), 16);
    b = CreateFileA((char *)details + 4, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(b != INVALID_HANDLE_VALUE, 17);
    CHECK(SetupDiDestroyDeviceInfoList(set), 18);
    CHECK(DeviceIoControl(a, 0x9C402000, request, 9, NULL, 0, &bytes, NULL), 19);
    CHECK(DeviceIoControl(b, 0x9C402004, request, 6, output, 3, &bytes, NULL) && bytes == 3, 20);
    CHECK(output[0] == 'A' && output[1] == 'B' && output[2] == 'C', 21);
    CHECK(CloseHandle(a) && CloseHandle(b), 22);
    ExitProcess(0);
}
