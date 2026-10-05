"""Connect QEMU guest COM3 to the ESP32-S3 native USB database UART.

The ESP32 bridge firmware keeps diagnostics on UART0. Native USB carries only
the M90GO/M90READY start handshake followed by unmodified database bytes.
This process never opens the original PC-side virtual database bridge.
"""

from __future__ import annotations

import argparse
import socket
import threading
import time
from pathlib import Path
from typing import BinaryIO


HELLO = b"M90GO\n"
READY = b"M90READY\n"


class EventLog:
    def __init__(self, path: Path | None) -> None:
        self._lock = threading.Lock()
        self._file = path.open("a", encoding="utf-8", buffering=1) if path else None

    def write(self, message: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
        with self._lock:
            print(line, flush=True)
            if self._file:
                self._file.write(line + "\n")

    def close(self) -> None:
        if self._file:
            self._file.close()


def wait_for_ready(port: BinaryIO, *, timeout: float = 45.0) -> bytes:
    """Start the ESP probe and return any database bytes after its ACK."""
    deadline = time.monotonic() + timeout
    next_hello = 0.0
    pending = bytearray()
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_hello:
            port.write(HELLO)
            port.flush()
            next_hello = now + 1.0
        pending.extend(port.read(256))
        position = pending.find(READY)
        if position >= 0:
            return bytes(pending[position + len(READY):])
        if len(pending) > 4 * len(READY):
            del pending[:-len(READY)]
    raise TimeoutError("ESP32 antwortet nicht mit M90READY; USB-Brücken-Firmware prüfen")


def connect_qemu(host: str, port: int, *, timeout: float = 30.0) -> socket.socket:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            connection = socket.create_connection((host, port), timeout=2.0)
            connection.settimeout(0.2)
            return connection
        except OSError as error:
            last_error = error
            time.sleep(0.25)
    raise ConnectionError(f"QEMU COM3 auf {host}:{port} nicht erreichbar: {last_error}")


def relay(port: BinaryIO, connection: socket.socket,
          events: EventLog, first: bytes = b"") -> None:
    stopped = threading.Event()
    failures: list[str] = []

    def usb_to_com3() -> None:
        count = 0
        try:
            if first:
                connection.sendall(first)
                count += len(first)
                events.write(f"ESP_TO_COM3 bytes={len(first)} data={first[:64].hex(' ').upper()}")
            while not stopped.is_set():
                data = port.read(1024)
                if not data:
                    continue
                connection.sendall(data)
                count += len(data)
                events.write(f"ESP_TO_COM3 bytes={len(data)} total={count} "
                             f"data={data[:64].hex(' ').upper()}")
        except (OSError, ValueError) as error:
            failures.append(f"ESP_TO_COM3 {error}")
        finally:
            stopped.set()

    def com3_to_usb() -> None:
        count = 0
        try:
            while not stopped.is_set():
                try:
                    data = connection.recv(1024)
                except socket.timeout:
                    continue
                if not data:
                    break
                written = port.write(data)
                if written != len(data):
                    raise OSError(f"USB short write {written}/{len(data)}")
                count += written
                events.write(f"COM3_TO_ESP bytes={written} total={count} "
                             f"data={data[:64].hex(' ').upper()}")
        except (OSError, ValueError) as error:
            failures.append(f"COM3_TO_ESP {error}")
        finally:
            stopped.set()

    worker = threading.Thread(target=usb_to_com3, name="esp-to-com3", daemon=True)
    worker.start()
    com3_to_usb()
    worker.join(timeout=1.0)
    for failure in failures:
        events.write(f"RELAY_ERROR {failure}")
    events.write("RELAY_STOPPED")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="ESP32 native USB COM port, e.g. COM7")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--tcp-port", type=int, default=4553)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--probe-only", action="store_true",
                        help="Check that the USB COM port opens; do not connect QEMU")
    args = parser.parse_args()
    if not args.port.upper().startswith("COM") or not args.port[3:].isdigit():
        parser.error("--port muss ein Windows-COM-Port sein, z.B. COM7")
    import serial  # pyserial is only required when using the live relay

    events = EventLog(args.log_file)
    try:
        if args.probe_only:
            device = serial.Serial(port=None, baudrate=115200, timeout=0.1,
                                   write_timeout=2.0)
            try:
                device.dtr = False
                device.rts = False
                device.port = args.port
                device.open()
                events.write(f"ESP_USB_PROBE_OK {args.port}")
            finally:
                device.close()
            return 0
        with connect_qemu(args.host, args.tcp_port) as connection:
            events.write(f"QEMU_COM3_CONNECTED {args.host}:{args.tcp_port}")
            device = serial.Serial(port=None, baudrate=115200, timeout=0.1,
                                   write_timeout=2.0)
            try:
                device.dtr = False
                device.rts = False
                device.port = args.port
                device.open()
                events.write(f"ESP_USB_OPEN {args.port}")
                first = wait_for_ready(device)
                events.write("ESP_READY")
                relay(device, connection, events, first)
            finally:
                device.close()
    except (OSError, TimeoutError, ConnectionError) as error:
        events.write(f"RELAY_FAILED {type(error).__name__}: {error}")
        return 1
    finally:
        events.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
