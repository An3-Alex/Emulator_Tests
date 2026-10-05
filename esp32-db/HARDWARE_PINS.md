# Preliminary database connector map (1 MB board)

## Physical connector

Both J8 and J9 are specified in the owner's KiCad schematic **and PCB** as
`Connector_DIN:DIN41612_R3_3x10_Male_Vertical_THT`: DIN 41612 / IEC 60603-2
type R/3, male, three rows `a/b/c` by ten positions, 30 contacts each. For a
bench mating adapter, look for the complementary **type 3R female, 30-contact**
connector. One specific manufacturer example is HARTING
[DIN-Signal 3R030FS-3,0C1-2, article 09 29 230 6801](https://www.harting.com/en-US/p/DIN-Signal-3R030FS-30C1-2-09292306801),
an angled through-hole female with rows `a/b/c`, positions `1..10`. A full
two-connector adapter needs two matching female connectors with the correct
spacing and orientation; the three-wire UART diagnostic needs access only
to J8. Check physical keying, mounting and contact numbering against the
actual board before purchasing or plugging anything in. A KiCad footprint
is not proof of the exact manufacturer's installed part.

Source: the owner's `DB1MB.kicad_sch` netlist, exported on 2026-09-27. These
are **schematic net names**, not measured voltage levels or proven signal
directions. The owner reports that the connector contact positions match the
**2 MB** database too; this has not been independently continuity-tested.
The owner's controller schematic is now available. See
[CONTROLLER_CONNECTOR.md](CONTROLLER_CONNECTOR.md) for the matched J4/J5
controller contacts, their mirrored J8/J9 database contacts, and the
remaining 2 MB board uncertainties.
Do not wire the whole table directly to an ESP32-S3 or to a live cabinet.

| Row | J8 a | J8 b | J8 c | J9 a | J9 b | J9 c |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | GND | cPQS5 | cPQS6 | GND | cPQS3 | cPQS4 |
| 2 | A21 via U11/RN12 (inferred) | cA22 | cA23 | cA6 | cA7 | cA8 |
| 3 | cPF5 | cTXD | FC2 via U11/RN12 (inferred) | cA2 | cA3 | cA5 |
| 4 | NC | PF4 via RN2 (inferred) | cRXD | NC | cA0 | cA1 |
| 5 | cPF1 | dangling via RN2 in this schematic | NC | cCLKOUT | NC | c12V |
| 6 | cPQS2 | cPQS0 | cPQS1 | cRW | cPE1 | cRESET |
| 7 | GND | DBIO14 | DBIO15 | GND | DBIO6 | DBIO7 |
| 8 | DBIO11 | DBIO12 | DBIO13 | DBIO3 | DBIO4 | DBIO5 |
| 9 | DBIO8 | DBIO9 | DBIO10 | DBIO0 | DBIO1 | DBIO2 |
| 10 | VDD | NC | cA20 | VDD | cPF2 | cPF3 |

Four contacts have automatic, uninformative KiCad net names, but their
on-board destinations can be traced: J8:a2 goes through RN12 to U11:8,
whose two 74LS08 inputs are both `A21`; J8:c3 goes through RN12 to U11:11,
whose two inputs are both `FC2`. These are buffered A21 and FC2 outputs,
respectively. J8:b4 goes through RN2 to MC68331 `PF4` and a VDD pull-up
through RN8; the loader configures PF4 as an input. J8:b5 goes only through
RN2 to an explicitly unconnected resistor pad in this schematic. These are
**1 MB netlist deductions**, not measured 2 MB signal levels or proof that
J8:b5 is unused on the owner's 2 MB board. `NC` means
unconnected in this particular schematic, not guaranteed unused in the
2 MB board even if the contact positions match. The 60 contacts comprise 44
named functional signals, three further functionally traced contacts, one
schematic dangling contact, one `c12V` line, six GND/VDD contacts and five
schematic NCs.

## What is beyond TX/RX

The schematic exposes a 16-line `DBIO0..15` data bus; eleven named `cA`
address lines plus the inferred A21 contact; `cRW`, `cRESET`, `cCLKOUT` and
inferred FC2; seven `cPQS` lines; four named `cPF` lines plus inferred PF4;
`cPE1`; and `cTXD`/`cRXD`. The address and data connections pass
through 74LS573 latches and 74LS245 transceivers on the database board.
Thus the connector is **not a UART-only interface**. The controller schematic
now identifies the connected bus devices and deliberate straps; it does not
establish the real 2 MB board's continuity or live bus timing and direction.
Assigning all 47 traced signals to separate ESP32 GPIOs would exceed the
available pins before USB, debug and flash/PSRAM reservations, and
would not reproduce the timing or bidirectional bus behavior. A level-shifted
external bus interface (likely CPLD/FPGA plus transceivers) needs design and
bench validation before a pin map can be committed.

`c12V` is J9:c5. It reaches the MC68331's PF6 input through board circuitry;
it is **not an ESP32 power input**. The matching controller contacts for the
database `VDD` are labelled `+5V` in the new controller schematic; verify
this on the actual hardware before designing any adapter. The SRAM has a separate `VCC`
rail on the board, rather than a direct J8/J9 contact. The ESP32-S3 is a
3.3 V device and must not receive original-board 5 V or 12 V signals on a
GPIO. Verify VDD, c12V, idle levels, polarity, transients and ground with
an isolated instrument before designing level translation.

The ATmega48V (U2) on this 1 MB schematic connects not just to protection
inputs but also to MC68331 `DSCLK`, `DSI`, `DSO`, `FREEZE` and RTC-related
lines. Its omission in the PC software prototype does **not** prove it can
be omitted in a physical drop-in board. The RTC4543 `DATA` pad appears
unconnected in the exported netlist, another reason to validate the physical
board rather than assume this drawing is complete.

## First-stage connector map: DBIO and UART

This is the exact **1 MB connector-contact map** for the proposed first
stage. `DBIO` is the shared 16-bit data bus, not 16 independent peripheral
GPIO signals. `D0..D15` below are logical channels for a future bidirectional,
voltage-protected bus front end; they are **not ESP32 GPIO numbers or a
direct-wiring instruction**. The reported matching contact positions on the
2 MB board still need continuity verification.

| Logical channel | Connector contact | 1 MB schematic net |
| --- | --- | --- |
| D0 | J9:a9 | DBIO0 |
| D1 | J9:b9 | DBIO1 |
| D2 | J9:c9 | DBIO2 |
| D3 | J9:a8 | DBIO3 |
| D4 | J9:b8 | DBIO4 |
| D5 | J9:c8 | DBIO5 |
| D6 | J9:b7 | DBIO6 |
| D7 | J9:c7 | DBIO7 |
| D8 | J8:a9 | DBIO8 |
| D9 | J8:b9 | DBIO9 |
| D10 | J8:c9 | DBIO10 |
| D11 | J8:a8 | DBIO11 |
| D12 | J8:b8 | DBIO12 |
| D13 | J8:c8 | DBIO13 |
| D14 | J8:b7 | DBIO14 |
| D15 | J8:c7 | DBIO15 |
| DB TX / provisional ESP GPIO17 | J8:b3 | cTXD, database to controller |
| DB RX / provisional ESP GPIO18 | J8:c4 | cRXD, controller to database |

UART1 on GPIO17/18 is already implemented as an optional, disabled-by-default
test mode. The DBIO bank is **not implemented** on the ESP32. A safe first
DBIO test would only observe the lines through a properly rated, high-impedance
level interface with all replacement-board drivers disabled. To drive or
respond on that bus later, the direction, output-enable, address, read/write
and handshake timing must be established; connecting only DBIO and UART does
not reproduce memory-mapped controller devices or guarantee a completed boot.

## Firmware-informed allocation proposal (not a wiring diagram)

The original Motorola SRAM and the ATmega's *internal-only* connections do
not need one ESP32 GPIO each. The owner runtime reads and writes board
registers at `0x800181..0x80019F`, and the PC emulator needs the board latch,
SCC and 10 ms board interrupt to advance. These register accesses can be
handled *inside* the ESP32 emulator; they do not, by themselves, prove that
the corresponding J8/J9 address/data lines must be reproduced externally.
Three formerly unnamed contacts now have traceable 1 MB functions (buffered
A21, buffered FC2 and PF4). Database J8:b5 remains dangling *on the 1 MB
board*, but the mating controller J4:b6 carries the door contact. Its
actual 2 MB-board use must therefore be checked, not assumed absent.

There are 44 named signal contacts across J8/J9, three further contacts whose
function can be inferred through on-board circuitry, one dangling contact,
plus `c12V`, power and ground. This is a *connectivity count from the 1 MB
schematic*, not a count of proven-required ESP32 GPIOs. Five of the 60
contacts are explicitly NC. If all 47 traced signals turn out to matter physically,
the N16R8 cannot give each a dedicated safe GPIO while retaining USB and
octal flash/PSRAM. In that case a bidirectional, level-translated/protected
programmable bus front end (`U_BUS` below) is needed. `U_BUS` is a
**conditional design proposal, not a selected or built part**; its channel
numbers are logical identifiers only. A slow I2C GPIO expander would not
substitute for timing-critical bus/interrupt handling.

| ESP32-S3 GPIO | Proposed role | Original contact |
| --- | --- | --- |
| 17 | UART1 TX, optional isolated bench test | J8:b3 `cTXD` |
| 18 | UART1 RX, optional isolated bench test | J8:c4 `cRXD` |
| 9 | `U_BUS` clock | No direct J8/J9 contact |
| 10 | `U_BUS` command/data out | No direct J8/J9 contact |
| 11 | `U_BUS` status/data in | No direct J8/J9 contact |
| 12 | `U_BUS` select | No direct J8/J9 contact |
| 13 | `U_BUS` buffered event/interrupt in | No direct J8/J9 contact |
| 14 | `U_BUS` reset/control out | No direct J8/J9 contact |

Only GPIO17/18 are present in the current firmware, and even those are
disabled by default. GPIO9..14 are **reserved candidates**, not implemented
firmware outputs and not safe to connect yet. GPIO19/20 stay available for
native USB; GPIO43/44 are left to the programming/console path; boot-strapping
GPIO0/3/45/46 and flash/PSRAM GPIO26..37 are deliberately not allocated.
The exact devkit revision and `U_BUS` design must be checked before turning
these reservations into a schematic.

Conditional logical `U_BUS` channel map, viewed from the replacement database
side. The table preserves every physically connected signal for investigation;
it does **not** assert that every channel needs implementation:

| Channel | Original contact(s) | Required treatment |
| --- | --- | --- |
| `DATA[0:15]` | J9:a9/b9/c9, J9:a8/b8/c8, J9:b7/c7, J8:a9/b9/c9, J8:a8/b8/c8, J8:b7/c7 = `DBIO0..15` in order | 16 bidirectional, tri-state-capable bus lines; timing and direction unknown |
| `ADDR` | J9:b4/c4/a3/b3/c3/a2/b2/c2, J8:c10/a2/b2/c2 = `cA0,cA1,cA2,cA3,cA5,cA6,cA7,cA8,cA20`, inferred `A21`, `cA22,cA23` | Preserve named bit identity rather than assuming a contiguous address range |
| `CTRL[0]` | J9:a6 `cRW` | Read/write phase, timing to be measured |
| `CTRL[1]` | J9:b6 `cPE1` | Motorola PE1 is configured as DSACK1, a bus handshake input |
| `CTRL[2]` | J9:c6 `cRESET` | Reset direction and open-drain behavior to be established |
| `CTRL[3]` | J9:a5 `cCLKOUT` | Clock output; cannot be approximated by slow software GPIO toggles |
| `CTRL[4]` | J8:c3, inferred buffered `FC2` | Function-code output through U11/RN12; verify on the physical board |
| `IRQ/PORT` | J8:a3 `cPF5`, J8:a5 `cPF1`, J8:b4 inferred `PF4`, J9:b10 `cPF2`, J9:c10 `cPF3` | PF5 is configured as IRQ5; PF1/2/3/4 as inputs in the observed loader setup |
| `QS[0:6]` | J8:b6/c6/a6, J9:b1/c1, J8:b1/c1 = `cPQS0..6` | Loader makes PQS1 an output and the rest inputs; later indirect use remains unresolved |
| `SENSE12` | J9:c5 `c12V` | **Voltage sense only through a rated isolating/level-conditioning circuit**; this feeds PF6 circuitry on the 1 MB board, never an ESP GPIO directly |
| `DOOR?` | J8:b5, mating controller J4:b6 `Tuerkontakt` | Connects only to an unconnected RN2 pad in the 1 MB schematic; check 2 MB continuity before omitting it |

No assignment to an ESP32 pin is claimed for the `U_BUS` channels individually:
that would be electrically and numerically impossible *if all are needed* on
this N16R8 devkit.
The firmware-derived functions are: SCI TX/RX enabled (`SCCR1=0x002C`),
PF5/PF6 selected as IRQ5/IRQ6 (`PFPAR=0x60`), PE1 as DSACK1
(`PEPAR=0xFF`), PQS1 output with other PQS0..6 inputs
(`PQSPAR:DDRQS=0x00:0x82`), and QSPI initially disabled
(`SPCR1=0x0404`). GP4..GP7 are used for the on-board serial RTC routine;
they are **not** extra J8/J9 contacts. The software configuration alone does
not establish the complete electrical protocol or prove which optional
signals the real 2 MB controller uses.

The existing PC emulator provides more than a cold disassembly: it observed
the unchanged database runtime write `0x13, 0x07` to `0x800181` and demand
`0x13` on readback; models the SCC at `0x800183/0x800187`; treats bit 4 of
`0x80019B` as the active-low cabinet-door input; and needs the original
vector-64 board timer (10 ms service path) as well as vector-134 UART service.
It also models the R4543 transactions at `0xFFF907`. These results establish
logical peripheral requirements, **not** a need to route each Motorola bus
pin out of the ESP32. QEMU `machine=none` stores the `0x800xxx` window in
synthetic RAM; it cannot reveal whether a given transaction uses J8/J9 at
all, nor its voltage, bus direction or propagation time. That remaining
physical mapping can now be narrowed with the controller schematic in
[CONTROLLER_CONNECTOR.md](CONTROLLER_CONNECTOR.md), but still needs 2 MB
board continuity and an isolated logic capture. A loader-only QEMU test reached the original receive
state at PC `0x072C` after 231 instructions; this validates control flow,
not connector wiring.

## Before choosing ESP32 pins

1. Check the actual 2 MB board's connector continuity against the now-known
   controller J4/J5 pinout on an unpowered/disconnected board.
2. Capture logic levels and timing on an isolated bench for both boot and
   normal operation, including reset, clock, bus direction and all port lines.
3. Decide whether the ESP32-S3 can service the measured protocol behind an
   external bus front end, and identify any functions needing a separate
   programmable-logic device.
4. Only then assign available N16R8 module GPIOs, accounting for its exact
   module/board schematic and pins reserved by flash, PSRAM, USB and boot.

The supplied picture of the dual-USB-C ESP32-S3-WROOM-1 board shows GPIO17
and GPIO18 exposed. The optional **UART-only** firmware test proposes
ESP32 GPIO17=TX and GPIO18=RX, both configurable and disabled by default.
Those are ESP pin numbers, **not** J8 pin numbers. GPIO19/20 are the native
USB pins in the picture and are not used by the database UART. Confirm the
silkscreen on the physical board; the N16R8 memory designation alone does
not identify every devkit revision or header orientation.

The picture also labels GPIO43 `U0TXD` and GPIO44 `U0RXD`. These are UART0's
default pins, commonly shared with the board's USB-to-serial programming/
console path. Using them for the database could mix boot/console output with
protocol traffic or contend with on-board circuitry, so the test firmware
rejects them. UART1 can be routed to GPIO17/18 instead; the USB log remains
independent. This pin choice still needs confirmation against the actual
board silkscreen and schematic.

For the UART-only bench connection, with the original database removed and
the ESP32 powered separately over USB, the owner's matching-contact report
gives this provisional wiring:

| ESP32-S3 test pin | Database connector contact | Direction |
| --- | --- | --- |
| GPIO17 / UART1 TX | J8:b3 / `cTXD` | ESP32 to controller |
| GPIO18 / UART1 RX | J8:c4 / `cRXD` | Controller to ESP32 |
| GND | J8:a1 or J8:a7 / GND | Common signal reference |

Do not attach connector `VDD` or `c12V` to the USB-powered ESP32. The reported
3.3 V reading must apply to the *controller-to-ESP32 RX* line under this
replacement wiring; a 3.3 V reading of the original database's TX alone
does not prove that. Confirm the actual idle/high and transient levels on
J8:c4 before connecting GPIO18, and confirm J8:b3 is not being actively
driven by the controller. There is no need to connect the data/address bus
for this UART-only diagnostic, but its absence may prevent boot progress.
