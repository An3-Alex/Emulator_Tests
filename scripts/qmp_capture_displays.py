#!/usr/bin/env python3
"""Capture multiple named QEMU display devices and reset events through QMP."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from qmp_capture import connect_with_retry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", action="append", required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--max-seconds", type=float, default=180.0)
    parser.add_argument("--quit", action="store_true")
    args = parser.parse_args()

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    events_path = output / "qmp-events.jsonl"
    qmp = connect_with_retry(args.host, args.port)
    start = time.monotonic()
    sequence = 0
    try:
        with events_path.open("a", encoding="utf-8", newline="\n") as event_file:
            while time.monotonic() - start < args.max_seconds:
                elapsed = time.monotonic() - start
                for device in args.device:
                    device_dir = output / device
                    device_dir.mkdir(exist_ok=True)
                    filename = device_dir / f"frame-{sequence:04d}.png"
                    qmp.execute(
                        "screendump",
                        {
                            "filename": filename.as_posix(),
                            "device": device,
                            "head": 0,
                            "format": "png",
                        },
                    )
                pending, qmp.events = qmp.events, []
                for event in pending:
                    event["capture_elapsed_seconds"] = round(elapsed, 3)
                    event_file.write(json.dumps(event, sort_keys=True) + "\n")
                    event_file.flush()
                    print(
                        f"event={event.get('event', 'UNKNOWN')} elapsed={elapsed:.1f}s",
                        flush=True,
                    )
                sequence += 1
                time.sleep(args.interval)
        print(f"captured_frames_per_display={sequence}", flush=True)
        if args.quit:
            try:
                qmp.execute("quit")
            except (EOFError, OSError):
                pass
    finally:
        qmp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
