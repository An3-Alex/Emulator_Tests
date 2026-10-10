"""Selection, validation, and launch plan for a user-supplied M90 setup.

Owner inputs are selectable independently of the original M90 file identities.
This module never copies disk images or database dumps into the application.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess

from admission_card import inspect_eeprom
from database_key import parse_key


# One schema for the form, saved values and start validation. No free-form
# QEMU arguments: hardware addresses and coupled ports are intentionally fixed.
EMULATION_FIELDS = (
    ("guest_ram_mib", "Spiel-PC", "RAM (MiB)", int, (512, 3072), "Standard: 2048; mehr RAM beschleunigt die CPU nicht."),
    ("guest_vcpus", "Spiel-PC", "Virtuelle CPUs", int, (1, 2), "Standard: 1; XP/Image-Kompatibilität bei Änderungen beachten."),
    ("acceleration", "Spiel-PC", "Beschleunigung", str, ("whpx", "tcg"), "WHPX: Windows-Hypervisor; TCG: Software-Emulation, langsamer."),
    ("graphics_backend", "Spiel-PC", "Grafikpfad", str, ("qemu3dfx", "swiftshader"), "Standard: qemu3dfx. QEMU-3dfx und seine Gastdateien werden automatisch bereitgestellt. Beide Ausgänge über GPU; der untere Bildschirm ist die Primäranzeige. Bedienfeld-Vorschau wird alle 2 Sekunden aktualisiert. SwiftShader rendert in Software und ist nur die Alternative, falls QEMU-3dfx nicht funktioniert."),
    ("qxl_vram_mib", "Spiel-PC", "QXL-Framebuffer je Anzeige (MiB)", int, (64, 128, 256), "Standard: 64; nicht der VRAM der Host-GPU. QEMU reserviert mindestens das Doppelte je QXL-PCI-RAM-Bereich. 256 MiB vergrößert diese Bereiche auf je 512 MiB und kann mit alten XP-Treibern Probleme verursachen."),
    ("usb_tablet", "Spiel-PC", "USB-Tablet statt PS/2-Maus ergänzen", bool, (), "Experimentell: benötigt einen passenden Gasttreiber."),
    ("swap_displays", "Spiel-PC", "Bildschirme tauschen", bool, (), "Bei QEMU-3dfx immer an: der untere Bildschirm ist die Primäranzeige. Bei SwiftShader nur bei abweichendem Image. Touch-Vorschau bleibt am Ausgang lower."),
    ("sound_enabled", "Spiel-PC", "Ton auf dem PC ausgeben", bool, (), "PCM-Bridge über COM1, ohne zusätzliche XP-Audiotreiber oder Mikrofonaufnahme. Aus schaltet nur die Host-Ausgabe stumm; die Bridge bleibt aktiv."),
    ("db_icount_shift", "Datenbank", "Instruktionstakt (icount shift)", int, (6, 5), "6: max. 15,625 Mio./s; 5: 31,25 Mio./s, bekannte Absturzgefahr. Nicht zyklengenau."),
    ("safe_tb", "Datenbank", "Stabiler Einzelinstruktionsmodus", bool, (), "Empfohlen: an. Aus nutzt größere TCG-Blöcke; bekannte Interrupt-Abstürze möglich."),
    ("db_timer_interval", "Datenbank", "CPU-Laufabschnitt (Sekunden)", float, (0.005, 0.05), "Standard: 0.01; häufigere Geräte-/Eingabebedienung. Kein Hardware-Timer-Preset und keine Änderung des Instruktionstakts."),
    ("duart_x1_hz", "Datenbank", "DUART-Eingangstakt (Hz)", int, (1, 10000000), "Standard: 3686400 (angenommen); nicht der 16-MHz-CPU-Takt. Änderung beeinflusst Timer."),
    ("db_connect_timeout", "Datenbank", "Verbindungs-Wartezeit (Sekunden)", float, (10, 600), "Standard: 120; Zeitlimit für die Verbindung zum Spiel-PC."),
    ("database_date", "Datenbank", "Startdatum/Uhrzeit (Programmer und RTC)", str, (), "Format: JJJJ-MM-TTThh:mm:ss. Programmer, RTC und INITVIDEO verwenden diesen Wert; die RTC läuft danach weiter."),
    ("db_key", "Datenbank", "Datenbank-Schlüssel (D3)", str, (), "auto: gespeicherter bzw. bekannter Schlüssel wird geprüft; passt keiner, sucht der Starter den Schlüssel der gewählten Datenbank einmalig (je nach CPU bis etwa 20 Minuten) und merkt ihn sich. Alternativ 8 Hex-Ziffern eintragen."),
    ("door_open", "Datenbank", "Tür beim Start offen", bool, (), "Standard: geschlossen; kann den Servicebetrieb auslösen."),
    ("trace_diagnostics", "Protokoll und Bedienung", "Zusätzliche Diagnose-Watchpoints", bool, (), "Standard: aus; kann die Datenbank deutlich verlangsamen."),
    ("show_live_log", "Protokoll und Bedienung", "Live-Protokoll öffnen", bool, (), "Die Logdatei wird auch ohne sichtbares Fenster geschrieben."),
    ("show_control_window", "Protokoll und Bedienung", "Bedienfenster öffnen", bool, (), "Automatentasten, +1 € über MP, Tür und Touch-Vorschau anzeigen."),
)


def validate_emulation(selection: Selection) -> list[str]:
    issues = []
    if selection.audio_output != "bridge":
        issues.append("Audio-Ausgabe: ausschließlich PCM-Bridge unterstützt")
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
        elif key == "db_key":
            try:
                valid = isinstance(value, str) and (value == "auto" or parse_key(value) is not None)
            except ValueError:
                valid = False
        else:
            valid = isinstance(value, str) and value in limits
        if not valid:
            issues.append(f"{label}: ungültiger Wert ({value!r})")
    if not issues and math.ceil(selection.db_timer_interval * selection.duart_x1_hz / (32 * 0x3A)) > 128:
        issues.append("DUART-Takt und CPU-Laufabschnitt überschreiten das Timer-Budget (128 Interrupts). Laufabschnitt verkleinern.")
    if selection.graphics_backend == "qemu3dfx" and not selection.swap_displays:
        issues.append("QEMU-3dfx: ‚Bildschirme tauschen‘ einschalten; lower muss Primäranzeige sein.")
    if selection.graphics_backend == "qemu3dfx" and selection.guest_ram_mib > 2048:
        issues.append("QEMU-3dfx: maximal 2048 MiB Gast-RAM wählen; der getrennte GPU-Speicherbereich liegt darüber.")
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
    # QEMU-3dfx, the default graphics path, needs the lower screen as primary.
    swap_displays: bool = True
    sound_enabled: bool = True
    audio_output: str = "bridge"
    guest_ram_mib: int = 2048
    guest_vcpus: int = 1
    acceleration: str = "whpx"
    graphics_backend: str = "qemu3dfx"
    qxl_vram_mib: int = 64
    usb_tablet: bool = False
    db_icount_shift: int = 6
    safe_tb: bool = True
    db_timer_interval: float = 0.01
    duart_x1_hz: int = 3686400
    db_connect_timeout: float = 120.0
    database_date: str = "2012-02-01T22:14:00"
    db_key: str = "auto"
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
        # Migrate the retired output without changing owner file selections.
        if data.get("audio_output") == "ac97":
            data["audio_output"] = "bridge"
        # Settings saved before QEMU-3dfx became the default belong to a
        # working copy set up for SwiftShader with the upper screen first.
        data.setdefault("graphics_backend", "swiftshader")
        data.setdefault("swap_displays", False)
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
    if Path(selection.admission_eeprom).stat().st_size != 256:
        issues.append("Zulassungskarte: EEPROM muss genau 256 Byte groß sein")
    else:
        try:
            inspect_eeprom(Path(selection.admission_eeprom).read_bytes())
        except ValueError as exc:
            issues.append(f"Zulassungskarte: {exc}")
    if selection.original_image and Path(selection.image).resolve() == Path(selection.original_image).resolve():
        issues.append("CF-Image: Original darf nicht als Arbeitskopie gestartet werden")
    if selection.original_image and Path(selection.original_image).is_file() and \
            Path(selection.image).samefile(selection.original_image):
        issues.append("CF-Image: Original darf nicht als Arbeitskopie gestartet werden (derselbe Dateiknoten)")
    for key, expected in (
        ("qemu_x86", "qemu-system-x86_64.exe"),
        ("qemu_m68k", "qemu-system-m68k.exe"),
        ("python", "python.exe"),
    ):
        if Path(getattr(selection, key)).name.lower() != expected:
            issues.append(f"{expected}: falsches Programm ausgewählt")
    return issues


def graphics_selection(selection: Selection, project: Path) -> Selection:
    """Select our complete GPU runtime automatically, never a stock EXE."""
    if selection.graphics_backend != "qemu3dfx":
        return selection
    from qemu3dfx_package import validate
    root = project / "build/qemu3dfx-runtime"
    validate(root)
    return replace(selection, qemu_x86=str(root / "host/qemu-system-x86_64.exe"),
                   swap_displays=True)


def check_runtime(selection: Selection, *, require_gpu_image: bool = True) -> list[str]:
    """Check installed interpreters/emulators without launching a VM."""
    issues = validate_selection(selection)
    if issues:
        return issues
    if selection.graphics_backend == "qemu3dfx":
        from qemu3dfx_package import verify_launch, validate
        try:
            if require_gpu_image:
                verify_launch(Path(selection.image), Path(selection.qemu_x86))
            else:
                validate(Path(selection.qemu_x86).resolve().parent.parent)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return [f"QEMU-3dfx: GPU-Arbeitskopie oder Laufzeitpaket nicht vorbereitet ({exc})"]
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
    # "auto" is resolved by the launcher before the start; a key written here
    # is passed as decimal so PowerShell binds it to its uint32 parameter.
    key = parse_key(selection.db_key)
    if key is not None:
        command.extend(("-D3", str(key)))
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
    command.append("-AudioBridge")
    if not selection.show_live_log:
        command.append("-NoEventWindow")
    if selection.swap_displays:
        command.append("-SwapDisplays")
    if selection.graphics_backend == "qemu3dfx":
        command.extend(("-GraphicsBackend", "qemu3dfx"))
    return command
