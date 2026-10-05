#ifndef CGOS_SHIM_ABI_H
#define CGOS_SHIM_ABI_H

#include <windows.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CGOS_CALL __stdcall
typedef void *HCGOS;

#define CGOS_BOARD_MAX_SIZE_ID_STRING 16
#define CGOS_BOARD_MAX_SIZE_SERIAL_STRING 16
#define CGOS_BOARD_MAX_SIZE_PART_STRING 20
#define CGOS_BOARD_MAX_SIZE_EAN_STRING 20

typedef struct CGOSTIME_TAG {
    unsigned short wYear;
    unsigned short wMonth;
    unsigned short wDayOfWeek;
    unsigned short wDay;
    unsigned short wHour;
    unsigned short wMinute;
    unsigned short wSecond;
    unsigned short wMilliseconds;
} CGOSTIME;

typedef struct CGOSBOARDINFOW_TAG {
    unsigned int dwSize;
    unsigned int dwFlags;
    WCHAR szReserved[CGOS_BOARD_MAX_SIZE_ID_STRING];
    WCHAR szBoard[CGOS_BOARD_MAX_SIZE_ID_STRING];
    WCHAR szBoardSub[CGOS_BOARD_MAX_SIZE_ID_STRING];
    WCHAR szManufacturer[CGOS_BOARD_MAX_SIZE_ID_STRING];
    CGOSTIME stManufacturingDate;
    CGOSTIME stLastRepairDate;
    WCHAR szSerialNumber[CGOS_BOARD_MAX_SIZE_SERIAL_STRING];
    unsigned short wProductRevision;
    unsigned short wSystemBiosRevision;
    unsigned short wBiosInterfaceRevision;
    unsigned short wBiosInterfaceBuildRevision;
    unsigned int dwClasses;
    unsigned int dwPrimaryClass;
    unsigned int dwRepairCounter;
    WCHAR szPartNumber[CGOS_BOARD_MAX_SIZE_PART_STRING];
    WCHAR szEAN[CGOS_BOARD_MAX_SIZE_EAN_STRING];
    unsigned int dwManufacturer;
} CGOSBOARDINFOW;

typedef char CGOSBOARDINFOW_must_be_0x130[(sizeof(CGOSBOARDINFOW) == 0x130) ? 1 : -1];

typedef struct CGOSTEMPERATUREINFO_TAG {
    unsigned int dwSize;
    unsigned int dwType;
    unsigned int dwFlags;
    unsigned int dwAlarm;
    unsigned int dwRes;
    unsigned int dwMin;
    unsigned int dwMax;
    unsigned int dwAlarmHi;
    unsigned int dwHystHi;
    unsigned int dwAlarmLo;
    unsigned int dwHystLo;
} CGOSTEMPERATUREINFO;

typedef char CGOSTEMPERATUREINFO_must_be_0x2c[(sizeof(CGOSTEMPERATUREINFO) == 0x2C) ? 1 : -1];

#define CGOS_TEMP_CPU       0x00010000u
#define CGOS_TEMP_BOARD     0x00040000u
#define CGOS_SENSOR_ACTIVE  0x00000001u

#ifdef __cplusplus
}
#endif

#endif
