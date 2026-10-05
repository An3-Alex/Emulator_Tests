#!/usr/bin/env python3
"""List a node from an offline Windows registry hive using python-hivex."""

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


def display_value(hive: hivex.Hivex, value: int) -> str:
    key = hive.value_key(value)
    value_type = hive.value_type(value)
    if isinstance(value_type, tuple):
        value_type = value_type[0]
    raw = hive.value_value(value)
    if isinstance(raw, tuple):
        raw = raw[-1]
    if value_type == 4 and len(raw) >= 4:
        rendered = f"0x{struct.unpack('<I', raw[:4])[0]:08X}"
    elif value_type in (1, 2, 7):
        rendered = raw.decode("utf-16-le", errors="replace").rstrip("\x00")
    else:
        rendered = raw.hex(" ")
    return f"{key or '(Default)'} type={value_type} value={rendered}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("hive")
    parser.add_argument("key", nargs="?", default="")
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--child-contains")
    args = parser.parse_args()

    hive = hivex.Hivex(args.hive)
    node = hive.root()
    traversed: list[str] = []
    try:
        for part in args.key.replace("\\", "/").split("/"):
            if not part:
                continue
            node = find_child(hive, node, part)
            traversed.append(part)
    except KeyError as exc:
        path = "\\".join(traversed)
        raise SystemExit(f"key component not found after {path!r}: {exc.args[0]}")

    if args.child_contains:
        needle = args.child_contains.casefold()
        matches = [
            child
            for child in hive.node_children(node)
            if needle in hive.node_name(child).casefold()
        ]
        if len(matches) != 1:
            names = ", ".join(hive.node_name(child) for child in matches)
            raise SystemExit(
                f"expected one child containing {args.child_contains!r}, "
                f"found {len(matches)}: {names}"
            )
        node = matches[0]
        traversed.append(hive.node_name(node))

    def walk(current: int, relative: str, depth: int) -> None:
        print("key=" + relative)
        for value in hive.node_values(current):
            print("value " + display_value(hive, value))
        if depth <= 0:
            return
        for child in hive.node_children(current):
            name = hive.node_name(child)
            walk(child, relative + "\\" + name, depth - 1)

    walk(node, "\\".join(traversed), max(args.depth - 1, 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
