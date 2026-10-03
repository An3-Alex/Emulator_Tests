# M90-Emulator

Windows-Starter für die Emulation eines Merkur-M90-Automaten mit eigenen
CF-Images und Datenbankdateien. QEMU emuliert den Spiel-PC und den
Motorola-68k-Datenbankprozessor getrennt. Die Einrichtung, der Start und die
Bedienung erfolgen über eine grafische Oberfläche.

CF-Images, Datenbankdateien und fremde Software werden nicht mitgeliefert.

## Funktionen

- Automatische Einrichtung einer getrennten Arbeitskopie des CF-Images,
  einschließlich QXL-Grafiktreiber und PCM-Audio-Bridge.
- Bedienfenster für Touch, Menü, Autostart, Einsatz, Maximaleinsatz, Start,
  Auszahlung, Service-Taste, Türschalter und „+1 € (MP)“ für Spielgeld.
- Optionales Live-Protokoll der Datenbankkommunikation.
- Gespeicherte Dateipfade und Einstellungen für Spiel-PC und Datenbank.
- „Alles beenden“ fährt den Spiel-PC herunter und beendet die zu diesem Lauf
  gehörenden Prozesse. Reagiert XP nicht, wird nur der eigene Prozessverbund
  beendet. Beim Schließen des Starters werden diese Prozesse ebenfalls beendet.

## Audio

Der Starter verwendet ausschließlich die PCM-Bridge: Die originale
irrKlang-Bibliothek liefert Audiodaten über COM1 an einen Windows-Audiohelfer.
Eine virtuelle Soundkarte und zusätzliche XP-Audiotreiber werden nicht
installiert. Alte AC’97-Einstellungen werden beim Laden auf Bridge umgestellt;
die ausgewählten Dateien bleiben erhalten.

Die Ausgabe verwendet Mono, entsprechend dem einzelnen Lautsprecher im
Originalautomaten. „Ton auf dem PC ausgeben“ schaltet die Hostausgabe ein oder
stumm; die Bridge bleibt auch im stummen Betrieb aktiv.

Der Audiohelfer startet vor QEMU und endet mit dem Datenbank-Lauf.
Stille wird im Gast mit ihrer ursprünglichen Dauer abgearbeitet, ohne Leerdaten
zu übertragen. Der Tonkanal öffnet sich erst beim ersten nichtstummen Puffer.
Mono-Mischung im Gast und ein schnellerer Audio-UART reduzieren den
Übertragungsaufwand; die Datenbankverbindung auf COM3 bleibt unverändert.
Flüssige Echtzeitwiedergabe über diesen seriellen Ausgabeweg ist noch nicht
gewährleistet. Details stehen unter [Audio-Anbindung](docs/audio.md).

## Bekannte Einschränkungen

- Der Spielstart kann mehrere Minuten dauern.
- Bei hoher Hostlast kann die Board-Verarbeitung weiter hinter Echtzeit
  zurückbleiben; begrenzte Timer-Pakete verhindern eine wachsende Aufholschlange.
- Bei frischen CF-Images kann der Start nach dem ersten XP-Neustart bei
  INITVIDEO stehen bleiben. Bis zur abgeschlossenen Initialisierung ist die
  Münzannahme nicht verfügbar.
- „+1 € (MP)“ benötigt abgeschlossene Initialisierung und freigegebene
  Münzannahme. Der virtuelle Prüfer sendet ein Münztelegramm; die Datenbank
  prüft und bucht es selbst. Das Live-Protokoll unterscheidet vorgemerkte,
  gesendete und gebuchte Einwürfe. Nicht gesendete Einwürfe verfallen nach
  30 Sekunden; bereits gesendete werden nicht automatisch wiederholt.
- Auszahlungsgeräte und der obere Bildschirm sind noch nicht vollständig
  nachgebildet.
- Andere Spielepakete können ausgewählt werden, benötigen aber kompatible
  Firmware, Konfiguration und Spiel-PC-Komponenten. Die Dateiauswahl allein
  bedeutet keine Laufzeitkompatibilität.

## Änderungen

