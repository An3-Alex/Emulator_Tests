# CGOS compatibility analysis

Status labels used throughout this report:

- **CONFIRMED** — directly established from the supplied binaries, headers, image, or runtime log.
- **LIKELY** — supported by evidence but not yet established end-to-end.
- **UNKNOWN** — not yet resolved.

## Scope and safety boundary

This project emulates only the missing legacy congatec CGOS board API needed for
diagnosis and interoperability. It does not remove or bypass licensing, PTB,
`REQUEST_KEY`, authenticity, encryption, game, or CF-card checks. It performs no
physical I/O, installs no kernel driver, activates no watchdog, and makes no
network request.

## Evidence log

The detailed binary and runtime findings are added as each stage is completed.

## Runtime compatibility findings

- **CONFIRMED:** SwiftShader build 5003 completes Direct3D initialization in
  the game's existing single-device/two-TFT mode; the logo is rendered and the
  game continues to its graphics checksum.
- **CONFIRMED:** the emulated board reports B945, manufacturer `congatec`, and
  serial `000000533731`; the application logs `Congatec Modul`.
- **CONFIRMED:** `CgosVgaSetBacklight` is called for unit 0 with setting 70.
  The shim now models one VGA unit, accepts CGOS settings 0 through 100, and
  stores backlight and contrast values for matching get calls.
- **CONFIRMED:** the following blocker was `OpenSRAM 32`. Win32 error 32 is a
  sharing violation. The prior proxy opened `m90_sram.bin` with read sharing
  only, preventing another process from opening it for write. Proxy build hash
  `CEBB001FCC05BEEAA78EFEC80DE3600658399B79F54084269D48F44934302149`
  adds `FILE_SHARE_WRITE`; the persistent SRAM format and IOCTL validation are
  unchanged. Runtime verification of the new proxy is pending.
- **CONFIRMED:** after SRAM access succeeded, the next fatal log was
  `Prozessor-String nicht auswertbar` at graficengine.cpp line 6104. Static
  inspection of the parser at 0x006DE5C2 shows accepted substrings `440`,
  `575`, and `8400`, mapping the last to `P8400`. The QEMU launch therefore
  initially reported `Intel(R) Core(TM)2 Duo CPU P8400 @ 2.26GHz`. The loader
  accepted the CPU syntax but displayed `Operating system doesn't fit to
  COMEXPRESS modul`. Since the image identifies itself as
  `M440_945_KOMBI_V20`, the active launch now reports the parser's matching
  `Intel(R) Celeron(R) M CPU 440 @ 1.86GHz`, retaining qemu32 plus SSE2.
  Runtime verification is pending.
- **CONFIRMED:** the M440 run reached the application's complete data-integrity
  comparison. `CheckTemp.txt` and `CheckSumAdpConfigTool.txt` differed in only
  two lines: the patched `game.exe` value (`388387867` versus expected
  `388383763`) and the resulting total (`2266307954` versus `2266299746`).
  Graphics, GOC, and sound entries matched. The log reported error code 28.
  The verified original executable (SHA-256
  `27C4553927397B1E8443D6CAEA12E5B4E7282E4948B67B85C80C4DB0D1D0427D`)
  has therefore been restored in both `NVRAM` and `WorkDir`; the expected
  checksum file was not changed.
- **CONFIRMED:** with the original executable, SRAM opens successfully through
  the app-local `FBWFLIB.dll`, but the next logged stop is
  `AudioDxSoundEngine.cpp, 28, SoundEngine nicht erstellt`. Adding QEMU HDA
  hardware did not change that result because the guest lacks an active driver.
  An x86/XP app-local `irrKlang.dll` proxy now forwards to the original system
  DLL while selecting irrKlang's documented `ESOD_NULL` output. Proxy SHA-256:
  `5BB36E7DAE437AFB3E68895237D18B65D50F5FAD2DBE4A2C905B8FC6006AF361`.
  Its exact MSVC x86 decorated export was verified with `dumpbin /exports`.
