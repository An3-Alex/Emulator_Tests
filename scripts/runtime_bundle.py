"""Materialize our scripts and binaries carried by a frozen launcher.

Owner images, database dumps and third-party binaries are never part of this
bundle. Logs and generated database state live beside, not inside, the EXE.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sys


POWERSHELL_FILES = (
    "program-and-start-emulator.ps1",
    "start-real-database.ps1",
    "test-swiftshader.ps1",
)
SHELL_FILES = (
    "prepare_image_stage.sh",
    "stage_display_verify.sh",
    "retry_qxl_install.sh",
    "finalize_image_stage.sh",
    "check_image_stage.sh",
    "update_runtime_graphics.sh",
    "stage_audio_image.sh",
    "export_audio_diagnostics.sh",
    "image_partition.sh",
    "stage_audio_bridge.sh",
    "install_qxl_helper_shell.sh",
)
REQUIRED_PYTHON_FILES = (
    "audio_driver_package.py",
    "audio_diagnostics.py",
    "audio_legacy_driver.py",
    "image_partition.py",
    "audio_image_stage.py",
    "audio_setup_runner.py",
    "audio_bridge_image.py",
    "pcm_audio_bridge.py",
    "admission_card.py",
    "cabinet_control_panel.py",
    "cabinet_controls.py",
    "emulator_launcher.py",
    "emulator_processes.py",
    "duart_timer.py",
    "graphics_update.py",
    "event_log_viewer.py",
    "image_setup.py",
    "inspect_owner_database.py",
    "m68k_database_bridge.py",
    "m68k_database_transform.py",
    "m68k_qemu_harness.py",
    "owner_config_runtime.py",
    "owner_database_runtime.py",
    "portable_launcher_model.py",
    "qmp_capture.py",
    "qxl_setup_runner.py",
    "rtc4543.py",
    "runtime_bundle.py",
    "serialloader_chip_emulator.py",
)
OWN_BINARIES = (
    Path("build/audio-software.exe"),
    Path("build/audio-installer.exe"),
    Path("build/audio-verify.exe"),
    Path("build/Cgos.dll"),
    Path("build/display-bootstrap.exe"),
    Path("build/qxl-installer.exe"),
    Path("build/display-verify.exe"),
    Path("build/d3d9-proxy/d3d9.dll"),
    Path("build/sram-compat/FBWFLIB.dll"),
    Path("build/irrklang-proxy/irrKlang.dll"),
)


def bundled_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def runtime_root() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[1]
    override = os.environ.get("M90_RUNTIME_ROOT")
    if override:
        return Path(override)
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return base / "M90 Emulator" / "runtime"


def runtime_payload(source: Path) -> list[tuple[Path, Path]]:
    """Return only project-owned files needed by the live launcher."""
    for name in REQUIRED_PYTHON_FILES:
        path = source / "scripts" / name
        if not path.is_file():
            raise FileNotFoundError(f"Laufzeit-Skript fehlt im Paket: {path}")
    files = [(source / name, Path(name)) for name in POWERSHELL_FILES]
    files.extend(
        (path, Path("scripts") / path.name)
        for path in sorted((source / "scripts").glob("*.py"))
    )
    files.extend((source / "scripts" / name, Path("scripts") / name) for name in SHELL_FILES)
    files.extend((source / relative, relative) for relative in OWN_BINARIES)
    for path, _ in files:
        if not path.is_file():
            raise FileNotFoundError(f"Laufzeitdatei fehlt im Paket: {path}")
    return files


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def materialize_runtime(source: Path, target: Path) -> Path:
    """Atomically refresh packaged scripts; preserve logs/build and owner data."""
    for origin, relative in runtime_payload(source):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and _digest(origin) == _digest(destination):
            continue
        temporary = destination.with_name(destination.name + ".new")
        shutil.copyfile(origin, temporary)
        temporary.replace(destination)
    return target


def project_for_launcher() -> Path:
    if not getattr(sys, "frozen", False):
        return runtime_root()
    return materialize_runtime(bundled_root(), runtime_root())
