# M90-Emulator

Dieses Projekt bringt ein eigenes Merkur-M90-CF-Image mit der dazugehörigen
Datenbank in einem lokalen QEMU-Emulator zum Laufen. Spiel-PC und
Datenbankprozessor werden getrennt emuliert. Ein Bedienfenster bietet die
Automatentasten, Türschalter, Service-Taste und Touch-Eingaben; ein Live-Protokoll
kann beim Start zugeschaltet werden.

## Funktionen

Der Starter erstellt aus dem eigenen CF-Image eine getrennte Arbeitskopie,
richtet den QXL-Grafiktreiber ein und startet danach den Spiel-PC zusammen mit
der emulierten Datenbank. Touch, die fünf Spieltasten, Auszahlungs- und
Service-Taste sowie der Türschalter sind im Bedienfenster erreichbar. Das
Ereignisprotokoll lässt sich beim Start optional öffnen.

Der Spielstart kann mehrere Minuten dauern. Eine Spielgeld-Gutschrift ist
derzeit nicht verfügbar; Auszahlungsgeräte und der obere Bildschirm sind
noch nicht vollständig nachgebildet.
Ton wird über eine virtuelle AC’97-Soundkarte und einen passenden SigmaTel-
XP-Treiber angebunden. Wie beim einzelnen Lautsprecher im Original wird der
Hostausgang in Mono betrieben. Im Starter lässt sich die Ausgabe stummschalten.

## Änderungen

- 0.1.10: Automatische SigmaTel-Audioeinrichtung in der Arbeitskopie mit
  Sicherung des bisherigen Starters und der Registry. Der abstürzende Realtek-
  Treiber wird deaktiviert; XP registriert den passenden Audioausgang vor dem
  Spielstart. Die Hostausgabe verwendet einen Monokanal.
- 0.1.9: Host-Ton verwendet SDL ohne Aufnahme statt DirectSound. Fehlende
  Mikrofon-/Aufnahmegeräte verhindern dadurch nicht mehr den QEMU-Start.
  Sofortige Startabbrüche erscheinen mit QEMUs konkreter Fehlermeldung;
  Datenbank und Bedienfenster werden dann nicht zusätzlich gestartet.
- 0.1.8: Sound verwendet AC’97 und WinMM statt des Null-Treibers, damit das
  Spiel echte Soundobjekte erhält. Die Audio-Anbindung wartet bei der ersten
  Geräteerkennung bis zu zwei Minuten auf eine XP-Audioausgabe und schreibt
  den Zustand ins Audio-Protokoll. Bestehende Arbeitskopien erhalten die neue
  Audio-DLL beim nächsten Start mit Sicherung der bisherigen Version.
  „Ton auf dem PC ausgeben“ schaltet nur die Host-Ausgabe um; die virtuelle
  Soundkarte bleibt auch im stummen Betrieb aktiv.
- 0.1.7: Neuer Tab „Emulationseinstellungen“ für Spiel-PC und Datenbank:
  RAM, virtuelle CPUs, WHPX/TCG, QXL-Speicher, Maus und Anzeigezuordnung,
  Instruktionslimit, TCG-Modus, Laufabschnitte, DUART-Takt, RTC-Startdatum,
  Verbindungswartezeit, Türzustand sowie Protokoll- und Bedienfenster.
  Werte werden geprüft und bleiben gespeichert; Standardwerte lassen sich
  ohne Änderung der Dateipfade wiederherstellen.
- 0.1.6: Die QXL-Wiederholung akzeptiert die aktuelle und die vorherige
  Anzeigeprüfung. Unterbrochene Einrichtungen lassen sich mit derselben
  Arbeitskopie fortsetzen. Fehlermeldungen zeigen zusätzlich die konkrete
  Ursache des fehlgeschlagenen Vorbereitungsschritts.
- 0.1.5: Der Datenbank-Timer folgt Preset und Interruptvektor der CC4-Firmware
  statt eines fest angenommenen 10-ms-Taktes. Beide Monitor-Ausgaben werden
  unabhängig vom gemeinsamen Software-Renderer zugeordnet; diese Zuordnung
  bleibt auch bei erneuter Video-Initialisierung erhalten. Windows richtet
  beide Anzeigen auf 32-Bit-Farbe ein. Der Starter aktualisiert eigene
  Grafikdateien in bestehenden Arbeitskopien mit Sicherung der vorherigen Dateien.
- 0.1.4: Die Bildschirmzuordnung lässt sich im Starter dauerhaft tauschen.
  Spielmenü und Touch-Vorschau verwenden dabei denselben unteren Ausgang.
  Touch erzeugt keine künstlichen Mehrfach-Downs oder verlängerten Klicks mehr:
  einmal drücken, bei Bewegung ziehen, beim Loslassen freigeben.
- 0.1.3: Windows-Pfade werden bei der Image-Einrichtung ohne Shell-Umdeutung
  an WSL übergeben. Ordner mit Leerzeichen bleiben erhalten; WSL-Probleme
  zeigen die konkrete Fehlermeldung und Hinweise zur Ubuntu-Einrichtung.
- 0.1.2: Die Image-Einrichtung aktiviert beide QXL-Anzeigen und wiederholt die
  Treiberinstallation bei Bedarf. Bereits eingerichtete Images lassen sich im
  Startfenster fortsetzen. `lower` bezeichnet nun den unteren Automatenbildschirm,
  `upper` den oberen.
