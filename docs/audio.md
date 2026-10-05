# Audio-Anbindung

## PCM-Bridge

Der Starter verwendet ausschließlich die PCM-Bridge und startet einen
Audiohelfer vor QEMU. Die eigene irrKlang-Proxy-DLL lädt weiterhin die
unveränderte Originalbibliothek aus `WINDOWS/system32`; sechs gezielt ersetzte
WinMM-Aufrufe übernehmen deren PCM-Puffer. Es werden keine Soundobjekte erfunden.
Die XP-Kerneltreiber bleiben auf dem Image erhalten, sind aber **nicht** dieser
Ausgabeweg. Insbesondere wird der originale HDA-Treiber nicht auf AC’97 umgebogen.

COM1 transportiert ausschließlich den Ton über QEMUs Loopback-Verbindung
`127.0.0.1:4765`; die Datenbank verwendet unverändert COM3. Es gibt keinen
zusätzlichen Gast-Netzwerktreiber oder Internetzugriff. SDL aus der ausgewählten
QEMU-Installation gibt den Ton in Mono aus, ohne Aufnahmegerät. Bis zu 32
Originalpuffer können vorausgeschickt werden; bei Ton folgt `WHDR_DONE` erst
auf die Wiedergabebestätigung. Vollständig stumme Puffer laufen mit ihrer
ursprünglichen Dauer lokal ab: keine PCM-Übertragung und kein Öffnen des
Tonkanals bis zum ersten nichtstummen Puffer. Stereo wird bereits im Gast
auf Mono gemischt. Nur der Audio-UART COM1 bekommt eine Baudbasis von
8.000.000; die Datenbank-UARTs behalten ihre bisherigen Takte.
Schließen/Reset beenden die Audiositzung und geben
wartende Puffer frei. Verlorene Verbindung und übergroße Pakete sind Fehler,
kein stiller Null-Treiber-Rückfall. Protokolle liegen im Laufzeitordner unter
`logs/audio-bridge-*.jsonl` sowie im Gast unter `NVRAM/irrklang_proxy.log`.

Der Starter setzt vor dem Boot den Marker `NVRAM/m90_audio_bridge.enabled`
in der getrennten Arbeitskopie. Gespeicherte AC’97-Einstellungen werden auf
Bridge umgestellt; ein zusätzlicher Audiotreiber wird nicht installiert.
Die früheren Audiotreiber-Installationshelfer werden nicht mehr in der EXE
mitgeliefert. Eine bereits
mit SigmaTel eingerichtete Arbeitskopie ist dadurch nicht automatisch ein
unveränderter Originaltreiber-Stand: Dateien können vorhanden sein, ohne dass
ein zugehöriges Gerät oder ein laufender Treiberdienst existiert.

Die Bridge verarbeitet echte irrKlang-Soundobjekte und PCM-Ausgabe.
Flüssige Echtzeitwiedergabe über den seriellen Ausgabeweg ist noch nicht
gewährleistet.

## Früherer AC’97-Weg (nicht mehr im Starter)

Die folgenden Abschnitte dokumentieren die frühere Anbindung und ihre Fehler.
Sie beschreiben nicht den aktuellen Einrichtungsablauf. Alte Hilfsprogramme
bleiben als Quellcode erhalten, sind aber nicht Teil des EXE-Pakets.

