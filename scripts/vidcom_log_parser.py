#!/usr/bin/env python3
"""Parse the legacy binary VidComLog record format without executing commands.

Each file stores newest records first.  A record consists of a little-endian
DWORD tick count, WORD command, WORD payload length, the payload, and CRLF.
The parser is deliberately read-only and places conservative bounds on input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path


MAX_FILE_SIZE = 64 * 1024 * 1024
MAX_PAYLOAD_SIZE = 0xFFFF
RECORD_HEADER = struct.Struct("<IHH")


@dataclass(frozen=True)
class VidComRecord:
    file_offset: int
    tick: int
    command: int
    payload_length: int
    payload_hex: str


def parse_records(data: bytes) -> list[VidComRecord]:
    if len(data) > MAX_FILE_SIZE:
        raise ValueError(f"input exceeds {MAX_FILE_SIZE} bytes")

    records: list[VidComRecord] = []
    offset = 0
    while offset < len(data):
        record_offset = offset
        if len(data) - offset < RECORD_HEADER.size:
            raise ValueError(f"truncated record header at offset 0x{offset:X}")
        tick, command, payload_length = RECORD_HEADER.unpack_from(data, offset)
        offset += RECORD_HEADER.size
        if payload_length > MAX_PAYLOAD_SIZE:
            raise ValueError(f"oversized payload at offset 0x{record_offset:X}")
        payload_end = offset + payload_length
        terminator_end = payload_end + 2
        if terminator_end > len(data):
            raise ValueError(f"truncated payload at offset 0x{record_offset:X}")
        payload = data[offset:payload_end]
        if data[payload_end:terminator_end] != b"\r\n":
            raise ValueError(f"missing CRLF at offset 0x{payload_end:X}")
        records.append(
            VidComRecord(
                file_offset=record_offset,
                tick=tick,
                command=command,
                payload_length=payload_length,
                payload_hex=payload.hex(" ").upper(),
            )
        )
        offset = terminator_end
    return records


def parse_file(path: Path) -> tuple[str, list[VidComRecord]]:
    size = path.stat().st_size
    if size > MAX_FILE_SIZE:
        raise ValueError(f"input exceeds {MAX_FILE_SIZE} bytes")
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest().upper(), parse_records(data)


def summarize_records(records: list[VidComRecord]) -> dict:
    chronological = list(reversed(records))
    gaps = [
        (current.tick - previous.tick, previous.tick, current.tick)
        for previous, current in zip(chronological, chronological[1:])
        if current.tick >= previous.tick
    ]
    largest_gap = max(gaps, default=(0, None, None))
    return {
        "record_count": len(records),
        "first_tick": chronological[0].tick if chronological else None,
        "last_tick": chronological[-1].tick if chronological else None,
        "largest_forward_gap": {
            "milliseconds": largest_gap[0],
            "after_tick": largest_gap[1],
            "before_tick": largest_gap[2],
        },
        "command_counts": {
            str(command): count
            for command, count in sorted(Counter(item.command for item in records).items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--chronological", action="store_true")
    parser.add_argument("--command", type=lambda value: int(value, 0), action="append")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    digest, records = parse_file(args.input)
    if args.command:
        allowed = set(args.command)
        records = [record for record in records if record.command in allowed]
    if args.chronological:
        records.reverse()
    output = {
        "source": str(args.input.resolve()),
        "sha256": digest,
        "stored_order": "newest-first",
        "output_order": "chronological" if args.chronological else "stored",
        "record_count": len(records),
        "records": [asdict(record) for record in records],
    }
    if args.summary:
        output = {
            "source": str(args.input.resolve()),
            "sha256": digest,
            "stored_order": "newest-first",
            "summary": summarize_records(records),
        }
    print(json.dumps(output, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
