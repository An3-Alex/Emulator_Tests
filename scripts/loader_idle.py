"""Bound only the ADP loader's CPU-idle wait, not device readiness.

Each original wait still checks GetSystemTimes and its 90-percent threshold.
After 5 seconds it leaves that CPU-load gate through the original cleanup.
Device initialization calls, retries and INITVIDEO communication are untouched.

The wait function is recognized by its instructions; the addresses that move
with a loader build are left out of the comparison. Every loader containing
the function is bounded the same way, a loader without it stays unchanged.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import struct

from graphics_update import inside, durable_copy
from patch_loader_null_device import code_section, trampoline_offset

# The loader of the verified CF image after the null-device patch.
SOURCE_HASH = "d5dd84e59c59a24af4f1dfdd486882bfc6fa777e0dcc22fa3ae7314999ab3aeb"
TIMEOUT_MS = 5_000
IMAGE_BASE = 0x400000
INIT_SITE = 0x4CE6
SLEEP_SITE = 0x4D7B
INIT_CAVE = 0x28880
CHECK_CAVE = 0x288A0
INIT_ORIGINAL = bytes.fromhex("89 44 24 20 A1 90 7E 47 00")
SLEEP_ORIGINAL = bytes.fromhex("68 C8 00 00 00 FF 15 F0 91 42 00")
TARGET = "WINDOWS/explorer_adp_before_qxl.exe"
BACKUP = "NVRAM/m90-loader-idle-backup/explorer_adp_before_qxl.exe"

# The wait function: push ebp; mov ebp,esp; and esp,-8 ... ret, 0x264 bytes.
FUNCTION_HEAD = bytes.fromhex("55 8b ec 83 e4 f8")
FUNCTION_LENGTH = 0x264
# Offsets in the function, counted from its first byte.
FRAME_OFFSET = 0x10       # sub esp,6Ch
INIT_OFFSET = 0x86        # mov [esp+20h],eax; mov eax,[counter]
SLEEP_OFFSET = 0x11B      # push 200; call [Sleep]
CLEANUP_OFFSET = 0x23C    # the wait's own exit
# 32-bit operands that differ between loader builds: absolute addresses of
# data and import slots, and calls that leave the function.
FUNCTION_RELOCATIONS = (23, 29, 34, 40, 57, 64, 77, 82, 92, 139, 145, 204, 210, 231, 263, 290, 391,
                        396, 419, 426, 448, 461, 466, 474, 486, 502, 515, 548, 557, 562, 574, 591, 596)
FUNCTION_DIGEST = "695e1546f0df907b5a6f2c265e4ea6607796d1bf8d3a4ecb9da992a50cbd1d31"
# The trampolines sit behind the null-device trampoline in the code padding.
INIT_CAVE_OFFSET = 0x30
CHECK_CAVE_OFFSET = 0x50
CAVE_ROOM = 128


@dataclass(frozen=True)
class Layout:
    """Where one loader build keeps the wait function and what the trampolines need."""
    function: int           # file offset of the wait function
    cave: int               # file offset of the null-device trampoline
    shift: int              # virtual address = file offset + shift (in the code section)
    tick: int               # virtual address of the GetTickCount import slot
    init_original: bytes
    sleep_original: bytes


VERIFIED = Layout(function=INIT_SITE - INIT_OFFSET, cave=INIT_CAVE - INIT_CAVE_OFFSET, shift=IMAGE_BASE,
                  tick=0x4290A8, init_original=INIT_ORIGINAL, sleep_original=SLEEP_ORIGINAL)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jump(source: int, destination: int) -> bytes:
    return b"\xe9" + struct.pack("<i", destination - (source + 5))


def trampolines(layout: Layout = VERIFIED) -> tuple[bytes, bytes]:
    # Reserve eight extra local bytes (0x74 instead of 0x6c). Saved EDI/ESI/EBX
    # at [esp+0/4/8] must never be reused. PUSHFD + PUSHAD move ESP down by
    # 36, so [esp+9c] is our new [original esp+78] local. Preserve flags/registers
    # around GetTickCount so the original instructions see identical state.
    address = lambda offset: offset + layout.shift
    tick = b"\xff\x15" + struct.pack("<I", layout.tick)
    init_site, sleep_site = layout.function + INIT_OFFSET, layout.function + SLEEP_OFFSET
    init_cave, check_cave = layout.cave + INIT_CAVE_OFFSET, layout.cave + CHECK_CAVE_OFFSET
    init = (layout.init_original[:4] + b"\x9c\x60" + tick
            + bytes.fromhex("89 84 24 9C 00 00 00") + b"\x61\x9d" + layout.init_original[4:])
    init += jump(address(init_cave) + len(init), address(init_site) + len(layout.init_original))
    check = (layout.sleep_original + b"\x9c\x60" + tick
             + bytes.fromhex("2B 84 24 9C 00 00 00") + b"\x3d" + struct.pack("<I", TIMEOUT_MS)
             + b"\x72\x07" + b"\x61\x9d")
    # Unsigned subtraction/comparison also works across GetTickCount rollover.
    check += jump(address(check_cave) + len(check), address(layout.function + CLEANUP_OFFSET))
    check += b"\x61\x9d"
    check += jump(address(check_cave) + len(check), address(sleep_site) + len(layout.sleep_original))
    return init, check


def replacements(layout: Layout = VERIFIED) -> list[tuple[int, bytes, bytes]]:
    init, check = trampolines(layout)
    address = lambda offset: offset + layout.shift
    init_site, sleep_site = layout.function + INIT_OFFSET, layout.function + SLEEP_OFFSET
    init_cave, check_cave = layout.cave + INIT_CAVE_OFFSET, layout.cave + CHECK_CAVE_OFFSET
    return [
        (layout.function + FRAME_OFFSET, bytes.fromhex("83 EC 6C"), bytes.fromhex("83 EC 74")),
        (init_site, layout.init_original,
         jump(address(init_site), address(init_cave)) + b"\x90" * 4),
        (sleep_site, layout.sleep_original,
         jump(address(sleep_site), address(check_cave)) + b"\x90" * 6),
        (init_cave, bytes(len(init)), init),
        (check_cave, bytes(len(check)), check),
    ]


def import_slot(data: bytes, library: str, symbol: str) -> int | None:
    """Virtual address of an import slot of a PE32 file."""
    try:
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        opt = pe + 24
        base = struct.unpack_from("<I", data, opt + 28)[0]
        count = struct.unpack_from("<H", data, pe + 6)[0]
        table = opt + struct.unpack_from("<H", data, pe + 20)[0]
        sections = [struct.unpack_from("<IIII", data, table + index * 40 + 8) for index in range(count)]

        def offset(rva: int) -> int:
            for virtual_size, address, raw_size, raw in sections:
                if address <= rva < address + max(virtual_size, raw_size):
                    return raw + rva - address
            raise ValueError("address outside the file")

        def text(rva: int) -> bytes:
            start = offset(rva)
            return data[start:data.index(b"\0", start)]

        cursor = offset(struct.unpack_from("<I", data, opt + 104)[0])
        for _ in range(4096):
            lookup, _, _, name, slots = struct.unpack_from("<IIIII", data, cursor)
            if not (lookup or name or slots):
                return None
            if text(name).decode("ascii", "replace").casefold() == library.casefold():
                for index in range(65536):
                    entry = struct.unpack_from("<I", data, offset(lookup or slots) + 4 * index)[0]
                    if not entry:
                        break
                    if not entry & 0x80000000 and text(entry + 2) == symbol.encode():
                        return base + slots + 4 * index
            cursor += 20
    except (struct.error, ValueError, IndexError):
        pass
    return None


def function_digest(body: bytes) -> str:
    masked = bytearray(body)
    for position in FUNCTION_RELOCATIONS:
        masked[position:position + 4] = bytes(4)
    return digest(masked)


def locate(data: bytes) -> Layout | None:
    """The wait function of a null-device-patched loader that is not the verified one."""
    cave = trampoline_offset(data)
    if cave is None:
        return None
    section = code_section(data, cave)
    if section is None:
        return None
    header, raw, raw_size, virtual_size = section
    if virtual_size != raw_size or cave + CHECK_CAVE_OFFSET + CAVE_ROOM > raw + raw_size:
        return None
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    shift = struct.unpack_from("<I", data, pe + 24 + 28)[0] + struct.unpack_from("<I", data, header + 12)[0] - raw
    found = []
    start = data.find(FUNCTION_HEAD, raw, raw + raw_size)
    while start >= 0:
        body = data[start:start + FUNCTION_LENGTH]
        if len(body) == FUNCTION_LENGTH and function_digest(body) == FUNCTION_DIGEST:
            found.append(start)
        start = data.find(FUNCTION_HEAD, start + 1, raw + raw_size)
    tick = import_slot(data, "KERNEL32.dll", "GetTickCount")
    if len(found) != 1 or tick is None:
        return None
    function = found[0]
    return Layout(function=function, cave=cave, shift=shift, tick=tick,
                  init_original=data[function + INIT_OFFSET:function + INIT_OFFSET + len(INIT_ORIGINAL)],
                  sleep_original=data[function + SLEEP_OFFSET:function + SLEEP_OFFSET + len(SLEEP_ORIGINAL)])


def layout_of(data: bytes) -> Layout | None:
    return VERIFIED if digest(data) == SOURCE_HASH else locate(data)


def patch_bytes(data: bytes) -> bytes:
    layout = layout_of(data)
    if layout is None:
        raise ValueError("Unrecognized ADP loader; no idle modification applied")
    init_cave = layout.cave + INIT_CAVE_OFFSET
    if data[init_cave:init_cave + CAVE_ROOM] != bytes(CAVE_ROOM):
        raise ValueError("ADP loader executable padding is not empty")
    if layout is VERIFIED and struct.unpack_from("<I", data, 0x1E8)[0] != 0x28000:
        raise ValueError("ADP loader executable section has an unexpected size")
    output = bytearray(data)
    for offset, previous, replacement in replacements(layout):
        if data[offset:offset + len(previous)] != previous:
            raise ValueError(f"Unexpected idle patch site: {offset:x}")
        output[offset:offset + len(previous)] = replacement
    return bytes(output)


def bounded_layouts(data: bytes):
    """Layouts a bounded loader could have: the verified one, then what its hooks point to."""
    yield VERIFIED
    cave = trampoline_offset(data)
    section = code_section(data, cave) if cave is not None else None
    if section is None:
        return
    header, raw, raw_size, _ = section
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    shift = struct.unpack_from("<I", data, pe + 24 + 28)[0] + struct.unpack_from("<I", data, header + 12)[0] - raw
    init_cave, check_cave = cave + INIT_CAVE_OFFSET, cave + CHECK_CAVE_OFFSET
    start = data.find(FUNCTION_HEAD, raw, raw + raw_size)
    while start >= 0:
        if data[start + INIT_OFFSET:start + INIT_OFFSET + 1] == b"\xe9":
            # The trampolines begin with the instructions they replaced.
            init = data[init_cave:init_cave + 26]
            yield Layout(function=start, cave=cave, shift=shift,
                         tick=struct.unpack_from("<I", init, 8)[0],
                         init_original=init[:4] + init[21:26],
                         sleep_original=data[check_cave:check_cave + len(SLEEP_ORIGINAL)])
        start = data.find(FUNCTION_HEAD, start + 1, raw + raw_size)


def restore(data: bytes) -> bytes | None:
    """The loader as it was before the idle wait was bounded; None: it is not bounded."""
    for layout in bounded_layouts(data):
        original = bytearray(data)
        for offset, previous, replacement in replacements(layout):
            if data[offset:offset + len(replacement)] != replacement:
                break
            original[offset:offset + len(replacement)] = previous
        else:
            # Verify the complete patched file, not only a marker or the hook bytes.
            try:
                if patch_bytes(bytes(original)) == data:
                    return bytes(original)
            except ValueError:
                pass
    return None


def is_current(data: bytes) -> bool:
    return restore(data) is not None


def update(root: Path, *, check_only: bool = False) -> str:
    root = root.resolve()
    target = inside(root, TARGET)
    backup = inside(root, BACKUP)
    temporary = inside(root, TARGET + ".idle-new")
    marker = inside(root, "NVRAM/m90_setup_stage.txt")
    if marker.exists() and marker.read_text().strip() != "stage=ready":
        raise ValueError("Image is not in ready stage")
    if temporary.exists():
        raise ValueError("Unfinished ADP idle update; inspect backup before retrying")
    data = target.read_bytes()
    original = restore(data)
    # The backup is the loader before this modification, whatever its version.
    if backup.exists() and backup.read_bytes() != (data if original is None else original):
        raise ValueError(f"Unrecognized file: {backup}")
    if original is not None:
        if not backup.is_file():
            raise ValueError("ADP idle backup missing")
        return "Loader idle wait already bounded (5 seconds per call)"
    if layout_of(data) is None:
        return "Hinweis: Diese Loader-Version enthält die bekannte Warteroutine nicht; sie bleibt unverändert."
    output = patch_bytes(data)
    if check_only:
        return "Loader idle wait update required"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        durable_copy(target, backup)
    if backup.read_bytes() != data:
        raise ValueError(f"Unrecognized file: {backup}")
    # Exclusive creation, fsync and atomic replacement. Until replace succeeds,
    # the original target stays intact. The verified backup is never overwritten.
    try:
        with temporary.open("xb") as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != output:
            raise ValueError(f"Unrecognized file: {temporary}")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return "Loader idle wait bounded (5 seconds per call); original backed up"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print(update(args.root, check_only=args.check_only))