- **CONFIRMED (2026-09-27, prior runtime log):** the database sent
  `01 02 42 00 ED 00 04` twice in `database-events-20260925-141609.log`.
  VidCom command `0x42` is `PLAYSOUND`, and little-endian sound ID `0x00ED`
  is `SMP_TurbobuchenDry` (237) in the owner's `headerSoundIds.h`. The
  owner's observation that controls are inactive until this announcement
  means earlier key/touch tests cannot establish an input fault. Later key
  and touch injections are present after the first announcement; their guest
  effect remains unverified. The app-local irrKlang proxy still forces
  `ESOD_NULL`, so the command cannot currently be heard. The live event
  viewer now marks the command as a *requested* readiness tone, not proof of
  audio playback or of complete machine readiness.
- **CONFIRMED:** after the forced XP filesystem repair, the original game no
  longer reported corrupt `LogFiles/logDatei.txt`. The current run created the
  null sound engine, opened persistent SRAM, recognized both logical 800x600
  TFTs, and stopped at the second Direct3D initialization path. The renderer
  hash in both application directories remained the verified SwiftShader 5003
  hash `FC5994B209A57A77275E5ECEE1904CD9139A344C69E221E54F05AF90580A90C9`.
- **CONFIRMED:** the two-tab D3D9 proxy made both device creations return
  `S_OK`; the unmodified game then passed its expected checksum value
  `388383763`, logged `Congatec Modul`, opened SRAM, initialized audio, and
  progressed into later machine initialization. The next stop was
  `graficengineDx9.cpp, 786, Err: API-Fkte` immediately after
  `CgosTemperatureCount` returned zero. The shim now exposes exactly two
  active CGOS 1.03.025 temperature objects (CPU and board), using the verified
  0x2C-byte `CGOSTEMPERATUREINFO` ABI. Fixed diagnostic readings are 45 C CPU
  and 40 C board; these are synthetic compatibility telemetry, not claims
  about physical hardware. Reproducible shim SHA-256:
  `16C16AABCE7F775BE87EA12CC0DBC64637428F663ED8CE693E4E02E499B14D51`.
- **CONFIRMED:** the former two-byte executable patch at VA `0x006E0D72`
  forced an existing one-adapter/two-TFT branch. Full static inspection of
  function `0x006D46B0` shows that it calls `EnumDisplayDevicesW` twice: it
  enumerates display devices, then counts their child monitors whose
  `StateFlags` include bit 0. Merely exposing one QXL PCI function was therefore
  insufficient; its default ROM still exposed multiple monitor outputs and the
  original game reached the logical adapter-1 Direct3D path. The active QEMU
  `qxl-vga,max_outputs=1` experiment still requested two D3D devices and was
  rejected as a final layout because it removed the required second QEMU tab.
  An
  external XP/x86 `IDirect3D9` proxy now maps adapter 1 to SwiftShader adapter
  0 and records creation calls, leaving the checksum-covered executable
  unchanged. The first proxy proved the second exclusive fullscreen creation
  hangs inside SwiftShader. The current two-tab proxy retains the first
  exclusive device but makes the mapped second device non-exclusive. Current
  reproducible proxy SHA-256:
  `31D2D484D4821EF34DD764E68A66338ED638926C66B73B14078D360713F4987F`.
  Runtime verification is in progress.
- **CONFIRMED (2026-09-27, read-only image log):** the last guest
  `NVRAM/d3d9_proxy.log` records both 800x600 Direct3D devices created with
  `S_OK`, but the adapter-1 device is remapped to SwiftShader adapter 0 and
  forced windowed. The owner's service-menu screenshot shows two side-by-side
  service views on `upper`, wrong colors, and a blank `lower` tab. The shared
  renderer mapping is a strong candidate for the missing independent second
  output; color corruption requires separate investigation. This has not
  been fixed or runtime-verified. The optional QEMU `-UsbTablet` profile
  exposes a USB HID tablet, but the 2026-09-27 run with it went black after
  a virtual Auszahlung pulse caused sound ID 237 to be sent. The PC did not
  ACK that sound command and the DB then repeated `GETPREVIOUSCOMMAND` (0x4B).
  The USB tablet is the only intentional x86 launch difference from the
  earlier run that ACKed the sound command, but causality is not established;
  USB therefore remains opt-in while the regression is isolated.
