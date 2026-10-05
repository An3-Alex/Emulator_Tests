#!/usr/bin/env python3
"""Read-only inspector for an owner-supplied Merkur database dump.

The tool records provenance and only decodes the small clear-text identification
header.  It does not decrypt, modify, execute, or silently accept unknown data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from collections import Counter
from pathlib import Path


MAX_DUMP_SIZE = 64 * 1024 * 1024
HEADER_MIN_SIZE = 0xE0
DATABASE_LOAD_BASE = 0x1000
DATABASE_MODULE_FAMILY = 0x61640400
LOADER_LOAD_BASE = 0x0400
LOADER_ENTRY_STUB_OFFSET = 0x0E
LOADER_ENTRY_TARGET = 0x0CC8
LOADER_SYNC_KILL_TABLE_OFFSET = 0x8B0
LOADER_SYNC_WAIT_TABLE_OFFSET = 0x8B8
LOADER_SYNC_KILL = bytes.fromhex("7C 6B 69 6C 6C FD C4 55 1B 53 59 4E 43 53 59 4E")
LOADER_SYNC_WAIT = bytes.fromhex("1B 53 59 4E 43 53 59 4E 43 57 41 49 54 47 4F 0A")


def c_string(data: bytes, offset: int, limit: int) -> str:
    raw = data[offset : offset + limit].split(b"\0", 1)[0]
    return raw.decode("ascii", errors="replace").rstrip()


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    size = len(data)
    return -sum((count / size) * math.log2(count / size) for count in counts.values())


def be32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def decode_loader_sync(data: bytes, lead: int, table_offset: int) -> bytes:
    """Decode the 15 post-lead bytes used by the loader comparison loop."""
    table = data[table_offset + 1 : table_offset + 16]
    return bytes([lead]) + bytes((value + 1) & 0xFF for value in table)


def inspect_database(data: bytes) -> tuple[dict, dict, dict, str]:
    stored_checksum = be32(data, 0x00)
    end_address = be32(data, 0x04)
    end_address_complement = be32(data, 0x08)
    module_id = be32(data, 0x0C)
    end_exclusive = be32(data, 0x10)
    entrypoint = be32(data, 0x4C)
    entrypoint_complement = be32(data, 0x50)
    expected_end_address = DATABASE_LOAD_BASE + len(data) - 1
    copyright_text = c_string(data, 0x18, 0x38).strip()
    product = c_string(data, 0x60, 0x19)
    edition = c_string(data, 0x79, 0x05)
    build_id = c_string(data, 0x7E, 0x07)
    release_id = c_string(data, 0x8C, 0x07)
    payload = data[HEADER_MIN_SIZE:]
    structural_validations = {
        "load_extent_matches_file_size": end_address == expected_end_address,
        "end_address_complement": end_address_complement == ((~end_address) & 0xFFFFFFFF),
        "module_family_616404": (module_id & 0xFFFFFF00) == DATABASE_MODULE_FAMILY,
        "entrypoint_complement": entrypoint_complement == ((~entrypoint) & 0xFFFFFFFF),
        "entrypoint_in_loaded_image": DATABASE_LOAD_BASE <= entrypoint <= end_address,
        "nonempty_payload": bool(payload),
    }
    identified_main_database = copyright_text.startswith("COPYRIGHT BY ADP ") or bool(product)
    role = "database" if identified_main_database else "database_module"
    validations = structural_validations | {
        # Full databases use an exclusive end pointer.  The observed small
        # auxiliary modules leave this optional field at zero.
        "end_exclusive_valid": end_exclusive == ((end_address + 1) & 0xFFFFFFFF)
        if role == "database" else end_exclusive in (0, (end_address + 1) & 0xFFFFFFFF),
    }
    if role == "database":
        validations |= {
        "copyright_header": copyright_text.startswith("COPYRIGHT BY ADP "),
        "merkur_magie_product": product.startswith("MERKUR MAGIE"),
        "cc4_edition": "CC4" in product,
        }
    header = {
        "prefix_hex": data[:0x18].hex(" ").upper(),
        "byte_order": "big-endian",
        "load_base": DATABASE_LOAD_BASE,
        "stored_checksum": stored_checksum,
        "end_address": end_address,
        "end_address_complement": end_address_complement,
        "end_exclusive": end_exclusive,
        "module_id": f"{module_id:08X}",
        "module_family": f"{module_id & 0xFFFFFF00:08X}",
        "entrypoint": entrypoint,
        "entrypoint_complement": entrypoint_complement,
        "entrypoint_file_offset": entrypoint - DATABASE_LOAD_BASE,
        "copyright": copyright_text,
        "product": product,
        "edition": edition,
        "build_id": build_id,
        "release_id": release_id,
        "payload_offset": HEADER_MIN_SIZE,
    }
    payload_info = {
        "size": len(payload),
        "shannon_entropy_bits_per_byte": round(entropy(payload), 6),
    }
    return header, payload_info, validations, role


def inspect_loader(data: bytes) -> tuple[dict, dict, dict]:
    copyright_text = c_string(data, 0x1C, 0x38).strip()
    version = c_string(data, 0x14, 0x08).strip()
    code_offset = 0x58
    payload = data[code_offset:]
    entry_target = be32(data, LOADER_ENTRY_STUB_OFFSET + 2)
    sync_kill = decode_loader_sync(data, 0x7C, LOADER_SYNC_KILL_TABLE_OFFSET)
    sync_wait = decode_loader_sync(data, 0x1B, LOADER_SYNC_WAIT_TABLE_OFFSET)
    validations = {
        "loader_signature": data.startswith(b"|load"),
        "copyright_header": copyright_text.startswith("COPYRIGHT BY ADP "),
        "loader_version": version.startswith("L 5.0b"),
        "absolute_entry_jump": data[LOADER_ENTRY_STUB_OFFSET:LOADER_ENTRY_STUB_OFFSET + 2]
        == b"\x4E\xF9",
        "entry_target_0cc8": entry_target == LOADER_ENTRY_TARGET,
        "sync_kill_sequence": sync_kill == LOADER_SYNC_KILL,
        "sync_wait_sequence": sync_wait == LOADER_SYNC_WAIT,
        "68020_movec_instruction": data[0x2D4:0x2D8] == b"\x4E\x7B\x08\x01",
        "nonempty_payload": bool(payload),
    }
    header = {
        "prefix_hex": data[:0x14].hex(" ").upper(),
        "signature": data[:5].decode("ascii"),
        "version": version,
        "copyright": copyright_text,
        "code_offset": code_offset,
        "code_architecture": "Motorola 68020-class (inferred from big-endian instructions including MOVEC)",
        "load_base": LOADER_LOAD_BASE,
        "entry_stub_address": LOADER_LOAD_BASE + LOADER_ENTRY_STUB_OFFSET,
        "entry_target": entry_target,
        "entry_target_file_offset": entry_target - LOADER_LOAD_BASE,
        "sync_kill_hex": sync_kill.hex(" ").upper(),
        "sync_wait_hex": sync_wait.hex(" ").upper(),
        "sync_wait_ascii": sync_wait[1:].decode("ascii").rstrip("\n"),
    }
    payload_info = {
        "size": len(payload),
        "shannon_entropy_bits_per_byte": round(entropy(payload), 6),
    }
    return header, payload_info, validations


def inspect_dump(path: Path) -> dict:
    size = path.stat().st_size
    if size > MAX_DUMP_SIZE:
        raise ValueError(f"dump exceeds the {MAX_DUMP_SIZE}-byte safety limit")
    data = path.read_bytes()
    if len(data) < HEADER_MIN_SIZE:
        raise ValueError(f"dump is shorter than the observed 0x{HEADER_MIN_SIZE:X}-byte header")

    if data.startswith(b"|load"):
        role = "loader"
        header, payload_info, validations = inspect_loader(data)
    else:
        header, payload_info, validations, role = inspect_database(data)

    notes = [
        "Only observed identification and structural header fields are decoded.",
        "Payload entropy alone does not prove encryption or compression.",
        "Recognition is not authenticity, licensing, or cryptographic-integrity verification.",
    ]
    if role in ("database", "database_module"):
        notes.insert(
            2,
            "The stored checksum is reported but not validated against ciphertext; the loader checks the post-transform memory image.",
        )

    return {
        "schema": "m90-owner-database-inspection-v3",
        "source": str(path.resolve()),
        "role": role,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest().upper(),
        "header": header,
        "payload": payload_info,
        "validations": validations,
        "recognized": all(validations.values()),
        "notes": notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dump", type=Path)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    report = inspect_dump(args.dump)
    if args.expected_sha256:
        expected = args.expected_sha256.replace(" ", "").upper()
        report["expected_sha256"] = expected
        report["hash_matches"] = report["sha256"] == expected
    print(json.dumps(report, indent=2, ensure_ascii=True))
    if args.expected_sha256 and not report["hash_matches"]:
        return 2
    return 0 if report["recognized"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
