# Owner-supplied database dump

The operator supplied a main database, its loader, and three auxiliary modules
read from their physical database. The
sources remain outside the project and are never modified or redistributed.
Their current forensic identity is:

| Property | Observed value |
|---|---|
| Size | 1,847,608 bytes (`0x1C3138`) |
| SHA-256 | `593CF4B3A1CCC83F206E1492E44B9D303EA3C05990059B8659D8308DA1DC2EE8` |
| Copyright header | `COPYRIGHT BY ADP LUEBBECKE GERMANY 2012` |
| Product | `MERKUR MAGIE  90 G   CC4` |
| Edition field | `1Aa/` |
| Build field | `151008` |
| Release field | `070101` |
| First opaque payload byte | offset `0xE0` |
| Big-endian module ID | `0x61640403` |
| Load base | `0x00001000` |
| Inclusive end address | `0x001C4137` |
| Exclusive end address | `0x001C4138` |
| Entry point | `0x00001500` (file offset `0x500`) |

The copyright field begins at `0x18` with one padding space; the printable
text itself starts at `0x19`.

The paired loader has a distinct format and role:

| Property | Observed value |
|---|---|
| Source name | `Loader_61640403_L5.0b_2MB.bin` |
| Size | 3,008 bytes (`0xBC0`) |
| SHA-256 | `B0768C65B34834C7A740615D2B0ABDB470AEC012FE4DC4A3C11531EFA221E109` |
| Signature | `|load` |
| Version | `L 5.0b` |
| Copyright header | `COPYRIGHT BY ADP LUEBBECKE GERMANY 2009` |
| Code start | offset `0x58` |
| Architecture | Motorola 68020-class, inferred from big-endian instructions including `MOVEC` |

The two files are structurally linked, not merely similarly named. The loader
accepts module family `0x61640400` after masking the low byte; the database ID
is `0x61640403`. Its header describes loading the complete file at address
`0x1000`: `0x1000 + 0x1C3138 - 1 = 0x001C4137`. Both the inclusive end address
and entry point have valid bitwise-complement partners (`0xFFE3BEC8` and
`0xFFFFEAFF`). The entry point `0x1500` resolves to file offset `0x500`.

The stored word at offset `0x00` is reported as `0x07AB140F`, but the validator
does not compare it with a sum over this file. Static analysis shows the loader
checks the memory image after its receive/transform step, so treating a mismatch
against the opaque transport bytes as corruption would be unsound.

The `2MB` filename does not describe the file length: the supplied loader is
3,008 bytes. It likely names its target/module class; this remains an inference
until the physical memory layout is documented.

`scripts/inspect_owner_database.py` is a bounded, read-only import gate. It
hashes the complete input and decodes only these observed clear-text fields.
It deliberately does not decrypt or rewrite the opaque payload, infer a
license, or claim authenticity from the header alone. The exact hash can be
pinned for repeatable tests:

```powershell
python .\scripts\inspect_owner_database.py `
  "C:\M90\Dateien\Magie_90_CC4.bin" `
  --expected-sha256 593CF4B3A1CCC83F206E1492E44B9D303EA3C05990059B8659D8308DA1DC2EE8
```

Run the same gate separately for the paired loader with its own pinned hash:

```powershell
python .\scripts\inspect_owner_database.py `
  "C:\M90\Dateien\Loader_61640403_L5.0b_2MB.bin" `
  --expected-sha256 B0768C65B34834C7A740615D2B0ABDB470AEC012FE4DC4A3C11531EFA221E109
```

The five files are never concatenated or altered by the validator. The
auxiliary module identities are:

| Source | Size | SHA-256 | Role |
|---|---:|---|---|
| `FactoryReset_61640403.xc` | 1,328 | `4F088DB4AF5F4A5D112A003EF312EB19B4388C25902FFA03F75742379A0CD5C4` | database module |
| `M90_Las_Vegas.bin` | 1,920 | `DCE3A865B742123C95EA4F0B14FA16F287DDF90CD86432F68B2301B70A919783` | database module |
| `M90_Multi_Juwel.bin` | 1,920 | `445EF8CB754D5BCD8E8CE3901B90F0F32B53A9269F1CC2605F16119B3732A911` | database module |

All three have valid load bounds and complement pairs, declare module ID
`61640403`, enter at `0x1500`, and intentionally omit the full database's
product text and exclusive-end field. The inspector consequently labels them
`database_module` rather than rejecting them as malformed main databases.

For an emulator import, validate the relationship and both pinned hashes in a
single read-only operation:

```powershell
python .\scripts\validate_owner_database_set.py `
  "C:\M90\Dateien\Magie_90_CC4.bin" `
  "C:\M90\Dateien\Loader_61640403_L5.0b_2MB.bin" `
  --expected-database-sha256 593CF4B3A1CCC83F206E1492E44B9D303EA3C05990059B8659D8308DA1DC2EE8 `
  --expected-loader-sha256 B0768C65B34834C7A740615D2B0ABDB470AEC012FE4DC4A3C11531EFA221E109
```

The dump is not yet installed into `m90_work.img`. Runtime use requires a
documented parser/protocol mapping that produces the same legitimate COM3
messages as the physical owner device. Until that mapping is proven, the
current host bridge remains a diagnostic reconstruction from owner logs.

The new `m68k_qemu_harness.py` and `m68k_database_transform.py` consume the
hash-pinned owner files directly and have proven the native loader handshake,
header validation and transform implementation. Complete execution is gated
on the original external D3 loader-entry value; D3=`0` has been tested and
rejected by the native checksum.