Der Spiel-PC erhält eine AC’97-Soundkarte (`PCI\VEN_8086&DEV_2415`). Der
Starter lädt den passenden SigmaTel-XP-Treiber 5.10.7144 vom
[originalen Dell-Paket](https://www.dell.com/support/home/en-us/drivers/driversdetails?driverid=r38271).
Das Paket und seine drei Treiberdateien werden per SHA-256 geprüft; der
OEM-Installer wird auf dem Host nicht ausgeführt. Drittanbieterdateien gehören
nicht zur eigenen EXE oder zum Repository.

Die Einrichtung verändert ausschließlich die getrennte Arbeitskopie, sichert
Starter und Registry unter `NVRAM/m90-audio-backup` und deaktiviert dort den
abstürzenden Realtek-Dienst. Zusätzlich werden dessen SYS-Datei, vorhandene
Cache-Kopien sowie die zugehörigen INF-/PNF-Dateien mit Hash-Manifest unter
`NVRAM/m90-audio-backup/realtek` gesichert und aus den aktiven Treiberpfaden
entfernt. Diese Dateien bleiben wiederherstellbar; fremde Treiber bleiben erhalten.

Die Einrichtung läuft in drei getrennten Gaststarts: Zuerst registriert XP
Software-Enumerator und WDM-Basiskomponenten **ohne virtuelle Soundkarte**.
Erst nach bestätigtem Abschluss wird AC’97 für die gezielte SigmaTel-Installation
samt Audioschnittstellen angeschlossen. Ein dritter Gaststart muss den Ausgang öffnen, einen
Mono-Prüfpuffer annehmen und dessen vollständige Wiedergabe bestätigen.
Erst dann wird der Spielstarter wiederhergestellt. Bereits eingerichtete
Arbeitskopien überspringen diese Installation. Unterbrochene Läufe können
fortgesetzt werden; ein fehlendes Audiogerät wird nicht als Erfolg behandelt.
Protokolle: `NVRAM/m90_audio_software.log`, `NVRAM/m90_audio_install.log`
und `NVRAM/m90_audio_verify.log`. Abgebrochene Einrichtungen früherer Versionen
beginnen mit der neuen Basisvorbereitung, ohne die ursprünglichen Backups zu überschreiben.

Der Dialog-Helfer läuft bereits parallel zur Einrichtung der XP-Audiokomponenten,
nicht erst während der anschließenden SigmaTel-Treiberinstallation.
Die Audio-Einrichtung zeigt den XP-Gast im QEMU-Fenster. Falls XP einen
Installationsdialog öffnet, kann dieser dort bestätigt werden. Ein Timeout
gibt den Spielstart weiterhin nicht frei. Vor dem Beenden der Setup-VM werden
beide XP-Bildschirme und der QEMU-Zustand gesichert; nach ihrem Ende werden
die Audioprotokolle, der Einrichtungsstatus und `WINDOWS/setupapi.log`
schreibgeschützt aus der Arbeitskopie kopiert. Der Fehlerbericht liegt unter
`%LOCALAPPDATA%\M90 Emulator\runtime\logs\audio-diagnostics\<Lauf>`;
der Starter nennt den genauen Ordner. Fehlende Logs und fehlgeschlagene
Erfassungen werden im Bericht ausgewiesen. Die letzten maximal 4 MiB pro
Gastprotokoll werden übernommen. Keine Registry, Spiel- oder Datenbankdateien
werden exportiert. Die Berichte können lokale Pfade enthalten und werden
nicht automatisch hochgeladen.

Die eigene `irrKlang.dll` in NVRAM/WorkDir lädt unverändert die originale
irrKlang 1.1.3 aus `WINDOWS/system32`, wählt aber WinMM (3) statt NULL (6).
Vor dem Erstellen der Engine wartet sie höchstens 120 Sekunden auf einen
waveOut-Ausgang. Das Protokoll `NVRAM/irrklang_proxy.log` zeigt Gerätezahl,
gewählten Treiber und Engine-Zeiger. Es gibt keine erfundenen Soundobjekte
und keinen stillen Rückfall auf den ungeeigneten Null-Treiber.

QEMU leitet den Ton über SDL an den Host weiter, mit `in.voices=0` ohne
Aufnahme. SDL gehört zur Windows-QEMU-Installation; die GTK-Anzeige bleibt
unverändert. DirectSound wird nicht mehr verwendet: dessen Aufnahmegeräte-
Initialisierung kann mit `Could not initialize DirectSoundCapture` abbrechen,
obwohl nur Wiedergabe nötig ist.
Nur `in.voices=0` im DirectSound-Pfad umgeht dessen Initialisierung nicht.
Der Originalautomat hat laut Besitzer einen Lautsprecher. Der Laufzeitstarter
mischt deshalb mit `out.channels=1` auf einen Mono-Hostausgang; die virtuelle
AC’97-Karte darf intern weiterhin Stereo anbieten. Die Lautsprecherzahl ist
keine Erklärung für den unten beschriebenen Kernelabsturz.
Stummschaltung verwendet QEMUs `none`-Backend, lässt aber AC’97 und WinMM
im Gast aktiv. Die Einstellung verändert weder Datenbanktakt noch Spielcode.
Sofortige QEMU-Startfehler werden vor dem Start von Datenbank und
Bedienfenstern erkannt und mit den letzten Zeilen des QEMU-Protokolls gemeldet.
Die kurze Startprüfung ersetzt keine Prüfung, ob der XP-Gast fertig gebootet hat.

Quellen: [QEMU SDL-Backend](https://github.com/qemu/qemu/blob/master/audio/sdlaudio.c),
[QEMU DirectSound-Backend](https://github.com/qemu/qemu/blob/master/audio/dsoundaudio.c)
und [Audio-Optionen](https://www.qemu.org/docs/master/system/qemu-manpage.html).

## Bekannter XP-Gastabbruch

Der übermittelte Diagnoseordner vom 01.10.2026 endet im eigenen Installationslog
bei `Preparing XP audio software devices.`; weder `Software bus setup` noch
der Abschluss der SigmaTel-Installation sind enthalten. Das grenzt den Abbruch
auf die Software-Basisvorbereitung ein, beweist aber ohne neuen Dump nicht,
welcher Treiber diesmal auf dem Stack lag. Der neue Ablauf trennt diesen
Schritt von AC’97 und macht den alten Realtek-Treiber nicht mehr ladbar.

Am 01.10.2026 wurde beim Start mit AC’97 auch ein XP-Bluescreen gemeldet:
`STOP 0x0000007E (0xC0000005, 0x90A6246D, ...)`,
`portcls.sys` mit Basis `0x90A62000`, Zeitstempel `41107f13`.
Die schreibgeschützt kopierte Treiberdatei passt zu diesem Zeitstempel.
Der Absturz wurde anschließend in einem temporären Snapshot-Gast ohne
Datenbank und mit stummem Host-Backend reproduziert. Der gesicherte Kontext
zeigt ECX=0 beim GUID-Vergleich (`cmp edx,[ecx]`) an Offset `0x46D`.
Ghidra und der Stack verfolgen den Aufruf von `ALCXWDM.SYS` über
`PcCreateSubdeviceDescriptor` zu `PcAddToPropertyTable`. Dessen Argumentplätze
enthalten 175 Eigenschaften bei einer ungültigen Elementgröße von 0; beim
Tabellenaufbau wird schließlich ein leerer GUID-Zeiger verglichen.
Die genaue Entstehung der ungültigen Realtek-Tabelle bleibt zu klären.
Eine rund 112 Sekunden lange Gegenprobe ohne AC’97-Karte zeigte diesen
STOP nicht, bestätigt aber noch keinen vollständigen Spielstart.
Ein anderer Host-Ausgabetreiber behebt diesen Kernelabsturz nicht automatisch.
Auch die Host-Stummschaltung entfernt die AC’97-Karte nicht. Die XP-Treiber-
Aktivierung wurden deshalb getrennt geprüft. Mit SigmaTel und ausdrücklicher
`DIF_INSTALLINTERFACES`-Registrierung erkennt XP einen WinMM-Ausgang; Öffnen,
Schreiben und Abschluss des Mono-Prüfpuffers waren erfolgreich. Der Realtek-
Bluescreen trat in diesen Einrichtungs- und Prüfläufen nicht mehr auf.
Das bestätigt noch keinen vollständigen Spielbetrieb. Windows-Systemtreiber
werden nicht gepatcht. Die Schnittstellenregistrierung folgt der
[Microsoft-SetupAPI-Dokumentation](https://learn.microsoft.com/en-us/windows/win32/api/setupapi/nf-setupapi-setupdiinstalldeviceinterfaces).

## Ursache des bisherigen Abbruchs

Das unveränderte Spiel ruft PLAYSOUND für `SMP_TurbobuchenDry` (237) auf.
In `AudioDxSoundEngine::PlaySoundW` bei 0x006B168B wird ein fehlendes Soundobjekt
protokolliert. Der Fehleraufruf bei 0x006B1721 setzt das letzte Argument von
`OutputToFile` (0x006D4030) auf eins. Der Logger ruft daraufhin bei 0x006D4382
`exit(0)` (0x0066521F) auf. Der erfolgreiche Exit-Code schließt daher einen
fatalen Spielabbruch nicht aus.

Die Original-DLL liest dieselbe OGG-Datei mit 2341 ms Länge, liefert im
Null-Treiber jedoch bei allen geprüften Kombinationen von Pause/Track/Effekten
keinen Sound-Zeiger. WinMM und der neue Proxy liefern ihn für den tatsächlichen
Spielaufruf (Pause=false, Track=false, Effekte=true). Die Datei war weder
fehlend noch beschädigt. Ursache war der ungeeignete Null-Ausgabetreiber.

## Native Prüfung ohne VM

`test-audio-proxy.ps1 -OriginalIrrKlang <eigene DLL> -SoundFile <eigene OGG>`
prüft die Anbindung stumm unter Windows. Benötigt werden Visual Studio Build
Tools und die hashgeprüfte Original-DLL; private Dateien werden nicht ins
Paket aufgenommen. Dies bestätigt Soundobjekte auf dem Host, nicht die
PnP-Aktivierung oder hörbare Wiedergabe im XP-Gast.

`python tests/sdl_playback_probe.py "C:/Program Files/qemu/SDL2.dll"`
öffnet QEMUs SDL-Wiedergabeausgang ausschließlich pausiert und schließt ihn
wieder. Die lokale Prüfung öffnete WASAPI mit 44100 Hz/Stereo bei null
Aufnahmegeräten, ohne Aufnahme, hörbare Ausgabe oder VM-Start. Das bestätigt
die Host-Anbindung, nicht die XP-Gerätetreiber.
Mit zusätzlichem Argument `1` wird derselbe Ausgang in Mono geöffnet;
auch diese pausierte Prüfung war mit WASAPI und 44100 Hz erfolgreich.

Der Starter ersetzt nur die eigenen Proxies und sichert vorherige Dateien
unter `NVRAM/m90-graphics-backups`. Original-DLL, CF-Original, Spielprogramm
und Datenbankdateien bleiben unverändert.
