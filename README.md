# M90-Emulator

Ein Windows-Programm, das einen Merkur-M90-Automaten auf dem PC nachbildet.
Es startet den virtuellen Spiel-PC und die Datenbank, richtet eine Arbeitskopie
des CF-Images ein und bietet ein Bedienfenster für Touch, Tasten, Türschalter,
Service und Spielgeldeinwurf. Ton und ein optionales Live-Protokoll sind enthalten.

[Emulator herunterladen](https://github.com/An3-Alex/Merkur_Emulator/releases/latest)

## Starten

1. `M90-Emulator.exe` öffnen und die benötigten Abhängigkeiten über den Starter einrichten.
2. Eigenes CF-Image, einen getrennten Arbeitskopie-Pfad und die passenden
   Datenbankdateien auswählen: Loader, Factory, Konfiguration und Spielepaket.
   Zulassungskarten-EEPROM und QXL-Treiber werden ebenfalls benötigt, die
   SwiftShader-DLL nur für den Grafikpfad SwiftShader.
3. Beim ersten Mal „Frisches Image einrichten“ verwenden. Danach „Emulator starten“.
   Eine bereits eingerichtete Arbeitskopie kann direkt gestartet werden.

Mit „Alles beenden“ werden die zum Emulator gehörenden Prozesse beendet.
Dateipfade und Einstellungen bleiben für den nächsten Start gespeichert.
Das Original-CF-Image wird nicht verändert.

[SwiftShader und QXL vorbereiten](docs/grafikdateien.md): passende Dateien,
Downloadquelle für QXL und Auswahl im Starter.

## Grafik und Voraussetzungen

Windows 10/11, die im Starter angezeigten Abhängigkeiten und ausreichend freier
Speicherplatz für die Arbeitskopie werden benötigt. Der Grafikpfad ist wählbar:
SwiftShader oder QEMU-3dfx. Bei QEMU-3dfx nutzen beide Bildschirme die
Host-Grafikkarte, mit getrennten Fenstern für unten und oben. Das Bedienfeld
zeigt weiterhin den unteren Spielbildschirm.

Der Start kann mehrere Minuten dauern. Grafik, Ton und Geräteanbindung sind
noch nicht vollständig nachgebildet. Andere Spielepakete benötigen passende
Dateien; ihre Auswahl allein garantiert keine Kompatibilität.

CF-Images, Datenbank-Firmware und proprietäre Gastdateien werden nicht
mitgeliefert. Die Quellen der enthaltenen Open-Source-Komponenten liegen beim
Release. Der Projektquellcode lässt sich mit Git herunterladen; weitere
technische Informationen stehen unter [docs](docs).
