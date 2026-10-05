#!/usr/bin/env python3
"""Watch QEMU resets and preserve the seconds immediately before each reset."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import time
from pathlib import Path


class QmpClient:
    def __init__(self, host: str, port: int) -> None:
        self.sock = socket.create_connection((host, port), timeout=2.0)
        self.sock.settimeout(5.0)
        self.reader = self.sock.makefile("r", encoding="utf-8", newline="\n")
        self.next_id = 1
        self.events: list[dict] = []
        greeting = self._read_message()
        if "QMP" not in greeting:
            raise RuntimeError(f"Unexpected QMP greeting: {greeting!r}")
        self.execute("qmp_capabilities")

    def _read_message(self) -> dict:
        while True:
            line = self.reader.readline()
            if not line:
                raise EOFError("QMP connection closed")
            message = json.loads(line)
            if "event" in message:
                self.events.append(message)
                continue
            return message

    def execute(self, command: str, arguments: dict | None = None) -> dict:
        command_id = self.next_id
        self.next_id += 1
        request: dict = {"execute": command, "id": command_id}
        if arguments is not None:
            request["arguments"] = arguments
        payload = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
        self.sock.sendall(payload)
        while True:
            message = self._read_message()
            if message.get("id") == command_id:
                if "error" in message:
                    raise RuntimeError(f"QMP {command} failed: {message['error']}")
                return message.get("return", {})

    def close(self) -> None:
        try:
            self.reader.close()
        finally:
            self.sock.close()


def connect_with_retry(host: str, port: int, timeout: float = 45.0) -> QmpClient:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return QmpClient(host, port)
        except (OSError, EOFError, RuntimeError) as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Could not connect to QMP at {host}:{port}: {last_error}")


def freeze_ring(
    ring_dir: Path, reset_dir: Path, slots: int, next_slot: int, extension: str
) -> int:
    reset_dir.mkdir(parents=True, exist_ok=False)
    copied = 0
    order = list(range(next_slot, slots)) + list(range(0, next_slot))
    for sequence, slot in enumerate(order):
        source = ring_dir / f"frame-{slot:02d}.{extension}"
        if source.exists():
            shutil.copy2(source, reset_dir / f"frame-{sequence:02d}.{extension}")
            copied += 1
    return copied


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4455)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=0.75)
    parser.add_argument("--slots", type=int, default=24)
    parser.add_argument("--max-seconds", type=float, default=420.0)
    parser.add_argument("--stop-after-resets", type=int, default=4)
    parser.add_argument("--post-reset-seconds", type=float, default=30.0)
    args = parser.parse_args()

    output = args.output.resolve()
    ring_dir = output / "ring"
    ring_dir.mkdir(parents=True, exist_ok=True)
    event_path = output / "qmp-events.jsonl"
    qmp = connect_with_retry(args.host, args.port)
    start = time.monotonic()
    reset_count = 0
    next_slot = 0
    last_reset: float | None = None
    probe_png = ring_dir / "capture-probe.png"
    try:
        qmp.execute(
            "screendump", {"filename": probe_png.as_posix(), "format": "png"}
        )
        capture_format = "png"
    except RuntimeError:
        capture_format = "ppm"
    print(
        f"QMP connected; capture={capture_format} interval={args.interval}s "
        f"slots={args.slots}",
        flush=True,
    )

    try:
        with event_path.open("a", encoding="utf-8", newline="\n") as event_file:
            while True:
                elapsed = time.monotonic() - start
                frame = ring_dir / f"frame-{next_slot:02d}.{capture_format}"
                if capture_format == "png":
                    qmp.execute(
                        "screendump",
                        {"filename": frame.as_posix(), "format": "png"},
                    )
                else:
                    qmp.execute(
                        "human-monitor-command",
                        {"command-line": f"screendump {frame.as_posix()}"},
                    )
                next_slot = (next_slot + 1) % args.slots

                pending, qmp.events = qmp.events, []
                for event in pending:
                    event["watch_elapsed_seconds"] = round(elapsed, 3)
                    event_file.write(json.dumps(event, sort_keys=True) + "\n")
                    event_file.flush()
                    name = event.get("event", "UNKNOWN")
                    print(f"event={name} elapsed={elapsed:.1f}s", flush=True)
                    if name == "RESET":
                        reset_count += 1
                        last_reset = time.monotonic()
                        reset_dir = output / f"reset-{reset_count:02d}-pre"
                        copied = freeze_ring(
                            ring_dir,
                            reset_dir,
                            args.slots,
                            next_slot,
                            capture_format,
                        )
                        print(
                            f"preserved {copied} pre-reset frames in {reset_dir.name}",
                            flush=True,
                        )

                if elapsed >= args.max_seconds:
                    reason = "maximum runtime reached"
                    break
                if (
                    reset_count >= args.stop_after_resets
                    and last_reset is not None
                    and time.monotonic() - last_reset >= args.post_reset_seconds
                ):
                    reason = f"{reset_count} resets captured"
                    break
                time.sleep(args.interval)

            final_frame = output / f"final.{capture_format}"
            if capture_format == "png":
                qmp.execute(
                    "screendump",
                    {"filename": final_frame.as_posix(), "format": "png"},
                )
            else:
                qmp.execute(
                    "human-monitor-command",
                    {"command-line": f"screendump {final_frame.as_posix()}"},
                )
            print(f"Stopping QEMU cleanly: {reason}", flush=True)
            try:
                qmp.execute("quit")
            except (EOFError, OSError):
                pass
    finally:
        qmp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
