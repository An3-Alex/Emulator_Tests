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
    if (
        stored_checksum != (sum(decoded[4:]) & 0xFFFFFFFF)
        or end_address != 0x1000 + len(decoded) - 1
        or module_id != CONFIG_MODULE_ID
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
