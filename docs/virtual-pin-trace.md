# Original database firmware: virtual port and bus trace

This is a **software activity trace, not an electrical connector capture**.
The hash-validated owner's 2 MiB cold seed was run unchanged by the Musashi
68020-class host probe for 500,000 slices of 1,000 emulated cycles. No XP/QEMU
PC reply or real cabinet hardware was connected. The run transmitted the
original 39-byte `INITVIDEO` frame, passed its 2012 date check, completed one
R4543 read and then continued waiting for external communication.

Reproduce after `esp32-db/build-host-smoke.ps1`:

```powershell
.\esp32-db\build\db-owner-probe.exe .\build\esp32-seed-1d60168c8c5b1f11.bin 500000
```

The optional `db_machine.mmio_trace` callback records each read/write of the
virtual MC68331 peripheral range `0xFFF000..0xFFFFFF` and external board range
`0x800000..0x8001FF`. It is disabled unless a caller installs it. Each
`MMIO_TRACE` line reports address, read/write counts, a byte-to-byte write
delta mask, first/last observed value and the first CPU PC. A write delta is
**not** a physical-pin transition: pin assignment, output enable, latches and
off-board circuitry must be applied before any electrical conclusion.

| Observed address | Read / write count | Defensible conclusion |
| --- | ---: | --- |
| `0xFFFC0F` | 0 / 45,044 | SCI data register written; only 39 bytes were operating-mode TX. The rest includes loader and initializer writes. |
| `0xFFFC0D` | 2,332 / 0 | SCI RX-ready polled; without a connected PC/board no RX payload arrived. |
| `0xFFF906` | 0 / 205 | GPT/GP direction register is rewritten during the run. |
| `0xFFF907` | 1,377 / 1,327 | GP port is actively bit-banged; the model completed one R4543 date read. |
| `0xFFFA15`, `0xFFFA17`, `0xFFFA1D`, `0xFFFA1F` | each 0 / 1 | Port E/F direction and pin functions are configured in the loader; this run does not show later direct reads or writes to their data registers. IRQ inputs can still act without port reads. |
| `0xFFFC15..0xFFFC17` | each 0 / 1 | Port QS data, assignment and direction are initialized; no later direct QS-port access was seen in this no-reply boot interval. |
| `0x800101` | 36,688 / 73,376 | Repeated external-board-window access by the original scanner. |
| `0x80018B` | 11,465 / 4,588 | Repeated board status/timer service. |
| `0x80019B` | 2,799 / 2 | Board input register, including the PC-emulator's door-switch bit. |
| `0x80019D`, `0x80019F` | 1 / 47,305 and 2,293 / 45,018 | Repeated board output/set/clear strobes. |

The KiCad **1 MB** netlist establishes that J8/J9 have 16 `DBIO` contacts,
11 named address contacts, a twelfth address contact at J8:a2 (A21 via
U11/RN12), and further serial, port and control signals. J8:c3 is FC2 via
U11/RN12, J8:b4 leads to PF4 via RN2, and J8:b5 terminates at an unconnected
resistor pad in this schematic. For
example, `DBIO0` is J9:a9 to U7 pin 19 and U8 pin 11; U8's other side reaches
the Motorola's D0 net. U7 is a 74LS573 latch and U8 a 74LS245 bidirectional
bus transceiver, not voltage translators. The same schematic leaves U7's
load pin and U5's direction pin unconnected, so it cannot be treated as a
complete timing/drive specification for a replacement board. It is also not
the missing **2 MB** database/backplane schematic.

The trace proves which *virtual registers* the firmware uses in this boot
interval. It does not prove that every connected J8/J9 line is needed by a
replacement. In particular, the `0x800xxx` model stands in for missing
hardware and cannot distinguish local database-board decoding from a
transaction over the real connector. Conversely, absence of a direct PF/PQS
data-register access does not rule out an IRQ, alternate function, later game
phase or external controller signal. An isolated logic capture or the actual
2 MB/controller schematic is required to turn this into a definitive pinout.
