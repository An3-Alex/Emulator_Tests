#!/usr/bin/env python3
"""Prepare the owner database firmware for execution by the m68k bridge.

This module is deliberately an execution gate, not a protocol replay.  It
accepts either the original loader input (D3) or a checksum-valid RAM image,
and produces the exact bytes that must be mapped at 0x1000 before entering the
database firmware at the entry point recorded in its own header.
"""

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

from m68k_database_transform import transform_database


MAX_DATABASE_SIZE = 64 * 1024 * 1024
LOAD_ADDRESS = 0x1000
RAW_PREFIX_SIZE = 0x100
EXPECTED_MODULE_FAMILY = 0x61640400


def key_mismatch_message(label: str, d3: int) -> str:
    return (
        f"{label} lässt sich mit dem Schlüssel D3={d3 & 0xFFFFFFFF:08X} nicht "
        "entschlüsseln (Prüfsumme falsch). Unter Emulationseinstellungen → "
        "Datenbank den Schlüssel auf „auto“ stellen oder den passenden Wert eintragen."
    )


def _read_pinned(path: Path, expected_sha256: str) -> tuple[bytes, str]:
    data = path.read_bytes()
    if len(data) > MAX_DATABASE_SIZE:
        raise ValueError("database exceeds safety size limit")
    digest = hashlib.sha256(data).hexdigest().upper()
    expected = expected_sha256.replace(" ", "").upper()
    if digest != expected:
        raise ValueError(
            f"database SHA-256 mismatch: expected {expected}, got {digest}"
        )
    return data, digest


def _validate_runtime(runtime: bytes) -> dict:
    if len(runtime) < RAW_PREFIX_SIZE:
        raise ValueError("runtime database is shorter than its header")
    stored_checksum, end_address, end_inverse, module_id, exclusive_end = (
        struct.unpack_from(">IIIII", runtime, 0)
    )
    entrypoint = struct.unpack_from(">I", runtime, 0x4C)[0]
    entry_inverse = struct.unpack_from(">I", runtime, 0x50)[0]
    expected_size = end_address - LOAD_ADDRESS + 1
    checks = {
        "size_matches_header": expected_size == len(runtime),
        "end_complement": end_inverse == ((~end_address) & 0xFFFFFFFF),
        "exclusive_end": exclusive_end == end_address + 1,
        "module_family": (module_id & 0xFFFFFF00) == EXPECTED_MODULE_FAMILY,
        "entry_complement": entry_inverse == ((~entrypoint) & 0xFFFFFFFF),
        "entry_in_image": LOAD_ADDRESS <= entrypoint <= end_address,
        "native_checksum": (sum(runtime[4:]) & 0xFFFFFFFF) == stored_checksum,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError("runtime database validation failed: " + ", ".join(failed))
    return {
        "load_address": f"{LOAD_ADDRESS:08X}",
        "end_address": f"{end_address:08X}",
        "entrypoint": f"{entrypoint:08X}",
        "module_id": f"{module_id:08X}",
        "stored_checksum": f"{stored_checksum:08X}",
        "validations": checks,
    }


def prepare_runtime(
    transport_path: Path,
    expected_transport_sha256: str,
    *,
    d3: int | None = None,
    runtime_dump_path: Path | None = None,
) -> tuple[bytes, dict]:
    if (d3 is None) == (runtime_dump_path is None):
        raise ValueError("provide exactly one of d3 or runtime_dump_path")
    transport, transport_digest = _read_pinned(
        transport_path, expected_transport_sha256
    )
    if runtime_dump_path is not None:
        runtime = runtime_dump_path.read_bytes()
        source = "owner-runtime-dump"
        if len(runtime) != len(transport):
            raise ValueError("runtime dump size differs from transport database")
        if runtime[:RAW_PREFIX_SIZE] != transport[:RAW_PREFIX_SIZE]:
            raise ValueError("runtime dump raw header differs from transport database")
    else:
        runtime = transform_database(transport, d3 or 0)
        source = "owner-transport-transformed"
    try:
        header = _validate_runtime(runtime)
    except ValueError as exc:
        # The raw header is not encrypted: with a wrong key only the checksum
        # over the decrypted payload fails.
        if d3 is not None and str(exc).endswith("native_checksum"):
            raise ValueError(key_mismatch_message("Datenbank", d3)) from exc
        raise
    report = {
        "schema": "m90-owner-database-runtime-v1",
        "transport": str(transport_path.resolve()),
        "transport_sha256": transport_digest,
        "runtime_source": source,
        "runtime_sha256": hashlib.sha256(runtime).hexdigest().upper(),
        "d3": None if d3 is None else f"{d3 & 0xFFFFFFFF:08X}",
        **header,
    }
    return runtime, report


def parse_int(value: str) -> int:
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("transport", type=Path)
    parser.add_argument("--expected-transport-sha256", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--d3", type=parse_int)
    source.add_argument("--runtime-dump", type=Path)
    parser.add_argument(
        "--write-runtime",
        type=Path,
        help="optional explicit output; never written unless requested",
    )
    args = parser.parse_args()
    runtime, report = prepare_runtime(
        args.transport,
        args.expected_transport_sha256,
        d3=args.d3,
        runtime_dump_path=args.runtime_dump,
    )
    if args.write_runtime:
        args.write_runtime.write_bytes(runtime)
        report["runtime_written_to"] = str(args.write_runtime.resolve())
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
