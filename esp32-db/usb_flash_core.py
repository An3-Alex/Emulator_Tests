"""Validated flash plans for the isolated ESP32-S3 database bench prototype."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import re
import struct
import sys
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path


PROJECT = Path(__file__).resolve().parent
REPOSITORY = PROJECT.parent
PARTITIONS = PROJECT / "partitions.csv"
BUILD = PROJECT / "build-idf"
DEFAULT_SEED = REPOSITORY / "build" / "esp32-seed.bin"
MANIFEST = REPOSITORY / "docs" / "owner-database-set.json"
SEED_SCRIPT = REPOSITORY / "scripts" / "build_esp32_seed.py"
GUI_CONFIG = PROJECT / ".generated" / "sdkconfig.gui"
IDF_REPO = PROJECT / ".tools" / "idf" / "v5.5.1" / "esp-idf"
IDF_WRAPPER = PROJECT / "Invoke-ESP32-IDF.ps1"
PROFILE_NAME = "m90-flash-profile.json"
FLASH_BYTES = 16 * 1024 * 1024
SEED_OFFSET = 0x310000
SEED_PARTITION_SIZE = 0x210000
SEED_IMAGE_SIZE = 0x1000 + 2 * 1024 * 1024
IMAGE_HEADER = struct.Struct(">8sIIII")
PARTITION_ENTRY = struct.Struct("<2sBBII16sI")
EXPECTED_FIRMWARE_OFFSETS = {0x0, 0x8000, 0x10000}
FLASH_SIZE_PATTERN = re.compile(r"Detected flash size:\s*(\d+)\s*(MB|MiB)", re.I)
PORT_PATTERN = re.compile(r"COM\d+", re.I)


@dataclass(frozen=True)
class FlashFile:
    offset: int
    path: Path


def source_files(folder: Path) -> dict[str, Path]:
    """Validate the owner's originals read-only against the pinned manifest."""
    if not folder.is_dir():
        raise ValueError("Datenbank-Ordner fehlt")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    roles = {
        "database": "Magie_90_CC4.bin",
        "loader": "Loader_61640403_L5.0b_2MB.bin",
        "config": "M90_Las_Vegas.bin",
        "factory": "FactoryReset_61640403.xc",
    }
    result: dict[str, Path] = {}
    for role, name in roles.items():
        matches = [member for member in manifest["members"]
                   if member["source_name"] == name]
        if len(matches) != 1:
            raise ValueError(f"Manifest-Eintrag fehlt oder ist doppelt: {name}")
        member = matches[0]
        path = folder / name
        if not path.is_file() or path.stat().st_size != member["size"]:
            raise ValueError(f"Originaldatei fehlt oder hat falsche Größe: {name}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest().upper() != member["sha256"]:
            raise ValueError(f"SHA-256 stimmt nicht: {name}")
        result[role] = path
    return result


def seed_destination(files: dict[str, Path]) -> Path:
    """Content-addressed output avoids overwriting an earlier user seed."""
    fingerprints = hashlib.sha256()
    for role in ("database", "loader", "config"):
        fingerprints.update(hashlib.sha256(files[role].read_bytes()).digest())
    return REPOSITORY / "build" / f"esp32-seed-{fingerprints.hexdigest()[:16]}.bin"


def seed_command(files: dict[str, Path], destination: Path) -> list[str]:
    return [sys.executable, str(SEED_SCRIPT),
            "--database", str(files["database"]),
            "--loader", str(files["loader"]),
            "--config", str(files["config"]),
            "--manifest", str(MANIFEST),
            "--output", str(destination)]


def temporary_seed_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.stem}-{uuid.uuid4().hex}.tmp")


def idf_command(action: str) -> list[str]:
    if action not in {"install", "check", "build"}:
        raise ValueError("Unbekannte ESP-IDF-Aktion")
    return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(IDF_WRAPPER), "-Action", action]


