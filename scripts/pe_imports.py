#!/usr/bin/env python3
"""List PE32 imports and their runtime IAT addresses using only stdlib."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def cstr(data: bytes, off: int) -> str:
    end = data.index(0, off)
    return data[off:end].decode("ascii", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    data = args.image.read_bytes()

    pe = u32(data, 0x3C)
    if data[pe : pe + 4] != b"PE\0\0":
        raise SystemExit("not a PE image")
    coff = pe + 4
    section_count = u16(data, coff + 2)
    optional_size = u16(data, coff + 16)
    optional = coff + 20
    if u16(data, optional) != 0x10B:
        raise SystemExit("only PE32 is supported")
    image_base = u32(data, optional + 28)
    import_rva = u32(data, optional + 96 + 8)
    sections_off = optional + optional_size
    sections: list[tuple[int, int, int, int]] = []
    for n in range(section_count):
        off = sections_off + n * 40
        virtual_size = u32(data, off + 8)
        virtual_address = u32(data, off + 12)
        raw_size = u32(data, off + 16)
        raw_pointer = u32(data, off + 20)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_pointer, raw_size))

    def rva_to_off(rva: int) -> int:
        for virtual_address, mapped_size, raw_pointer, raw_size in sections:
            if virtual_address <= rva < virtual_address + mapped_size:
                delta = rva - virtual_address
                if delta >= raw_size:
                    raise ValueError(f"RVA 0x{rva:X} has no file data")
                return raw_pointer + delta
        if rva < sections_off:
            return rva
        raise ValueError(f"unmapped RVA 0x{rva:X}")

    descriptor = rva_to_off(import_rva)
    print("DLL\tIAT_ADDRESS\tIMPORT")
    while any(data[descriptor : descriptor + 20]):
        original_thunk = u32(data, descriptor)
        name_rva = u32(data, descriptor + 12)
        first_thunk = u32(data, descriptor + 16)
        dll = cstr(data, rva_to_off(name_rva))
        names = original_thunk or first_thunk
        index = 0
        while True:
            value = u32(data, rva_to_off(names + index * 4))
            if value == 0:
                break
            if value & 0x80000000:
                symbol = f"ordinal:{value & 0xFFFF}"
            else:
                hint_name = rva_to_off(value)
                symbol = cstr(data, hint_name + 2)
            address = image_base + first_thunk + index * 4
            print(f"{dll}\t0x{address:08X}\t{symbol}")
            index += 1
        descriptor += 20
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
