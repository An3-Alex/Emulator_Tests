#!/usr/bin/env python3
"""Replace one existing REG_MULTI_SZ value with exact before/after checks."""

from __future__ import annotations

import argparse
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


def encode(values: list[str]) -> bytes:
    return ("\0".join(values) + "\0\0").encode("utf-16-le")


def read_raw(hive: hivex.Hivex, value: int) -> bytes:
    value_type = hive.value_type(value)
    type_code = value_type[0] if isinstance(value_type, tuple) else value_type
    if type_code != 7:
        raise TypeError(f"value is registry type {type_code}, not REG_MULTI_SZ")
    raw = hive.value_value(value)
    return raw[-1] if isinstance(raw, tuple) else raw


def locate(hive: hivex.Hivex, key: str) -> int:
    node = hive.root()
    for part in key.replace("\\", "/").split("/"):
        if part:
            node = find_child(hive, node, part)
    return node


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("hive")
    parser.add_argument("key")
    parser.add_argument("value")
    parser.add_argument("new")
    parser.add_argument("--expect", required=True)
    args = parser.parse_args()
    before_expected = encode([args.expect])
    after_expected = encode([args.new])

    hive = hivex.Hivex(args.hive, write=True)
    node = locate(hive, args.key)
    before = read_raw(hive, find_value(hive, node, args.value))
    if before != before_expected:
        raise SystemExit(f"refusing unexpected current value: {before.hex()}")
    hive.node_set_value(node, {"key": args.value, "t": 7, "value": after_expected})
    hive.commit(None)

    verify = hivex.Hivex(args.hive)
    after = read_raw(verify, find_value(verify, locate(verify, args.key), args.value))
    if after != after_expected:
        raise SystemExit(f"verification failed: {after.hex()}")
    print(f"before={args.expect}")
    print(f"after={args.new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
