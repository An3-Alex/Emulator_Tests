#!/usr/bin/env python3
"""Prepare and inspect an ATmega48 V3 admission-card EEPROM image.

The layout comes from the owner's ZLK programmer source.  This module models
the EEPROM contents only; it deliberately does not pretend that a start code
is stored here or that the card's bit-banged wire protocol is implemented.
"""

from __future__ import annotations

import argparse
from pathlib import Path


EEPROM_SIZE = 256
ASCII_NUMBER_OFFSET = 40
PACKED_ID_OFFSET = 64
ERGO_M90_ID = bytes.fromhex("06 32 11 55")


def _check_number(number: str) -> None:
    if len(number) != 9 or not number.isascii() or not number.isdigit():
        raise ValueError("admission number must contain exactly nine ASCII digits")


def packed_admission_number(number: str) -> bytes:
    """Encode the programmer's leading-zero plus nine-digit BCD field."""
    _check_number(number)
    return bytes.fromhex("0" + number)


def build_m90_eeprom(template: bytes, number: str) -> bytes:
    """Preserve the template and set only its two number fields and M90 ID."""
    if len(template) != EEPROM_SIZE:
        raise ValueError(f"ATmega48 card EEPROM must be {EEPROM_SIZE} bytes")
    _check_number(number)
    image = bytearray(template)
    image[ASCII_NUMBER_OFFSET:ASCII_NUMBER_OFFSET + 9] = number.encode("ascii")
    image[PACKED_ID_OFFSET:PACKED_ID_OFFSET + 9] = (
        packed_admission_number(number) + ERGO_M90_ID
    )
    return bytes(image)


def inspect_eeprom(image: bytes) -> tuple[str, bytes]:
    """Return the number and model bytes after checking both number copies."""
    if len(image) != EEPROM_SIZE:
        raise ValueError(f"ATmega48 card EEPROM must be {EEPROM_SIZE} bytes")
    try:
        number = image[ASCII_NUMBER_OFFSET:ASCII_NUMBER_OFFSET + 9].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("EEPROM contains a non-ASCII admission number") from exc
    _check_number(number)
    if image[PACKED_ID_OFFSET:PACKED_ID_OFFSET + 5] != packed_admission_number(number):
        raise ValueError("EEPROM admission-number copies disagree")
    return number, image[PACKED_ID_OFFSET + 5:PACKED_ID_OFFSET + 9]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--number", required=True, help="nine-digit admission number")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = build_m90_eeprom(args.template.read_bytes(), args.number)
    if args.output.resolve() == args.template.resolve():
        parser.error("output must differ from the original template")
    if args.output.exists():
        parser.error("output already exists; refusing to overwrite it")
    args.output.write_bytes(output)
    number, model = inspect_eeprom(output)
    print(f"Card EEPROM ready: {args.output} ({len(output)} bytes, "
          f"number ending {number[-4:]}, model {model.hex(' ').upper()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
