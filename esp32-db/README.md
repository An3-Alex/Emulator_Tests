# ESP32-S3 N16R8 database probe

This is an **offline/bench prototype**, not a working replacement for the
physical 1 MB or 2 MB database board. It does not drive cabinet connector
pins, coin devices, protection inputs, or a real-money machine.

The user-supplied Motorola loader and database are not included. The host
tool `scripts/build_esp32_seed.py` validates their SHA-256 hashes and native
runtime checksum, then produces a 4096-byte header plus exactly 2 MiB of
cold-boot SRAM. It reproduces the loader, runtime and statically proven
M90_Las_Vegas writes used by the existing Windows bridge. This is **not** a
post-initialization live SRAM snapshot.

The firmware loads a CRC-checked image from `db_seed`, `db_a`, or `db_b` into
8 MB PSRAM, chooses the highest valid generation and starts the same 68020-
class execution path used in the PC prototype. The large Musashi opcode tables
are placed in PSRAM BSS; they do not fit safely in internal RAM. The original
UART initializer is called under a saved CPU context at the runtime I/O
initialization boundary. Afterward, a bounded 10 ms board-timer model invokes
the original vector-64 board handler and then the original vector-134 UART
handler. The Epson R4543 serial port returns the same fixed 2012 calendar seed
as the PC bridge; its clock advances with emulated cycles, but it is not yet
backed by persistent time storage. Unknown hardware addresses
stop the bounded CPU probe and are reported; they are not silently declared
compatible. The current prototype does **not** save changing SRAM state to
flash. Arbitrary power-loss persistence therefore remains unsolved.

## Reversible PC emulator mode

`start-real-database.ps1` remains the normal software-only database mode.
It needs no ESP32 and is not changed by the experiment. To try the ESP32
instead, select **QEMU-USB-Brücke aktivieren** in the flasher GUI (leave the
GPIO UART test off), prepare the firmware and flash it with the validated seed
to the loose board. The ESP32-S3's native USB Serial/JTAG COM port then carries
raw database bytes; UART0 on its other USB connector carries diagnostics.
Do not run the flasher's text log monitor on the raw database port.

From the project root, use the native USB COM port reported by Windows:

```powershell
.\start-esp32-database.ps1 -Port COM7
```

The separate launcher connects guest COM3 to the ESP32 through a USB relay
and records both directions under `logs/`. It does not start the existing
software database bridge. To switch back, end this run and invoke
`start-real-database.ps1` normally. Neither the QEMU image nor the original
database files are converted or overwritten. `-DryRun` prints the ESP32
startup plan without launching QEMU or opening USB.

This is an experimental transport, not proof that all backplane hardware is
emulated or game startup completes. Unknown hardware accesses still halt the
ESP CPU safely. This USB mode runs beyond the normal 400-million-cycle probe
limit while pacing the emulated CPU to at most 16 million cycles per second.
The x86 QEMU CPU speed is unaffected.

## Offline checks

From the project root:

```powershell
python -m unittest tests.test_build_esp32_seed -v
python -m unittest tests.test_esp32_musashi_tables -v
.\esp32-db\build-host-smoke.ps1
```

`build-host-smoke.ps1` generates the Musashi opcode tables and executes a
synthetic Motorola instruction sequence. If the owner-specific seed was
created, `build\db-owner-probe.exe <seed-path>` can additionally execute a
bounded diagnostic run and report the first unsupported register access.
The optional second argument raises the cap to at most 2,000,000 chunks of
1,000 emulated cycles. It also reports UART-state transitions. A host test
with the owner seed now reaches `58A9` through the original initializer, sees
the owner's subsequent clear at `0x186B8`, and later reaches `58A9` again
through the original runtime. With the board timer and R4543 model, the owner
firmware itself transmits a 39-byte `INITVIDEO` frame containing
`2012-02-01 22:14`; the host probe observed one complete R4543 read. The
separate experimental USB mode now provides a PC reply transport, but no
end-to-end board or game-start proof. The normal on-device probe is capped at
400 million emulated cycles and yields periodically.
Musashi is pinned under
`third_party/Musashi` at commit `313ebf1bd9f4d0d93341eb5ce21fd8a119e9dbdd`
and keeps its upstream permissive license in `readme.txt`.

## ESP-IDF build

```powershell
cd esp32-db
.\Invoke-ESP32-IDF.ps1 -Action install
.\Invoke-ESP32-IDF.ps1 -Action check
```

The GUI can run this setup and the build itself. It checks out official
ESP-IDF v5.5.1 into `.tools/idf/v5.5.1/esp-idf` and installs its ESP32-S3
tools into `.tools/esp-idf-tools`; the first run needs Git, Python 3.13 and
an internet connection. The generated GUI config selects 16 MB flash and
octal PSRAM. The owner-specific seed is built separately and flashed only
to the `db_seed` partition at offset `0x310000`. Never overwrite an existing
`db_a`/`db_b` image without an explicit backup.

