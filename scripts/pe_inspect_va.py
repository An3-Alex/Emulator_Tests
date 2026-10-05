#!/usr/bin/env python3
"""Inspect raw bytes or strings at virtual addresses in a PE32 image."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("address", nargs="+", type=lambda value: int(value, 0))
    parser.add_argument("--length", type=int, default=96)
    args = parser.parse_args()

    data = args.image.read_bytes()
    pe = u32(data, 0x3C)
    optional = pe + 24
    image_base = u32(data, optional + 28)
    section_count = u16(data, pe + 6)
    section_table = optional + u16(data, pe + 20)
    sections: list[tuple[str, int, int, int]] = []
    for index in range(section_count):
        entry = section_table + index * 40
        name = data[entry : entry + 8].rstrip(b"\0").decode("ascii", "replace")
        virtual_size = u32(data, entry + 8)
        virtual_address = u32(data, entry + 12)
        raw_size = u32(data, entry + 16)
        raw_offset = u32(data, entry + 20)
        sections.append((name, virtual_address, max(virtual_size, raw_size), raw_offset))

    for address in args.address:
        rva = address - image_base if address >= image_base else address
        for name, start, size, raw_offset in sections:
            if start <= rva < start + size:
                offset = raw_offset + rva - start
                chunk = data[offset : offset + args.length]
                ascii_text = chunk.split(b"\0", 1)[0].decode("cp1252", "replace")
                utf16_text = chunk.decode("utf-16-le", "replace").split("\0", 1)[0]
                print(f"address=0x{address:08X} section={name} offset=0x{offset:X}")
                print("hex=" + chunk.hex(" "))
                safe_ascii = repr(ascii_text).encode("ascii", "backslashreplace").decode("ascii")
                safe_utf16 = repr(utf16_text).encode("ascii", "backslashreplace").decode("ascii")
                print("ascii=" + safe_ascii)
                print("utf16=" + safe_utf16)
                break
        else:
            print(f"address=0x{address:08X} is not file-backed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
