#!/usr/bin/env python3
"""Bounded read-only Motorola 68000 disassembler for owner-supplied dumps."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

dependency_dir = Path(__file__).parents[1] / ".deps" / "capstone"
if dependency_dir.is_dir():
    sys.path.insert(0, str(dependency_dir))

from capstone import (
    CS_ARCH_M68K,
    CS_MODE_BIG_ENDIAN,
    CS_MODE_M68K_000,
    CS_MODE_M68K_020,
    Cs,
)


MAX_INPUT_SIZE = 64 * 1024 * 1024


def parse_int(value: str) -> int:
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--offset", type=parse_int, default=0)
    parser.add_argument("--length", type=parse_int, default=0x100)
    parser.add_argument("--address", type=parse_int)
    parser.add_argument("--cpu", choices=("68000", "68020"), default="68000")
    args = parser.parse_args()

    size = args.input.stat().st_size
    if size > MAX_INPUT_SIZE:
        parser.error(f"input exceeds {MAX_INPUT_SIZE} bytes")
    if args.offset < 0 or args.length <= 0 or args.offset + args.length > size:
        parser.error("requested range is outside the input")
    data = args.input.read_bytes()[args.offset : args.offset + args.length]
    address = args.offset if args.address is None else args.address
    cpu_mode = CS_MODE_M68K_000 if args.cpu == "68000" else CS_MODE_M68K_020
    md = Cs(CS_ARCH_M68K, CS_MODE_BIG_ENDIAN | cpu_mode)
    for instruction in md.disasm(data, address):
        raw = instruction.bytes.hex(" ").upper()
        print(
            f"{instruction.address:08X}  {raw:<26} "
            f"{instruction.mnemonic:<10} {instruction.op_str}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