- 0.1.15: Start ohne zusätzliche Arbeitskopie-Checkbox. Der
  Vorbereitungsstatus des gewählten Images wird weiterhin automatisch geprüft.
  INITVIDEO verwendet die eingestellte Datenbank-Uhrzeit auch
  bei Wiederholungen. Das Live-Protokoll zeigt die tatsächlich gesendete Zeit
  und das konfigurierte RTC-Startdatum statt einer festen 2012-Beschriftung.
  Begrenzte Eingabewarteschlange mit reserviertem Loslassen.
  Tastenklicks werden einzeln beim Board-Scan übergeben; schnelle Folgeklicks
  überschreiben keine laufende Betätigung. Kurze Tastendrücke bleiben bis zu
  einem vollständigen Hauptprogramm-Laufabschnitt aktiv und können nicht
  innerhalb eines Interrupt-Bündels verschwinden. Überholte Touch-Bewegungen werden
  zusammengefasst. Türzustände werden beim Board-Zugriff übernommen und im
  Live-Protokoll getrennt als vorgemerkt und übergeben angezeigt.
  Tastenstände werden pro Board-Scan gemeinsam übertragen, statt jeden
  Kontakt einzeln über die Debug-Verbindung abzufragen.
  Registeränderungen für Interrupts werden gemeinsam übertragen; auf einer
  leeren seriellen Verbindung entfallen unnötige UART-Interrupts. Der Board-Timer
  verwendet eine monotone Zeitbasis mit begrenztem Rückstand. Höchstens vier
  Timer-Interrupts werden gebündelt, danach erhält das Hauptprogramm wieder
  Rechenzeit für Tasten und Rückmeldungen. Lange Host-Unterbrechungen erzeugen
  keine unbegrenzte Aufholwarteschlange. Firmware-Wartezähler werden nicht
  vorzeitig auf null gesetzt. Die CPU-Instruktionsgrenze bleibt unverändert.
  Die Datenbank-Zeitscheibe wird auch bei häufigen Hardwarezugriffen vollständig
  abgearbeitet; serielle Verarbeitung lässt fällige Board-Ticks nicht dauerhaft
  warten. Der virtuelle Münzprüfer meldet authentifizierte Euro-Kanalwerte,
  bestätigt Routing- und Annahmebefehle und berücksichtigt die gesendeten
  Kanalfreigaben. Bei wartenden Einwürfen zeigt das Log die einzelnen Sperrflags.
  Geräteprofile mit Münzeingangssignal erhalten vor dem Münztelegramm einen
  begrenzten Eingangspuls. Die originale Firmware öffnet das Annahmefenster;
  Guthaben und Freigabevariablen werden nicht durch die Bridge gesetzt.
  Aktivierung und Loslassen des Münzeingangs erscheinen im Live-Protokoll.
  Der ausgewählte originale Factory-Code wird vor dem Programmieren des frischen
  DB-RAMs ausgeführt, einschließlich seines nativen Initialisierungsmarkers.
- 0.1.14: Bedienfeld um „+1 € (MP)“ erweitert. Ein-Euro-Spielgeldeinwurf
  über den virtuellen Münzprüfer,
  mit aktiver Kanalzuordnung, Annahmesperre, Münzauthentifizierung und
  getrennten Protokolleinträgen für Übertragung und Buchung.
- 0.1.13: ausschließliche
  PCM-Bridge mit Monowiedergabe, lokal abgearbeiteter Stille, schnellerem
  Audio-UART, begrenzten Puffern und eigener Prozessverwaltung. Der zusätzliche
  Audiotreiber-Installer entfällt. Oberfläche und Dokumentation verwenden
  neutrale Texte ohne persönliche Rechner- oder Benutzerpfadbezüge.
  „Alles beenden“ beendet den zugehörigen Prozessverbund. Der Datenbank-Laufabschnitt
  ist standardmäßig 0,01 Sekunden; vorhandene Einstellungen bleiben erhalten.
  Leere DUART-Empfangspuffer melden wieder den korrekten Status.
  QXL-Abschlussmeldungen warten auf den verfügbaren Gast-Port. Unterbrochene
  Einrichtungen erhalten beim Fortsetzen den aktualisierten Helfer.
- 0.1.12: XP-Audiobasis wird ohne virtuelle Soundkarte eingerichtet. Danach
  folgen die getrennte SigmaTel-Installation und Ausgabeprüfung. Die alten
  Realtek-Treiberdateien werden aus den aktiven Pfaden genommen und mit
  Wiederherstellungsmanifest gesichert. Abgebrochene Audio-Einrichtungen
  früherer Versionen können mit derselben Arbeitskopie fortgesetzt werden.
