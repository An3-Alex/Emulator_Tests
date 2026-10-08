# Timer, Anzeigen und Laufzeit-Updates

## Datenbank-Timer

Die CC4-Routine bei `0x1825C` programmiert ACR `F0`, Counter-Preset `0074`
und IVR `40`; der alternative Zweig schreibt `003A` und IVR `41`.
Die Bridge übernimmt diese Schreibwerte nach der I/O-Initialisierung und
beobachtet spätere Firmware-Schreibzugriffe.

ACR[6:4] = 111 wählt Timerbetrieb mit X1/16. Counter-Ready entsteht einmal
pro vollständiger Rechteckperiode: `2 * 16 * preset / X1` Sekunden.
Quelle: [NXP SCC68681, Timer und ISR[3], Seiten 16–17](https://www.nxp.com/docs/en/data-sheet/SCC68681.pdf).

Bei 3,6864 MHz (Einstellung „DUART-Eingangstakt“) sind das 1,006944 ms
(`0074`) bzw. 0,503472 ms (`003A`). Unterstützt sind dieser Timerbetrieb und
die beiden CC4-Vektoren; andere Quellen/Vektoren und Presets null/eins werden
abgelehnt. Ein Bruchteil-Akkumulator verhindert Rundungsdrift. Höchstens 128
Interrupts pro CPU-Zeitscheibe werden nachgeholt; bei Umprogrammierung wird
der ausstehende Tick-Batch verworfen. Das ist eine Host-Bridge, kein
zyklusgenaues DUART-/CPU32-Gerät.

## Zwei Anzeigen

Der Spiel-PC hat zwei QXL-Anzeigen mit je 800×600. Die D3D9-Bridge des Gasts
ordnet logischen Adapter null der Windows-Primäranzeige und Adapter eins der
Sekundäranzeige zu, positioniert die Spielfenster rahmenlos und hält beide
Geräte nicht-exklusiv. Ein fehlender zweiter Windows-Monitor führt zu einem
CreateDevice-Fehler statt zur Ausgabe beider Geräte auf einem Monitor.

Gerendert wird je nach Grafikpfad mit SwiftShader (Software) oder über
QEMU-3dfx auf der Host-GPU, siehe [QEMU3DFX.md](QEMU3DFX.md). Die
Bedienfeld-Vorschau zeigt immer die untere Anzeige (`lower`).

## Laufzeit-Updates vorhandener Arbeitskopien

Beim Start prüft der Starter die Arbeitskopie und aktualisiert nur eigene,
per SHA-256 bekannte Dateien: Display-Bootstrap (`WINDOWS/explorer.exe`),
CGOS-Shim (`WINDOWS/system32/Cgos.dll`), D3D9-Bridge (nur Softwarepfad),
irrKlang-Proxy, SRAM-Proxy `FBWFLIB.dll` und die Service-Anbindung. Unbekannte
Dateien werden nicht ersetzt. Zuerst wird ausschließlich lesend geprüft; nur
die ausgewählte, vom Original verschiedene Arbeitskopie wird beschreibbar
geöffnet. Der vorherige Stand bleibt mit Manifest unter
`NVRAM/m90-graphics-backups`; bei I/O-Fehlern werden bereits ersetzte Dateien
zurückgesetzt. Spiel, Datenbank, QXL-Treiber, SRAM-Daten und Registry bleiben
unangetastet.
