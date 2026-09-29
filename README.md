# M90-Emulator

Dieses Projekt bringt ein eigenes Merkur-M90-CF-Image mit der dazugehörigen
Datenbank in einer lokalen QEMU-Testumgebung zum Laufen. Spiel-PC und
Datenbankprozessor werden getrennt emuliert. Ein Bedienfenster bietet die
Automatentasten, Türschalter, Service-Taste und Touch-Eingaben; ein Live-Protokoll
kann beim Start zugeschaltet werden.

## Aktueller Stand

Das vorbereitete Test-Image erreicht die Spielauswahl. Touch, Haupttasten und
Spielstart wurden im laufenden Test beobachtet. Die längere Stabilität und die
Einrichtung eines *unveränderten* CF-Images sind noch nicht abschließend
verifiziert. Derzeit ist dies eine Entwicklungsfassung, kein fertiges
Ein-Klick-Installationspaket. Eine Spielgeld-Gutschrift ist noch nicht verfügbar.

## Lokal starten

Unter Windows `Start-Emulator-UI.cmd` doppelklicken. Das Startfenster lässt
folgende eigene Dateien auswählen:

- vorbereitetes CF-Image (eine **Kopie**, nie das einzige Original),
- `Magie_90_CC4.bin`, `Loader_61640403_L5.0b_2MB.bin`,
  `FactoryReset_61640403.xc` und `M90_Las_Vegas.bin`,
- 256-Byte-EEPROM-Abbild der Zulassungskarte.

Python 3.10+ und QEMU mit `qemu-system-x86_64.exe` und
`qemu-system-m68k.exe` werden benötigt. Das Startfenster prüft die gewählten
Dateien und Programme vor dem Start. Fehlendes Python bzw. QEMU kann nach
Bestätigung über den Windows-Paketmanager installiert werden. Der Haken
„Live-Protokoll“ öffnet das Ereignisfenster; ohne Haken bleibt das Protokoll
weiterhin als lokale Logdatei erhalten. Zum Beenden das QEMU-Fenster schließen.

Wichtig: Die Oberfläche **bereitet ein frisches CF-Image noch nicht automatisch
vor**. Ein unverändertes Image nicht direkt starten, sondern erst eine Kopie
anlegen und die noch zu dokumentierende Vorbereitung durchführen. Das
Startfenster verändert die gewählten Quelldateien nicht vorab; QEMU schreibt
beim Betrieb jedoch in das ausgewählte Image.

## Private Daten und Updates

CF-Images, Datenbank-Dumps, Zulassungskarten, Logs und fremde Grafik-DLLs
gehören nicht in dieses Repository. Sie werden lokal ausgewählt und durch
`.gitignore` ausgeschlossen. In einem Git-Checkout kann das Startfenster unter
„Updates prüfen“ neue Commits aus dem privaten Repository laden, solange keine
lokalen Quellcode-Änderungen vorliegen. Danach das Startfenster neu öffnen.
Für einen zweiten PC ist GitHub-Anmeldung erforderlich. Ein Update-Verfahren
für ein künftiges einzelnes EXE-Paket fehlt noch.

## Hintergründe

Die Datenbank ist kein bloßer Antwort-Stub: der Motorola-68k-Code läuft in
QEMU; die Host-Brücke bildet die serielle Verbindung und das Board-I/O nach.
Die virtuelle Uhr wird für das M90-Setup auf 2012 gesetzt. Originaltreue
bei Münzprüfer, Auszahlung, Ton und zweitem Monitor ist noch Gegenstand der
Tests. Technische Untersuchungen stehen in [`docs/`](docs/); ältere,
teilweise überholte Notizen liegen in
[`docs/project-background.md`](docs/project-background.md).

Offline-Tests: `python -m unittest discover -s tests -p "test_*.py" -q`.
