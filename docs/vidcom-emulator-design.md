# COM3 and owner-supplied database design

## Current development topology

The present database-side emulator is intentionally external while the serial
protocol is being reconstructed:

```text
game.exe (XP guest) -> COM3 -> QEMU serial TCP 127.0.0.1:4553
                    -> scripts/vidcom_init_bridge.py (host)
```

It implements explicit connected, init, discovery, startup, render and
interactive phases, sends only evidenced display-side packets, and records
both directions. Discovery queries wait for their `07 <length> <command>` data
reply; mutating render commands use stop-and-wait ACK/NAK flow control. It
contains no proprietary database image and does not
model an acceptor, dispenser, payout, jackpot, or other monetary device.

## Intended final topology

The final runtime is to be self-contained in the working XP image:

```text
game.exe -> virtual COM3 endpoint -> XP-compatible VidCom emulator
                                   -> validated owner-supplied data set
```

The host TCP bridge is a diagnostic implementation, not the deployment
architecture. The in-image endpoint must remain Win32/x86 and Windows XP
compatible and must preserve the packet ordering and packed little-endian ABI
established from `commandointerpreter.h` and live captures.

## Separation of emulator and data

The emulator distribution must not include the original database, game data,
graphics, sounds, or device secrets. A separate import operation accepts a
file copied by the owner from their physical machine and writes only to a
dedicated data directory inside `m90_work.img`.

Before activation, the importer must:

1. retain the input file unchanged as evidence or operate on a verified copy;
2. record filename, size, SHA-256, import time, and parser version;
3. identify the format and declared software/protocol version;
4. parse bounded lengths before consuming records;
5. verify available native checksums rather than changing or bypassing them;
6. compare MachineKey and configuration fields with the selected profile;
7. reject truncated, ambiguous, unsupported, or mismatched input;
8. make activation atomic, leaving the previous data set recoverable.

The read-only forensic precursor is
`scripts/vidcom_log_parser.py`. It validates the binary record boundaries and
CRLF terminators, caps file and payload sizes, records the source SHA-256, and
can emit the newest-first source order or reverse it into chronological order.
It never transmits or executes parsed commands.

The development bridge can optionally consume a separately supplied,
SHA-256-pinned owner VidCom log. It selects only the evidenced initial render
range (ticks 66343 through 74718), requires both range boundaries, and rejects
the entire prefix if any command is outside the explicit display/audio
allowlist. `REQUEST_KEY` (69) is not allowlisted. Payloads remain in the owner
file and are not embedded in the emulator source.

The owner log proves that `INITVIDEO` is only the transition into the video
command loop. The database then issues CREATE, visibility, value, animation,
and game-object commands. A black but responsive window after INIT therefore
does not by itself establish a SwiftShader failure: without the post-INIT
stream, the renderer has no machine objects to display.

The optional continuation is independently hash-pinned to owner file
`VidComLog.5.part_00.txt`. Only ticks 314890 through 373156 are accepted; this
range ends before the first recorded touch/input event. It is rejected if it
contains an unapproved command. Automatic INIT retries are bounded and begin
only after a configurable boot delay, preventing a stale connection from
flooding an initialized serial parser.

Post-INIT display commands are transmitted with stop-and-wait flow control.
Each command must receive the exact four-byte ACK before the next record is
sent; timeout terminates the diagnostic session. A host-side exclusive lock
also prevents concurrent bridge instances from corrupting the serial state.
The original machine configuration specifies 9600 baud. The host bridge must
also pace bytes at the equivalent 8-N-1 wire rate: QEMU's socket character
backend otherwise injects a large VARIPARA command faster than the emulated
UART/guest parser can drain it, yielding truncated commands and discarded
payload bytes even though the logical record itself is valid.

Run 11 proved that averaging 9600 baud in eight-byte bursts is still
insufficient: the guest emitted `NAK` error 5 (`ERR_TIMEOUTRECEIVE`) and logged
discarded payload bytes. The default is therefore strict one-byte pacing at
the 8-N-1 interval. A NAK now has priority even when the guest coalesces it
with a following ACK; the state machine aborts immediately instead of sending
subsequent records into a rejected parser state.

An exact `--resume-owner-continuation-at TICK` recovery mode may resume only
on a validated owner-log record boundary. It exists so a diagnostic pause or
host-console disconnect does not require replaying already acknowledged
CREATE/render commands into the initialized guest.

If no compatible owner-supplied file is installed, the endpoint exposes only
a diagnostic state and must not synthesize proprietary content. Validation
failure is reported as such; it is not converted into success by patching the
consumer or altering expected checksums.

The bridge now accepts `--owner-database`, `--owner-loader`, and a mandatory
SHA-256 pin for each. It validates the pair before opening COM3 and prints
`OWNER_DATABASE_SET_VALIDATED` only when both individual formats, both hashes,
the module-family relationship, and the loader entry mapping pass. This is an
import gate only; the current bridge does not claim to derive live replies from
the database application yet.

## Implemented protocol evidence

The fixed INIT frame is 39 bytes:

```text
01 02 22 00 <34-byte packed PARA_INITVIDEO> 04
```

The live guest accepted this form and replied with an ACK and ready response.
The development bridge currently fixes the database time to the recorded
`2012-02-01 22:14`. `F_Uhr` remained after that correction because the
database firmware also reads its own serial RTC. The remaining
payload fields are retained from the supplied system's historical VidCom log.

The reboot immediately following the first accepted INIT is retained. Static
symbols and disassembly prove that monitor configuration writes
`\NVRAM\tftcfg.bin` and deliberately requests a Windows restart. Suppressing
that reboot would diverge from the original startup behavior.

## Explicit non-goals

- bypassing `REQUEST_KEY`, authenticity, integrity, PTB, licensing, or CF-card
  checks;
- manufacturing or altering expected checksums to accept modified protected
  files;
- simulating money acceptance, payout, dispenser, or jackpot behavior;
- bundling database data not supplied by the owner.
