# Historische Entwicklungsnotizen (teilweise überholt)

Diese Notizen dokumentieren frühere Analyseschritte und sind keine aktuelle
Installationsanleitung. Für den gegenwärtigen Stand siehe `../README.md`.

Forensic Win32/x86 compatibility work for the user-supplied working copy.
Example path: `C:\M90\Images\m90_work.img`. Historical installation scripts
require explicit `M90_WORK_IMAGE` and `M90_ORIGINAL_IMAGE` environment variables
and refuse the original image. For example, in WSL:

```bash
export M90_WORK_IMAGE=/mnt/c/M90/Images/m90_work.img
export M90_ORIGINAL_IMAGE=/mnt/c/M90/Images/original.img
```

The normal graphical starter selects these paths through its UI.

The active design keeps the checksum-covered original `game.exe` intact and
provides missing legacy interfaces externally:

- `Cgos.dll`: logged B945/congatec board API with original serial
  `000000533731`;
- `FBWFLIB.dll`: persistent file-backed SRAM compatibility;
- app-local `irrKlang.dll`: forwards to the original XP DLL with the documented
  null audio backend; the firmware's Turbobuchen readiness sound is currently
  visible in the event log but inaudible;
- two emulated QXL devices exposed as the visible `lower` and `upper` QEMU
  tabs; the primary game screen belongs to the lower physical cabinet display;
- an optional `-UsbTablet` QEMU launch flag for testing the original PC's USB
  mouse path; the default remains PS/2 while a black-screen regression seen
  in the first USB-tablet run is investigated;
- app-local `d3d9.dll`: logs Direct3D creation, loads SwiftShader as
  `swiftshader_d3d9.dll`, and maps the cabinet's logical adapter 1 to the
  software renderer's adapter 0 without changing `game.exe`; its second device
  is non-exclusive to avoid opening two exclusive SwiftShader devices on the
  same software adapter;
- app-local SwiftShader: temporary Direct3D evaluation dependency only; its
  license does not permit treating it as a redistributable project artifact.
- development-only COM3 bridge: connects QEMU's guest COM3 to host TCP port
  4553 and sends the structurally verified M90 `INITVIDEO` packet. It can also
  validate and replay a non-monetary startup/render range from separately
  supplied, SHA-256-pinned owner VidCom logs. It reconnects after the game's intentional
  monitor-configuration reboot. The selected emulated database time is
  `2012-02-01 22:14`; no acceptor, dispenser, payout, or jackpot device is
  implemented.

Build the XP-targeted components from a PowerShell prompt with Visual Studio
Build Tools installed:

```powershell
.\build.ps1 -Mode phase2
.\build-sram-compat.ps1
.\build-irrklang-proxy.ps1
.\build-d3d9-proxy.ps1
.\test-cgos-smoke.ps1
```

To run the current original-database emulator yourself, double-click
`Start-Emulator.cmd` in this folder. It first validates the Loader,
FactoryReset, 2012 date, M90_Las_Vegas config and Magie_90_CC4 database in
the confirmed programming order, then starts the guarded QEMU VM, the original
 database firmware bridge, a live event-log window, and a cabinet control
 window. Bridge output is written directly to a timestamped file under
 `logs/` and displayed in the event-log window; the console shows the file
 path and launcher errors. Leave the console open while testing; close QEMU
 when finished, then press Ctrl+C if the database bridge is still running.
 The console stays open after exit so any startup error remains readable.
 The original owner database,
loader, and working XP image must remain at the paths configured in
`start-real-database.ps1` and `test-swiftshader.ps1`.

The current visible WHPX test command is encapsulated in:

```powershell
.\test-swiftshader.ps1
```

Run the automatic development bridge in a second terminal:

```powershell
python .\scripts\vidcom_init_bridge.py --auto --log .\logs\vidcom-bridge.log
```

Automatic mode waits 165 seconds for guest boot, then retries INIT at
30-second intervals for at most three attempts until the guest's exact ACK is
observed. It then sends the evidenced read-only SYSTEMINFO and graphics
checksum sequence once, followed by the six evidenced startup status lines at
their historical relative timings. The final two lines report that acceptor
and dispenser are absent; they do not emulate those devices. No operator input
is required. Frames are clocked one byte at a time at the original 9600-baud
8-N-1 rate so QEMU's socket-backed UART does not overrun on large commands.
The bridge is presently a protocol-reconstruction tool on the
Windows host, not the final in-image emulator. The intended owner-supplied
database import and in-image runtime boundary are described in
[`docs/vidcom-emulator-design.md`](docs/vidcom-emulator-design.md).
The forensic gate and currently observed identity of an owner-read CC4 dump
are documented in [`docs/owner-database-import.md`](docs/owner-database-import.md).
The paired 68020 loader's address map, receive framing, transform boundary and
database validation path are documented in
[`docs/database-loader-analysis.md`](docs/database-loader-analysis.md).
Exact debugger locations and the read-only validator for obtaining the
remaining D3/USP value or a post-transform RAM dump are documented in
[`docs/database-runtime-capture.md`](docs/database-runtime-capture.md).
The matching shared DB/GForce protocol headers extracted read-only from the
image are inventoried and cross-checked in
[`docs/headerfilesfordb-analysis.md`](docs/headerfilesfordb-analysis.md).

