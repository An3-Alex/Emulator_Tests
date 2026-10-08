"""Reproduce the verified M90_Las_Vegas module's writes to 2 MB SRAM.

The owner's decrypted 0x1500 entry clears the final 0x400 RAM bytes, copies
0x200 bytes from 0x1578 to RAM_SIZE-0x400, then 0x18 bytes from 0x1562 to
RAM_SIZE-0x80. It calls the loader reset routine afterwards. This module
models only those statically proven memory operations, not arbitrary config
module code.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from m68k_database_transform import transform_database
from owner_database_runtime import EXPECTED_MODULE_FAMILY as MODULE_FAMILY, key_mismatch_message


RAM_SIZE = 2 * 1024 * 1024
CONFIG_ENTRY = 0x1500
CONFIG_MODULE_ID = 0x61640403
CONFIG_ENTRY_PREFIX = bytes.fromhex("42 B8 10 00 22 78 00 00")
CONFIG_CLEAR_START = RAM_SIZE - 0x400
CONFIG_COPY_START = CONFIG_CLEAR_START
CONFIG_COPY_SOURCE_OFFSET = 0x578
CONFIG_COPY_SIZE = 0x200
IDENTITY_COPY_START = RAM_SIZE - 0x80
IDENTITY_COPY_SOURCE_OFFSET = 0x562
IDENTITY_COPY_SIZE = 0x18
FACTORY_ENTRY = 0x1500
FACTORY_ENTRY_CODE = bytes.fromhex(
    "20 7C 00 01 00 00 22 78 00 00 93 FC 00 00 00 10 "
    "B1 C9 62 04 42 98 60 F8 42 B9 00 00 10 00 "
    "23 FC 49 4E 49 54 00 FF FD 00 30 7C 04 08 4E 90 60 FE"
)


def prepare_factory_runtime(path: Path, expected_sha256: str, d3: int) -> tuple[bytes, int]:
    """Validate the owner's native Factory entry, including its INIT write.

    Executable upload modules use zero in header field 0x10, unlike the main
    DB image's exclusive-end field. Do not apply the DB-image header rules.
    """
    raw = path.read_bytes()
    if len(raw) != 0x500 + len(FACTORY_ENTRY_CODE):
        raise ValueError("unsupported Factory module layout")
    digest = hashlib.sha256(raw).hexdigest().upper()
    if digest != expected_sha256.replace(" ", "").upper():
        raise ValueError("factory SHA-256 mismatch")
    decoded = transform_database(raw, d3)
    checksum, end, inverse, module_id = struct.unpack_from(">IIII", decoded)
    entry, entry_inverse = struct.unpack_from(">II", decoded, 0x4C)
    if checksum != (sum(decoded[4:]) & 0xFFFFFFFF):
        raise ValueError(key_mismatch_message("Factory-Modul", d3))
    if (
        end != 0x1000 + len(decoded) - 1
        or inverse != (~end & 0xFFFFFFFF)
        or module_id & 0xFFFFFF00 != MODULE_FAMILY
        or entry != FACTORY_ENTRY
        or entry_inverse != (~entry & 0xFFFFFFFF)
        or decoded[0x500:] != FACTORY_ENTRY_CODE
    ):
        raise ValueError("factory module does not match verified native reset code")
    return decoded, entry


def prepare_config_writes(
    path: Path, expected_sha256: str, d3: int,
) -> tuple[list[tuple[int, bytes]], str]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest().upper()
    if digest != expected_sha256.replace(" ", "").upper():
        raise ValueError("config SHA-256 mismatch")
    if len(raw) < CONFIG_COPY_SOURCE_OFFSET + CONFIG_COPY_SIZE:
        raise ValueError("config module is shorter than its RAM-copy source")
    decoded = transform_database(raw, d3)
    stored_checksum, end_address, _, module_id = struct.unpack_from(">IIII", decoded)
    entrypoint = struct.unpack_from(">I", decoded, 0x4C)[0]
    if stored_checksum != (sum(decoded[4:]) & 0xFFFFFFFF):
        raise ValueError(key_mismatch_message("Konfiguration", d3))
    # Other configurations of the module family use the same RAM-copy entry;
    # the copied bytes themselves are the configuration.
    if (
        end_address != 0x1000 + len(decoded) - 1
        or module_id & 0xFFFFFF00 != MODULE_FAMILY
        or entrypoint != CONFIG_ENTRY
        or decoded[0x500:0x508] != CONFIG_ENTRY_PREFIX
    ):
        raise ValueError("config module does not match verified RAM-copy code")
    writes = [
        (CONFIG_CLEAR_START, bytes(0x400)),
        (CONFIG_COPY_START, decoded[
            CONFIG_COPY_SOURCE_OFFSET:CONFIG_COPY_SOURCE_OFFSET + CONFIG_COPY_SIZE
        ]),
        (IDENTITY_COPY_START, decoded[
            IDENTITY_COPY_SOURCE_OFFSET:IDENTITY_COPY_SOURCE_OFFSET + IDENTITY_COPY_SIZE
        ]),
    ]
    return writes, digest
