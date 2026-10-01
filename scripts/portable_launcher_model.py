"""Selection, validation, and launch plan for a user-supplied M90 setup.

Only hashes of known-compatible input files are accepted. In particular, this
module never copies owner disk images or database dumps into the application.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess

from admission_card import ERGO_M90_ID, inspect_eeprom


KNOWN_SHA256 = {
    "database": "593CF4B3A1CCC83F206E1492E44B9D303EA3C05990059B8659D8308DA1DC2EE8",
    "loader": "B0768C65B34834C7A740615D2B0ABDB470AEC012FE4DC4A3C11531EFA221E109",
    "factory": "4F088DB4AF5F4A5D112A003EF312EB19B4388C25902FFA03F75742379A0CD5C4",
    "config": "DCE3A865B742123C95EA4F0B14FA16F287DDF90CD86432F68B2301B70A919783",
}
KNOWN_CF_BYTES = 16_139_354_112

# One schema for the form, saved values and start validation. No free-form
# QEMU arguments: hardware addresses and coupled ports are intentionally fixed.
EMULATION_FIELDS = (
    ("guest_ram_mib", "Spiel-PC", "RAM (MiB)", int, (512, 3072), "Standard: 2048; mehr RAM beschleunigt die CPU nicht."),
    ("guest_vcpus", "Spiel-PC", "Virtuelle CPUs", int, (1, 2), "Standard: 1; XP/Image-Kompatibilität bei Änderungen beachten."),
    ("acceleration", "Spiel-PC", "Beschleunigung", str, ("whpx", "tcg"), "WHPX: Windows-Hypervisor; TCG: Software-Emulation, langsamer."),
    ("qxl_vram_mib", "Spiel-PC", "QXL-Grafikspeicher je Anzeige (MiB)", int, (64, 128, 256), "Standard: 64; gilt für beide QXL-Geräte."),
    ("usb_tablet", "Spiel-PC", "USB-Tablet statt PS/2-Maus ergänzen", bool, (), "Experimentell: benötigt einen passenden Gasttreiber."),
    ("swap_displays", "Spiel-PC", "Bildschirme tauschen", bool, (), "Nur bei abweichendem Image; Touch-Vorschau bleibt am Ausgang lower."),
    ("sound_enabled", "Spiel-PC", "Ton auf dem PC ausgeben", bool, (), "AC97/WinMM über SDL, ohne Mikrofonaufnahme; aus schaltet nur die Host-Ausgabe stumm. Die virtuelle Soundkarte bleibt aktiv."),
    ("db_icount_shift", "Datenbank", "Instruktionstakt (icount shift)", int, (6, 5), "6: max. 15,625 Mio./s; 5: 31,25 Mio./s, bekannte Absturzgefahr. Nicht zyklengenau."),
    ("safe_tb", "Datenbank", "Stabiler Einzelinstruktionsmodus", bool, (), "Empfohlen: an. Aus nutzt größere TCG-Blöcke; bekannte Interrupt-Abstürze möglich."),
    ("db_timer_interval", "Datenbank", "CPU-Laufabschnitt (Sekunden)", float, (0.005, 0.05), "Standard: 0.05; kleinere Abschnitte erhöhen den Debugger-Aufwand. Kein Hardware-Timer-Preset."),
    ("duart_x1_hz", "Datenbank", "DUART-Eingangstakt (Hz)", int, (1, 10000000), "Standard: 3686400 (angenommen); nicht der 16-MHz-CPU-Takt. Änderung beeinflusst Timer."),
    ("db_connect_timeout", "Datenbank", "Verbindungs-Wartezeit (Sekunden)", float, (10, 600), "Standard: 120; Zeitlimit für die Verbindung zum Spiel-PC."),
    ("database_date", "Datenbank", "Startdatum/Uhrzeit (Programmer und RTC)", str, (), "Format: 2012-02-01T22:14:00; M90 erwartet normalerweise Jahr 2012. RTC läuft danach weiter."),
    ("door_open", "Datenbank", "Tür beim Start offen", bool, (), "Standard: geschlossen; kann den Servicebetrieb auslösen."),
    ("trace_diagnostics", "Protokoll und Bedienung", "Zusätzliche Diagnose-Watchpoints", bool, (), "Standard: aus; kann die Datenbank deutlich verlangsamen."),
    ("show_live_log", "Protokoll und Bedienung", "Live-Protokoll öffnen", bool, (), "Die Logdatei wird auch ohne sichtbares Fenster geschrieben."),
    ("show_control_window", "Protokoll und Bedienung", "Bedienfenster öffnen", bool, (), "Automatentasten, Tür und Touch-Vorschau anzeigen."),
)


def validate_emulation(selection: Selection) -> list[str]:
    issues = []
    for key, _group, label, kind, limits, _help in EMULATION_FIELDS:
        value = getattr(selection, key)
        if kind is bool:
            valid = type(value) is bool
        elif kind in (int, float):
            valid = type(value) in ((int,) if kind is int else (int, float))
            if valid:
                valid = (value in limits if key in ("qxl_vram_mib", "db_icount_shift")
                         else limits[0] <= value <= limits[1])
                valid = valid and math.isfinite(value)
        elif key == "database_date":
            try:
                date = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
                valid = 2000 <= date.year <= 2099 and date.strftime("%Y-%m-%dT%H:%M:%S") == value
            except (ValueError, TypeError):
                valid = False
        else:
            valid = isinstance(value, str) and value in limits
        if not valid:
            issues.append(f"{label}: ungültiger Wert ({value!r})")
    if not issues and math.ceil(selection.db_timer_interval * selection.duart_x1_hz / (32 * 0x3A)) > 128:
        issues.append("DUART-Takt und CPU-Laufabschnitt überschreiten das Timer-Budget (128 Interrupts). Laufabschnitt verkleinern.")
    return issues


@dataclass(frozen=True)
class Selection:
    image: str = ""
    original_image: str = ""
    swiftshader: str = ""
    qxl_driver_dir: str = ""
    database: str = ""
    loader: str = ""
    factory: str = ""
    config: str = ""
    admission_eeprom: str = ""
    qemu_x86: str = ""
    qemu_m68k: str = ""
    python: str = ""
    show_live_log: bool = False
    swap_displays: bool = False
    sound_enabled: bool = True
    guest_ram_mib: int = 2048
    guest_vcpus: int = 1
    acceleration: str = "whpx"
    qxl_vram_mib: int = 64
    usb_tablet: bool = False
    db_icount_shift: int = 6
    safe_tb: bool = True
    db_timer_interval: float = 0.05
    duart_x1_hz: int = 3686400
    db_connect_timeout: float = 120.0
    database_date: str = "2012-02-01T22:14:00"
    door_open: bool = False
    trace_diagnostics: bool = False
    show_control_window: bool = True

    @classmethod
    def from_json(cls, path: Path) -> Selection:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        allowed = set(cls.__dataclass_fields__)
        if not isinstance(data, dict):
            raise ValueError("Einstellungen müssen ein JSON-Objekt sein")
        result = cls(**{key: value for key, value in data.items() if key in allowed})
        issues = validate_emulation(result)
        if issues:
            raise ValueError("\n".join(issues))
        return result

    def save(self, path: Path) -> None:
        issues = validate_emulation(self)
        if issues:
            raise ValueError("\n".join(issues))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def validate_selection(selection: Selection) -> list[str]:
    """Return actionable failures; never modify selected files."""
    issues: list[str] = validate_emulation(selection)
    paths = {
        "CF-Image": selection.image,
        "Datenbank": selection.database,
        "Loader": selection.loader,
        "Factory": selection.factory,
        "Konfiguration": selection.config,
        "Zulassungskarte": selection.admission_eeprom,
        "QEMU für Spiel-PC": selection.qemu_x86,
        "QEMU für Datenbank": selection.qemu_m68k,
        "Python": selection.python,
    }
    for label, value in paths.items():
        if not value:
            issues.append(f"{label}: Datei auswählen")
        elif not Path(value).is_file():
            issues.append(f"{label}: Datei nicht gefunden: {value}")
    if issues:
        return issues
    for key, label in (
        ("database", "Datenbank"), ("loader", "Loader"),
        ("factory", "Factory"), ("config", "Konfiguration"),
    ):
        actual = file_sha256(Path(getattr(selection, key)))
        if actual != KNOWN_SHA256[key]:
            issues.append(
                f"{label}: Diese Version ist noch nicht als M90-kompatibel "
                f"verifiziert (SHA-256 {actual[:12]}…)."
            )
    if Path(selection.admission_eeprom).stat().st_size != 256:
        issues.append("Zulassungskarte: EEPROM muss genau 256 Byte groß sein")
    else:
        try:
            _, model = inspect_eeprom(Path(selection.admission_eeprom).read_bytes())
        except ValueError as exc:
            issues.append(f"Zulassungskarte: {exc}")
        else:
            if model != ERGO_M90_ID:
                issues.append(
                    f"Zulassungskarte: Modell {model.hex(' ').upper()} ist nicht M90"
                )
    if selection.original_image and Path(selection.image).resolve() == Path(selection.original_image).resolve():
        issues.append("CF-Image: Original darf nicht als Arbeitskopie gestartet werden")
    image_size = Path(selection.image).stat().st_size
    if image_size != KNOWN_CF_BYTES:
        issues.append(
            f"CF-Image: erwartete Größe {KNOWN_CF_BYTES} Byte, gefunden {image_size} Byte"
        )
    for key, expected in (
        ("qemu_x86", "qemu-system-x86_64.exe"),
        ("qemu_m68k", "qemu-system-m68k.exe"),
        ("python", "python.exe"),
    ):
        if Path(getattr(selection, key)).name.lower() != expected:
            issues.append(f"{expected}: falsches Programm ausgewählt")
    return issues


def check_runtime(selection: Selection) -> list[str]:
    """Check installed interpreters/emulators without launching a VM."""
    issues = validate_selection(selection)
    if issues:
        return issues
    for path, label, arguments in (
        (selection.qemu_x86, "QEMU Spiel-PC", ["--version"]),
        (selection.qemu_m68k, "QEMU Datenbank", ["--version"]),
        (selection.python, "Python 3.10+", [
            "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
        ]),
    ):
        try:
            completed = subprocess.run(
                [path, *arguments], capture_output=True, text=True,
                timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            issues.append(f"{label}: nicht ausführbar ({exc})")
        else:
            if completed.returncode != 0:
                issues.append(f"{label}: Versions-/Startprüfung fehlgeschlagen")
    if not Path(selection.python).with_name("pythonw.exe").is_file():
        issues.append("Python: pythonw.exe fehlt; das Bedienfenster kann so nicht starten")
    return issues


def launch_command(selection: Selection, project: Path) -> list[str]:
    """Build a no-shell command; each user path remains one argument."""
    issues = validate_emulation(selection)
    if issues:
        raise ValueError("\n".join(issues))
    command = [
        "powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(project / "program-and-start-emulator.ps1"),
        "-Qemu", selection.qemu_x86,
        "-QemuM68k", selection.qemu_m68k,
        "-Python", selection.python,
        "-Image", selection.image,
        "-Database", selection.database,
        "-Loader", selection.loader,
        "-FactoryReset", selection.factory,
        "-Config", selection.config,
        "-AdmissionEeprom", selection.admission_eeprom,
        "-GuestRamMiB", str(selection.guest_ram_mib),
        "-GuestVcpus", str(selection.guest_vcpus),
        "-Acceleration", selection.acceleration,
        "-QxlVramMiB", str(selection.qxl_vram_mib),
        "-DbIcountShift", str(selection.db_icount_shift),
        "-DbTimerInterval", str(selection.db_timer_interval),
        "-DuartX1Hz", str(selection.duart_x1_hz),
        "-DbConnectTimeout", str(selection.db_connect_timeout),
        "-DatabaseDate", selection.database_date,
    ]
    if not selection.safe_tb:
        command.append("-FastTb")
    if selection.usb_tablet:
        command.append("-UsbTablet")
    if selection.door_open:
        command.append("-DoorOpen")
    if selection.trace_diagnostics:
        command.append("-TraceDiagnostics")
    if not selection.show_control_window:
        command.append("-NoControlWindow")
    if not selection.sound_enabled:
        command.append("-MuteAudio")
    if not selection.show_live_log:
        command.append("-NoEventWindow")
    if selection.swap_displays:
        command.append("-SwapDisplays")
    return command
