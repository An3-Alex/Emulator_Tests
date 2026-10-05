#!/usr/bin/env python3
"""Map the game's second D3D9 device to software adapter zero."""
import argparse, hashlib
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("source", type=Path)
p.add_argument("destination", type=Path)
a = p.parse_args()
data = bytearray(a.source.read_bytes())
expected_hash = "13b38c44cde88ee4af9a01796814d3502505eb1eda1073f66899a35bbd32aba0"
actual = hashlib.sha256(data).hexdigest()
if actual != expected_hash:
    raise SystemExit(f"source hash mismatch: {actual}")
needle = bytes.fromhex("c7 45 94 01 00 00 00 8b 4d fc 81 c1 f4 00 00 00")
replacement = bytes.fromhex("c7 45 94 00 00 00 00 8b 4d fc 81 c1 f4 00 00 00")
hits = [i for i in range(len(data)) if data.startswith(needle, i)]
if hits != [0x292795]:
    raise SystemExit(f"unexpected patch sites: {[hex(i) for i in hits]}")
data[hits[0]:hits[0] + len(needle)] = replacement
a.destination.write_bytes(data)
print(hashlib.sha256(data).hexdigest())
