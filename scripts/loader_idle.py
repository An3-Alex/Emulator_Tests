"""Bound only the known ADP loader's CPU-idle wait, not device readiness.

Each original wait still checks GetSystemTimes and its 90-percent threshold.
After 5 seconds it leaves that CPU-load gate through the original cleanup.
Device initialization calls, retries and INITVIDEO communication are untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import struct

from graphics_update import inside, durable_copy, require_hash

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


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jump(source: int, destination: int) -> bytes:
    return b"\xe9" + struct.pack("<i", destination - (source + 5))


def trampolines() -> tuple[bytes, bytes]:
    # Reserve eight extra local bytes (0x74 instead of 0x6c). Saved EDI/ESI/EBX
    # at [esp+0/4/8] must never be reused. PUSHFD + PUSHAD move ESP down by
    # 36, so [esp+9c] is our new [original esp+78] local. Preserve flags/registers
    # around GetTickCount so the original instructions see identical state.
    tick = bytes.fromhex("FF 15 A8 90 42 00")
    init = (INIT_ORIGINAL[:4] + b"\x9c\x60" + tick
            + bytes.fromhex("89 84 24 9C 00 00 00") + b"\x61\x9d" + INIT_ORIGINAL[4:])
    init += jump(IMAGE_BASE + INIT_CAVE + len(init), IMAGE_BASE + INIT_SITE + len(INIT_ORIGINAL))
    check = (SLEEP_ORIGINAL + b"\x9c\x60" + tick
             + bytes.fromhex("2B 84 24 9C 00 00 00") + b"\x3d" + struct.pack("<I", TIMEOUT_MS)
             + b"\x72\x07" + b"\x61\x9d")
    # Unsigned subtraction/comparison also works across GetTickCount rollover.
    check += jump(IMAGE_BASE + CHECK_CAVE + len(check), 0x404E9C)
    check += b"\x61\x9d"
    check += jump(IMAGE_BASE + CHECK_CAVE + len(check), IMAGE_BASE + SLEEP_SITE + len(SLEEP_ORIGINAL))
    return init, check


def replacements() -> list[tuple[int, bytes, bytes]]:
    init, check = trampolines()
    return [
        (0x4C70, bytes.fromhex("83 EC 6C"), bytes.fromhex("83 EC 74")),
        (INIT_SITE, INIT_ORIGINAL, jump(IMAGE_BASE + INIT_SITE, IMAGE_BASE + INIT_CAVE) + b"\x90" * 4),
        (SLEEP_SITE, SLEEP_ORIGINAL, jump(IMAGE_BASE + SLEEP_SITE, IMAGE_BASE + CHECK_CAVE) + b"\x90" * 6),
        (INIT_CAVE, bytes(len(init)), init),
        (CHECK_CAVE, bytes(len(check)), check),
    ]


def patch_bytes(data: bytes) -> bytes:
    if digest(data) != SOURCE_HASH:
        raise ValueError("Unrecognized ADP loader; no idle modification applied")
    if data[INIT_CAVE:INIT_CAVE + 128] != bytes(128):
        raise ValueError("ADP loader executable padding is not empty")
    if struct.unpack_from("<I", data, 0x1E8)[0] != 0x28000:
        raise ValueError("ADP loader executable section has an unexpected size")
    output = bytearray(data)
    for offset, previous, replacement in replacements():
        if data[offset:offset + len(previous)] != previous:
            raise ValueError(f"Unexpected idle patch site: {offset:x}")
        output[offset:offset + len(previous)] = replacement
    return bytes(output)


def is_current(data: bytes) -> bool:
    # Verify the complete patched file, not only a marker or the hook bytes.
    original = bytearray(data)
    for offset, previous, replacement in replacements():
        if data[offset:offset + len(replacement)] != replacement:
            return False
        original[offset:offset + len(replacement)] = previous
    return digest(original) == SOURCE_HASH and patch_bytes(bytes(original)) == data


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
    if backup.exists():
        require_hash(backup, {SOURCE_HASH})
    if is_current(data):
        if not backup.is_file():
            raise ValueError("ADP idle backup missing")
        return "Loader idle wait already bounded (5 seconds per call)"
    output = patch_bytes(data)
    if check_only:
        return "Loader idle wait update required"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        durable_copy(target, backup)
    require_hash(backup, {SOURCE_HASH})
    # Exclusive creation, fsync and atomic replacement. Until replace succeeds,
    # the original target stays intact. The verified backup is never overwritten.
    try:
        with temporary.open("xb") as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        require_hash(temporary, {digest(output)})
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
