#!/usr/bin/env python3
"""Force the game's existing single-D3D-device/two-TFT mode."""
import argparse, hashlib
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument("source",type=Path); p.add_argument("destination",type=Path); a=p.parse_args()
d=bytearray(a.source.read_bytes())
expected="13b38c44cde88ee4af9a01796814d3502505eb1eda1073f66899a35bbd32aba0"
actual=hashlib.sha256(d).hexdigest()
if actual != expected: raise SystemExit(f"source hash mismatch: {actual}")
offset=0x2e0d72
if d[offset:offset+2] != bytes.fromhex("75 13"): raise SystemExit("branch bytes mismatch")
d[offset:offset+2]=bytes.fromhex("90 90")
a.destination.write_bytes(d)
print(hashlib.sha256(d).hexdigest())