- **CONFIRMED:** `WorkDir\HeaderFilesForDB\commandointerpreter.h` in the
  supplied image is byte-identical to the retained forensic copy (SHA-256
  `DBBD9FC6F2ED00C6BBBDEB50111763999A6F466339322181023D2672E0202553`).
  It defines `SOH=0x01`, `STX=0x02`, `EOT=0x04`, `ACK=0x06`, command 34 as
  `VID_COM_INITVIDEO`, and the packed `PARA_INITVIDEO` layout.
- **CONFIRMED:** static inspection of `KomProtokol` and a live COM3 capture
  establish the fixed-command framing as
  `SOH STX command-low command-high payload EOT`. The accepted 39-byte frame
  began `01 02 22 00`, carried the evidenced 34-byte M90 payload, and ended
  in `04`. The guest returned ACK bytes `06 03 00 00` followed by its ready
  indication `02 01 85 2D`.
- **CONFIRMED:** the accepted INIT was decoded and logged by the unmodified
  `game.exe`: GForce version 20121212, MachineKey 83, 12536 graphic objects,
  interpreter version 1581, 5007 sounds, GO ID 11327, five hardware buttons,
  TFT sizes 19 and 17, Ergoline cabinet, device-name ID 1, and mode 2. This is
  protocol evidence only; it does not emulate monetary peripherals.
- **CONFIRMED:** the reboot after the accepted INIT is an intentional monitor
  setup path rather than an access violation. The linker map identifies
  `0x006D85A0` as `GraficEngine::SetMonitorBootEinstellung` and
  `0x006D6CA0` as `GraficEngine::ReStartWindowsSystem`. The former writes
  `\NVRAM\tftcfg.bin` and then unconditionally calls the latter. That method
  sends `WM_DESTROY` with `wParam=2`; `WindowProc` at `0x00654699` stores the
  value as the shutdown mode, and `Game_Shutdown` interprets 2 as Windows
  restart. The bridge therefore reconnects instead of suppressing the reboot.
- **CONFIRMED:** the recorded INIT carried the deployment-era date
  `2012-02-01 22:14`, and the game applied it to the guest clock. Per operator
  selection, the current bridge again uses the recorded `2012-02-01 22:14`
  for those six time fields. `F_Uhr` still appeared after both INITVIDEO
  packets carried 2012, proving that this field alone is insufficient. All
  evidenced machine and video fields remain byte-for-byte unchanged. Unit
  tests verify the 39-byte framing, fixed time, and preserved configuration
  fields.

- **CONFIRMED:** the owner runtime reads an Epson R4543-compatible RTC over
  `$FFF907`: bit 6 is CE, bit 5 WR, bit 4 CLK, and bit 7 bidirectional DATA.
  Routine `$609A6` reads 52 bits as BCD seconds, minutes, hours, weekday,
  day, month, and year. The earlier bridge wrongly toggled bit 7 as a periodic
  clock, so it could not provide a valid calendar. The corrected bridge now
  models the R4543 serial register with an initial 2012-02-01 22:14 clock.
  Offline tests pass; a new live boot past `F_Uhr` is still to be verified.

- **OPERATOR HARDWARE EVIDENCE:** the board carries a Motorola MC68331CAG16
  and an R4543 RTC; its 2 MB of battery-backed SRAM is likely a
  CY62167EV30LL. The QEMU/m68020 CPU is an instruction-set approximation,
  not a full MC68331 SIM/GPT/QSM model. The confirmed programming sequence
  is Loader, FactoryReset, date, M90_Las_Vegas config, then Magie_90_CC4 RAM
  database.