For a visible integrated run, `start-emulator.ps1` first validates the main
database, loader and three auxiliary modules with their pinned hashes and
structural relationships, then opens QEMU with
the `upper` and `lower` display tabs, and keeps the paced COM3 bridge log in the
calling console. Guest-initiated reboots remain enabled. Add
`-PauseBeforeLargeCommand` to stop immediately before owner-log tick 321046 for
the current command-64 diagnostic.

The visible x86 launcher uses WHPX, one guest vCPU, normal host priority and
no host affinity limit. `test-swiftshader.ps1 -DryRun` emits the full launch
plan as JSON without checking for or starting QEMU; offline tests assert the
two display tabs, restricted network, COM3 server, and absence of
`-no-reboot`.

`start-real-database.ps1 -DryRun` composes that visible VM plan with the pinned
owner database/loader and the guarded m68k bridge. The higher-level
`program-and-start-emulator.ps1 -DryRun` additionally includes the original
Upload-R, FactoryReset, date, config, and database programming sequence. Both commands
return before programming data, opening the image, or creating a process.

For an already initialized diagnostic guest, `--resume-owner-render` sends
the safe discovery, status, and hash-pinned display ranges without another
INIT. The bridge rejects digest mismatches, missing boundary records, and any
command outside its explicit display/audio allowlists. It never replays
`REQUEST_KEY`, touch/button input, or monetary-device commands.