- 0.1.11: Audio-Einrichtung mit sichtbarem XP-Fenster und frühzeitig
  parallel laufendem Dialog-Helfer. Bei Fehlern werden Bildschirmbilder und
  Installationsprotokolle automatisch lokal gesichert. Unterbrochene
  Audio-Einrichtungen können mit derselben Arbeitskopie fortgesetzt werden.
  Die feste CF-Dateigröße, feste Partitionsgrenzen und die M90-Datei-Whitelist
  entfallen. Eigene Pakete wie M88 können ausgewählt werden; die bekannte
  CC4-Hardwareanbindung und die Formatprüfungen gelten weiterhin.
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

## Einrichtung und Start

Die [M90-Emulator.exe aus Release 0.1.15 herunterladen](https://github.com/An3-Alex/Emulator_Tests/releases/download/v0.1.15/M90-Emulator.exe)
oder die [Release-Seite öffnen](https://github.com/An3-Alex/Emulator_Tests/releases/tag/v0.1.15).
Änderungen aus dem Entwicklungsstand `main` sind erst nach einem neuen Build
in der EXE enthalten.

CF-Images werden unabhängig von ihrer Kapazität akzeptiert. Bei der Einrichtung
wird die NTFS-Partition aus dem MBR einschließlich logischer Partitionen
ermittelt; ein reines NTFS-Volume ist ebenfalls möglich. Mehrere NTFS-Volumes
werden nicht automatisch geraten. GPT und andere Dateisysteme sind im
XP-Profil noch nicht unterstützt. Die Arbeitskopie muss weiterhin vom Original
getrennt sein. Die Auswahl anderer Pakete wie M88 bedeutet noch keine bestätigte
Laufzeitkompatibilität: Boot-ROM-Kontext, Firmware-Struktur, Config-Code und
die anzupassenden XP-/Spielkomponenten müssen zur bisherigen Anbindung passen.

Unter Windows `Start-Emulator-UI.cmd` doppelklicken oder die `M90-Emulator.exe`
aus dem GitHub-Release starten. Die Kurzanleitung im Fenster zeigt
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
erkennt. Bei Bedarf wird die Treiberinstallation einmal wiederholt.
Audio wird über die PCM-Bridge eingerichtet, ohne zusätzliches Audiotreiberpaket.
Das Original bleibt unverändert. Ein unterbrochener QXL-Treiberlauf kann mit derselben
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
mit `ntfs-3g` und `python3-hivex` sowie genug freier Speicherplatz für die
Arbeitskopie des ausgewählten CF-Images und die Laufzeitdateien.
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

## Eigene Dateien, Build und Updates

CF-Images, Datenbank-Dumps, Zulassungskarten, Logs und fremde Grafik-DLLs
gehören nicht in dieses Repository. Sie werden lokal ausgewählt und durch
`.gitignore` ausgeschlossen.

Eine einzelne Start-EXE lässt sich mit `build-launcher-exe.ps1` erzeugen
(Build-Abhängigkeit: PyInstaller und zuvor gebaute Eigenkomponenten). Sie legt
die Laufzeitdateien im App-Datenordner ab. Images, Dumps und fremde
Grafik-/Treiberdateien werden nicht eingebettet.

Der Quellcode wird über Git aktualisiert (`git pull` im Repository).
Eigene Änderungen vorher sichern oder committen. Für eine aktualisierte
Start-EXE anschließend neu bauen oder eine neue Release-EXE verwenden.
Ein Git-Update ersetzt keine bereits heruntergeladene EXE.

## Hintergründe

Die Datenbank ist kein bloßer Antwort-Stub: der Motorola-68k-Code läuft in
QEMU; die Host-Brücke bildet die serielle Verbindung und das Board-I/O nach.
Der DUART-Timer verwendet zunächst einen konfigurierbaren Eingangstakt von
3,6864 MHz; der tatsächliche Quarz der Originalplatine ist noch nicht bestätigt.
Die virtuelle Uhr wird für das M90-Setup auf 2012 gesetzt. Details zur
[Image-Einrichtung](docs/image-preparation.md) stehen in der Dokumentation; ältere,
teilweise überholte Notizen liegen in
[`docs/project-background.md`](docs/project-background.md).