- **CONFIRMED:** with the recovered D3 boot-ROM value, both the FactoryReset
  and M90_Las_Vegas modules satisfy their native post-transform checksums.
  The FactoryReset entry clears the 2 MB RAM from `0x10000` upward. The
  M90_Las_Vegas entry clears its last 1 KB, then copies 512 config bytes to
  `0x1FFC00` and 24 identity bytes to `0x1FFF80`. The bridge now applies
  those exact config writes from the hash-pinned module before running the
  unchanged Magie_90_CC4 firmware. Persistence of runtime SRAM changes is
  still unimplemented.

- **CONFIRMED:** disassembly of the unmodified `game.exe` establishes the
  complete INIT call chain. `VideoCommander::InitVideo` at `0x0069BD20`
  dispatches the packed structure through the graphics-engine vtable.
  `GraficEngineDx9Games::GameEngineRun` at `0x00423DD0` compares the supplied
  MachineKey, GForce version, GO count, command-interpreter version, sound
  count, GO ID, and REEST version with the values loaded from the installed
  owner data. The evidenced payload satisfies its hard comparisons:
  MachineKey 83, GForce 20121212, 12536 GOs, interpreter 1581, 5007 sounds,
  and GO ID 11327. It then calls the DX9/base implementations at `0x006E7600`
  and `0x006DF920`; the latter performs monitor, audio-configuration, package,
  COM-Express, and rotation checks before requesting a reboot only when one
  of those checks changes persistent configuration.
- **CONFIRMED:** INIT does not itself draw the machine UI. The supplied owner
  `VidComLog.5.part_01.txt` records the next database-originated phase: six
  startup-status commands followed by 37 commands from ticks 66343 through
  74718. A live resume run replayed this hash-pinned range without command 69,
  input events, or monetary-device families. The guest ACKed the sequence and
  returned object feedback for GO ID 11327, proving that COM3 parsing and the
  graphics command dispatcher were active. The display area changed and the
  SwiftShader splash was cleared; the contaminated run then raised an
  `ntdll.dll` access violation after two stale bridge clients had concurrently
  sent repeated INIT frames, so that run is not valid end-to-end evidence.
- **CONFIRMED:** the next owner-log range selected for a clean run is ticks
  314890 through 373156 of `VidComLog.5.part_00.txt` (SHA-256
  `31E3211B1E96ABC9A4848636349FC28233BA4817AC4FD300AEDC504460194B9E`).
  It contains 34 display/audio records and ends before the first recorded
  TOUCHCLICKDOWN at tick 375515. The importer rejects the entire range if a
  command falls outside its explicit display/audio allowlist.
- **CONFIRMED:** an unpaced replay reached the 491-byte payload of command 64
  at tick 321046, then produced an access-violation dialog without returning
  an ACK. The extracted guest log lists 238 `unerwartetes Zeichen ignorieren`
  entries whose chronological byte sequence begins at the command payload's
  object ID and matches the transmitted data. Static inspection shows command
  64 first reads a two-byte variable length and expects that many bytes plus
  its object/subcommand fields. `machineIni/iniMagie90.txt` specifies 9600
  baud. The failure is therefore a UART delivery overrun caused by injecting
  the frame at TCP speed, not evidence of a SwiftShader failure. The bridge now
  clocks every frame in small chunks at the configured 9600-baud 8-N-1 rate.
  Paced runtime verification is pending.

## 2026-09-29: cabinet buttons and admission card

- **CONFIRMED:** the original firmware's key table at `0x5B942` maps the five
  panel buttons to logical IDs 2 through 6. The timer-driven, multiplexed
  board scanner at `0x73506` writes their active-low levels to `0x1E247B`
  plus the mapping offset and latches press edges at `0x1E2503` plus that
  offset. It scans successive 0x10-byte banks; the higher offsets for Menu
  and Einsatz are not evidence of an SCC-A button protocol. The physical
  cable topology of the owner's panel has not yet been verified.
