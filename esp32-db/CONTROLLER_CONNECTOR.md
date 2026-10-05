# Controller-side database connector (Steuereinheit)

Source: the owner's `Steuereinheit.kicad_sch`, exported with KiCad 10 as an
XML netlist and compared contact-by-contact with the owner's
`DB1MB.kicad_sch`. These are **schematic nets**, not measured levels or proof
that the actual 2 MB board has the same internal circuit. On the controller,
the database mates with DIN41612 **J4 and J5**. Controller J8 is the coin
unit (`Muenzeinheit`) and controller J9 is the acceptor; neither is a
database connector. The older database-board drawing calls its two mating
connectors **J8 and J9**.

## Contact orientation

The named data and serial nets match with the same `a/b/c` row and a reversed
position number: controller `J4` to database `J8`, controller `J5` to
database `J9`, and controller position `n` to database position `11-n`.
For example, controller `J4:b8 /DB_RX` meets database `J8:b3 cTXD`;
controller `J4:c7 /DB_TX` meets database `J8:c4 cRXD`. This is a
schematic-netlist deduction, not a substitute for checking the physical
connector orientation before making an adapter.

| Controller contact | Controller net | 1 MB database contact | 1 MB database net |
| --- | --- | --- | --- |
| J4:a1 | +5V | J8:a10 | VDD |
| J4:a2 | DBIO8 | J8:a9 | DBIO8 |
| J4:a3 | DBIO11 | J8:a8 | DBIO11 |
| J4:a4 | GND | J8:a7 | GND |
| J4:a5 | unconnected | J8:a6 | cPQS2 |
| J4:a6 | SDA/PFO | J8:a5 | cPF1 |
| J4:a7 | +5V | J8:a4 | unconnected |
| J4:a8 | INTRn | J8:a3 | cPF5 |
| J4:a9 | cA21 | J8:a2 | buffered A21 (inferred) |
| J4:a10 | GND | J8:a1 | GND |
| J4:b1 | +5V | J8:b10 | unconnected |
| J4:b2 | DBIO9 | J8:b9 | DBIO9 |
| J4:b3 | DBIO12 | J8:b8 | DBIO12 |
| J4:b4 | DBIO14 | J8:b7 | DBIO14 |
| J4:b5 | BOOTMODE | J8:b6 | cPQS0 |
| J4:b6 | Tuerkontakt | J8:b5 | RN2 to unconnected pad in 1 MB drawing |
| J4:b7 | unconnected | J8:b4 | PF4 via RN2 (inferred) |
| J4:b8 | DB_RX | J8:b3 | cTXD |
| J4:b9 | cA22 | J8:b2 | cA22 |
| J4:b10 | GND | J8:b1 | cPQS5 via RN1 |
| J4:c1 | +5V | J8:c10 | cA20 via RN3 |
| J4:c2 | DBIO10 | J8:c9 | DBIO10 |
| J4:c3 | DBIO13 | J8:c8 | DBIO13 |
| J4:c4 | DBIO15 | J8:c7 | DBIO15 |
| J4:c5 | unconnected | J8:c6 | cPQS1 |
| J4:c6 | Y1 | J8:c5 | unconnected |
| J4:c7 | DB_TX | J8:c4 | cRXD |
| J4:c8 | IntACKn | J8:c3 | buffered FC2 (inferred) |
| J4:c9 | cA23 | J8:c2 | cA23 |
| J4:c10 | GND | J8:c1 | cPQS6 via RN1 |
| J5:a1 | +5V | J9:a10 | VDD |
| J5:a2 | DBIO0 | J9:a9 | DBIO0 |
| J5:a3 | DBIO3 | J9:a8 | DBIO3 |
| J5:a4 | GND | J9:a7 | GND |
| J5:a5 | R/W | J9:a6 | cRW |
| J5:a6 | cEXTCLK | J9:a5 | cCLKOUT |
| J5:a7 | +5V | J9:a4 | unconnected |
| J5:a8 | ADDRIO3 | J9:a3 | cA2 |
| J5:a9 | ADDRIO6 | J9:a2 | cA6 |
| J5:a10 | GND | J9:a1 | GND |
| J5:b1 | +5V | J9:b10 | cPF2 via RN10 |
| J5:b2 | DBIO1 | J9:b9 | DBIO1 |
| J5:b3 | DBIO4 | J9:b8 | DBIO4 |
| J5:b4 | DBIO6 | J9:b7 | DBIO6 |
| J5:b5 | IODTACK | J9:b6 | cPE1 |
| J5:b6 | CEACRT | J9:b5 | unconnected |
| J5:b7 | ADDRIO1 | J9:b4 | cA0 |
| J5:b8 | ADDRIO4 | J9:b3 | cA3 |
| J5:b9 | ADDRIO7 | J9:b2 | cA7 |
| J5:b10 | GND | J9:b1 | cPQS3 via RN11 |
| J5:c1 | +5V | J9:c10 | cPF3 via RN10 |
| J5:c2 | DBIO2 | J9:c9 | DBIO2 |
| J5:c3 | DBIO5 | J9:c8 | DBIO5 |
| J5:c4 | DBIO7 | J9:c7 | DBIO7 |
| J5:c5 | RESET | J9:c6 | cRESET |
| J5:c6 | EXTRES | J9:c5 | c12V via R7 |
| J5:c7 | ADDRIO2 | J9:c4 | cA1 |
| J5:c8 | ADDRIO5 | J9:c3 | cA5 |
| J5:c9 | ADDRIO8 | J9:c2 | cA8 |
| J5:c10 | GND | J9:c1 | cPQS4 via RN12 |