- 0.1.1: Das Startfenster zeigt Phase und verstrichene Zeit der Image-Einrichtung.

## Lokal starten

Unter Windows `Start-Emulator-UI.cmd` doppelklicken oder die `M90-Emulator.exe`
aus dem privaten GitHub-Release starten. Die Kurzanleitung im Fenster zeigt
die Reihenfolge: Abhängigkeiten prüfen, eigene Dateien auswählen, frisches
Image einrichten, danach prüfen und starten. Das Startfenster fragt nach:

- eigenem Original-CF-Image und einem **neuen Dateinamen für die Arbeitskopie**,
- `Magie_90_CC4.bin`, `Loader_61640403_L5.0b_2MB.bin`,
  `FactoryReset_61640403.xc` und `M90_Las_Vegas.bin`,
- M90-Zulassungskarten-EEPROM (256 Byte),
- eigenem SwiftShader-5003-DLL und QXL-Treiberordner.

Auf „Frisches Image einrichten“ klicken und warten. Phase und verstrichene
Zeit werden im Startfenster angezeigt; eine feste Restzeit lässt sich bei der
großen Image-Kopie und Windows-Gastinstallation nicht verlässlich angeben.
Der Starter prüft die
Dateien, kopiert das Image, installiert QXL automatisch in einem temporären
Windows-Gast und gibt die Kopie erst frei, wenn Windows beide Anzeigen wirklich
erkennt. Bei Bedarf wird die Treiberinstallation einmal wiederholt. Das
passende Audio-Treiberpaket wird einmalig von Dell geladen und danach in der
Arbeitskopie eingerichtet; hierfür wird Internetzugang benötigt. Das
Original bleibt unverändert. Ein unterbrochener Treiberlauf kann mit derselben
Arbeitskopie fortgesetzt werden. Erst danach „Emulator starten“ wählen. Der
Haken „Live-Protokoll“ öffnet optional das Ereignisfenster; die Logdatei wird
auch ohne Haken geschrieben.

Im Tab „Emulationseinstellungen“ Optionen ändern und „Einstellungen speichern“
wählen. Änderungen gelten ab dem nächsten Start; der temporäre Gast zur
Image-Einrichtung verwendet weiterhin seine festen Einstellungen. Dateipfade
und Optionen liegen unter `%APPDATA%\M90 Emulator\settings.json`.
Empfohlen bleiben 2048 MiB, eine CPU, WHPX, icount shift 6 und der stabile
Einzelinstruktionsmodus. Shift 5 und größere TCG-Blöcke können die bekannten
Interrupt-Abstürze auslösen; Diagnose-Watchpoints können erheblich bremsen.
„Emulations-Standardwerte“ setzt nur Optionen zurück, nicht die ausgewählten
Dateien. Board-Adressen, lokale Schnittstellen und Firmware-SRAM bleiben fest.

Benötigt werden Windows 10/11, Python 3.10+, QEMU (x86 und m68k), WSL/Ubuntu
mit `ntfs-3g` und `python3-hivex` sowie genug Platz für die etwa 16-GB-Kopie.
Die Oberfläche bietet Installationshilfen für QEMU, Python und WSL. Eine
Ubuntu-Ersteinrichtung oder ein Windows-Neustart kann einmalig nötig sein.
QEMU schreibt beim Spielen nur in die gewählte Arbeitskopie.
Der QEMU-Reiter `lower` ist der untere Bildschirm des echten Automaten;
`upper` ist der obere.
Standardmäßig ist die primäre Windows-Anzeige `upper`, die zweite Anzeige mit
dem Spielmenü und der Touch-Vorschau `lower`. „Bildschirme tauschen“ ist für
abweichende Images gedacht. Wurde der Haken früher als Behelf gesetzt, zunächst
deaktivieren. Der Bootbildschirm kann auf dem anderen Ausgang liegen als das
spätere Spielmenü. Vor einem Grafik-Update müssen alle QEMU-Instanzen geschlossen
sein. Die ersetzten Dateien liegen in der Arbeitskopie unter
`NVRAM/m90-graphics-backups`; das Original wird dafür nicht eingehängt.

## Private Daten und Updates

CF-Images, Datenbank-Dumps, Zulassungskarten, Logs und fremde Grafik-DLLs
gehören nicht in dieses Repository. Sie werden lokal ausgewählt und durch
`.gitignore` ausgeschlossen.

Eine einzelne Start-EXE lässt sich lokal mit `build-launcher-exe.ps1` erzeugen
(Build-Abhängigkeit: PyInstaller und zuvor gebaute Eigenkomponenten). Sie legt
unsere Laufzeitdateien im lokalen App-Datenordner ab. Images, Dumps und fremde
Grafik-/Treiberdateien werden nicht eingebettet.

## Hintergründe

Die Datenbank ist kein bloßer Antwort-Stub: der Motorola-68k-Code läuft in
QEMU; die Host-Brücke bildet die serielle Verbindung und das Board-I/O nach.
Der DUART-Timer verwendet zunächst einen konfigurierbaren Eingangstakt von
3,6864 MHz; der tatsächliche Quarz der Originalplatine ist noch nicht bestätigt.
Die virtuelle Uhr wird für das M90-Setup auf 2012 gesetzt. Details zur
[Image-Einrichtung](docs/image-preparation.md) stehen in der Dokumentation; ältere,
teilweise überholte Notizen liegen in
[`docs/project-background.md`](docs/project-background.md).
