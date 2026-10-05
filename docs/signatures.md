# Original binary signatures

These values were calculated from the read-only forensic copies under
`original/`. `Get-AuthenticodeSignature` reports `NotSigned` for all four
files; this means no embedded Authenticode signature was found and is not a
statement about provenance.

| File | Bytes | SHA-256 | PE | Link timestamp | Subsystem |
|---|---:|---|---|---|---|
| `ADPInterface.dll` | 61,440 | `E8F56CC80FCB57A88A4D4655C57419422A7D987C4587D31CEA104F357EB43335` | x86, OS 4.00 | 2008-01-07 07:57:53 | Windows GUI 4.00 |
| `Cgos.dll` | 65,536 | `480703586EA6F5BDC9AE3D8AA7BB47F03FA4D8234B48A3F2ABC92356FB76A14E` | x86, OS 4.00 | 2006-09-27 09:47:25 | Windows GUI 4.00 |
| `Cgos.sys` | 12,288 | `EDB7AB600F3DF86526204B7D85629DCAC20C41509ACE013B91B21FE5C87C6F40` | x86, OS 4.00 | 2006-09-27 12:09:16 | Native 4.00 |
| `adp-loader.exe` | 2,088,960 | `2FB4233B541431A1B940ED5AF6F11096B7FD5846316E3C4E55BD0E9A7B37A5C1` | x86, OS 4.00 | 2014-05-12 08:23:55 | Windows GUI 4.00 |

The loader contains file/product version `7.0.0.5`, company
`adp Gauselmann GmbH`, and description `Anwendung`. The other three files do
not expose populated version-resource fields through Win32 version APIs.

The link timestamps are PE metadata and are recorded as evidence, not assumed
to be trustworthy wall-clock creation times.
