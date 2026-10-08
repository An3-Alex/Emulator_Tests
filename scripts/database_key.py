"""Find the loader key (D3) belonging to a database package.

The boot ROM hands the loader a per-package value in D3. It seeds the stream
transform of the database, its configuration and its Factory module. A key is
accepted only when the native checksum over the complete decrypted database
matches; the selected files are opened read-only.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
from typing import Callable

from m68k_database_transform import RAW_PREFIX_SIZE, initialize_state, transform_database
from owner_database_runtime import key_mismatch_message

DEFAULT_KEY = 0xD27B7159
KEY_SPACE = 1 << 32
LOAD_ADDRESS = 0x1000
_KEY_TEXT = re.compile(r"(?:0[xX])?([0-9A-Fa-f]{8})")


def parse_key(text: str) -> int | None:
    """None for "auto", else the 32-bit key written as eight hex digits."""
    text = text.strip()
    if text.lower() == "auto":
        return None
    match = _KEY_TEXT.fullmatch(text)
    if not match:
        raise ValueError("Datenbank-Schlüssel: „auto“ oder 8 Hex-Ziffern eingeben")
    return int(match[1], 16)


def _plausible_vector(value: int, end_address: int) -> bool:
    return value in (0, 0xFFFFFFFF) or (value % 2 == 0 and LOAD_ADDRESS <= value <= end_address)


def key_matches(data: bytes, d3: int) -> bool:
    if len(data) < RAW_PREFIX_SIZE + 8:
        return False
    stored, end_address = struct.unpack_from(">II", data)
    if end_address != LOAD_ADDRESS + len(data) - 1:
        return False
    # Cheap filter first: the first two decrypted 68020 vectors.
    state, i, j = initialize_state(data[:RAW_PREFIX_SIZE], d3)
    vectors = bytearray()
    for value in data[RAW_PREFIX_SIZE:RAW_PREFIX_SIZE + 8]:
        i = (i + 1) & 0xFF
        j = (j + state[i]) & 0xFF
        state[i], state[j] = state[j], state[i]
        vectors.append(value ^ state[(state[i] + state[j]) & 0xFF])
    if not all(_plausible_vector(v, end_address) for v in struct.unpack(">II", vectors)):
        return False
    return sum(transform_database(data, d3)[4:]) & 0xFFFFFFFF == stored


class KeyCache:
    """Keys found earlier, by SHA-256 of the database file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, digest: str) -> int | None:
        value = self._load().get(digest)
        try:
            return parse_key(value) if isinstance(value, str) else None
        except ValueError:
            return None

    def put(self, digest: str, key: int) -> None:
        data = self._load()
        data[digest] = f"{key:08X}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)


def search_key(database: Path, tool: Path, progress: Callable[[float], None],
               started: Callable[[subprocess.Popen], None] = lambda _process: None) -> int | None:
    """Run the native full search; progress receives the searched fraction."""
    if not tool.is_file():
        raise ValueError(f"Schlüsselsuche nicht verfügbar: {tool.name} fehlt im Laufzeitordner")
    threads = max(1, (os.cpu_count() or 2) - 1)
    process = subprocess.Popen(
        [str(tool), str(database), "0", str(KEY_SPACE), str(threads)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    started(process)
    found = None
    with process.stdout:
        for line in process.stdout:
            if line.startswith("D3_PROGRESS"):
                tested = re.search(r"tested=(\d+)", line)
                if tested:
                    progress(min(1.0, int(tested[1]) / KEY_SPACE))
            elif line.startswith("D3_FOUND="):
                found = int(line.split("=", 1)[1], 16)
    process.wait()
    return found


def resolve_key(database: Path, setting: str, cache: KeyCache, tool: Path,
                progress: Callable[[float], None], notice: Callable[[str], None],
                started: Callable[[subprocess.Popen], None] = lambda _process: None) -> int:
    data = database.read_bytes()
    explicit = parse_key(setting)
    if explicit is not None:
        if not key_matches(data, explicit):
            raise ValueError(key_mismatch_message("Datenbank", explicit))
        return explicit
    digest = hashlib.sha256(data).hexdigest().upper()
    for candidate in dict.fromkeys(k for k in (cache.get(digest), DEFAULT_KEY) if k is not None):
        if key_matches(data, candidate):
            cache.put(digest, candidate)
            return candidate
    struct_ok = len(data) >= RAW_PREFIX_SIZE + 8 and \
        struct.unpack_from(">I", data, 4)[0] == LOAD_ADDRESS + len(data) - 1
    if not struct_ok:
        raise ValueError("Datenbank: keine Datenbankdatei dieser Modulfamilie (Kopfzeile passt nicht)")
    notice("Schlüssel dieser Datenbank unbekannt: automatische Suche startet "
           "(einmalig, dauert je nach CPU einige Minuten).")
    found = search_key(database, tool, progress, started)
    if found is None or not key_matches(data, found):
        raise ValueError("Für diese Datenbank wurde kein passender Schlüssel gefunden.")
    cache.put(digest, found)
    notice(f"Schlüssel gefunden und gespeichert: D3={found:08X}")
    return found
