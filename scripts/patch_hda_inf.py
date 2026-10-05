#!/usr/bin/env python3
"""Add only the QEMU HDA codec ID to Microsoft's XP UAA function-driver INF."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


EXPECTED_SHA256 = "837e4707249f9ee58bcb9fc340d43f85c54873f7fa29664fcaea29449dfe86ab"
ANCHOR = (
    "%HdAudioFunctionDriver.DDKCodec.DeviceDesc% = HdAudModel,   "
    "HDAUDIO\\FUNC_01&VEN_2003&DEV_5678&SUBSYS_00000000&REV_0000"
)
QEMU_LINE = (
    "%HdAudioFunctionDriver.DDKCodec.DeviceDesc% = HdAudModel,   "
    "HDAUDIO\\FUNC_01&VEN_1AF4&DEV_0022"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    raw = args.source.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != EXPECTED_SHA256:
        raise SystemExit(f"source SHA-256 mismatch: {actual}")

    text = raw.decode("utf-16")
    if QEMU_LINE in text:
        raise SystemExit("QEMU HDA line already present in source")
    if text.count(ANCHOR) != 1:
        raise SystemExit(f"expected exactly one anchor, got {text.count(ANCHOR)}")

    patched = text.replace(ANCHOR, ANCHOR + "\r\n" + QEMU_LINE)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patched.encode("utf-16"))
    print(f"output={args.output}")
    print(f"sha256={hashlib.sha256(args.output.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