## Windows USB flasher

Double-click `Start-ESP32-Flasher.cmd` to open the GUI. The launcher creates
an isolated Python 3.13 environment and installs `esptool` and `pyserial` if
needed. Pick the folder containing the four owner's originals; the GUI
checks their pinned SHA-256 hashes and generates a content-addressed cold
seed. `FactoryReset` is checked but **not executed**; this is not an
initialized database snapshot. Choose `Toolchain einrichten` once and then
`Abbild + Firmware vorbereiten`. The GUI checks the seed CRC, compiled
partition table, 16 MB flash setting and actual UART pin configuration.
It detects Windows COM ports, probes the connected ESP32-S3 and flash size,
then flashes only the selected images after explicit confirmation.
"Firmware + Datenbank" writes the bootloader, partition table, application
and `db_seed`; "Nur Datenbank" writes only `db_seed`. Neither action erases
the whole chip or writes `db_a`/`db_b`.

The GUI firmware build lives in `esp32-db/build-idf` and is separate from host
diagnostic binaries. On this machine, ESP-IDF 5.5.1 compiled both the
UART-disabled and GPIO17/18 UART-test variants successfully. Both USB-bridge
and GPIO-UART checkboxes start **off**; selecting either mode and pressing
Prepare builds it. The GUI rechecks the
actual Kconfig and file hashes before a full flash. USB flashing still
requires the actual board and its identified COM port. Use a standalone
board on USB only; do not connect this probe firmware to the original
controller.

Remaining work before connection to a real controller: capture the 2 MB
board/backplane pinout and logic levels; model the MC68331 SIM/QSM/timers,
board latch, SCC channels, RTC and all normal-operation I/O; benchmark CPU
timing on N16R8; design power-fail-safe writeback; verify cold and power-loss
starts on an isolated test bench.

## USB live log and CPU rate

The Windows flasher has a **Live-Log starten** button for the normal probe
or the separate UART0 diagnostics port. Do not use it on the USB-bridge data
port. Select the appropriate ESP32 COM port; the read-only monitor displays firmware
events at 115200 baud and can save the visible text with **Log speichern**.
Stop the monitor before building or flashing because the COM port is held
open. `pyserial` is required (`python -m pip install pyserial`). A reset or
replug may be necessary to see the earliest boot lines. The log reports
image selection, CPU/UART state, database TX bytes, RTC reads, board-timer
ticks, progress and the first unsupported register access. Controller RX
traffic appears only if the optional physical UART mode is enabled and wired.

The firmware paces the *emulated* CPU to a maximum average of 16 million
Musashi cycles per wall-clock second. Both the main execution slices and the
saved-context UART initializer count toward that budget. It reports the
measured average in `DB_PROGRESS`. This is a rate ceiling, **not** a
cycle-accurate MC68331 timing model and not a guarantee that the ESP32-S3 can
actually sustain 16 MHz of emulated work. The ESP32-S3's own clock is not
reconfigured or capped by this setting. The optional PC host probe has no
wall-clock pacing.

See [HARDWARE_PINS.md](HARDWARE_PINS.md) for the 1 MB connector net map,
the owner's report of matching 2 MB contact positions, and unresolved
electrical behavior.
The reproducible original-firmware virtual-port trace is in
[virtual-pin-trace.md](../docs/virtual-pin-trace.md); it identifies active
registers without claiming that QEMU reproduces physical J8/J9 voltages.

## Optional physical UART bench test

The supplied dual-USB-C N16R8 pinout picture shows GPIO17 and GPIO18 on
the header. The new `M90 database bench probe` options in `idf.py menuconfig`
can enable UART1 at **9600 baud, 8N1**, with proposed ESP GPIO17 as TX and
GPIO18 as RX. The mode is **off by default**; a normal flash does not transmit
on those pins. The USB log is a separate 115200-baud interface. The firmware
logs `DB_UART_WIRE enabled` and every received byte when the mode is active.

Select the pins using the *actual* board silkscreen and verify the target
line levels with a meter/scope first. The owner's reported 3.3 V TX reading
is useful, but it does not prove every transient or the controller RX input's
requirements. The 1 MB schematic identifies database `cTXD` as J8:b3 and
`cRXD` as J8:c4, and the owner reports that these contacts match the 2 MB
connector. The provisional three-wire mapping is in
[HARDWARE_PINS.md](HARDWARE_PINS.md). Power the ESP32 only from its own USB
connection. Do not feed J8/J9 `VDD` or `c12V` to the ESP.
No other cabinet lines should be connected for this initial test.

The UART mode merely passes the emulated database's TX bytes to UART1 and
offers controller RX bytes to its one-byte emulated receive register. It
does **not** establish compatibility with the whole backplane, implement
the controller's other signals, persist changed RAM, or guarantee progress
past the first exchange. The ESP-IDF build is proven offline. Electrical
compatibility and runtime behavior on a real board are still untested.
