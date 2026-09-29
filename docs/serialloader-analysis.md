# SerialLoader wire evidence

The owner supplied `SerialLoader.exe` from the physical database programming
procedure.  It was inspected read-only.

| Property | Value |
|---|---|
| SHA-256 | `0084627878C3C3D4A2D78A4C7F4A89A4F4E313D894E8C45773E32C1662A92988` |
| Runtime | .NET Framework / WinForms |
| Initial serial rate | 57,600 baud |
| Type-B continuation rate | 115,200 baud |
| File block size | 64 bytes |

The constructor initializes the exact 24-byte control table
`7C 6B 69 6C 6C FD C4 55 1B 53 59 4E 43 53 59 4E 43 57 41 49 54 47 4F 0A`.
The first eight bytes are the kill sequence; the following sixteen bytes are
`ESC SYNCSYNCWAITGO LF`. This independently confirms the sequences recovered
from the 68020 loader.

The `Upload (R)` path sends the selected loader unchanged. It writes its first
256 bytes individually at 57,600 baud, waits 25 ms, optionally switches to
115,200 baud for the recognized Type-B family, and sends the remainder in
64-byte blocks. The database `Upload (L)` path first sends the sixteen-byte
WAITGO sequence one byte at a time with a 2 ms delay and then starts the same
file-transfer worker. Incoming status is displayed as two-byte pairs beginning
with `1B`; this matches the documented `1B31`, `1B32` and `1B33` states.

The supplied `Magie_90_CC4.bin` and three small module dumps start directly
with their internal module headers. Their first eight bytes do not satisfy the
loader's separate eight-byte transfer-header checksum. They are therefore raw
module/flash representations, not complete SerialLoader wire captures. For a
raw dump, the harness may precede the module bytes with a valid all-zero
transfer header; the loader explicitly accepts that header and skips its
optional board-facing setup.

`SerialLoader.exe` does not transmit the 32-bit value found in loader register
`D3` at entry `0x040E`. It is already present when the uploaded loader begins
execution, so its source is the physical database boot ROM/controller rather
than the selected loader or database file. Replaying the documented PC-side
programming sequence alone therefore cannot observe that value.

The value has now been recovered from the owner database itself by a complete
32-bit candidate search: `D3=0xD27B7159`. The search used the decoded 68020
vectors only as an early filter and accepted the result solely after the full
unmodified native checksum matched. Python and QEMU/m68020 independently
confirm the result. No physical boot-ROM dump is required for this owner image.

The emulator follows the same two-stage architecture. Programming mode models
the boot controller and the documented Upload-R/date/factory-reset/database
sequence. Operating mode starts the resulting 68020 runtime and bridges its
UART to guest COM3. A chip profile must therefore contain the controller's D3
value (or the resulting post-transform RAM image); choosing an arbitrary value
would create a flash image that fails the original loader checksum and is not a
faithful emulation of the owner's module.

The reusable IL inspection helper is `scripts/dump_dotnet_il.ps1`. It resolves
metadata tokens and was used to verify `MainForm..ctor`, `method_1`, `method_2`
and `backgroundWorker_0_DoWork` without executing the supplied application.
