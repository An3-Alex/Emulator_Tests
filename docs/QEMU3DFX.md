# QEMU-3dfx-Grafikpfad

Die Datenbank bleibt im vorhandenen M68k-Emulator. QEMU-3dfx betrifft nur den
Spiel-PC und dessen Grafik; RTC, Eingabeprotokoll und PCM-Audiobridge bleiben
unverändert.

Für das D3D9-Spiel ist der Pfad:

`D3D9 → WineD3D → OpenGL-Gastwrapper → QEMU mesapt → Host-Grafikkarte`.

Beide Gastanzeigen verwenden diesen Pfad. Der Gast-Monitor des jeweiligen
Fensters bestimmt den GPU-Ausgang: unten/primär ist Ausgang 0, oben ist Ausgang 1.
Fenster, Basiskontexte, Bereitschaft und Skalierung bleiben je Ausgang getrennt.
Der 3dfx-Modus lädt zur Darstellung keine SwiftShader-DLL; diese bleibt für den
separat wählbaren Softwaremodus verfügbar.

Ein bloßer Austausch der QEMU-EXE aktiviert keine D3D9-Beschleunigung. Die
zusammengehörigen Gast-DLLs und der XP-Treiber `fxptl.sys` werden ebenfalls
benötigt. Die Vorbereitung registriert seinen MAPMEM-Dienst ausschließlich in
der Offline-Registry des XP-Gasts. Auf dem Host wird weder `INSTDRV.EXE` noch
ein Gasttreiber ausgeführt. Das Hostfenster verwendet SDL ohne `gl=on`; der Passthrough verwaltet
seinen eigenen OpenGL-Kontext.

Die Zuständigkeit für die GPU-Ausgabe wird pro Konsole getrennt von den
Fenstergrößeninformationen gespeichert. Vergrößern, Maximieren und
Wiederherstellen können sie deshalb nicht zurücksetzen. Während Passthrough
aktiv ist, speichert SDL Änderungen der QXL-Zeichenfläche, ohne das GPU-Fenster,
seinen Kontext oder seine Präsentation zu ersetzen. Beim Ende der GPU-Ausgabe
wird die zuletzt gespeicherte QXL-Fläche wieder verwendet.

Das GPU-Fenster hat die native Spielauflösung als Mindestgröße. So bleibt die
vollständige Bildquelle auch beim Verkleinern verfügbar; kleinere Fenster
würden einen separaten Renderpuffer benötigen. Größenwechsel des Hostfensters
werden während der GPU-Ausgabe nicht als neuer XP-Anzeigemodus weitergereicht.
Zu kleine oder leere Zeichenflächen während Größenwechseln/Minimieren werden
vor Bildkopie und Skalierung abgefangen. Beim Rückwechsel zu QXL wird die
GPU-Mindestgröße aufgehoben.

Der zusätzliche Host-Skalierer verwendet auch in OpenGL-Kompatibilitätskontexten
ein eigenes Vertex-Array-Objekt. Zeichenattribute und Pufferzeiger des Spiels
werden dadurch nicht verändert. Seine Texturbindung auf Einheit null wird
getrennt von der aktiven Textur-Einheit des Spiels gesichert und wiederhergestellt.
Diese Zustandsgrenze gilt bei jedem skalierten Bild, nicht nur beim Größenwechsel.

MAPMEM verwendet den nativen XP-Treiberpfad
`\SystemRoot\system32\drivers\fxptl.sys`. Ein vorhandener älterer Pfadeintrag
wird gezielt mit Sicherung der aktuellen Registry korrigiert; andere
Diensteinstellungen bleiben erhalten. Vor der Grafikinitialisierung prüft
die Adapter-Bridge den laufenden Gasttreiber und startet ihn bei Bedarf.
OpenGL und WineD3D werden aus dem Ordner der Adapter-Bridge geladen,
unabhängig vom Arbeitsverzeichnis des Spielprozesses. Windows-Ladefehler
stehen mit ihrem Fehlercode in `NVRAM/d3d9_proxy.log`; ein Rückfall auf das
normale XP-OpenGL wird nicht als GPU-Beschleunigung akzeptiert.

