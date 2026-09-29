# M90-Emulator

Dieses Projekt bringt ein eigenes Merkur-M90-CF-Image mit der dazugehörigen
Datenbank in einer lokalen QEMU-Testumgebung zum Laufen. Spiel-PC und
Datenbankprozessor werden getrennt emuliert. Ein Bedienfenster bietet die
Automatentasten, Türschalter, Service-Taste und Touch-Eingaben; ein Live-Protokoll
kann beim Start zugeschaltet werden.

## Aktueller Stand

Das bisher vorbereitete Test-Image erreicht die Spielauswahl. Touch,
Haupttasten und Spielstart wurden im laufenden Test beobachtet. Für ein frisches
Image gibt es nun eine automatische Einrichtung mit eigener Arbeitskopie;
Kopieren, QXL-Treiberinstallation und Abschluss wurden an einer frischen Kopie
getestet. Diese Kopie hat nach beiden `INITVIDEO`-Phasen die Spielauswahl mit
Spielkacheln angezeigt. Beim ersten Versuch blieb der Bildschirm nach einer
Touch-Eingabe schwarz; ein weiterer Lauf deckte einen verlorenen
Münzprüfer-Handshake auf. Nach Korrektur dieses Protokollfehlers erreichte
dieselbe Kopie erneut die Spielauswahl und lud „African Cash“ per Touch.
Das Laden dauerte allerdings mehrere Minuten, der zweite Bildschirm blieb
schwarz, und Langzeitstabilität ist noch nicht bestätigt. Eine
Spielgeld-Gutschrift ist noch nicht verfügbar. Gegen doppelte bzw. hängen
bleibende Touch-Eingaben gibt es einen Offline-getesteten Fix; dessen Wirkung
im laufenden Spiel ist noch nicht bestätigt.

## Lokal starten

Unter Windows `Start-Emulator-UI.cmd` doppelklicken oder die `M90-Emulator.exe`
aus dem privaten GitHub-Release starten. Das Startfenster fragt nach:

- eigenem Original-CF-Image und einem **neuen Dateinamen für die Arbeitskopie**,
- `Magie_90_CC4.bin`, `Loader_61640403_L5.0b_2MB.bin`,
  `FactoryReset_61640403.xc` und `M90_Las_Vegas.bin`,
- M90-Zulassungskarten-EEPROM (256 Byte),
- eigenem SwiftShader-5003-DLL und QXL-Treiberordner.

Auf „Frisches Image einrichten“ klicken und warten. Der Starter prüft die
Dateien, kopiert das Image, installiert QXL automatisch in einem temporären
Windows-Gast und gibt die Kopie erst nach Log-/Registry-Prüfung frei. Das
Original bleibt unverändert. Ein unterbrochener Treiberlauf kann mit derselben
Arbeitskopie fortgesetzt werden. Erst danach „Emulator starten“ wählen. Der
Haken „Live-Protokoll“ öffnet optional das Ereignisfenster; die Logdatei wird
auch ohne Haken geschrieben.

Benötigt werden Windows 10/11, Python 3.10+, QEMU (x86 und m68k), WSL/Ubuntu
mit `ntfs-3g` und `python3-hivex` sowie genug Platz für die etwa 16-GB-Kopie.
Die Oberfläche bietet Installationshilfen für QEMU, Python und WSL. Eine
Ubuntu-Ersteinrichtung oder ein Windows-Neustart kann einmalig nötig sein.
QEMU schreibt beim Spielen nur in die gewählte Arbeitskopie.

## Private Daten und Updates

CF-Images, Datenbank-Dumps, Zulassungskarten, Logs und fremde Grafik-DLLs
gehören nicht in dieses Repository. Sie werden lokal ausgewählt und durch
`.gitignore` ausgeschlossen. Updates des Quellcodes gehen einfach per `git pull`
im Projektordner. Für die einzelne Start-EXE lädt man bei einer neuen Version
die neue Datei aus den privaten GitHub-Releases herunter; automatische Updates
gibt es derzeit nicht. Auf einem zweiten PC ist GitHub-Zugriff auf das private
Repository nötig.

Eine einzelne Start-EXE lässt sich lokal mit `build-launcher-exe.ps1` erzeugen
(Build-Abhängigkeit: PyInstaller und zuvor gebaute Eigenkomponenten). Sie legt
unsere Laufzeitdateien im lokalen App-Datenordner ab. Images, Dumps und fremde
Grafik-/Treiberdateien werden nicht eingebettet. Die frische Image-Einrichtung
ist neu und noch nicht als vollständig stabiler Endnutzer-Installer freigegeben.

## Hintergründe

Die Datenbank ist kein bloßer Antwort-Stub: der Motorola-68k-Code läuft in
QEMU; die Host-Brücke bildet die serielle Verbindung und das Board-I/O nach.
Die virtuelle Uhr wird für das M90-Setup auf 2012 gesetzt. Originaltreue
bei Münzprüfer, Auszahlung, Ton und zweitem Monitor ist noch Gegenstand der
Tests. Der schreibgeschützte [Vergleich von Original und Arbeitskopie](docs/image-preparation.md)
ist dokumentiert. Weitere technische Untersuchungen stehen in [`docs/`](docs/); ältere,
teilweise überholte Notizen liegen in
[`docs/project-background.md`](docs/project-background.md).

Offline-Tests: `python -m unittest discover -s tests -p "test_*.py" -q`.
