# Database loader static analysis

This note records only behavior verified in the owner-supplied binaries. The
source files are hash-pinned in `owner-database-set.json` and remain read-only.
No licensing, authenticity, key-request, payout, acceptor or dispenser logic is
altered or emulated here.

## Address mapping and entry

The loader is a Motorola 68020-class big-endian image loaded at `0x0400`.
Runtime addresses therefore equal file offsets plus `0x400` for resident code
and data. At file offset `0x0E` (runtime `0x040E`) an absolute `JMP 0x0CC8`
transfers control to file offset `0x08C8`. The `MOVEC D0,VBR` instruction at
file offset `0x02D4` confirms that the code is not limited to a base 68000.

At runtime `0x0CE8`, the entry code copies incoming register `D3` to `A0` and
then executes `MOVE A0,USP`. The transform initializer later reads USP and XORs
it with `0x2378BF41` while constructing its 256-byte state. Consequently, the
complete transform context is not contained in either supplied BIN: `D3` is an
external boot-interface value. The loader does not overwrite it before the
jump to its main routine at `0x06A4`.

| File offset | Runtime address | Observed role |
|---:|---:|---|
| `0x013C` | `0x053C` | Initializes two 256-byte transform tables/state |
| `0x01EC` | `0x05EC` | Applies the byte-wise stream transform |
| `0x02A4` | `0x06A4` | Existing-image check and boot/receive decision |
| `0x032C` | `0x072C` | Serial receive state machine |
| `0x0694` | `0x0A94` | Communication hardware initialization |
| `0x0714` | `0x0B14` | Database structural validation |
| `0x0774` | `0x0B74` | Database additive checksum validation |
| `0x0818` | `0x0C18` | Error indication and serial status byte |
| `0x0894` | `0x0C94` | UART byte transmit |

The hardware initialization at `0x0CC8` writes the controller window from
`0xFFF900` through `0xFFFD4E`. Channel-A receive-ready is bit 6 of
`0xFFFC0D`, receive data is `0xFFFC0F`, and transmit-ready is bit 0 of
`0xFFFC0C`; transmit data also uses `0xFFFC0F`. The alternate interface uses
bit 0 of `0x800183` and data register `0x800187`.

## Serial synchronization and transfer

The receive state machine polls either the controller registers at
`0xFFFC0D/0xFFFC0F` or the alternate interface at `0x800183/0x800187`. It
recognizes two exact 16-byte synchronization sequences. They are decoded from
tables resident at runtime `0x0CB0` and `0x0CB8` by adding one to each of the 15
bytes following the lead byte:

```text
7C 6B 69 6C 6C FD C4 55 1B 53 59 4E 43 53 59 4E
1B 53 59 4E 43 53 59 4E 43 57 41 49 54 47 4F 0A
```

The printable part of the second sequence is `SYNCSYNCWAITGO` followed by LF.
After synchronization, the loader reads eight transport-header bytes. The
eighth byte must equal the modulo-256 sum of the preceding seven. A non-zero
header is also passed through a board-facing bit-bang routine; its semantic
fields are not yet proven.

For owner-provided raw module dumps, a valid all-zero transfer header can be
supplied before the dump. The first `0x100` database bytes are then received
directly into memory at
`0x1000`. These bytes contain the structural header used to determine the final
address and entry point. After that header passes validation, the loader
initializes its stream-transform state. Bytes from `0x1100` through the
inclusive end address are transformed one at a time as they are received.

## Database validation and boot

For `Magie_90_CC4.bin`, the structural values are:

| Database offset | Runtime address | Value | Meaning |
|---:|---:|---:|---|
| `0x00` | `0x1000` | `0x07AB140F` | Stored post-transform additive checksum |
| `0x04` | `0x1004` | `0x001C4137` | Inclusive loaded-image end |
| `0x08` | `0x1008` | `0xFFE3BEC8` | Bitwise complement of end |
| `0x0C` | `0x100C` | `0x61640403` | Module ID |
| `0x10` | `0x1010` | `0x001C4138` | Exclusive end / next address |
| `0x4C` | `0x104C` | `0x00001500` | Application entry point |
| `0x50` | `0x1050` | `0xFFFFEAFF` | Bitwise complement of entry point |

