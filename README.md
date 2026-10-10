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
Das Original-CF-Image wird nicht verändert. „Emulator deinstallieren…“ ganz
unten in den Einstellungen entfernt Laufzeitdaten, Protokolle und
Einstellungen, auf Wunsch auch die Arbeitskopie.

[SwiftShader und QXL vorbereiten](docs/grafikdateien.md): passende Dateien,
Downloadquelle für QXL und Auswahl im Starter.

## Grafik und Voraussetzungen

Windows 10/11, die im Starter angezeigten Abhängigkeiten und ausreichend freier
Speicherplatz für die Arbeitskopie werden benötigt.

Der vorgesehene Grafikpfad ist QEMU-3dfx, einzustellen unter
„Emulationseinstellungen → Grafikpfad“: Beide Bildschirme nutzen die
Host-Grafikkarte, mit getrennten Fenstern für unten und oben. Das Bedienfeld
zeigt weiterhin den unteren Spielbildschirm. SwiftShader rendert in Software
und ist nur die Alternative, falls QEMU-3dfx auf dem PC nicht funktioniert.

Der Start kann mehrere Minuten dauern.

## Andere CF-Karten und Datenbanken

CF-Karte und Datenbank gehören zusammen; viele unterscheiden sich nur in den
enthaltenen Spielen. Ein CF-Image mit anderer Spielversion wird wie das
geprüfte eingerichtet. Der Starter nennt dabei jede Datei der Karte, die von
der geprüften Version abweicht.

Unter „Emulationseinstellungen → Datenbank-Schlüssel (D3)“ steht standardmäßig
`auto`: Ist der Schlüssel einer gewählten Datenbank unbekannt, sucht ihn der
Starter einmalig (je nach CPU bis etwa 20 Minuten) und merkt ihn sich. Passt
eine Datenbank nicht zu den Adressen, die die Bridge nachbildet, startet sie
trotzdem; der Starter nennt dann die betroffenen Funktionen (z. B. Touch,
Münzeingang).

## Protokolle

Jede Logart nutzt eine feste Datei im Laufzeitordner `logs` (bzw. `NVRAM` im
Gast). Ab 10 MB beginnt sie neu; der vorige Inhalt bzw. der letzte Lauf bleibt
als `*.old.*` erhalten.

CF-Images, Datenbank-Firmware und proprietäre Gastdateien werden nicht
mitgeliefert. Die Quellen der enthaltenen Open-Source-Komponenten liegen beim
Release. Der Projektquellcode lässt sich mit Git herunterladen; weitere
technische Informationen stehen unter [docs](docs).

## Geprüfte Kombinationen

Mit diesen CF-Karten und Datenbanken läuft der Emulator sicher.

**Kombination 1:** CF-Karte mit der Startanzeige `M440_945_KOMBI_V20`,
`SW-Date: 20121212`, `ADP - LOADER VERSION V7.0.0.5`.

| Teil | Datei | SHA-256 |
| --- | --- | --- |
| CF-Karte | `game.exe` | `27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d` |
| Loader | `Loader_61640403_L5.0b_2MB.bin` | `b0768c65b34834c7a740615d2b0abdb470aec012fe4dc4a3c11531efa221e109` |
| Factory | `FactoryReset_61640403.xc` | `4f088db4af5f4a5d112a003ef312eb19b4388c25902ffa03f75742379a0cd5c4` |
| Konfiguration | `M90_Las_Vegas.bin` | `dce3a865b742123c95ea4f0b14fa16f287ddf90cd86432f68b2301b70a919783` |
| Spielepaket | `Magie_90_CC4.bin` | `593cf4b3a1ccc83f206e1492e44b9d303ea3c05990059b8659d8308da1dc2ee8` |
