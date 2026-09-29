"""Materialize the source-only runtime carried by a frozen launcher.

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
    """Return only source-owned files needed by the live launcher."""
    files = [(source / name, Path(name)) for name in POWERSHELL_FILES]
    files.extend(
        (path, Path("scripts") / path.name)
        for path in sorted((source / "scripts").glob("*.py"))
    )
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
