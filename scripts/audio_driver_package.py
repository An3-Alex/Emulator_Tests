"""Fetch the OEM package as data; never execute its self-extracting EXE."""
from __future__ import annotations

import hashlib
from pathlib import Path
import urllib.request
import zipfile

URL = "https://downloads.dell.com/audio/S9wua05i.exe"
PACKAGE_HASH = "86ed3b2daab318cb8faacfc6c1291bf6d62e0bc0fba6dcfbc9546882cc359157"
FILES = {
    "stac97.inf": "4c2ef7cad03f175b01fd127ae0c8b1c82fab5d548e214ad711c4a0b7743fc98b",
    "stac97.sys": "8812a4a33f7907450a835072f386dfa7b2616ef2515df69ffed117f25fbdd88c",
    "stac97.cat": "2c00bd898e19d92a576219db81b5068b6638c82d5a1c1a9a5229f10ef1efe5ea",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate(directory: Path) -> None:
    for name, expected in FILES.items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != expected:
            raise ValueError(f"SigmaTel-Treiberdatei fehlt oder hat falsche Version: {path}")


def extract(package: Path, directory: Path) -> None:
    if digest(package.read_bytes()) != PACKAGE_HASH:
        raise ValueError("SigmaTel-Paket: SHA-256 stimmt nicht; keine Installation")
    # Only allowlisted flat members, not extractall (no path traversal).
    with zipfile.ZipFile(package) as archive:
        payload = {name: archive.read(f"ich/wdm/{name}") for name in FILES}
    for name, data in payload.items():
        if digest(data) != FILES[name]:
            raise ValueError(f"SigmaTel-Paket: unerwartete Datei {name}")
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in payload.items():
        path = directory / name
        if path.is_symlink():
            raise ValueError(f"Symlink refused: {path}")
        temporary = path.with_suffix(path.suffix + ".new")
        if temporary.exists() or temporary.is_symlink():
            raise ValueError(f"Unfinished driver file: {temporary}")
        with temporary.open("xb") as output:
            output.write(data)
        temporary.replace(path)
    validate(directory)


def ensure(directory: Path) -> Path:
    try:
        validate(directory)
        return directory
    except ValueError:
        pass
    directory.mkdir(parents=True, exist_ok=True)
    package = directory / "S9wua05i.exe"
    if not package.is_file() or digest(package.read_bytes()) != PACKAGE_HASH:
        print("SigmaTel-XP-Treiber wird von Dell geladen (1,5 MB)…", flush=True)
        request = urllib.request.Request(URL, headers={"User-Agent": "M90-Emulator/0.1.10"})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000 or digest(data) != PACKAGE_HASH:
            raise ValueError("SigmaTel-Download hat unerwartete Größe/Prüfsumme")
        if package.is_symlink():
            raise ValueError(f"Symlink refused: {package}")
        temporary = package.with_suffix(".download")
        if temporary.exists() or temporary.is_symlink():
            raise ValueError(f"Unfinished download: {temporary}")
        with temporary.open("xb") as output:
            output.write(data)
        temporary.replace(package)
    extract(package, directory)
    return directory
