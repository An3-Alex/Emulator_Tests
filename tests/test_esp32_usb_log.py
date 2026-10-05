"""USB log reader tests; no COM port is opened."""

import sys
import threading
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "esp32-db"))
from usb_log_core import stream_serial_log  # noqa: E402


class FakeSerial:
    def __init__(self, port, *, baudrate, timeout):
        self.settings = (port, baudrate, timeout)
        self.reads = iter((b"DB_CLOCK max=16.00\n", b"DB_TX 01 02\n"))
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def read(self, length):
        assert length == 256
        return next(self.reads)


class UsbLogTests(unittest.TestCase):
    def test_read_only_and_stoppable(self):
        stop = threading.Event()
        instances = []
        messages = []

        def factory(*args, **kwargs):
            instance = FakeSerial(*args, **kwargs)
            instances.append(instance)
            return instance

        def emit(message):
            messages.append(message)
            if len(messages) == 2:
                stop.set()

        stream_serial_log("COM7", stop, emit, factory)
        self.assertEqual(instances[0].settings, ("COM7", 115200, 0.25))
        self.assertTrue(instances[0].closed)
        self.assertEqual(messages, ["DB_CLOCK max=16.00\n", "DB_TX 01 02\n"])


if __name__ == "__main__":
    unittest.main()