The loader masks the low module-ID byte and requires `0x61640400`, directly
linking this loader family to database ID `0x61640403`. It also checks both
complement pairs and address bounds. Its checksum routine sums every byte from
runtime address `0x1004` through the inclusive address stored at `0x1004`, then
compares the 32-bit result with the word at `0x1000`.

If a valid already-loaded image is present, the loader sets VBR to `0x1100` and
calls the address stored at `0x104C`. Otherwise it enters the serial receiver,
validates the first `0x100` bytes, receives/transforms the remainder, repeats
the checksum test, and only then boots the database application.

## Consequence for the emulator

The database BIN is a transport representation, not something the x86 game can
consume as a plain file. The implemented operating path therefore runs the
owner database-side program on a QEMU `m68020` and connects its original UART
MMIO to guest COM3. Selection remains explicit and SHA-256 pinned. The older
`vidcom_init_bridge.py` remains a diagnostic record-replay tool only;
`m68k_database_bridge.py` generates no recorded protocol replies.

The unique checksum-valid transform state has since been recovered and is
documented below. The bridge accepts that original `D3` value or a separately
validated post-transform owner RAM dump; it does not accept an unchecked or
guessed runtime image.

Static inspection of the owner-supplied `SerialLoader.exe` confirms the loader
and database upload ordering, serial rates and synchronization bytes, but also
shows that the PC tool does not send `D3`. The value already exists when the
uploaded loader is entered and therefore originates in the physical module's
boot ROM/controller. See `serialloader-analysis.md`.

## Executed QEMU harness evidence

`scripts/m68k_qemu_harness.py` runs the hash-pinned loader on QEMU's real
`m68020` core. QEMU maps a 16 MiB flat address space so the 24-bit MMIO window
exists, while address zero reports the loader's actual 2 MiB RAM layout; this
places its stack at `0x1FFF80` and avoids overlap with `0xFFxxxx` MMIO.

With no resident database, the unmodified loader follows
`0x040E -> 0x0CC8 -> 0x06A4 -> 0x072C` and emits UART bytes `1B 32`. Feeding
the native 16-byte `SYNCSYNCWAITGO` sequence, an all-zero transport header
(whose final byte validly equals the sum of the first seven), and exactly the
first `0x100` bytes of the owner database makes the loader reach `0x0994` after
3,635 instructions. That address is the call site for the original transform
initializer, and can only be reached after the structural database header has
passed the loader's own checks.

### Boot-ROM runtime handoff

The loader entry at `0x0CC8` receives register context from the controller boot
ROM. At `0x06B4` it compares incoming `D2` with the literal `0x5F72D920`.
Failure enters the SerialLoader programming path; this was observed live as a
stable RX wait at `PC=0x00000742`, `SR=0x2704`. Success calls the unchanged
validator at `0x0B74`, which checks the header/complement pairs, memory bounds,
module family `0x616404xx`, and additive image checksum. Only then does it set
`VBR=0x1100` and call the entry pointer stored at `0x104C` (owner image:
`0x00001500`). The operating bridge now supplies the statically evidenced D2
cookie rather than falling into programming mode.

The first corrected handoff reached runtime `PC=0x00019CB0` with a runtime
stack (`A7=0x001FFB88`) and `D2=0x5F72D920`. This also exposed an MMIO
watchpoint alias: the bridge's byte watch at `0xFFFC0D` fires for 16-bit reads
starting at `0xFFFC0C`. At `0x19CAA/0x19CBC` the firmware tests TX-ready bit 8;
these accesses are not RX polls. The bridge now classifies all statically
identified overlapping TX checks by their post-instruction PC while retaining
the genuine RX polling sites, including `0xC57B8` (bit 6).

