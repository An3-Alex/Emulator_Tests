# CF-Image: Befund für eine reproduzierbare Einrichtung

Stand: 29.09.2026. Beide 16.139.354.112-Byte-Images wurden ausschließlich
schreibgeschützt eingehängt; QEMU war beim Vergleich der Arbeitskopie beendet.
Das separate Ausgangsimage blieb unverändert.

## Gesicherte Unterschiede

| Ort | Ausgangsimage | laufende Arbeitskopie |
| --- | --- | --- |
| `WINDOWS/system32/Cgos.dll` | Original `48070358…` | eigener Shim `16c16aab…` |
| `WINDOWS/explorer.exe` | Original `2fb4233b…` | Display-Bootstrap `ebee642d…` |
| `WINDOWS/explorer_adp_before_qxl.exe` | fehlt | gepatchter ADP-Loader `d5dd84e5…` |
| `NVRAM` und `WorkDir` | keine lokalen Proxies | `FBWFLIB.dll`, `d3d9.dll`, `irrKlang.dll`, `swiftshader_d3d9.dll` |
| QXL | nicht installiert | Treiber unter `WINDOWS/INF`, `WINDOWS/system32` und `NVRAM/qxl-driver` |

`game.exe` ist in beiden Images an beiden Programmorten unverändert
(`27c45539…`). Die Arbeitskopie enthält darüber hinaus Laufzeitlogs, SRAM,
Windows-Gerätestatus und Registry-Änderungen. Die Dateizahl stieg von 27.189
auf 27.300; diese Differenz ist **keine** Installationsliste, weil sie auch
nachträglich erzeugte Laufzeitdateien enthält.

Die aktuell lokal gebauten Dateien `Cgos.dll`, `d3d9.dll`, Display-Bootstrap
und gepatchter ADP-Loader passen per SHA-256 zu den Gegenstücken im laufenden
Image. Die aktuellen Source-Builds von `FBWFLIB.dll` und `irrKlang.dll` sind
**nicht** bytegleich mit den im bewährten Image installierten Versionen.
Diese Abweichung muss vor einer Neuinstallation funktional geprüft werden.

## Noch offen

Eine sichere Image-Vorbereitung muss eine vom Nutzer gewählte Originaldatei
zuerst kopieren, den Ausgangszustand prüfen, die benötigten eigenen Komponenten
bauen oder bereitstellen, die vom Nutzer separat bereitgestellten Grafik- und
Treiberdateien prüfen und die Gast-Registry-/PnP-Schritte reproduzieren. Die
vorhandenen experimentellen Installationsskripte binden sich an den bisherigen
Arbeitsdateipfad und sind keine portable Ein-Klick-Rezeptur. Keine dieser
Schreibschritte wurde am separaten Ausgangsimage ausgeführt.

Zur erneuten reinen Bestandsaufnahme dient
`scripts/compare_image_inventory_ro.sh`; es verwendet für beide Images
`losetup --read-only` und `ntfs-3g -o ro` und räumt seine Mounts wieder auf.
