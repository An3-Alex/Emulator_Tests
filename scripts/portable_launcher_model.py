"""Selection, validation, and launch plan for a user-supplied M90 setup.

Only hashes of known-compatible input files are accepted. In particular, this
module never copies owner disk images or database dumps into the application.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import subprocess


KNOWN_SHA256 = {
    "database": "593CF4B3A1CCC83F206E1492E44B9D303EA3C05990059B8659D8308DA1DC2EE8",
    "loader": "B0768C65B34834C7A740615D2B0ABDB470AEC012FE4DC4A3C11531EFA221E109",
    "factory": "4F088DB4AF5F4A5D112A003EF312EB19B4388C25902FFA03F75742379A0CD5C4",
    "config": "DCE3A865B742123C95EA4F0B14FA16F287DDF90CD86432F68B2301B70A919783",
}
KNOWN_CF_BYTES = 16_139_354_112


@dataclass(frozen=True)
class Selection:
    image: str = ""
    database: str = ""
    loader: str = ""
    factory: str = ""
    config: str = ""
    admission_eeprom: str = ""
    qemu_x86: str = ""
    qemu_m68k: str = ""
    python: str = ""
    show_live_log: bool = False

    @classmethod
    def from_json(cls, path: Path) -> Selection:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in data.items() if key in allowed})

    def save(self, path: Path) -> None:
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
    issues: list[str] = []
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
        "-DbIcountShift", "6",
    ]
    if not selection.show_live_log:
        command.append("-NoEventWindow")
    return command
