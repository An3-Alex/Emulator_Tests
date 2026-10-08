# Audio-Anbindung

Der Ton des Spiel-PCs läuft ausschließlich über die PCM-Bridge. Der Starter
startet den Audiohelfer vor QEMU; ein Gast-Audiotreiber wird nicht installiert.

Die eigene `irrKlang.dll` in `NVRAM` und `WorkDir` lädt unverändert die
Originalbibliothek aus `WINDOWS/system32`; sechs gezielt ersetzte
WinMM-Aufrufe übernehmen deren PCM-Puffer. Es werden keine Soundobjekte
erfunden. Stereo wird bereits im Gast auf Mono gemischt und als IMA-ADPCM
(Protokoll `M9A2`) übertragen; der Host dekodiert und gibt den Ton über das
SDL der QEMU-Installation aus, ohne Aufnahmegerät.

COM1 transportiert ausschließlich den Ton über QEMUs Loopback-Verbindung
`127.0.0.1:4765`; die Datenbank verwendet unverändert COM3. Es gibt keinen
zusätzlichen Gast-Netzwerktreiber oder Internetzugriff. Nur der Audio-UART
COM1 bekommt eine Baudbasis von 8.000.000; die Datenbank-UARTs behalten ihre
Takte.

Bis zu 32 Originalpuffer können vorausgeschickt werden; `WHDR_DONE` folgt
erst auf die Wiedergabebestätigung. Vollständig stumme Puffer laufen mit
ihrer ursprünglichen Dauer lokal ab, ohne Übertragung. Schließen/Reset
beenden die Audiositzung und geben wartende Puffer frei. Verlorene Verbindung
und übergroße Pakete sind Fehler, kein stiller Rückfall auf einen
Null-Treiber.

„Ton auf dem PC ausgeben“ aus schaltet nur die Host-Ausgabe stumm; die Bridge
bleibt aktiv. Der Starter setzt vor dem Boot den Marker
`NVRAM/m90_audio_bridge.enabled` in der Arbeitskopie.

Protokolle: im Laufzeitordner `logs/audio-bridge.jsonl`, im Gast
`NVRAM/irrklang_proxy.log`. Der Starter ersetzt nur die eigenen Proxies und
sichert vorherige Dateien unter `NVRAM/m90-graphics-backups`. Original-DLL,
CF-Original, Spielprogramm und Datenbankdateien bleiben unverändert.
