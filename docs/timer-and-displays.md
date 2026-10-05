# Timer und zwei Anzeigen

## Datenbank-Timer

Die CC4-Routine bei `0x1825C` programmiert ACR `F0`, Counter-Preset `0074`
und IVR `40`; der alternative Zweig schreibt `003A` und IVR `41`.
Die Brücke übernimmt diese Schreibwerte nach der I/O-Initialisierung und
beobachtet spätere Firmware-Schreibzugriffe. Die Werte stammen aus dem
MMIO-RAM-Ersatz in QEMU, nicht aus CPU-lesbaren echten Presetregistern.

ACR[6:4] = 111 wählt Timerbetrieb mit X1/16. Counter-Ready entsteht einmal
pro vollständiger Rechteckperiode: `2 * 16 * preset / X1` Sekunden.
Quelle: [NXP SCC68681, Timer und ISR[3], Seiten 16–17](https://www.nxp.com/docs/en/data-sheet/SCC68681.pdf).

Bei angenommenen 3,6864 MHz sind das 1,006944 ms (`0074`) bzw. 0,503472 ms
(`003A`). Der Firmware-Zähler mit Teiler zehn liegt damit bei etwa 10 ms;
der bisherige feste 10-ms-Basistakt machte diesen Pfad etwa zehnmal langsamer.
Der Quarz der tatsächlichen Platine ist noch nicht vermessen. `--duart-x1-hz`
setzt ihn ausdrücklich in der Python-Brücke, unabhängig vom MC68331-CPU-Takt.
Unterstützt sind der belegte Timerbetrieb und die beiden belegten CC4-Vektoren.
Andere Quellen/Vektoren und undokumentierte Presets null/eins werden abgelehnt.

Ein Bruchteil-Akkumulator verhindert Rundungsdrift. Maximal 128 Interrupts pro
CPU-Zeitscheibe sind erlaubt; Debugger-Laufzeit erzeugt keinen unbegrenzten
Nachholstau. Bei Umprogrammierung wird der alte noch ausstehende Tick-Batch
verworfen. QEMU-`icount`-Schutz und Single-Instruction-Modus bleiben erhalten.
Dies ist weiterhin eine Host-Brücke, kein vollständiges zyklusgenaues
DUART-/CPU32-Gerät. Bestehende serielle Kompatibilitätspfade bleiben vorerst
bestehen. Die tatsächliche Bootzeit ist noch nicht neu gemessen.

## Grafik

SwiftShader bleibt der gemeinsame Software-Renderer (Backend-Adapter null).
Die Präsentation erfolgt auf zwei Windows-Monitoren: logischer Adapter null
auf Windows primär, Adapter eins auf Windows sekundär. Der Proxy meldet
die jeweiligen Monitorhandles, positioniert die zugehörigen Spielfenster
rahmenlos und hält beide Geräte nicht-exklusiv. Ein Device-Wrapper erhält
diese Zuordnung bei `Reset`, Zusatz-Swapchains und über `GetDirect3D`.
Ressourcenobjekte werden unverändert an SwiftShader weitergegeben; dies ist
keine allgemeine Virtualisierung aller Direct3D-COM-Objekte.

Der Bootstrap setzt beide Windows-Ausgänge auf 32-Bit-Farbe. QEMU benennt
standardmäßig den ersten Ausgang `upper`, den zweiten `lower`; die
Bedienfeld-Vorschau bleibt auf `lower`. Ein fehlender zweiter Windows-Monitor
führt zu einem CreateDevice-Fehler statt zur Ausgabe beider Geräte auf dem
ersten Monitor. Die laufende Spielausgabe ist noch nicht neu bestätigt.

## Vorhandene Arbeitskopien

Beim Start führt die UI nach der Statusprüfung ein gezieltes Grafik-Update
aus. Quellen und vorhandene Guest-Dateien müssen bekannte SHA-256-Werte haben.
Zuerst wird ausschließlich lesend validiert. Nur die ausgewählte, vom Original
verschiedene Arbeitskopie wird anschließend beschreibbar geöffnet.
Ersetzt werden `WINDOWS/explorer.exe` sowie `d3d9.dll` in `NVRAM` und `WorkDir`.
Der vorherige Stand bleibt mit Manifest in einem eigenen Ordner unter
`NVRAM/m90-graphics-backups`. Gewöhnliche I/O-Fehler setzen bereits ersetzte
Dateien zurück; bei Stromausfall bleiben Sicherung und Manifest erhalten.
Drei separate Dateien können nicht in einer einzigen atomaren Operation
ausgetauscht werden. Spiel, Datenbank, QXL-Treiber und Registry bleiben dabei
unangetastet. Für dieses Update muss das Original als separate Datei ausgewählt
bleiben; es wird weder eingehängt noch verändert.

Die Offline-Prüfung umfasst Timer-Rechnung, Vektorauswahl, Startpläne,
Fensterplatzierungsregel, Paket-Inhalt und Grafikdatei-Update mit Rollback.
Für diese Änderung wurde kein Emulator gestartet und kein CF-Image geändert.
Beide tatsächlichen Spielausgaben, Farben, Eingaben und Bootzeit müssen im
nächsten kontrollierten Lauf noch bestätigt werden.
