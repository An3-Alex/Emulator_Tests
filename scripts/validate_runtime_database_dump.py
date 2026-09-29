#!/usr/bin/env python3
"""Validate an owner-captured post-transform 68020 database RAM dump."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from inspect_owner_database import inspect_database
from m68k_database_transform import MAX_DATABASE_SIZE, transform_database


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def validate_runtime_dump(
    transport_path: Path,
    expected_transport_sha256: str,
    runtime_path: Path,
    d3: int | None = None,
) -> dict:
    transport = transport_path.read_bytes()
    runtime = runtime_path.read_bytes()
    if len(transport) > MAX_DATABASE_SIZE or len(runtime) > MAX_DATABASE_SIZE:
        raise ValueError("database input exceeds safety size limit")
    expected = expected_transport_sha256.replace(" ", "").upper()
    transport_digest = sha256(transport)
    if transport_digest != expected:
        raise ValueError(
            f"transport SHA-256 mismatch: expected {expected}, got {transport_digest}"
        )
    if len(runtime) < 0x100:
        raise ValueError("runtime dump is shorter than the raw header")

    header, payload, header_checks, role = inspect_database(runtime)
    stored_checksum = struct.unpack_from(">I", runtime, 0)[0]
    calculated_checksum = sum(runtime[4:]) & 0xFFFFFFFF
    validations = {
        "same_size_as_transport": len(runtime) == len(transport),
        "raw_prefix_matches_transport": runtime[:0x100] == transport[:0x100],
        "recognized_main_database_header": role == "database" and all(header_checks.values()),
        "native_additive_checksum": stored_checksum == calculated_checksum,
        "payload_is_post_transform": runtime[0x100:] != transport[0x100:],
    }
    expected_runtime = None
    if d3 is not None:
        expected_runtime = transform_database(transport, d3)
        validations["matches_supplied_d3"] = runtime == expected_runtime

    entry_offset = header["entrypoint_file_offset"]
    entry_bytes = (
        runtime[entry_offset:entry_offset + 16]
        if 0 <= entry_offset < len(runtime) else b""
    )
    return {
        "schema": "m90-owner-runtime-database-validation-v1",
        "transport_source": str(transport_path.resolve()),
        "transport_sha256": transport_digest,
        "runtime_source": str(runtime_path.resolve()),
        "runtime_sha256": sha256(runtime),
        "size": len(runtime),
        "role": role,
        "header": header,
        "payload": payload,
        "stored_checksum": f"{stored_checksum:08X}",
        "calculated_checksum": f"{calculated_checksum:08X}",
        "entry_bytes_hex": entry_bytes.hex(" ").upper(),
        "d3": None if d3 is None else f"{d3 & 0xFFFFFFFF:08X}",
        "validations": validations,
        "recognized_runtime_dump": all(validations.values()),
        "handling": "Both files were opened read-only; no transformed copy was written.",
    }


def parse_int(value: str) -> int:
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("transport", type=Path)
    parser.add_argument("runtime_dump", type=Path)
    parser.add_argument("--expected-transport-sha256", required=True)
    parser.add_argument("--d3", type=parse_int)
    args = parser.parse_args()
    report = validate_runtime_dump(
        args.transport, args.expected_transport_sha256, args.runtime_dump, args.d3
    )
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["recognized_runtime_dump"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
