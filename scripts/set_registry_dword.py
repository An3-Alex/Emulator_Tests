#!/usr/bin/env python3
"""Set one existing REG_DWORD in an offline registry hive with verification."""

from __future__ import annotations

import argparse
import struct

import hivex


def find_child(hive: hivex.Hivex, node: int, name: str) -> int:
    wanted = name.casefold()
    for child in hive.node_children(node):
        if hive.node_name(child).casefold() == wanted:
            return child
    raise KeyError(name)


def find_value(hive: hivex.Hivex, node: int, name: str) -> int:
    wanted = name.casefold()
    for value in hive.node_values(node):
        if hive.value_key(value).casefold() == wanted:
            return value
    raise KeyError(name)


def read_dword(hive: hivex.Hivex, value: int) -> int:
    value_type = hive.value_type(value)
    type_code = value_type[0] if isinstance(value_type, tuple) else value_type
    if type_code != 4:
        raise TypeError(f"value is registry type {type_code}, not REG_DWORD")
    raw = hive.value_value(value)
    if isinstance(raw, tuple):
        raw = raw[-1]
    if len(raw) < 4:
        raise ValueError("REG_DWORD data is shorter than four bytes")
    return struct.unpack("<I", raw[:4])[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("hive")
    parser.add_argument("key")
    parser.add_argument("value")
    parser.add_argument("new_value", type=lambda text: int(text, 0))
    parser.add_argument("--expect", type=lambda text: int(text, 0))
    args = parser.parse_args()

    hive = hivex.Hivex(args.hive, write=True)
    node = hive.root()
    for part in args.key.replace("\\", "/").split("/"):
        if part:
            node = find_child(hive, node, part)
    value = find_value(hive, node, args.value)
    before = read_dword(hive, value)
    if args.expect is not None and before != args.expect:
        raise SystemExit(
            f"refusing change: current=0x{before:08X}, expected=0x{args.expect:08X}"
        )

    hive.node_set_value(
        node,
        {"key": args.value, "t": 4, "value": struct.pack("<I", args.new_value)},
    )
    hive.commit(None)
    hive = hivex.Hivex(args.hive)
    node = hive.root()
    for part in args.key.replace("\\", "/").split("/"):
        if part:
            node = find_child(hive, node, part)
    after = read_dword(hive, find_value(hive, node, args.value))
    if after != args.new_value:
        raise SystemExit(
            f"verification failed: wrote 0x{args.new_value:08X}, read 0x{after:08X}"
        )
    print(f"before=0x{before:08X}")
    print(f"after=0x{after:08X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
