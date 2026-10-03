# QEMU-3dfx-Grafikpfad

Die Datenbank bleibt im vorhandenen M68k-Emulator. QEMU-3dfx betrifft nur den
Spiel-PC und dessen Grafik; RTC, Eingabeprotokoll und PCM-Audiobridge bleiben
unverändert.

Für das D3D9-Spiel ist der Pfad:

`D3D9 → WineD3D → OpenGL-Gastwrapper → QEMU mesapt → Host-Grafikkarte`.

Ein bloßer Austausch der QEMU-EXE aktiviert keine D3D9-Beschleunigung. Die
zusammengehörigen Gast-DLLs und der XP-Treiber `fxptl.sys` werden ebenfalls
benötigt. Die Vorbereitung registriert seinen MAPMEM-Dienst ausschließlich in
der Offline-Registry des XP-Gasts. Auf dem Host wird weder `INSTDRV.EXE` noch
ein Gasttreiber ausgeführt. Das Hostfenster verwendet SDL ohne `gl=on`; der Passthrough verwaltet
seinen eigenen OpenGL-Kontext.

## Quellen und Build

- [QEMU-3dfx](https://github.com/kjliew/qemu-3dfx), Revision
  `920661f3b48bd278b93acd9cf9ff8c968afb02c9`.
- [QEMU 9.2.2](https://download.qemu.org/qemu-9.2.2.tar.xz), SHA256
  `752eaeeb772923a73d536b231e05bcc09c9b1f51690a41ad9973d900e4ec9fbf`.
- [WineD3D-Buildquellen](https://github.com/startergo/wined3d-windows), Revision
  `f977ef3903b444fb5cfd65c543cde1ad5e8f601c`, verwendete Wine-Version 1.8.7.

`scripts/build_qemu3dfx.sh` akzeptiert `host` oder `guest`, den separaten
QEMU-3dfx-Checkout und das QEMU-Archiv. Der Hostbuild nutzt MSYS2 UCRT64 mit
GCC, pkgconf, GLib, Pixman, SDL2, Epoxy, zlib, Python, distlib, Meson, Ninja,
SPICE und libslirp. Der Gastbuild nutzt MINGW32 mit GCC, gendef, make, Perl,
xxd, git und rsync. Der Compiler bleibt beim SSE2-Instruktionssatz des
virtuellen Spiel-PCs. Kein Buildschritt startet einen Gast.

QEMU und die Wrapper sind GPL-lizenziert, WineD3D LGPL-lizenziert. Bei einer
Binärveröffentlichung gehören Lizenztexte und die zugehörigen verwendeten
Quellen samt Änderungen dazu.

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
Grafikpfad“ wählbar, bleibt aber ohne passende GPU-Arbeitskopie gesperrt.
Standard bleibt SwiftShader.

Die Migration erfolgt an einer getrennten Arbeitskopie. Original-CF,
persistenter SRAM und die bisherige funktionierende Variante werden nicht
durch die Auswahl des anderen Grafikpfads verändert.

## Paket und Arbeitskopie

`scripts/qemu3dfx_package.py build` bündelt den Host mit allen rekursiv
benötigten Nicht-System-DLLs, den x86-BIOS-Dateien und den fünf Gastdateien.
Die Parameter sind `--checkout`, `--runtime` (UCRT64-bin), `--wine`, `--proxy`
(die hybride D3D9-Bridge) und `--destination` (ein noch nicht vorhandener Ordner).
Ein SHA256-Manifest prüft das Paket vor der Installation und vor jedem Start.

Die WineD3D-Dateien entstehen im gepinnten Build-Checkout mit
`build-ci.sh --versions 1.8.7` in der MINGW32-Umgebung. Anschließend werden die
Importnamen mit dem dortigen `docker/strip_import_decorations.py` normalisiert.
Die optionalen Win9x-PE-Headeränderungen werden für XP nicht angewendet. Das
Gastpaket enthält `wined3d.dll`, dessen D3D9-Frontend als `wined3d_d3d9.dll`,
`opengl32.dll`, die hybride `d3d9.dll` und `fxptl.sys`.

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

Die Bedienfeld-Vorschau stammt weiterhin aus QXL, nicht aus dem darüber
liegenden Host-OpenGL-Bild. In dieser Variante sind deshalb Vorschau-Touchs
gesperrt; direkte Touchs und die Kalibriertaste bleiben verfügbar.
Die GPU-Ausgabe wird direkt im unteren SDL-Fenster bedient, nicht über die
QXL-Vorschau.

Für den Starter die GPU-Arbeitskopie als Start-Image und die paketierte Host-EXE
als „QEMU Spiel-PC“ wählen, dann den Grafikpfad auf `qemu3dfx` stellen und
„Bildschirme tauschen“ einschalten. „Frisches Image einrichten“ installiert in
diesem Modus keinen neuen Gast, sondern prüft eine bereits migrierte Kopie.
Eine frische CF-Kopie zuerst mit SwiftShader vollständig einrichten, danach
eine separate Kopie mit `prepare-qemu3dfx.ps1` migrieren. Zurückwechseln bedeutet
den bisherigen Renderer, dessen QEMU und dessen unveränderte Arbeitskopie
wieder auszuwählen.

`qemu3dfx_image.py <eingehängte-GPU-Kopie> --restore` stellt eine unveränderte
Offline-Installation zurück. Wenn der Gast seine Registry inzwischen verändert
hat, wird eine automatische Rücknahme verweigert, damit keine neueren
Geräteeinstellungen verloren gehen. Die ursprüngliche Spielkopie ist der
unabhängige Rückweg.
