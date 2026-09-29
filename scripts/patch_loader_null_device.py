#!/usr/bin/env python3
"""Patch the loader's null device-path crash without changing valid paths.

The original routine dereferences its optional string object as ``ptr + 4``
even when device enumeration left ``ptr`` as NULL.  The trampoline below
returns the routine's normal failure value for NULL and executes the original
CreateFileW path byte-for-byte for every non-NULL pointer.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
from pathlib import Path


ORIGINAL_SHA256 = "2fb4233b541431a1b940ed5af6f11096b7fd5846316e3c4e55bd0e9a7b37a5c1"
PATCH_OFFSET = 0x1943E
PATCH_EXPECTED = bytes.fromhex("6a 00 68 00 00 00 c0")
PATCH_BYTES = bytes.fromhex("e9 0d f4 00 00 90 90")
CAVE_OFFSET = 0x28850
CAVE_EXPECTED = bytes(23)
TEXT_VIRTUAL_SIZE_OFFSET = 0x1E8
TEXT_VIRTUAL_SIZE_EXPECTED = 0x2784E
TEXT_VIRTUAL_SIZE_PATCHED = 0x28000
CAVE_BYTES = bytes.fromhex(
    "85 c0"          # test eax,eax
    "74 0c"          # jz null_path
    "6a 00"          # original push 0 (share mode)
    "68 00 00 00 c0" # original push 0xC0000000 (access)
    "e9 e5 0b ff ff" # jmp 0x00419445 (original add eax,4)
    "83 c4 10"       # null_path: discard four prior CreateFileW arguments
    "33 c0"          # return FALSE
    "5e"             # restore ESI saved by the routine
    "c3"             # return to caller
)


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
            f"refusing unknown loader: expected {ORIGINAL_SHA256}, got {actual_hash}"
        )
    if source[PATCH_OFFSET : PATCH_OFFSET + len(PATCH_EXPECTED)] != PATCH_EXPECTED:
        raise SystemExit("patch-site bytes do not match the verified loader")
    if source[CAVE_OFFSET : CAVE_OFFSET + len(CAVE_EXPECTED)] != CAVE_EXPECTED:
        raise SystemExit("selected executable padding is not empty")
    if struct.unpack_from("<I", source, TEXT_VIRTUAL_SIZE_OFFSET)[0] != TEXT_VIRTUAL_SIZE_EXPECTED:
        raise SystemExit(".text virtual size does not match the verified loader")

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