Once that alias was corrected, the runtime repeatedly reached `CLR.B
$FFFC0F` at `0x19CB6` and `0x19CC8` (stop PCs `0x19CBC/0x19CCE`). Forwarding
these UART-initialization clears as NUL payload produced another guest IRQ
flood. They are therefore kept internal by exact PC-and-value matching. The
real buffered runtime transmitter at `0xC5F36` remains forwarded, including
legitimate NUL or `FF` bytes originating there.

### Database-board latch and timer model

The first runtime board probe writes `0x13`, then `0x07`, to the latch at
`0x800181` and expects the subsequent read to return `0x13`. A second variant
writes `0x02`, then `0x07`, and expects `0x02`. Plain RAM incorrectly returns
the last write (`0x07`), causing the timeout calls at `0x18358` or `0x183C4`.
Both call `0x16562`, a statically verified infinite hardware-error blink loop.
The bridge models the latch feedback only at the two post-write PCs
`0x18324/0x1837A`; it does not skip either comparison or patch the firmware.
A live run logged `DB_BOARD_LATCH_FEEDBACK value=13 pc=00018324` and then
reached the normal initialization return at `0x18482`.

The runtime vector table at VBR `0x1100` maps vector 134 to `0xC6BD0`. The
initialization routine programs the corresponding timer registers at
`0xFFF900..0xFFF920`; the handler advances the compare register and calls the
unchanged database UART RX/TX routines at `0xC5790` and `0xC5F0E`. Because
QEMU `machine=none` has no original controller timer, the bridge supplies a
68020 format-zero interrupt frame after `0x18482` has been observed and after
verifying the vector target in runtime memory. The guarded default lets QEMU
run for 50 ms and then keeps it stopped for 50 ms after every injected frame,
for the user-requested 50% duty cycle. Startup validation enforces a run slice
of at least 5 ms, a sleep of at least 50 ms, and a maximum duty cycle of 50%; Windows also starts
the m68k QEMU process below normal priority. The first
integrated run then reached the genuine RX poll at `PC=0xC57B8` and reported
`DB_WAITING_FOR_COM3`.

Static inspection of the hash-pinned runtime
`4E6D0FD7148FD66639687CB79714FF2F6BB663DFE4D9CF420E4194E44337E177`
shows that the board-I/O return at `0x18482` precedes the first known UART-init
call at `0x1861A`. Function `0xC5F82` writes the transient state `0xA756` to
`0x1EBBB0`, initializes the UART buffers, and writes the ready magic `0x58A9`
at `0xC602A`. The normal dispatcher calls `0xC603E`, which returns true only
when that word equals `0x58A9`; `0xC60A0` clears it during shutdown/reset.
When the board boot-ROM context has not supplied this initialization, the
bridge calls the unchanged `0xC5F82` routine through RSP, waits at a private
return sentinel, verifies that the routine produced `0x58A9`, and restores all
18 m68k registers plus the temporary return-stack bytes. It does not write the
ready value directly, fabricate an INIT packet, or bypass the firmware state
check.

A guarded live run proved that making `0x58A9` a prerequisite for timer
injection creates a circular wait: after board-I/O completion the runtime
remained at `PC=0x6D7D2`, waiting for flag `0x1E2B05` to be cleared, while the
UART state remained zero. The timer handler itself only calls UART RX/TX, and
both return immediately while `0xC603E` reports not ready, so timer injection
alone cannot break that wait. This confirms that UART setup belongs to the
missing controller boot context rather than to the later command loop. The
bridge now executes the original initializer after the verified board-I/O
return and only then enables the existing 10% duty-cycle timer.

