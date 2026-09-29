#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include "cgos_abi.h"

__declspec(dllimport) unsigned int CGOS_CALL CgosLibInitialize(void);
__declspec(dllimport) unsigned int CGOS_CALL CgosLibUninitialize(void);
__declspec(dllimport) unsigned int CGOS_CALL CgosBoardOpen(unsigned int, unsigned int, unsigned int, HCGOS *);
__declspec(dllimport) unsigned int CGOS_CALL CgosBoardClose(HCGOS);
__declspec(dllimport) unsigned int CGOS_CALL CgosBoardGetInfoW(HCGOS, CGOSBOARDINFOW *);
__declspec(dllimport) unsigned int CGOS_CALL CgosTemperatureCount(HCGOS);
__declspec(dllimport) unsigned int CGOS_CALL CgosTemperatureGetInfo(HCGOS, unsigned int, CGOSTEMPERATUREINFO *);
__declspec(dllimport) unsigned int CGOS_CALL CgosTemperatureGetCurrent(HCGOS, unsigned int, unsigned int *, unsigned int *);

static int equal_wascii(const WCHAR *wide, const char *ascii) {
    while (*ascii && *wide == (WCHAR)(unsigned char)*ascii) { ++wide; ++ascii; }
    return *ascii == 0 && *wide == 0;
}

static void zero_bytes(void *pointer, unsigned int size) {
    volatile unsigned char *byte = (volatile unsigned char *)pointer;
    while (size--) *byte++ = 0;
}

void __cdecl mainCRTStartup(void) {
    HCGOS handle = 0;
    CGOSBOARDINFOW board;
    CGOSTEMPERATUREINFO temperature;
    unsigned int value = 0, status = 0, unit;
    int result = 0;
    if (!CgosLibInitialize()) result = 1;
    if (!result && !CgosBoardOpen(0, 0, 0, &handle)) result = 2;
    zero_bytes(&board, sizeof(board)); board.dwSize = sizeof(board);
    if (!result && !CgosBoardGetInfoW(handle, &board)) result = 3;
    if (!result && (!equal_wascii(board.szBoard, "B945") ||
        !equal_wascii(board.szManufacturer, "congatec") ||
        !equal_wascii(board.szSerialNumber, "000000533731"))) result = 4;
    if (!result && CgosTemperatureCount(handle) != 2) result = 5;
    for (unit = 0; !result && unit < 2; ++unit) {
        zero_bytes(&temperature, sizeof(temperature)); temperature.dwSize = sizeof(temperature);
        if (!CgosTemperatureGetInfo(handle, unit, &temperature)) result = 6;
        if (!result && (temperature.dwSize != 0x2C ||
            temperature.dwType != (unit ? CGOS_TEMP_BOARD : CGOS_TEMP_CPU) ||
            temperature.dwFlags != CGOS_SENSOR_ACTIVE)) result = 7;
        if (!result && !CgosTemperatureGetCurrent(handle, unit, &value, &status)) result = 8;
        if (!result && (value != (unit ? 40000u : 45000u) || status != CGOS_SENSOR_ACTIVE)) result = 9;
    }
    zero_bytes(&temperature, sizeof(temperature)); temperature.dwSize = sizeof(temperature);
    if (!result && CgosTemperatureGetInfo(handle, 2, &temperature)) result = 10;
    if (handle) CgosBoardClose(handle);
    CgosLibUninitialize();
    ExitProcess((UINT)result);
}
