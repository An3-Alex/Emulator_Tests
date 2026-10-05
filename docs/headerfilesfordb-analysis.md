# HeaderFilesForDB evidence

The directory `/WorkDir/HeaderFilesForDB` was located in `m90_work.img` while
QEMU was stopped and the NTFS partition was mounted read-only. Copies were
placed under `extracted/HeaderFilesForDB` for local analysis. The image and the
original files were not modified.

| File | Size | SHA-256 |
|---|---:|---|
| `commandointerpreter.h` | 54,367 | `DBBD9FC6F2ED00C6BBBDEB50111763999A6F466339322181023D2672E0202553` |
| `commonGameDefinitions.h` | 4,068 | `4A6E1B3A5CEF1161CADF303B3C546591D84F1B00D5AC4DA17A073C00BB158E71` |
| `DefGameIds.h` | 17,700 | `C27FAB1DFAAA53C573867A5F1DFC959FF5EAEE1544C29787C51854240710E997` |
| `DefGO.h` | 544,774 | `E92648A6AFA2927782D095089F84045A39F5391251700360DF742D65406A4F89` |
| `DefGOEx.h` | 530,608 | `93EB6BE383119DA2EFB02848FD223DBA33355C70D2853A030833DE83B33FD16B` |
| `DefServiceData.h` | 2,620 | `3372C31F5405335BD3420D5B9658BF66D218FA3ADAF74103099ADABF17AFEE2B` |
| `defswpackages.h` | 11,126 | `4505BBEFA7C743E65BF7DB1A57294CD4B0DAE9863B7CF16F2C746A619E19D875` |
| `HeaderGOKoordinaten.h` | 207,132 | `F0A41E2D330F01A493BE3F098BE18B38B08676BE4A03FDC46368E86A8CD1311F` |
| `headerSoundIds.h` | 147,281 | `B08C354A1748EF6CBB752AE8B6F47EEE6B3BE18316E6AD74690A931B15321104` |
| `headerTxtIds.h` | 5,338 | `1B8298519EDAFA5D58A5DA3B8B8031503582448CD253CEC5BD0B9B122913122D` |

## Confirmed protocol contract

`commandointerpreter.h` is the shared DB/GForce wire contract. It specifies
one-byte packing, a two-byte command ID, `SOH=0x01`, `STX=0x02`, `EOT=0x04`,
`ACK=0x06`, and a maximum command size of 1,100 bytes. Its line contract is
exactly 1,581, matching the `comInterpretVersion=1581` value accepted during
the observed INITVIDEO exchange.

Important command numbers independently match the live log parser:

| ID | Symbol | Role |
|---:|---|---|
| 34 | `VID_COM_INITVIDEO` | Initialize video contract |
| 64 | `VID_COM_COMMAND_VARIPARA` | Variable-length object subcommand |
| 65 | `VID_COM_TOUCHCLICKDOWN` | Touch input event |
| 69 | `VID_COM_REQUEST_KEY` | Application authenticity request; not replayed |
| 70 | `VID_COM_STARTUPTXT` | Startup status text |
| 73 | `VID_COM_GRAPHICCHECKSUM` | Graphics checksum query |
| 79 | `VID_COM_SYSTEMINFO` | GForce system-information query |

The command-64 structure is packed as `WORD numWerte`, `WORD objId`, one-byte
`secComId`, followed by `numWerte` bytes. Therefore its variable payload after
the length word is exactly `numWerte + 3` bytes. This confirms that the earlier
491-byte command with `numWerte=486` was structurally correct; its failure was
caused by unpaced serial delivery, not by a malformed owner record.

`DefGO.h` and `DefGOEx.h` independently name object ID 11327 as `Menue`. In the
menu-specific subcommand enum, ID 102 is `GAME_MENUE_SETACTIVEGAMES`. The
continuation log contains two command-64 records with exactly this object and
subcommand: 486 bytes (162 three-byte entries) at tick 321046 and 15 bytes
(five entries) at tick 329203. The former is therefore the large batch that
populates the active-games menu, explaining why losing bytes there leaves the
renderer without a usable game selection.

The headers describe the PC-side graphics protocol and object IDs. They do not
contain the database application's executable dispatcher or the external `D3`
value used by the 68020 loader transform, so they cannot by themselves generate
the complete live behavior of the physical database.
