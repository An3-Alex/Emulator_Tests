# M90-Emulator

Windows-Starter für die Emulation eines Merkur-M90-Automaten mit eigenen
CF-Images und Datenbankdateien. QEMU emuliert den Spiel-PC und den
Motorola-68k-Datenbankprozessor getrennt. Die Einrichtung, der Start und die
Bedienung erfolgen über eine grafische Oberfläche.

CF-Images, Datenbankdateien und proprietäre Gastsoftware werden nicht mitgeliefert.
Die Open-Source-Grafiklaufzeit für QEMU-3dfx ist in der Starter-EXE enthalten.

## Funktionen

- Automatische Einrichtung einer getrennten Arbeitskopie des CF-Images,
  einschließlich QXL-Grafiktreiber und PCM-Audio-Bridge.
- Bedienfenster für Touch, Menü, Autostart, Einsatz, Maximaleinsatz, Start,
  Auszahlung, Service-Taste, Türschalter, Touch-Kalibrierung und „+1 € (MP)“ für Spielgeld.
- Optionales Live-Protokoll der Datenbankkommunikation.
- Gespeicherte Dateipfade und Einstellungen für Spiel-PC und Datenbank.
- Separat wählbarer QEMU-3dfx-Grafikpfad: unterer Spielbildschirm über
  WineD3D/OpenGL-Passthrough, oberer Bildschirm weiterhin über SwiftShader.
  Die enthaltene Laufzeit und ihre Gastdateien werden automatisch eingerichtet.
  Einrichtung und Grenzen: [QEMU-3dfx](docs/QEMU3DFX.md).
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

- 0.1.19: QEMU-3dfx einschließlich Host-DLLs, BIOS und Gastwrappern ist in der EXE
  enthalten. Der Starter wählt die passende QEMU-Datei und Primäranzeige selbst,
  richtet frische Images mit SDL ein und installiert den Grafikpfad in der
  getrennten Arbeitskopie. Manuelle Paketpfade und Manifest-Auswahl entfallen.
  Erneute Starts erhalten normale XP-Registryänderungen und prüfen stattdessen
  gezielt die GPU-Diensteinstellungen und Grafikdateien.
  Der QEMU-3dfx-WHPX-Pfad emuliert den Interruptcontroller in QEMU, während
  die CPU weiterhin hardwarebeschleunigt läuft. Das Netzwerk-BIOS ist enthalten.
  GPU-Puffer und Register liegen außerhalb der Speicherbereiche beider QXL-
  Bildschirme; der Spiel-PC verwendet dabei maximal 2048 MiB RAM.
- 0.1.18: Der Starter kann eine vorbereitete QEMU-3dfx-Kopie mit zwei SDL-Fenstern
  starten. Unvollständige Grafikpakete und nicht vorbereitete Images werden
  abgewiesen. Die Migration sichert Gast-DLLs und die XP-Registry; der bisherige
  Grafikpfad und der Datenbanktakt bleiben unverändert. Direkte Touchs gehen
  nur an den unteren Bildschirm. Die GPU-Vorschau ist noch nicht verfügbar.
- 0.1.18: Die Service-Anwendung verwendet denselben dateibasierten SRAM wie das Spiel,
  einschließlich ihrer ANSI-Geräteabfragen. Mehrere SRAM-Handles und parallele
  Zugriffe werden unterstützt. Die ursprüngliche Service-Datei wird gesichert;
  vorhandene SRAM-Inhalte bleiben erhalten.
- 0.1.18: „Touch kalibrieren“ im Bedienfeld öffnet eine Zwei-Punkt-Kalibrierung ohne
  Service-Menü: zunächst das Fadenkreuz unten links, danach oben rechts
  anklicken und jeweils loslassen. Die Einstellung wird pro Arbeitsimage
  gespeichert. Abbrechen behält die bisherigen Werte. Während einer laufenden
  Kalibrierung im originalen Service-Menü wird keine Bedienfeld-Kalibrierung
  übernommen. Die Bestätigung erscheint im Live-Protokoll.
- 0.1.17: Die virtuelle Touch-Einheit unterstützt die Zwei-Punkt-
  Kalibrierung im Service-Menü mit Bestätigung beim Loslassen. Die Kalibrierung
  wird je Arbeitsimage in `<Image>.touch.json` gespeichert; ein normaler Reset
  behält sie bei. Die Touch-Pakete berücksichtigen den nativen 800-/960-Pixel-
  Modus einschließlich des 80-Pixel-Versatzes. Kalibrierung und Bildschirmmodus
  erscheinen im Live-Protokoll.
- 0.1.16: INITVIDEO wird anhand seiner festen Länge erkannt;
  ein `04` innerhalb der Kalender- oder Gerätedaten beendet das Paket nicht mehr.
  Bei `F_UHR` erscheinen Kalenderwerte und Firmware-Prüfwerte im Live-Protokoll.
  RTC-Leseereignisse zeigen den zu Beginn der Übertragung erfassten Kalender.
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

Die [M90-Emulator.exe aus Release 0.1.17 herunterladen](https://github.com/An3-Alex/Emulator_Tests/releases/download/v0.1.17/M90-Emulator.exe)
oder die [Release-Seite öffnen](https://github.com/An3-Alex/Emulator_Tests/releases/tag/v0.1.17).
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