Die Diagnoseausgaben des OpenGL-Wrappers werden in einem begrenzten
Speicherpuffer formatiert. Sie benötigen kein XP-`NUL`-Gerät und können die
Grafikinitialisierung nicht durch einen fehlgeschlagenen Datei-Handle abbrechen.
`scripts/qemu3dfx_guest_patch.py` übernimmt diese Anpassung beim Gastbuild
für den Wrapper und seine Timer-/Gamma-Hooks.
Bereits vorbereitete Arbeitskopien erhalten die Korrektur beim nächsten Start,
wenn beide alten Wrapper unverändert erkannt werden. Die vorherigen DLLs und
der Installationsmarker bleiben unter `NVRAM/m90-qemu3dfx-backup` gesichert.
Unbekannte Grafikdateien werden nicht ersetzt; SRAM und XP-Registry bleiben
bei dieser DLL-Aktualisierung erhalten.

WineD3D erzeugt Shadertexte über die echte Formatierungsfunktion der in XP
vorhandenen `msvcrt.dll`. Ein eigener CRT-Start wird nicht benötigt. Insbesondere
`__ms_vsnprintf` darf kein Platzhalter sein, der Erfolg meldet, ohne die Ausgabe
zu schreiben: Das führt zu leeren Shaderprogrammen und schwarzer Spielausgabe.
`scripts/wined3d_format_patch.py` ersetzt die Formatter beim Build; der
Grafik-Updatepfad sichert jede Aktualisierung getrennt, ohne ältere Sicherungen
zu überschreiben.

`WAITING FOR IDLE STATE` gehört zum ADP-Loader: Er prüft den CPU-Leerlauf
innerhalb von XP und wartet auf mindestens 90 Prozent. Die Meldung ist keine
Warteanforderung der Datenbank und kein INITVIDEO-Befehl. Der Starter begrenzt
die reine CPU-Leerlaufwartezeit des unterstützten Loaders auf 5 Sekunden pro
Aufruf. Kürzere erfolgreiche Prüfungen enden weiterhin sofort; die folgenden
Geräteprüfungen werden nicht übersprungen. Der vorherige Loader bleibt unter
`NVRAM/m90-loader-idle-backup` erhalten.

Der Hostbuild auf Basis von QEMU 11.1.0 verwendet bei WHPX `kernel-irqchip=off` sowohl
für die Einrichtung als auch beim Spielstart. Damit bleibt die CPU
hardwarebeschleunigt, während QEMU den Interruptcontroller übernimmt.
Die Einstellung betrifft nicht den separaten Datenbankprozessor.

WHPX übernimmt die konfigurierte CPU-Modellbezeichnung auch für die drei
CPUID-Namensblätter `0x80000002–0x80000004`. Ohne diese Anpassung würde XP
den Namen der Host-CPU sehen, obwohl im Startbefehl die Gast-CPU eingestellt
ist. QEMU 11.1.0 enthält diese CPUID-Abfragen bereits; das Portierungsskript
prüft sie vor dem Build. Für den älteren 9.2-Build ergänzt
`scripts/qemu3dfx_host_patch.py` die Exit-Listen. Die Hardwarebeschleunigung
bleibt aktiv.

Der gemeinsame Host-/Gastbuild verwendet für die GPU-Puffer einen eigenen
Adressbereich `0x90000000–0x9FFFFFFF`, außerhalb der QXL-PCI-BARs. Der virtuelle
Spiel-PC darf in diesem Grafikpfad höchstens 2048 MiB RAM erhalten, damit dieser
Bereich nicht mit Gast-RAM kollidiert. Beide Buildseiten und das Paketmanifest
verwenden dieselbe Kennung `m90-dual-qxl-v1`.

## Quellen und Build

