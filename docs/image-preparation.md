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

## Automatische Vorbereitung (neuer Teststand)

`scripts/prepare_image_stage.sh` hängt das Original ausschließlich lesend ein,
prüft Größe und unveränderte Schlüsseldateien und erstellt eine neue Kopie.
Nur in dieser Kopie werden eigene Shim-/Proxy-Dateien, der gepatchte Loader,
die vom Nutzer bereitgestellten QXL-/SwiftShader-Dateien und der temporäre
QXL-Installer eingesetzt. Der FBWF-Startwert wird nur in der Kopie vorläufig
geändert. Ein unvollständiger Kopiervorgang bleibt als `.m90-partial` sichtbar
und wird nie stillschweigend überschrieben.

Der temporäre Windows-XP-Gast registriert die beiden QXL-Geräte per SetupAPI.
Der verwendete Treiber ist nicht signiert; nach ausdrücklicher Bestätigung im
Starter werden die dazugehörigen XP-Dialoge nur für diesen ausgewählten
Treiber im Setup-Gast bestätigt. Der Installer sendet seinen Erfolg über COM1.
Nach sauberem Gast-Shutdown startet ein zweiter Gastlauf und prüft, ob Windows
beide QXL-Anzeigen tatsächlich erkennt und den zweiten Bildschirm aktivieren
kann. Falls SetupAPI zwar Erfolg meldet, die Anzeigen aber noch inaktiv sind,
wiederholt der Starter die QXL-Installation einmal und prüft erneut. Erst nach
erfolgreicher Anzeigeprüfung stellt `scripts/finalize_image_stage.sh` FBWF
zurück und aktiviert den normalen Display-Bootstrap. Bereits vorbereitete
Kopien ohne diesen Nachweis werden bei erneuter Einrichtung nachgeprüft.
`scripts/check_image_stage.sh` liest den Status schreibgeschützt aus.

Der Fall trat am 30.09.2026 bei einer frischen Kopie auf: Zwei QXL-Geräte waren
im Gerätemanager installiert und gestartet, aber `EnumDisplayDevices` meldete
für `DISPLAY1/2` leere Beschreibungen und keine Desktop-Anbindung. Nach der
zweiten Geräteinstallation erkannte der Prüfstart beide Monitore; die Kopie
wurde erst dann als `ready` abgeschlossen.

Der komplette Kopier-/QXL-/Abschlussweg wurde mit einer separaten Testkopie
des Ausgangsimages erfolgreich ausgeführt; der Status war danach `ready`.
Der anschließende Start aus dieser frischen Kopie erreichte nach zwei
`INITVIDEO`-Phasen die Spielauswahl mit Spielkacheln. Nach der ersten
Touch-Eingabe wurden beide Bildschirme schwarz, während die QEMU-Prozesse und
das Datenbanklog weiterliefen. Ein zweiter Start derselben Kopie prüft, ob
die erstmalige Einrichtung des zweiten QXL-Bildschirms beteiligt war.
Dieser zweite Start blieb stattdessen bei `FOUL PR`: der SCC-B-Watchpoint las
für zwei vollständige aufeinanderfolgende Prüferbytes denselben Restzähler
und verwarf deshalb fälschlich die Challenge. Ein Regressionstest mit den
konkreten neun Bytes und die korrigierte Zähler-Auswertung beseitigten diesen
Fehler im dritten Start. Dort öffnete ein Touch auf „African Cash“ das Spiel,
allerdings erst nach mehreren Minuten. Der untere Monitor blieb schwarz.
Interaktive Bedienung über längere Zeit und dauerhafte Stabilität sind nicht
bestätigt; die Ursache des ersten Schwarzbilds ist noch nicht bewiesen.
Die aktuellen Source-Builds von `FBWFLIB.dll` und `irrKlang.dll` unterscheiden
sich weiterhin von der zuvor funktionierenden Arbeitskopie und brauchen den
vollen Laufzeittest.

Zur erneuten reinen Bestandsaufnahme dient
`scripts/compare_image_inventory_ro.sh`; es verwendet für beide Images
`losetup --read-only` und `ntfs-3g -o ro` und räumt seine Mounts wieder auf.
