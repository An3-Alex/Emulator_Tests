#!/usr/bin/env python3
"""Reproduce the owner loader's 0x053C/0x05EC stream transform read-only."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


MAX_DATABASE_SIZE = 64 * 1024 * 1024
RAW_PREFIX_SIZE = 0x100
SEED_XOR = 0x2378BF41


def initialize_state(raw_prefix: bytes, d3: int) -> tuple[list[int], int, int]:
    if len(raw_prefix) != RAW_PREFIX_SIZE:
        raise ValueError("loader transform requires exactly 0x100 raw prefix bytes")
    seed = ((d3 & 0xFFFFFFFF) ^ SEED_XOR).to_bytes(4, "big")
    state = list(range(256))
    key = [
        raw_prefix[index]
        ^ seed[index & 3]
        ^ index
        ^ ((index * 4) & 0xFF)
        for index in range(256)
    ]
    j = 0
    for index in range(256):
        j = (j + state[index] + key[index]) & 0xFF
        state[index], state[j] = state[j], state[index]
    return state, 0, 0


def transform_payload(payload: bytes, state: list[int], i: int = 0, j: int = 0) -> bytes:
    output = bytearray(len(payload))
    for offset, value in enumerate(payload):
        i = (i + 1) & 0xFF
        j = (j + state[i]) & 0xFF
        state[i], state[j] = state[j], state[i]
        output[offset] = value ^ state[(state[i] + state[j]) & 0xFF]
    return bytes(output)


def transform_database(data: bytes, d3: int) -> bytes:
    if len(data) < RAW_PREFIX_SIZE:
        raise ValueError("database is shorter than its raw prefix")
    state, i, j = initialize_state(data[:RAW_PREFIX_SIZE], d3)
    return data[:RAW_PREFIX_SIZE] + transform_payload(data[RAW_PREFIX_SIZE:], state, i, j)


def inspect_transform(path: Path, expected_sha256: str, d3: int) -> dict:
    data = path.read_bytes()
    if len(data) > MAX_DATABASE_SIZE:
        raise ValueError("database exceeds safety size limit")
    digest = hashlib.sha256(data).hexdigest().upper()
    expected = expected_sha256.replace(" ", "").upper()
    if digest != expected:
        raise ValueError(f"database SHA-256 mismatch: expected {expected}, got {digest}")
    transformed = transform_database(data, d3)
    stored_checksum = struct.unpack_from(">I", transformed, 0)[0]
    calculated_checksum = sum(transformed[4:]) & 0xFFFFFFFF
    entrypoint = struct.unpack_from(">I", transformed, 0x4C)[0]
    entry_offset = entrypoint - 0x1000
    entry_bytes = (
        transformed[entry_offset:entry_offset + 16]
        if 0 <= entry_offset < len(transformed) else b""
    )
    return {
        "schema": "m90-owner-database-transform-check-v1",
        "source": str(path.resolve()),
        "source_sha256": digest,
        "d3": f"{d3 & 0xFFFFFFFF:08X}",
        "seed_after_xor": f"{((d3 & 0xFFFFFFFF) ^ SEED_XOR):08X}",
        "raw_prefix_bytes": RAW_PREFIX_SIZE,
        "transformed_payload_bytes": len(data) - RAW_PREFIX_SIZE,
        "stored_checksum": f"{stored_checksum:08X}",
        "calculated_checksum": f"{calculated_checksum:08X}",
        "checksum_matches": stored_checksum == calculated_checksum,
        "entrypoint": f"{entrypoint:08X}",
        "entry_bytes_hex": entry_bytes.hex(" ").upper(),
        "handling": "The source was opened read-only; transformed bytes were held only in process memory.",
    }


def parse_int(value: str) -> int:
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--d3", type=parse_int, required=True)
    args = parser.parse_args()
    report = inspect_transform(args.database, args.expected_sha256, args.d3)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["checksum_matches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