def gui_sdkconfig(uart_enabled: bool, tx_gpio: int, rx_gpio: int,
                  usb_bridge: bool = False) -> str:
    """Generate only the GUI-owned sdkconfig, never edit a user's sdkconfig."""
    if uart_enabled and usb_bridge:
        raise ValueError("UART-GPIO-Test und QEMU-USB-Brücke sind getrennte Modi")
    if uart_enabled and (tx_gpio == rx_gpio or
                         any(pin in {0, 3, 19, 20, 43, 44, 45, 46} or
                             26 <= pin <= 37 or not 0 <= pin <= 48
                             for pin in (tx_gpio, rx_gpio))):
        raise ValueError("UART-GPIOs sind gleich oder für Boot/USB/Flash reserviert")
    defaults = (PROJECT / "sdkconfig.defaults").read_text(encoding="utf-8")
    lines = [defaults.rstrip(), 'CONFIG_IDF_TARGET="esp32s3"']
    if uart_enabled:
        lines.extend(("CONFIG_M90_DB_UART_TEST=y",
                      f"CONFIG_M90_DB_UART_TX_GPIO={tx_gpio}",
                      f"CONFIG_M90_DB_UART_RX_GPIO={rx_gpio}"))
    else:
        lines.append("# CONFIG_M90_DB_UART_TEST is not set")
    if usb_bridge:
        lines.extend(("CONFIG_M90_DB_USB_BRIDGE=y",
                      "CONFIG_ESP_CONSOLE_SECONDARY_NONE=y",
                      "# CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG is not set"))
    else:
        lines.append("# CONFIG_M90_DB_USB_BRIDGE is not set")
    return "\n".join(lines) + "\n"


def read_partitions(path: Path = PARTITIONS) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(line for line in stream if not line.lstrip().startswith("#"))
        for row in reader:
            if not row or not row[0].strip():
                continue
            if len(row) < 5:
                raise ValueError("Ungültige ESP32-Partitionstabelle")
            name = row[0].strip()
            if name in result:
                raise ValueError(f"Doppelte Partition: {name}")
            result[name] = (int(row[3].strip(), 0), int(row[4].strip(), 0))
    if result.get("db_seed") != (SEED_OFFSET, SEED_PARTITION_SIZE):
        raise ValueError("db_seed-Partition weicht vom geprüften Layout ab")
    if result.get("factory") != (0x10000, 0x300000):
        raise ValueError("Firmware-Partition weicht vom geprüften Layout ab")
    spans = sorted((offset, offset + size, name) for name, (offset, size) in result.items())
    for previous, current in zip(spans, spans[1:]):
        if previous[1] > current[0]:
            raise ValueError(f"Überlappende Partitionen: {previous[2]}, {current[2]}")
    if spans[-1][1] > FLASH_BYTES:
        raise ValueError("Partitionstabelle überschreitet 16 MB Flash")
    return result


