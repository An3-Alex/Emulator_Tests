# SwiftShader und QXL vorbereiten

Die Grafikdateien werden im Starter ausgewählt. Die Einrichtung kopiert sie in
die getrennte Arbeitskopie des CF-Images; das Original bleibt unverändert.
Auf dem Windows-10/11-Host müssen diese XP-Treiber nicht installiert werden.

## QXL-Treiber

Verwendet wird **qxl-win32-0.6.1**, der alte 32-Bit-Treiber für den XP-Gast.

1. Das Paket vom [offiziellen SPICE-Server herunterladen](https://www.spice-space.org/download/binaries/qxl-win32-0.6.1.zip).
2. Das ZIP vollständig in einen eigenen Ordner entpacken.
3. Im Starter unter „QXL-Treiberordner“ den Ordner auswählen, in dem
   `qxl.inf`, `qxl.sys` und `qxldd.dll` direkt liegen.

Nicht den neueren QXL-WDDM-DOD-Treiber und nicht ein x64-Paket verwenden.
Der Starter prüft die Dateien und installiert den ausgewählten Treiber im Gast
während „Frisches Image einrichten“.

QXL ist freie Software. Der Windows-XDDM-Treiber steht unter
[GPLv2](https://gitlab.com/spice/win32/qxl/-/raw/master/xddm/COPYING).
Bei eigener Weiterverteilung müssen insbesondere Lizenzhinweise und die
GPL-Anforderungen an den zugehörigen Quellcode eingehalten werden.
Der [Treiberquellcode](https://gitlab.com/spice/win32/qxl) ist öffentlich zugänglich.
Ein Downloadlink allein ersetzt nicht die Lizenzpflichten einer eigenen Verteilung.

## SwiftShader-DLL

Der aktuelle Starter benötigt **SwiftShader Build 5003, Direct3D 9, x86/32 Bit,
Windows XP**. Eine beliebige Datei namens `d3d9.dll` reicht nicht aus.

1. Eine rechtmäßig bezogene Kopie dieses Legacy-Pakets einschließlich seiner
   Lizenz und README bereithalten und entpacken.
2. Die **32-Bit-`d3d9.dll`** daraus im Feld „SwiftShader-DLL“ auswählen.
   Nicht unsere Proxy-DLL und nicht die Windows-System-DLL auswählen.
3. Die übrigen Image- und Datenbankdateien auswählen und
   „Frisches Image einrichten“ ausführen. Anschließend „Emulator starten“.

SHA-256 der unterstützten SwiftShader-DLL:

```text
FC5994B209A57A77275E5ECEE1904CD9139A344C69E221E54F05AF90580A90C9
```

Zur Kontrolle in PowerShell:

```powershell
Get-FileHash -LiteralPath 'D:\Grafikdateien\SwiftShader\d3d9.dll' -Algorithm SHA256
```

Bei „Version nicht verifiziert“ stimmt die ausgewählte Datei nicht mit der
unterstützten Version überein. Die Prüfung sollte nicht umgangen werden.
Die Einrichtung legt den Renderer als `swiftshader_d3d9.dll` neben den
Gastprogrammen ab; `d3d9.dll` dort ist die mitgelieferte Weiterleitungs-DLL.
Keine Dateien von Hand in `Windows\System32` ersetzen.

### Warum nicht einfach die aktuelle Google-Version?

[Googles aktuelles SwiftShader-Projekt](https://github.com/google/swiftshader)
implementiert Vulkan und steht unter Apache-2.0. Es liefert nicht die hier
benötigte alte XP-D3D9-DLL. Auch Umbenennen einer Vulkan-DLL macht sie nicht
kompatibel. Die Apache-Lizenz des heutigen Quellcodes belegt außerdem keine
Weiterverteilungsrechte für eine ältere Demo-Binärdatei.

Für Build 5003 ist hier kein frei weiterverteilbares Downloadpaket zugesichert;
diese DLL wird deshalb nicht im Emulator-Release mitgeliefert. Maßgeblich sind
die Lizenzbedingungen des tatsächlich verwendeten Legacy-Pakets. Ein eigener
Build aus geeignetem historischem Quellcode wäre eine separate Portierung und
ist kein bereits unterstützter Ersatz.

## Auswahl des Grafikpfads

Bei „SwiftShader“ werden beide Anzeigen in Software gerendert. Bei „QEMU-3dfx“
nutzt der untere Spielbildschirm die Host-GPU, während der obere weiterhin
SwiftShader verwendet. Die SwiftShader-DLL wird daher für beide Optionen benötigt.

Die Einstellung „QXL-Framebuffer je Anzeige“ ist nicht der Grafikspeicher der
Host-GPU. Sie setzt QEMUs `vgamem_mb`. QEMU reserviert dafür je QXL-Gerät einen
PCI-RAM-Bereich von mindestens der doppelten Größe: bei 128 MiB mindestens
256 MiB je Gerät, bei 256 MiB mindestens 512 MiB je Gerät. Die separaten
QXL-Oberflächenspeicher bleiben dabei unverändert. Mehr Framebuffer-Speicher
beschleunigt die 3D-Ausgabe nicht automatisch; für zwei 800×600-Anzeigen ist
bereits die Standardeinstellung 64 MiB großzügig. Bei Abstürzen nach einer
Erhöhung auf den zuvor funktionierenden Wert zurückgehen.

## Langsame Walzen mit QEMU-3dfx

QEMU-3dfx ersetzt nicht sämtliche emulierte Hardware durch native Hardware.
Auch mit GPU-Rendering können die Gast-CPU, Grafikaufrufe durch die
Virtualisierungsgrenze und das Software-Rendering der oberen Anzeige bremsen.
Die Grafikpfad-Auswahl allein belegt deshalb keinen bestimmten Engpass.

Die Spiel-PC-Beschleunigung sollte auf WHPX stehen, sofern diese auf dem Host
verfügbar ist. Die QXL-Speichergröße ist kein FPS-Regler. Zum Eingrenzen im
Laufzeitordner `logs/swiftshader-qemu.stderr.log` ansehen: Die Rendererzeile
nennt die verwendete OpenGL-GPU; `M90_GPU_PRESENT` protokolliert ungefähr alle
zehn Sekunden die tatsächlich ausgeführten Bildübergaben. Der Dateiname gilt
auch beim QEMU-3dfx-Grafikpfad. FPS meint hier Bildübergaben, nicht zwingend
unterschiedliche Bildinhalte. Für einen Vergleich im selben Spiel einmal die
Werte im Standbild und während laufender Walzen erfassen.