Startup and owner-render commands use protocol flow control: the bridge waits
for the exact `06 03 00 00` acknowledgement of each command before sending the
next. Discovery queries instead wait for the guest's framed `07 <length>
<command> <payload>` reply. A 30-second timeout aborts the session instead of filling the guest UART
queue. A locked bridge-instance file prevents two host senders from attaching
to COM3 concurrently.

The database-side execution path now uses the owner files themselves:
`scripts/m68k_qemu_harness.py` runs the loader on QEMU's m68020 core and models
its UART registers, while `scripts/m68k_database_transform.py` implements the
same stream transform for practical full-file validation. QEMU differential
testing matches for the first 32 transformed bytes. Full execution correctly
identified the original external loader context as `D3=0xD27B7159`. The
independent Python implementation and QEMU/m68020 differential execution both
confirm it, including the unchanged native checksum `0x07AB140F` and valid
vector/entry code. The tested zero value remains rejected.

`scripts/m68k_database_bridge.py` is the real operating-mode path. It starts a
second, headless QEMU with an m68020 CPU, loads a checksum-valid owner runtime
through the debugger without writing a decrypted copy, and maps the original
UART MMIO registers to the XP VM's TCP-backed COM3. It is separate from the
older `vidcom_init_bridge.py` record replay. Run it through
`start-real-database.ps1` with either the verified original controller `-D3`
(now the default) or a validated `-RuntimeDump`; both inputs are rejected
unless the firmware's own
header and additive checksum pass.

This database bridge presently runs on the Windows host, alongside the visible
x86 QEMU process. It is not an XP executable: current Python and the m68k QEMU
process are host dependencies. Moving it inside the image would additionally
require an XP-compatible virtual null-modem or serial driver, because
`game.exe` and an in-guest database process cannot both own COM3. The portable
runtime unit is therefore the image plus the guarded host launcher; the CGOS,
SRAM, audio, and graphics compatibility DLLs installed in the image remain
Win32/x86 and XP-compatible.

The bridge also models the controller UART's TX-ready timing. Operating-mode
COM3 exposes only the loaded database runtime (`PC >= 0x1000`). SerialLoader
status `1B32` and the finite loader `FF` idle sequence are completed internally,
as they precede the Windows game connection on physical hardware. Retaining
those boot bytes in QEMU's socket serial buffer until XP opened COM3
reproducibly caused STOP `0x7F/0x08`. All bytes produced by the database runtime
remain on COM3. See `docs/database-loader-analysis.md` for disassembly and run
evidence.

The runtime hardware model also reproduces the two statically identified
database-board latch replies at `0x800181`. After that probe completes, it
verifies vector 134 (`0xC6BD0`) and supplies the missing controller timer as
68020 format-zero interrupt frames. The bridge separately observes the
UART state word `0x1EBBB0`. If the missing board boot-ROM context has not
initialized it, the bridge executes the owner's unchanged initializer at
`0xC5F82` under a temporary debugger return breakpoint, verifies its `0x58A9`
result, and restores the complete CPU register context before continuing. It
does not write the ready magic directly or fabricate an application INIT
packet. The unchanged UART routines retain their own `0x58A9` state check.

The m68k bridge runs at normal host priority without an artificial 50% pause.
QEMU's instruction counter (`shift=6,align=on,sleep=on`) caps the emulated
database at 15,625,000 instructions per second. The earlier `shift=5` setting
repeatedly triggered an internal QEMU abort; the lower setting reached the
game screen in a live test. This is an instruction-rate limit, not
cycle-accurate MC68331 timing. The default still uses faster translation
blocks; `start-real-database.ps1 -SafeTb` selects the older single-instruction
fallback for diagnostics. The combined `program-and-start-emulator.ps1` launcher
also accepts `-SafeTb` and `-DbIcountShift 5|6` and forwards them to the bridge.
The debugger run slice defaults to 10 ms and values below
5 ms are rejected. The control panel captures the lower cabinet display every
2 seconds to avoid spending startup time on repeated full-screen QMP dumps.
Logging-only watchpoints are off by default; pass
`start-real-database.ps1 -TraceDiagnostics` to restore them for investigation.
The live boot and transition into Buster were observed; longer gameplay and
the virtual coin-credit path still need validation.

The ATmega48 V3 admission-card EEPROM can be prepared independently from the
owner-supplied 256-byte template. For Ergoline M90, the programmer's layout is
the nine-digit ASCII number at offset 40 and packed BCD number plus
`06 32 11 55` at offset 64. The template is never changed:

```powershell
python scripts/admission_card.py --template 'C:\M90\Dateien\eeprom.bin' --number 123456789 --output build\admission-card-m90.eeprom.bin
```

Use `start-real-database.ps1 -AdmissionEeprom
build\admission-card-m90.eeprom.bin -DryRun` to inspect the launch plan without
starting QEMU; omit `-DryRun` only for a live test. The existing AUX reply is
preserved when no EEPROM is selected. With a selected M90 EEPROM, the virtual
card's `0x31`/`0x34` identity and `0x32` indexed read use its contents.
This is not a cycle-accurate AVR/card emulation. A live run reached the game
selection screen without `CODE / Neu`, but the remaining encrypted response
bytes, wire timing and sustained gameplay have not been validated. The start
code is entered at the machine and is **not** written by the
supplied V3 EEPROM programmer. The separate `zlk_v1.exe` is an older AT90S1200
programmer, not an M90 start-code generator; it was inspected but not run.

`scripts/serialloader_chip_emulator.py` validates the documented programming
order as explicit chip states and models the observed `1B31`, `1B32` and
`1B33` status transitions. Its date frame is byte-for-byte compatible with the
statically inspected SerialLoader layout. `program-and-start-emulator.ps1`
checks Upload-R, FactoryReset, the selected historical date (default
2012-02-01), M90_Las_Vegas config and Magie_90_CC4 database before launching
the real COM3 operating mode. This preflight does not yet persist the full
battery-backed SRAM state between runs.
The Las-Vegas module's checksum-valid 68020 entry code was also inspected:
it clears the final 1 KB of 2 MB SRAM, copies 512 config bytes to `0x1FFC00`,
and copies 24 identity bytes to `0x1FFF80`. The operating bridge applies
exactly these RAM writes from the pinned, decoded module before entering the
original database runtime; other module side effects are not inferred.

It deliberately permits guest reboots. The forensic deliverables are:

- [analysis and runtime evidence](docs/analysis.md)
- [static import table](docs/import-table.md)
- [original binary signatures](docs/signatures.md)
- [`CGOSBOARDINFOW` layout and identity](docs/board-info.md)
- [COM3 and owner-supplied database design](docs/vidcom-emulator-design.md)
- [physical database runtime capture](docs/database-runtime-capture.md)
- [original SerialLoader wire analysis](docs/serialloader-analysis.md)

Installation scripts verify the exact image path and SHA-256 values before
replacing files.

The current reproducible D3D9 compatibility proxy is built from
[`src/d3d9_proxy.cpp`](src/d3d9_proxy.cpp) and
[`src/d3d9_proxy.def`](src/d3d9_proxy.def), with SHA-256
`31D2D484D4821EF34DD764E68A66338ED638926C66B73B14078D360713F4987F`.
[`scripts/install_d3d9_proxy.sh`](scripts/install_d3d9_proxy.sh) preserves the
verified SwiftShader 5003 DLL under `swiftshader_d3d9.dll` in both application
directories before installing the proxy.

The current phase-2 CGOS shim SHA-256 is
`16C16AABCE7F775BE87EA12CC0DBC64637428F663ED8CE693E4E02E499B14D51`.
In addition to the verified board identity and VGA/backlight surface, it
provides the two CGOS temperature objects required by the observed application
call sequence. Their values are explicitly synthetic diagnostic telemetry.
The x86 smoke test validates the exact board strings, sensor count and types,
ABI sizes, readings, active status, and rejection of an out-of-range unit; it
passes against the current reproducible build.