def validate_seed(path: Path) -> tuple[int, int]:
    if not path.is_file() or path.stat().st_size != SEED_IMAGE_SIZE:
        raise ValueError("Datenbank-Abbild fehlt oder hat nicht exakt 2 MiB plus Header")
    with path.open("rb") as stream:
        header = stream.read(0x1000)
        magic, version, generation, size, expected_crc = IMAGE_HEADER.unpack_from(header)
        if magic != b"M90S3RAM" or version != 1 or size != 2 * 1024 * 1024:
            raise ValueError("Unbekanntes Datenbank-Abbildformat")
        if any(header[IMAGE_HEADER.size:]):
            raise ValueError("Unerwartete Daten im Abbild-Header")
        crc = 0
        for _ in range(size // 4096):
            block = stream.read(4096)
            if len(block) != 4096:
                raise ValueError("Datenbank-Abbild ist unvollständig")
            crc = zlib.crc32(block, crc)
        if crc != expected_crc:
            raise ValueError("CRC32 des Datenbank-Abbilds stimmt nicht")
    if SEED_IMAGE_SIZE > read_partitions()["db_seed"][1]:
        raise ValueError("Datenbank-Abbild ist größer als seine Partition")
    return generation, crc


def validate_binary_partitions(path: Path) -> None:
    """Reject stale IDF builds with a different binary partition layout."""
    data = path.read_bytes()
    if len(data) not in (0xC00, 0x1000):
        raise ValueError("Kompilierte Partitionstabelle hat eine ungültige Länge")
    actual: dict[str, tuple[int, int]] = {}
    for index in range(0, len(data), PARTITION_ENTRY.size):
        entry = data[index:index + PARTITION_ENTRY.size]
        if entry == b"\xff" * PARTITION_ENTRY.size:
            break
        if entry.startswith(b"\xeb\xeb"):
            continue  # optional ESP-IDF MD5 footer
        magic, _, _, offset, size, label, flags = PARTITION_ENTRY.unpack(entry)
        if magic != b"\xaa\x50" or flags != 0:
            raise ValueError("Ungültige oder verschlüsselte Partitionstabelle")
        name = label.split(b"\x00", 1)[0].decode("ascii")
        if name in actual:
            raise ValueError(f"Doppelte Binär-Partition: {name}")
        actual[name] = (offset, size)
    if actual != read_partitions():
        raise ValueError("Kompilierte Partitionstabelle passt nicht zu partitions.csv")


def firmware_files(build_dir: Path = BUILD) -> list[FlashFile]:
    metadata = build_dir / "flasher_args.json"
    if not metadata.is_file():
        raise ValueError("Firmware fehlt: zuerst mit ESP-IDF bauen")
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    chip = payload.get("extra_esptool_args", {}).get("chip", "")
    if chip.lower() != "esp32s3":
        raise ValueError("Firmware wurde nicht für ESP32-S3 gebaut")
    flash_size = payload.get("flash_settings", {}).get("flash_size", "")
    if str(flash_size).lower() not in {"16mb", "16m"}:
        raise ValueError("Firmware wurde nicht für 16 MB Flash gebaut")
    entries = payload.get("flash_files")
    if not isinstance(entries, dict):
        raise ValueError("Firmware-Flashliste fehlt")
    root = build_dir.resolve()
    files: list[FlashFile] = []
    seen: set[int] = set()
    for address, relative_name in entries.items():
        offset = int(address, 0)
        if offset not in EXPECTED_FIRMWARE_OFFSETS or offset in seen:
            raise ValueError(f"Unerwarteter Firmware-Flashbereich: {address}")
        if not isinstance(relative_name, str):
            raise ValueError("Ungültiger Firmware-Dateiname")
        target = (root / relative_name).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError(f"Firmware-Datei fehlt oder liegt außerhalb des Builds: {relative_name}")
        limit = {0x0: 0x8000, 0x8000: 0x1000, 0x10000: 0x300000}[offset]
        if target.stat().st_size == 0 or target.stat().st_size > limit:
            raise ValueError(f"Firmware-Datei überschreitet ihren Bereich: {relative_name}")
        seen.add(offset)
        files.append(FlashFile(offset, target))
    if seen != EXPECTED_FIRMWARE_OFFSETS:
        raise ValueError("Bootloader, Partitionstabelle oder Anwendung fehlt")
    validate_binary_partitions(next(item.path for item in files if item.offset == 0x8000))
    return sorted(files, key=lambda item: item.offset)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record_firmware_profile(uart_enabled: bool, tx_gpio: int, rx_gpio: int,
                            build_dir: Path = BUILD, usb_bridge: bool = False) -> dict:
    """Only mark a finished build flashable after checking its actual Kconfig."""
    config_path = build_dir / "config" / "sdkconfig.json"
    if not config_path.is_file():
        raise ValueError("ESP-IDF-Konfigurationsnachweis fehlt")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    actual_enabled = bool(config.get("M90_DB_UART_TEST", False))
    actual_usb_bridge = bool(config.get("M90_DB_USB_BRIDGE", False))
    if actual_enabled != uart_enabled:
        raise ValueError("Firmware-UART-Modus passt nicht zur Auswahl")
    if actual_usb_bridge != usb_bridge or (actual_usb_bridge and actual_enabled):
        raise ValueError("Firmware-USB-Brückenmodus passt nicht zur Auswahl")
    if actual_usb_bridge and not config.get("ESP_CONSOLE_SECONDARY_NONE", False):
        raise ValueError("USB-Brücke teilt den Datenkanal mit der Log-Konsole")
    if actual_enabled and (config.get("M90_DB_UART_TX_GPIO") != tx_gpio or
                           config.get("M90_DB_UART_RX_GPIO") != rx_gpio):
        raise ValueError("Firmware-UART-Pins passen nicht zur Auswahl")
    files = firmware_files(build_dir)
    profile = {
        "schema": 1,
        "uart_enabled": actual_enabled,
        "usb_bridge": actual_usb_bridge,
        "tx_gpio": tx_gpio if actual_enabled else None,
        "rx_gpio": rx_gpio if actual_enabled else None,
        "firmware_sha256": {hex(item.offset): _file_sha256(item.path)
                             for item in files},
    }
    output = build_dir / PROFILE_NAME
    temporary = build_dir / f".{PROFILE_NAME}-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return profile


def validate_firmware_profile(build_dir: Path = BUILD) -> dict:
    path = build_dir / PROFILE_NAME
    if not path.is_file():
        raise ValueError("Geprüftes Firmware-Profil fehlt: erst mit der Oberfläche bauen")
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("schema") != 1 or not isinstance(profile.get("uart_enabled"), bool):
        raise ValueError("Firmware-Profil hat ein unbekanntes Format")
    actual = {hex(item.offset): _file_sha256(item.path)
              for item in firmware_files(build_dir)}
    if actual != profile.get("firmware_sha256"):
        raise ValueError("Firmware-Dateien haben sich seit dem Build verändert")
    return profile


def make_flash_plan(seed: Path, include_firmware: bool,
                    build_dir: Path = BUILD) -> list[FlashFile]:
    read_partitions()
    validate_seed(seed)
    files = firmware_files(build_dir) if include_firmware else []
    files.append(FlashFile(SEED_OFFSET, seed.resolve()))
    for item in files:
        if item.offset + item.path.stat().st_size > FLASH_BYTES:
            raise ValueError(f"Flash-Datei reicht über 16 MB hinaus: {item.path.name}")
    for previous, current in zip(files, files[1:]):
        if previous.offset + previous.path.stat().st_size > current.offset:
            raise ValueError("Flash-Dateien überlappen sich")
    return files


def validate_port(port: str) -> str:
    if not PORT_PATTERN.fullmatch(port.strip()):
        raise ValueError("Bitte einen gültigen Windows-COM-Port wählen")
    return port.strip().upper()


def esptool_available() -> bool:
    return importlib.util.find_spec("esptool") is not None


def probe_command(port: str) -> list[str]:
    return [sys.executable, "-m", "esptool", "--chip", "esp32s3",
            "--port", validate_port(port), "flash-id"]


def check_probe_output(output: str) -> None:
    match = FLASH_SIZE_PATTERN.search(output)
    if not match or int(match.group(1)) != 16:
        raise ValueError("ESP32-S3 mit 16 MB Flash nicht bestätigt; kein Flashen")


def flash_command(port: str, files: list[FlashFile]) -> list[str]:
    command = [sys.executable, "-m", "esptool", "--chip", "esp32s3",
               "--port", validate_port(port), "write-flash", "--flash-size", "16MB"]
    for item in files:
        command.extend((hex(item.offset), str(item.path)))
    return command
