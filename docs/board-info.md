# `CGOSBOARDINFOW` ABI and emulated identity

The layout below comes from the supplied CGOS header and is enforced at build
time by `sizeof(CGOSBOARDINFOW) == 0x130`. Win32 `WCHAR` is two bytes and the
structure uses normal 4-byte x86 alignment.

| Offset | Size | Field | Emulated value |
|---:|---:|---|---|
| `0x000` | 4 | `dwSize` | `0x130` |
| `0x004` | 4 | `dwFlags` | `0` |
| `0x008` | 32 | `szReserved[16]` | empty |
| `0x028` | 32 | `szBoard[16]` | `B945` |
| `0x048` | 32 | `szBoardSub[16]` | empty |
| `0x068` | 32 | `szManufacturer[16]` | `congatec` |
| `0x088` | 16 | `stManufacturingDate` | zeroed/unknown |
| `0x098` | 16 | `stLastRepairDate` | zeroed/unknown |
| `0x0A8` | 32 | `szSerialNumber[16]` | `000000533731` |
| `0x0C8` | 2 | `wProductRevision` | `0`/unknown |
| `0x0CA` | 2 | `wSystemBiosRevision` | `0`/unknown |
| `0x0CC` | 2 | `wBiosInterfaceRevision` | `0`/unknown |
| `0x0CE` | 2 | `wBiosInterfaceBuildRevision` | `0`/unknown |
| `0x0D0` | 4 | `dwClasses` | `1` (`CGOS_BOARD_CLASS_CPU`) |
| `0x0D4` | 4 | `dwPrimaryClass` | `1` (`CGOS_BOARD_CLASS_CPU`) |
| `0x0D8` | 4 | `dwRepairCounter` | `0`/unknown |
| `0x0DC` | 40 | `szPartNumber[20]` | empty/unknown |
| `0x104` | 40 | `szEAN[20]` | empty/unknown |
| `0x12C` | 4 | `dwManufacturer` | `0`/unknown |

`CgosBoardGetInfoW` first reads the caller-provided `dwSize`. In emulation mode
it succeeds only when the pointer is non-null and the requested size is at
least `0x130`; it then zeroes the full structure, restores `dwSize`, and copies
only the three identity strings and two class fields shown above. This avoids
inventing BIOS revisions, dates, part numbers, EANs, or repair history.

Runtime logs confirm calls with `requestedSize=0x130`, the returned identity,
and the application's resulting `Congatec Modul` classification. The serial is
the legitimate identity recovered from this supplied system; the shim contains
no generator or selector for arbitrary identities.

## `CGOSTEMPERATUREINFO` ABI

The supplied CGOS 1.03.025 header defines temperature values in thousandths of
a degree Celsius. The x86 structure is 0x2C bytes; this is also enforced by a
compile-time size assertion.

| Offset | Size | Field | Emulated value |
|---:|---:|---|---|
| `0x00` | 4 | `dwSize` | `0x2C` |
| `0x04` | 4 | `dwType` | CPU `0x00010000` or board `0x00040000` |
| `0x08` | 4 | `dwFlags` | active `0x1` |
| `0x0C` | 4 | `dwAlarm` | `0` |
| `0x10` | 4 | `dwRes` | `1000` |
| `0x14` | 4 | `dwMin` | `0` |
| `0x18` | 4 | `dwMax` | `100000` |
| `0x1C` | 4 | `dwAlarmHi` | `85000` |
| `0x20` | 4 | `dwHystHi` | `5000` |
| `0x24` | 4 | `dwAlarmLo` | `0` |
| `0x28` | 4 | `dwHystLo` | `0` |

`CgosTemperatureCount` exposes exactly two units because the application has
separate board- and CPU-temperature consumers. Current readings are fixed at
40000 and 45000 respectively, with `CGOS_SENSOR_ACTIVE`. These are synthetic,
safe-range compatibility values and are never described as measurements from
the absent physical board.