The address labels do not all retain the same number across the pair
(`ADDRIO1` meets `cA0`, for example). Preserve contact identity instead of
renaming or shifting bits by assumption. `+5V` and `GND` on the controller
side also meet several buffered Motorola input nets on the 1 MB board;
these may be intentional straps through resistor networks. They are **not**
permission to tie those pins directly to an ESP32 or assume the 2 MB board
uses them identically. In particular `J5:c6 EXTRES` meets the 1 MB board's
`c12V` sense circuit; never treat that contact as an ESP32 supply.

## What the controller does with the bus

Controller U15 is a `68C681`: its `D0..D7` pins are on `DBIO0..7`, its
`A1..A4` pins are on `ADDRIO1..4`, and its interrupt and acknowledge pins
reach `J4:a8` and `J4:c8`. U17 (`74HC138`) decodes `ADDRIO6..8` and enables
the UART, IO, YM sound and DAC sections. U1/U5 (`74HC573`/`74HC574`) latch
the lower eight data bits as outputs; U10 (`74HC245`) places external input
states, including `OutEMP` from controller coin-unit J8:5 and `OutEMP2`
from J14:5, onto that lower data bus. The controller therefore contains the
actual coin/peripheral interface logic; the database CPU accesses it over
the parallel bus rather than all coin lines appearing as separate database
connector contacts. `DBIO8` additionally reaches U12 (`74HC125`), while
`DBIO9..15` have only the database connector as a named endpoint in this
exported controller netlist.

The door contact appears at controller `J4:b6` and at door connectors J1/J21.
In the *1 MB database schematic*, the mating J8:b5 stops at an unconnected
resistor-network pad. This is a real discrepancy to resolve on the actual
2 MB board, not proof that the door is irrelevant. The same schematic has
other deliberate/unresolved differences at connector contacts; the
controller drawing alone cannot establish 2 MB continuity, voltage levels,
bus turnaround timing or whether an ESP32 can service it without external
programmable logic. For now the safe USB-to-QEMU experiment needs no
cabinet connection; a physical drop-in adapter should remain unconnected
until those measurements and a bus front end exist.
