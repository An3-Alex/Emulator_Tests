#!/usr/bin/env python3
"""Guard game.exe's missing SetupAPI device path before CreateFileW."""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
from pathlib import Path


ORIGINAL_SHA256 = "27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d"
PATCH_OFFSET = 0x283C63
PATCH_EXPECTED = bytes.fromhex("8b 48 28 83 c1 04")
PATCH_BYTES = bytes.fromhex("e9 48 9a 07 00 90")
CAVE_OFFSET = 0x2FD6B0
CAVE_EXPECTED = bytes(27)
CAVE_BYTES = bytes.fromhex(
    "8b 48 28"       # original: mov ecx,[eax+28h]
    "85 c9"          # test ecx,ecx
    "74 09"          # jz null_path
    "83 c1 04"       # original: point past the string header
    "51"             # original: push file name
    "e9 aa 65 f8 ff" # jmp 0x00683C6A (original CreateFileW call)
    "83 c4 18"       # null_path: discard six prior CreateFileW arguments
    "83 c8 ff"       # simulate INVALID_HANDLE_VALUE
    "e9 a5 65 f8 ff" # jmp 0x00683C70 (normal post-call handling)
)
TEXT_VIRTUAL_SIZE_OFFSET = 0x230
TEXT_VIRTUAL_SIZE_EXPECTED = 0x2FC6A0
TEXT_VIRTUAL_SIZE_PATCHED = 0x2FD000


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    source = args.source.read_bytes()
    actual_hash = digest(source)
    if actual_hash != ORIGINAL_SHA256:
        raise SystemExit(
            f"refusing unknown game.exe: expected {ORIGINAL_SHA256}, got {actual_hash}"
        )
    if source[PATCH_OFFSET : PATCH_OFFSET + len(PATCH_EXPECTED)] != PATCH_EXPECTED:
        raise SystemExit("patch-site bytes do not match the verified game.exe")
    if source[CAVE_OFFSET : CAVE_OFFSET + len(CAVE_EXPECTED)] != CAVE_EXPECTED:
        raise SystemExit("selected executable padding is not empty")
    if struct.unpack_from("<I", source, TEXT_VIRTUAL_SIZE_OFFSET)[0] != TEXT_VIRTUAL_SIZE_EXPECTED:
        raise SystemExit(".text virtual size does not match the verified game.exe")

    patched = bytearray(source)
    patched[PATCH_OFFSET : PATCH_OFFSET + len(PATCH_BYTES)] = PATCH_BYTES
    patched[CAVE_OFFSET : CAVE_OFFSET + len(CAVE_BYTES)] = CAVE_BYTES
    struct.pack_into("<I", patched, TEXT_VIRTUAL_SIZE_OFFSET, TEXT_VIRTUAL_SIZE_PATCHED)

    args.destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.destination.with_suffix(args.destination.suffix + ".tmp")
    temporary.write_bytes(patched)
    os.replace(temporary, args.destination)
    print(f"source_sha256={actual_hash}")
    print(f"patched_sha256={digest(patched)}")
    print(f"patch_offset=0x{PATCH_OFFSET:X}")
    print(f"trampoline_offset=0x{CAVE_OFFSET:X}")
    print(f"text_virtual_size=0x{TEXT_VIRTUAL_SIZE_PATCHED:X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