- **CONFIRMED:** before the latest change, a GUI Menu pulse was applied as
  `current=08 event=10`, but after 200 board scans its level was still `08`
  despite release. The absent machine=none input hardware left multiple
  active-low switches apparently held. The bridge now publishes idle-high
  levels for all mapped switches on every scan and discards false scanner
  edges for inactive switches. Offline suite: 179 passing tests. In the
  subsequent live run, a Menu pulse returned to `current=18 event=00` after
  release; no `EVENT_HW_BTN` frame was seen while the guest displayed
  `CODE / Neu`, so gameplay button behavior is not yet verified.
- **CONFIRMED:** the live guest reached the game selection UI but displayed
  `CODE / Neu`. The owner's manual says this means the electronic admission
  card is not programmed/recognized or its start code has not been entered.
  The current bridge has no admission-card model. The original procedure
  closes the door and starts the machine with the card inserted, then begins
  service operation and enters an eight-digit code with the cabinet controls.
  Do not treat the visible UI as evidence of playable readiness.
- **OPERATOR-SUPPLIED:** the physical admission card and a matching code are
  available. `zlk_v1.exe` was inspected read-only and not executed; SHA-256
  `47A77116D351AFC85203357370876F000B8C225843B028319207A0F589EA8CBD`,
  unsigned PyInstaller executable. Card connector/protocol data and the
  exact model-specific entry sequence are still needed before card emulation.

Manual references: https://mfl.de/FAQ/ and
https://www.geldspielfreunde.de/index.php?attachment/67475-tu-betriebsanleitung-merkur-ideal-ergoline-slant-top-slimline-pdf/=

### Admission-card firmware supplied later

- Owner identified `firmware_v3.bin` as a dump from the admission card. The
  file was read but not run or modified: 4096 bytes, SHA-256
  `24C2936D600464B0DA520CFC9E25DAB3718756D1A22AA8E1A802BA477AC6407F`.
  Its first words match AVR instruction encodings (`RJMP`, `RETI`, `RET`),
  while all bytes from offset `0x492` onward are `FF`. The populated program
  region is therefore about 1.2 KB. This is firmware, not a plain-text
  admission number or code list.
- Later owner-supplied programmer source, KiCad schematic, and 256-byte
  `eeprom.bin` resolve the MCU as an ATmega48-family card. The firmware has
  `0x31`..`0x36` command comparisons and accesses EEPROM control at I/O
  `0x1F`; the schematic labels MOSI/MISO/SCK. The V3 programmer writes a
  nine-digit ASCII number at EEPROM `0x28`, then the same number as five
  packed BCD bytes followed by a four-byte machine ID at `0x40`. For
  Ergoline M90 the ID is `06 32 11 55`. It does **not** write a start code.
- Static inspection of `zlk_v1.exe` found a PyInstaller program identified
  internally as a ZLK V1 AT90S1200 firmware uploader. Its machine table does
  not include M90; it was not executed. It is not evidence that a start code
  belongs in the ATmega48 V3 EEPROM.
- Disassembly of the original database at `0x6D848..0x6D906` confirms that
  command `0x31` decodes the nine-digit BCD number and first two machine-ID
  bytes from the 11-byte response; `0x34` decodes the last two machine-ID
  bytes at `0x6DA1E..0x6DA5E`. `0x32` reads an indexed byte. These map to
  the V3 EEPROM layout, so the existing AUX responder is the card candidate,
  not a separate unrelated peripheral. Its old default values remain for
  comparison. With `--admission-eeprom` it now reads identity/model/byte
  replies from a validated M90 image. The five extra response bytes of
  command `0x34`, the full wire-level bit timing, and guest `CODE / Neu`
  behavior still require live verification.
- `scripts/admission_card.py` prepares an M90 image from a 256-byte template
  without altering the template. A sample `build/admission-card-m90.eeprom.bin`
  was generated from the supplied EEPROM using its sample number 123456789.
  This is test data, **not** a proven matching admission card or start code.
