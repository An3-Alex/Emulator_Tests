#!/usr/bin/env python3
"""Select irrKlang's documented null output driver for the QEMU compatibility build."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path


SOURCE_SHA256 = "7c75908848d95bde2f04797c82d496dff1301f637815a68c7d965ebdee81cb97"
SIGNATURE = bytes.fromhex("68 fc 57 7e 00 6a 00 6a 3d 6a 00 ff 15 04 e4 6f 00")
DRIVER_VALUE_INDEX = 10
ESOD_NULL = 6


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    source = args.source.read_bytes()
    actual = digest(source)
    if actual != SOURCE_SHA256:
        raise SystemExit(f"refusing unknown source: expected {SOURCE_SHA256}, got {actual}")

    matches = [
        offset
        for offset in range(0, len(source) - len(SIGNATURE) + 1)
        if source.startswith(SIGNATURE, offset)
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected one irrKlang constructor signature, got {len(matches)}")

    offset = matches[0]
    patched = bytearray(source)
    patched[offset + DRIVER_VALUE_INDEX] = ESOD_NULL
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.destination.with_suffix(args.destination.suffix + ".tmp")
    temporary.write_bytes(patched)
    os.replace(temporary, args.destination)

    print(f"source_sha256={actual}")
    print(f"patched_sha256={digest(patched)}")
    print(f"patch_offset=0x{offset + DRIVER_VALUE_INDEX:X}")
    print("irrklang_driver=ESOD_NULL (6)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