The next guarded run reached the real receive poll at `PC=0xC57B8`, but exposed
a host debugger timing bug: after the 10 ms execution timeout, the RSP client
also allowed only 10 ms for QEMU's Ctrl+C stop reply. That control response is
not part of the emulated CPU run slice. The bridge now allows up to two seconds
for the stop acknowledgement and restores the 10 ms timeout before resuming;
the 90 ms stopped-state sleep and 10% duty-cycle guard are unchanged.

An empty receive poll must not suspend the emulated processor. The original
UART reports RX-ready clear and lets the interrupt handler return; blocking the
GDB watchpoint until XP supplied a byte created a circular wait at the game's
`Waiting for InitVideo` stage. The bridge now writes status zero and continues
immediately when its host receive queue is empty, allowing the database
application to initiate traffic on later timer ticks.

### Loader idle burst and QEMU COM3 compatibility

Static disassembly identifies a second loader transmit site at `0x0C6E`
(`MOVE.B #$FF,$00FFFC0F`; hardware-watchpoint stop PC `0x0C76`). It sits in a
finite LED-delay loop controlled by `D4-D7`; the ordinary byte transmitter is
the separate routine at `0x0C94`, whose store ends at `0x0CAA`. In a physical
controller the UART's TX-ready bit at `0xFFFC0C` provides hardware pacing.

Two visible QEMU/WHPX runs forwarded bootloader output through the
socket-backed legacy COM port and reproducibly ended in Windows XP STOP
`0x0000007F` with first parameter `0x00000008` (double fault). The m68020 side
had already completed the burst and reached its RX wait, so this is evidence of
guest COM interrupt overload, not a database checksum failure. The operating
bridge therefore keeps the complete loader address range below `0x1000`
internal. That includes the SerialLoader status `1B 32` and the idle burst; both
normally precede the Windows operating-mode connection. Every transmit from
the loaded database runtime at `0x1000` or above remains visible on COM3. This
boundary is encoded by `should_forward_tx` and covered by a unit test which
also proves that an `FF` emitted by runtime code is not filtered.

Because the watchpoint transport would otherwise stop once for every one of
the loop's thousands of idle writes, the emulator advances from the first
observed `0x0C76` stop to the loop epilogue at `0x0C8A`. Disassembly proves the
skipped range only updates the delay counters and two LED registers
(`0x80019D/0x80019F`); image validation and the runtime handoff occur outside
it and remain unmodified.

`scripts/m68k_database_transform.py` reproduces routines `0x053C` and
`0x05EC` in memory. A 32-byte differential run compared it against bytes
produced by the QEMU-executed loader and matched exactly:

```text
AB 26 FD C1 A1 E2 5D 79 13 EC 1D 81 F3 7F 8D 6B
16 3C 6A 79 19 23 52 E7 E2 BB F7 12 CD 6B 5D 87
```

The controlled D3=`0` run does not validate: the native stored checksum is
`0x07AB140F`, while the transformed result sums to `0x0E0B5B6E`. Its entry
bytes are not plausible code.

The complete 32-bit D3 space was subsequently searched with
`src/recover_database_d3.cpp`. Candidates first had to decode the first two
68020 vector entries to aligned addresses inside the image; survivors were
then checked against the unmodified native checksum over all 1,847,604 checked
bytes. After 3,531,436,378 candidates and 152 vector survivors, exactly one
checksum match was found: `D3=0xD27B7159` (`D3 XOR 0x2378BF41 = 0xF103CE18`).
The independent Python implementation calculates exactly `0x07AB140F` and
decodes the entry at `0x1500` to valid 68020 instructions beginning
`4F F9 00 1F FC 00 4B F9 00 1E 04 2A 20 3C 00 00`.

An independent QEMU/m68020 differential run with this D3 also produced the
expected first 32 runtime bytes at `0x1100`:

```text
00 08 00 00 00 00 15 00 00 06 0C F6 00 06 0C F6
00 06 0C F6 00 06 0C F6 00 06 0C F6 00 06 0C F6
```

This resolves the former D3/runtime-dump gate without modifying the owner
database, its stored checksum, or a loader validation branch.
