#!/usr/bin/env python3
"""Send one HMP input command through an existing QEMU QMP endpoint."""

from __future__ import annotations

import argparse
import time

from qmp_capture import connect_with_retry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--key", default="ret")
    parser.add_argument("--system-reset", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--quit", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=15.0)
    args = parser.parse_args()

    qmp = connect_with_retry(args.host, args.port)
    try:
        if args.quit:
            qmp.execute("quit")
            print("sent quit", flush=True)
            return 0
        if args.system_reset:
            qmp.execute("system_reset")
            print("sent system_reset", flush=True)
            if args.resume:
                qmp.execute("cont")
                print("sent cont", flush=True)
        else:
            qmp.execute(
                "human-monitor-command",
                {"command-line": f"sendkey {args.key}"},
            )
            print(f"sent key={args.key}", flush=True)
        deadline = time.monotonic() + args.wait_seconds
        while time.monotonic() < deadline:
            qmp.execute("query-status")
            pending, qmp.events = qmp.events, []
            for event in pending:
                print(f"event={event.get('event', 'UNKNOWN')}", flush=True)
            time.sleep(0.5)
    finally:
        qmp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
