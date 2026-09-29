# Static import table

## Hardware-facing edges

| Importer | Imported module | Relevant imports | Finding |
|---|---|---|---|
| `ADPInterface.dll` | `Cgos.dll` | `CgosLibInitialize`, `CgosLibUninitialize`, `CgosLibIsAvailable`, `CgosLibInstall`, `CgosLibGetVersion`, `CgosLibGetLastError`, `CgosBoardOpen`, `CgosBoardClose`, `CgosBoardGetInfoW`, `CgosI2CCount`, `CgosI2CIsAvailable`, `CgosI2CRead`, `CgosI2CWrite`, `CgosVgaCount`, `CgosVgaGetContrast`, `CgosVgaSetContrast`, `CgosVgaGetBacklight`, `CgosVgaSetBacklight`, `CgosWDogSetConfig`, `CgosWDogDisable`, `CgosWDogTrigger`, `CgosTemperatureCount`, `CgosTemperatureGetInfo`, `CgosTemperatureGetCurrent` | Exact CGOS surface required by the shim. |
| `ADPInterface.dll` | `Jida.dll` | Ordinals 2, 3, 4, 5, 9, 16-19, 29-32, 40, 43, 48, 49, 56, 59, 79-81 | Alternative board-family backend; no names encoded in the import table. |
| `ADPInterface.dll` | `UspEpc.dll` | `UspOpen`, `UspClose`, `UspGetVersion`, `UspGetLastError`, `UspGetBoardInfo`, I2C, backlight/contrast, watchdog, and thermal APIs | Alternative board-family backend. |
| `Cgos.dll` | `KERNEL32.dll` | `CreateFileA`, `DeviceIoControl`, `CloseHandle`, service-related helpers | User-mode client opens and controls the kernel device. |
| `Cgos.dll` | `ADVAPI32.dll` | SCM and service creation/start/delete APIs | Original DLL can install/manage `Cgos.sys`. |
| `Cgos.sys` | `ntoskrnl.exe` | `IoCreateDevice`, `IoCreateSymbolicLink`, `MmMapIoSpace`, `MmUnmapIoSpace`, pool allocation, event/delay and completion APIs | Original driver maps physical firmware/I/O space; unsuitable for direct use under generic QEMU hardware. |
| `Cgos.sys` | `HAL.dll` | `ExAcquireFastMutex`, `ExReleaseFastMutex` | Kernel synchronization dependency. |
| `adp-loader.exe` | `FBWFLIB.dll` | `FbwfIsFilterEnabled`, `FbwfDisableFilter` | Loader explicitly manages the XP Embedded file-based write filter. |
| `adp-loader.exe` | `IPHLPAPI.dll` | `GetIfTable` | Loader inspects network interfaces/MAC identity. |
| `adp-loader.exe` | `SETUPAPI.dll` | device/interface enumeration and registry-property APIs | Loader enumerates expected hardware. |

All inspected images import Win32 APIs available to Windows XP or older and
carry x86 PE headers. `ADPInterface.dll` is the only inspected binary with a
direct named dependency on the CGOS API. The compatibility DLL exports this
exact 24-function subset with the original ordinals recorded in `src/Cgos.def`.
`dumpbin /exports` confirms 24 named exports and the required sparse ordinal
range 1 through 96 in the phase-2 build. Its verified SHA-256 is
`D210E22986444BE1FC6767DC9774880985F4753DB8A30D03D1357F576640A664`.

## Exact CGOS import addresses in `ADPInterface.dll`

| IAT | Import | IAT | Import |
|---:|---|---:|---|
| `0x1000A000` | `CgosBoardClose` | `0x1000A004` | `CgosLibUninitialize` |
| `0x1000A008` | `CgosLibGetLastError` | `0x1000A00C` | `CgosBoardOpen` |
| `0x1000A010` | `CgosLibIsAvailable` | `0x1000A014` | `CgosLibInstall` |
| `0x1000A018` | `CgosLibInitialize` | `0x1000A01C` | `CgosI2CIsAvailable` |
| `0x1000A020` | `CgosI2CCount` | `0x1000A024` | `CgosLibGetVersion` |
| `0x1000A028` | `CgosBoardGetInfoW` | `0x1000A02C` | `CgosI2CWrite` |
| `0x1000A030` | `CgosI2CRead` | `0x1000A034` | `CgosVgaCount` |
| `0x1000A038` | `CgosVgaGetContrast` | `0x1000A03C` | `CgosVgaSetContrast` |
| `0x1000A040` | `CgosVgaGetBacklight` | `0x1000A044` | `CgosVgaSetBacklight` |
| `0x1000A048` | `CgosWDogSetConfig` | `0x1000A04C` | `CgosWDogDisable` |
| `0x1000A050` | `CgosWDogTrigger` | `0x1000A054` | `CgosTemperatureGetCurrent` |
| `0x1000A058` | `CgosTemperatureGetInfo` | `0x1000A05C` | `CgosTemperatureCount` |