- [QEMU-3dfx](https://github.com/kjliew/qemu-3dfx), Revision
  `920661f3b48bd278b93acd9cf9ff8c968afb02c9`.
- [QEMU 11.1.0](https://github.com/qemu/qemu/tree/v11.1.0), Revision
  `84f07211cc5b4fc6a371559bf8a5de4fb068e648` als Hostbasis.
- [WineD3D-Buildquellen](https://github.com/startergo/wined3d-windows), Revision
  `f977ef3903b444fb5cfd65c543cde1ad5e8f601c`, verwendete Wine-Version 1.8.7.

`scripts/build_qemu3dfx_modern.sh` akzeptiert den QEMU-11.1.0-Checkout und
den separaten QEMU-3dfx-Checkout. `qemu3dfx_modern_port.py` übernimmt die
gepinnte Grafik-Anbindung mit den aktualisierten QEMU-APIs und dem bestehenden
GPU-Speicherlayout. Der neue WHPX-Unterbau bleibt erhalten; die fünf Gastdateien
ändern sich durch das Hostupdate nicht. Der Hostbuild nutzt MSYS2 UCRT64 mit
GCC, pkgconf, GLib, Pixman, SDL2, Epoxy, zlib, Python, distlib, Meson, Ninja,
SPICE und libslirp. Der Gastbuild nutzt MINGW32 mit GCC, gendef, make, Perl,
xxd, git und rsync. Für die Gastwrapper bleibt `scripts/build_qemu3dfx.sh guest`
mit dem QEMU-9.2.2-Archiv nutzbar; dessen SHA256 ist
`752eaeeb772923a73d536b231e05bcc09c9b1f51690a41ad9973d900e4ec9fbf`.
Der Compiler bleibt beim SSE2-Instruktionssatz des
virtuellen Spiel-PCs. Kein Buildschritt startet einen Gast.

Die lokalen CRT-Speicherfunktionen im WineD3D-Build werden mit
`-fno-builtin -fno-tree-loop-distribute-patterns` kompiliert. So erzeugt GCC
innerhalb von `memset` keinen Aufruf von `memset` selbst. Die Anpassung erfolgt
mit `scripts/wined3d_crt_patch.py` vor einem vollständigen Wine-Build.
`scripts/rebuild_wined3d_crt.sh` aktualisiert alternativ die CRT-Objekte und
verlinkt einen vorhandenen 1.8.7-Build erneut, ohne die Toolchain zu verändern.
Bekannte unveränderte ältere WineD3D-Dateien werden in vorbereiteten
Arbeitskopien ebenfalls paarweise mit Sicherung aktualisiert.

QEMU und die Wrapper sind GPL-lizenziert, WineD3D LGPL-lizenziert. Bei einer
Binärveröffentlichung gehören Lizenztexte und die zugehörigen verwendeten
Quellen samt Änderungen dazu.

Bei einer Veröffentlichung liegen die zugehörigen Quellen im GitHub-Release als
`gpu-corresponding-sources.tar.gz`, `gpu-dependency-sources.tar` und
`qemu-9.2.2.tar.xz` für den Gastbuild. Das erste Archiv enthält die tatsächlich
verwendeten QEMU-11.1.0-/QEMU-3dfx-/WineD3D-Quellen einschließlich der Änderungen
und der BIOS-Quellen; das zweite die
MSYS2-Quellpakete mit Versions- und SHA256-Verzeichnis. Das QEMU-Originalarchiv
enthält auch die BIOS-/iPXE-Quellen. Die eigenen Adapter- und Buildänderungen
stehen zusätzlich im Repository. `scripts/package_gpu_sources.py` bündelt
diese Dateien anhand der installierten Paketmetadaten.

## Bildschirme und Auswahl

Der Starter verwendet standardmäßig SwiftShader und bietet QEMU-3dfx als
separaten Grafikpfad an. Der gemeinsame
Zeichenflächenzustand des Upstream-Passthroughs ist nicht gleichbedeutend
mit zwei unabhängig präsentierbaren Monitoren.

`build-d3d9-proxy.ps1 -Qemu3dfx` erzeugt eine getrennte Adapter-Bridge unter
`build/d3d9-qemu3dfx`. Logischer Adapter 0 (Windows-Primäranzeige, im SDL-Lauf
der untere Spielbildschirm) nutzt `wined3d_d3d9.dll`; Adapter 1 bleibt bei
`swiftshader_d3d9.dll`. Der bisherige Build ohne Schalter bleibt unverändert.
Die Variante ist über den expliziten Startparameter `-GraphicsBackend qemu3dfx`
verfügbar. In der Starter-Oberfläche ist sie unter „Emulationseinstellungen →
Grafikpfad“ wählbar. Die EXE enthält die Laufzeit und richtet die Gastdateien automatisch ein.
Standard bleibt SwiftShader.

Die Migration erfolgt an einer getrennten Arbeitskopie. Original-CF,
persistenter SRAM und die bisherige funktionierende Variante werden nicht
durch die Auswahl des anderen Grafikpfads verändert.

## Paket und Arbeitskopie

`scripts/qemu3dfx_package.py build` bündelt den Host mit allen rekursiv
benötigten Nicht-System-DLLs, den x86-BIOS-Dateien und den fünf Gastdateien.
Die Parameter sind `--checkout`, `--runtime` (UCRT64-bin), `--wine`, `--proxy`
(die Dual-GPU-D3D9-Bridge) und `--destination` (ein noch nicht vorhandener Ordner).
Für den aktuellen Host kommt `--host-source <QEMU-11.1.0-Checkout>` hinzu;
dessen Build liegt unter `build-m90`. Das Manifest hält Host- und
GPU-Transportrevision getrennt fest. Ältere Pakete bleiben lesbar.
Ein SHA256-Manifest prüft das Paket vor der Installation und vor jedem Start.

Die WineD3D-Dateien entstehen im gepinnten Build-Checkout mit
`build-ci.sh --versions 1.8.7` in der MINGW32-Umgebung. Anschließend werden die
Importnamen mit dem dortigen `docker/strip_import_decorations.py` normalisiert.
Die optionalen Win9x-PE-Headeränderungen werden für XP nicht angewendet. Das
Gastpaket enthält `wined3d.dll`, dessen D3D9-Frontend als `wined3d_d3d9.dll`,
`opengl32.dll`, die Dual-GPU-`d3d9.dll` und `fxptl.sys`.

Für die Vorbereitung muss eine vollständig eingerichtete, separate Kopie
vorliegen. `prepare-qemu3dfx.ps1 -SourceImage <bisherige-Kopie>
-Image <GPU-Kopie> -Bundle <Paketordner>` aktualisiert nur diese GPU-Kopie,
ohne Gaststart. DLLs und SYSTEM-Registry werden darin vorher gesichert;
`m90_sram.bin` wird nicht beschrieben. Ein vorbereiteter Gast wird nicht über
die normale Grafikeinrichtung zurückgesetzt: Diese verweigert fremde Renderer.

Die vorhandene Startkette erhält zusätzlich `-GraphicsBackend qemu3dfx
-SwapDisplays -Qemu <Paketordner>\host\qemu-system-x86_64.exe`. Alle bisherigen
Parameter für Datenbankdateien, RTC, Prozessor-Timing und Audio bleiben gültig.
Ohne passenden Vorbereitungsbeleg `<GPU-Kopie>.qemu3dfx.json` erfolgt kein Start.
Das untere Fenster heißt `QEMU (M90-3dfx-0)`, das obere `QEMU (M90-3dfx-1)`.
Direkte Touchs werden ausschließlich aus dem unteren Fenster übernommen.

Die Bedienfeld-Vorschau wechselt mit der unteren Anzeige von QXL zum echten
Host-OpenGL-Spielbild. QMP fordert den nativen Frame vor der Fensterskalierung
an; ohne Bildanforderung findet kein Readback statt. Die Vorschau wird etwa
alle zwei Sekunden aktualisiert und kann ebenfalls für Touch verwendet werden.
OpenGL-Pixelpack-, PBO- und Read-Framebuffer-Zustand werden nach dem Abgriff
wiederhergestellt. Die gecachte QXL-Startanzeige ersetzt kein GPU-Spielbild.

Das QEMU-Protokoll enthält ungefähr alle zehn Sekunden `M90_GPU_PRESENT` mit
der Zahl der ausgeführten Bildübergaben pro Sekunde. Die Protokollierung ist
kein vollständiger Frame-Profiler; sie unterscheidet nicht zwischen identischen
und veränderten Bildern. Die OpenGL-Rendererzeile nennt die verwendete GPU.

Im Starter unter „Emulationseinstellungen → Grafikpfad“ `qemu3dfx` wählen.
Die mitgelieferte Host-EXE und Primäranzeige werden automatisch ausgewählt.
Das Paket wird unter `%LOCALAPPDATA%\M90 Emulator\runtime\build\qemu3dfx-runtime`
bereitgestellt; `manifest.json` ist das interne SHA256-Verzeichnis und muss
nicht selbst erstellt oder ausgewählt werden.

Für ein frisches CF-Image Original und einen getrennten Arbeitskopie-Pfad sowie
die benötigten Eigentümerdateien auswählen, dann „Frisches Image einrichten“.
Der Starter erstellt die Kopie, startet XP kurz für die QXL-Installation und
Anzeigeprüfung, beendet diesen Gast und installiert die GPU-Dateien samt
MAPMEM-Dienst. Anschließend „Emulator starten“ wählen. Bei einer schon fertig
eingerichteten normalen Arbeitskopie erfolgt die GPU-Migration beim Start
automatisch, sofern das getrennte Original ausgewählt ist. Original und
Arbeitskopie dürfen nicht dieselbe Datei sein. Eine frühere Spielkopie als
separaten Rückweg aufbewahren.

Zurückwechseln bedeutet
den bisherigen Renderer, dessen QEMU und dessen unveränderte Arbeitskopie
wieder auszuwählen.

`qemu3dfx_image.py <eingehängte-GPU-Kopie> --restore` stellt eine unveränderte
Offline-Installation zurück. Wenn der Gast seine Registry inzwischen verändert
hat, wird eine automatische Rücknahme verweigert, damit keine neueren
Geräteeinstellungen verloren gehen. Die ursprüngliche Spielkopie ist der
unabhängige Rückweg.
