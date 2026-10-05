#!/usr/bin/env python3
"""Run one read-only human-monitor command through QMP."""

from __future__ import annotations

import argparse

from qmp_capture import connect_with_retry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    qmp = connect_with_retry(args.host, args.port)
    try:
        result = qmp.execute(
            "human-monitor-command", {"command-line": args.command}
        )
        print(result, end="" if str(result).endswith("\n") else "\n")
    finally:
        qmp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
