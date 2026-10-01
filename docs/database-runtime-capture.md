# Physical database runtime capture

The transport BIN alone does not contain the external D3 value supplied to the
owner loader. One of the following read-only captures is sufficient to finish
the database-side emulator. Addresses are 24-bit Motorola 68020 addresses.

## Smallest capture: D3 or USP

Set a debugger breakpoint at loader entry `0x0000040E`, before executing the
absolute jump, and record register D3 as eight hexadecimal digits. The loader
copies D3 to USP at `0x00000CE8..0x00000CEA` and does not overwrite the value
before transform initialization.

Equivalent alternative: stop immediately before instruction `0x0000054C`
inside the transform initializer and record USP. This USP value is the same
32-bit input originally supplied in D3. Do not substitute the database module
ID, serial number, current time, or another guessed value.

## Alternative: post-transform RAM dump

After the complete database transfer, stop at `0x0000070E`, immediately before
the loader calls its additive checksum routine. Dump this inclusive range:

```text
start: 0x00001000
end:   0x001C4137
size:  0x001C3138 (1,847,608 bytes)
```

The dump must begin with `07 AB 14 0F 00 1C 41 37` and retain the complete
runtime payload. Do not dump only the flash/transport representation again.

Validate a capture read-only with:

```powershell
python .\scripts\validate_runtime_database_dump.py `
  "C:\M90\Dateien\Magie_90_CC4.bin" `
  "C:\path\to\runtime-1000-1c4137.bin" `
  --expected-transport-sha256 593CF4B3A1CCC83F206E1492E44B9D303EA3C05990059B8659D8308DA1DC2EE8
```

The validator requires the exact size, unchanged raw `0x100`-byte prefix,
valid header relationships, changed post-transform payload, and the loader's
native additive checksum `0x07AB140F`. It does not repair, patch, or write the
capture.
