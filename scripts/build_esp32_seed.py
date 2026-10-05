"""Build a checked, 2 MiB *cold-boot seed* for the ESP32-S3 prototype.

This is not a live SRAM snapshot and does not include changing accounting state.
The source files remain outside the distributable firmware.
"""

from __future__ import annotations

import argparse
import json
import struct
import zlib
from pathlib import Path

from owner_config_runtime import CONFIG_CLEAR_START, RAM_SIZE, prepare_config_writes
from owner_database_runtime import LOAD_ADDRESS, prepare_runtime


SECTOR_SIZE = 4096
LOADER_ADDRESS = 0x400
MAGIC = b"M90S3RAM"
VERSION = 1
HEADER = struct.Struct(">8sIIII")  # magic, version, generation, size, CRC32
DEFAULT_D3 = 0xD27B7159


def make_ram_image(
    loader: bytes, runtime: bytes, config_writes: list[tuple[int, bytes]],
) -> bytes:
    if not loader or LOADER_ADDRESS + len(loader) > LOAD_ADDRESS:
        raise ValueError("loader does not fit before database at 0x1000")
    if not runtime or LOAD_ADDRESS + len(runtime) > CONFIG_CLEAR_START:
        raise ValueError("database overlaps the config SRAM region")
    ram = bytearray(RAM_SIZE)
    struct.pack_into(">I", ram, 0, RAM_SIZE)
    ram[LOADER_ADDRESS:LOADER_ADDRESS + len(loader)] = loader
    ram[LOAD_ADDRESS:LOAD_ADDRESS + len(runtime)] = runtime
    for address, data in config_writes:
        if address < 0 or address + len(data) > RAM_SIZE:
            raise ValueError("config write is outside 2 MiB SRAM")
        ram[address:address + len(data)] = data
    return bytes(ram)


def pack_image(ram: bytes, generation: int = 0) -> bytes:
    if len(ram) != RAM_SIZE:
        raise ValueError("RAM image must be exactly 2 MiB")
    if not 0 <= generation <= 0xFFFFFFFF:
        raise ValueError("generation is outside uint32 range")
    header = HEADER.pack(
        MAGIC, VERSION, generation, RAM_SIZE, zlib.crc32(ram),
    )
    return header + bytes(SECTOR_SIZE - len(header)) + ram


def unpack_image(image: bytes) -> tuple[bytes, int]:
    if len(image) != SECTOR_SIZE + RAM_SIZE:
        raise ValueError("wrong image size")
    magic, version, generation, size, checksum = HEADER.unpack_from(image)
    if magic != MAGIC or version != VERSION or size != RAM_SIZE:
        raise ValueError("wrong image header")
    ram = image[SECTOR_SIZE:]
    if zlib.crc32(ram) != checksum:
        raise ValueError("RAM CRC32 mismatch")
    return ram, generation


def _member(manifest: dict, role: str) -> dict:
    candidates = [item for item in manifest["members"] if item["role"] == role]
    if len(candidates) != 1:
        raise ValueError(f"manifest must contain exactly one {role}")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--loader", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--d3", type=lambda value: int(value, 0), default=DEFAULT_D3)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    loader_member = _member(manifest, "loader")
    database_member = _member(manifest, "database")
    config_members = [
        item for item in manifest["members"]
        if item["role"] == "database_module" and item["source_name"] == "M90_Las_Vegas.bin"
    ]
    if len(config_members) != 1:
        raise ValueError("manifest must contain the M90_Las_Vegas config")
    import hashlib

    loader = args.loader.read_bytes()
    if hashlib.sha256(loader).hexdigest().upper() != loader_member["sha256"]:
        raise ValueError("loader SHA-256 mismatch")
    runtime, report = prepare_runtime(
        args.database, database_member["sha256"], d3=args.d3,
    )
    config_writes, _ = prepare_config_writes(
        args.config, config_members[0]["sha256"], args.d3,
    )
    ram = make_ram_image(loader, runtime, config_writes)
    image = pack_image(ram)
    unpack_image(image)
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    args.output.write_bytes(image)
    print(
        f"ESP32_SEED_WRITTEN path={args.output.resolve()} bytes={len(image)} "
        f"entry={report['entrypoint']} runtime_sha256={report['runtime_sha256']} "
        "cold_boot_seed=1 live_initialized_snapshot=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
