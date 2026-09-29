"""Offline protocol tests; no ESP32 board or QEMU process is started."""

import queue
import socket
import sys
import threading
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from esp32_com3_relay import EventLog, HELLO, READY, relay, wait_for_ready  # noqa: E402


class FakeUsb:
    def __init__(self, incoming: bytes = b"") -> None:
        self.incoming: queue.Queue[bytes] = queue.Queue()
        if incoming:
            self.incoming.put(incoming)
        self.written: list[bytes] = []

    def read(self, size: int) -> bytes:
        try:
            return self.incoming.get(timeout=0.1)
        except queue.Empty:
            return b""

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass


class RelayTests(unittest.TestCase):
    def test_handshake_returns_first_database_bytes(self):
        usb = FakeUsb(b"boot noise" + READY + b"\x01\x02")
        self.assertEqual(wait_for_ready(usb, timeout=1), b"\x01\x02")
        self.assertEqual(usb.written, [HELLO])

    def test_bidirectional_relay_and_clean_stop(self):
        host, guest = socket.socketpair()
        self.addCleanup(host.close)
        self.addCleanup(guest.close)
        host.settimeout(0.2)
        guest.settimeout(2.0)
        usb = FakeUsb()
        events = EventLog(None)
        worker = threading.Thread(target=relay,
                                  args=(usb, host, events, b"\x01\x02"),
                                  daemon=True)
        worker.start()
        self.assertEqual(guest.recv(2), b"\x01\x02")
        guest.sendall(b"PC")
        for _ in range(20):
            if b"PC" in usb.written:
                break
            threading.Event().wait(0.02)
        self.assertIn(b"PC", usb.written)
        usb.incoming.put(b"DB")
        self.assertEqual(guest.recv(2), b"DB")
        guest.shutdown(socket.SHUT_WR)
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
