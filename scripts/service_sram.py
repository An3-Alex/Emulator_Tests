"""Attach the verified XP service executable to the shared SRAM compatibility DLL.

Adds only a PE import section. Original instructions/resources and original
imports are preserved. Unknown service versions are never patched.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import struct

SERVICE_HASH = "d9d04e9b70bf0fc6ec3dd4e57624940b107881067b4d1952f581bfc12befa2c8"
# Earlier shims: before the service import, and before the service was
# recognized by its import (an 8.3-named service got no SRAM and crashed).
PREVIOUS_SRAM_HASHES = {"4d62ee6e183ba534f7ac7d2780d4a5fb90f2394bc4fc68fd6d6b9ea640b3aa94",
                        "31cac0b2141d2c8896e9dcf5cc2bd19ea0e3e0e1196625b9a79645d1edc6c9d6",
                        "4b98b1f2a60e939e1dc40c135e73edb47805249493d566e3f0599106f21ed608"}  # before the 10-MB guest log limit
SRAM_HASH = "555f2a7b6e886e9476b7f83ecee89c3cfa369c823f82f5bdf4c6181c7ce41dd2"
SERVICE_PATH = "WorkDir/GGSG_Servic/GGSG_Servic.exe"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def patch_service(source: bytes, *, writable_iat: bool = True) -> bytes:
    """writable_iat=False reproduces the earlier patch, which XP cannot load: its
    loader unprotects only the section holding the import directory while
    binding, so the Borland IATs in read-only .idata faulted (0xC0000005)."""
    if digest(source) != SERVICE_HASH:
        raise ValueError("Unrecognized service executable; original must remain unchanged")
    data = bytearray(source)
    u16 = lambda off: struct.unpack_from("<H", data, off)[0]
    u32 = lambda off: struct.unpack_from("<I", data, off)[0]
    pe = u32(0x3C)
    opt = pe + 24
    count = u16(pe + 6)
    table = opt + u16(pe + 20)
    if data[pe:pe + 4] != b"PE\0\0" or u16(pe + 4) != 0x14C or u16(opt) != 0x10B:
        raise ValueError("Expected XP x86 PE32 service")
    sections = [(u32(table + i * 40 + 12), u32(table + i * 40 + 8),
                 u32(table + i * 40 + 20), u32(table + i * 40 + 16)) for i in range(count)]
    header = table + count * 40
    first_raw = min(raw for _, _, raw, size in sections if size)
    if header + 40 > first_raw or any(data[header:header + 40]):
        raise ValueError("No safe space for additive SRAM import section")
    def offset(rva):
        for va, _, raw, size in sections:
            if va <= rva < va + size:
                return raw + rva - va
        raise ValueError("Invalid import RVA")
    old_import = offset(u32(opt + 104))
    descriptors = bytearray()
    cursor = old_import
    while any(data[cursor:cursor + 20]):
        if cursor + 20 > len(data) or len(descriptors) > 8192:
            raise ValueError("Invalid import descriptors")
        descriptors += data[cursor:cursor + 20]
        cursor += 20
    if writable_iat:
        # The moved import directory no longer marks the original IATs for
        # the XP loader; their sections must allow writes during binding.
        thunks = {struct.unpack_from("<I", descriptors, index + 16)[0]
                  for index in range(0, len(descriptors), 20)}
        for index, (va, virtual, _, size) in enumerate(sections):
            if any(va <= thunk < va + max(virtual, size) for thunk in thunks):
                flags = table + index * 40 + 36
                struct.pack_into("<I", data, flags, u32(flags) | 0x80000000)
    align = lambda number, unit: (number + unit - 1) // unit * unit
    section_alignment, file_alignment = u32(opt + 32), u32(opt + 36)
    rva = align(max(va + max(size, virtual) for va, virtual, _, size in sections), section_alignment)
    raw = align(len(data), file_alignment)
    descriptor_end = len(descriptors) + 40
    ilt, iat = descriptor_end, descriptor_end + 8
    dll_name = descriptor_end + 16
    symbol = align(dll_name + len(b"FBWFLIB.dll\0"), 2)
    payload = bytearray(symbol + len(b"\0\0SramCompatInitialize\0"))
    payload[:len(descriptors)] = descriptors
    struct.pack_into("<IIIII", payload, len(descriptors), rva + ilt, 0, 0, rva + dll_name, rva + iat)
    struct.pack_into("<I", payload, ilt, rva + symbol)
    struct.pack_into("<I", payload, iat, rva + symbol)
    payload[dll_name:dll_name + 12] = b"FBWFLIB.dll\0"
    payload[symbol:] = b"\0\0SramCompatInitialize\0"
    raw_size = align(len(payload), file_alignment)
    struct.pack_into("<8sIIIIIIHHI", data, header, b".m90sram", len(payload), rva,
                     raw_size, raw, 0, 0, 0, 0, 0xC0000040)
    struct.pack_into("<H", data, pe + 6, count + 1)
    struct.pack_into("<I", data, opt + 8, u32(opt + 8) + raw_size)
    struct.pack_into("<I", data, opt + 56, align(rva + len(payload), section_alignment))
    struct.pack_into("<I", data, opt + 64, 0)  # checksum of original no longer applies
    struct.pack_into("<II", data, opt + 104, rva, descriptor_end)
    struct.pack_into("<II", data, opt + 96 + 11 * 8, 0, 0)  # discard bound import cache
    data.extend(bytes(raw - len(data)))
    data.extend(payload)
    data.extend(bytes(raw_size - len(payload)))
    return bytes(data)


def update(root: Path, proxy: Path, *, check_only: bool = False) -> str:
    from graphics_update import inside
    root = root.resolve()
    replacement = proxy.read_bytes()
    if digest(replacement) != SRAM_HASH:
        raise ValueError("Unrecognized SRAM compatibility DLL")
    service = inside(root, SERVICE_PATH)
    backup = service.with_name(service.name + ".pre-m90-sram")
    inside(root, str(backup.relative_to(root)))
    current = service.read_bytes()
    original = backup.read_bytes() if backup.exists() else current
    patched = patch_service(original)
    if current not in (original, patched, patch_service(original, writable_iat=False)):
        raise ValueError("Service differs from original and verified SRAM version")
    if current != original and not backup.exists():
        raise ValueError("Original service backup missing")
    targets = [inside(root, "NVRAM/FBWFLIB.dll"), inside(root, "WorkDir/FBWFLIB.dll"),
               service.with_name("FBWFLIB.dll")]
    changed = current != patched
    for target in targets:
        if target.exists():
            actual = digest(target.read_bytes())
            if actual != SRAM_HASH and actual not in PREVIOUS_SRAM_HASHES:
                raise ValueError(f"Unrecognized SRAM DLL: {target}")
            changed |= actual != SRAM_HASH
        else:
            changed = True
        if target.with_name(target.name + ".m90-sram-new").exists():
            raise ValueError(f"Unfinished SRAM update: {target}")
    if not changed:
        return "Service SRAM already current"
    if check_only:
        return "Service SRAM update required"
    # Back up every existing file before changing any; do not touch SRAM data.
    if not backup.exists():
        with backup.open("xb") as stream:
            stream.write(original); stream.flush(); os.fsync(stream.fileno())
    old_files = [(target, target.read_bytes() if target.exists() else None) for target in targets]
    for target, previous in old_files:
        saved = target.with_name(target.name + ".pre-service-sram")
        inside(root, str(saved.relative_to(root)))
        if previous is not None and not saved.exists():
            with saved.open("xb") as stream:
                stream.write(previous); stream.flush(); os.fsync(stream.fileno())
    installed = []
    def replace(target, content):
        temporary = target.with_name(target.name + ".m90-sram-new")
        try:
            with temporary.open("xb") as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    try:
        for target, _ in old_files:
            replace(target, replacement); installed.append(target)
        # DLL is installed first, then add the import to the executable.
        replace(service, patched)
    except Exception:
        for target, previous in reversed(old_files):
            if target in installed:
                if previous is None: target.unlink(missing_ok=True)
                else: replace(target, previous)
        raise
    return "Service SRAM updated; original executable and DLLs backed up"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("proxy", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print(update(args.root, args.proxy, check_only=args.check_only))
