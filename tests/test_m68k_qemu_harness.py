import importlib.util
import socket
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "m68k_qemu_harness.py"
SPEC = importlib.util.spec_from_file_location("m68k_qemu_harness", MODULE_PATH)
assert SPEC and SPEC.loader
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class RspHelpersTests(unittest.TestCase):
    def test_rsp_connection_disables_nagle_for_small_packets(self):
        fake_socket = mock.Mock()
        with mock.patch.object(
            HARNESS.socket, "create_connection", return_value=fake_socket
        ):
            self.assertIs(HARNESS.connect_rsp(1235, 1.0), fake_socket)
        fake_socket.setsockopt.assert_called_once_with(
            socket.IPPROTO_TCP, socket.TCP_NODELAY, 1
        )

    def test_rsp_buffers_split_packets_and_preserves_following_reply(self):
        class FakeSocket:
            def __init__(self):
                self.chunks = [b"+$OK#", b"9a+$0", b"1#61"]
                self.read_sizes = []
                self.sent = []

            def settimeout(self, value):
                pass

            def recv(self, size):
                self.read_sizes.append(size)
                return self.chunks.pop(0) if self.chunks else b""

            def sendall(self, data):
                self.sent.append(data)

        sock = FakeSocket()
        rsp = HARNESS.RspClient(sock)
        self.assertEqual(rsp.command("q"), "OK")
        self.assertEqual(rsp.command("p0"), "01")
        self.assertEqual(sock.read_sizes, [4096, 4096, 4096])
        self.assertEqual(sock.sent.count(b"+"), 2)

    def test_rsp_rejects_bad_checksum_after_buffered_read(self):
        class FakeSocket:
            def settimeout(self, value):
                pass

            def recv(self, size):
                return b"$OK#00"

            def sendall(self, data):
                self.last_sent = data

        sock = FakeSocket()
        with self.assertRaisesRegex(ValueError, "checksum"):
            HARNESS.RspClient(sock).command("q")
        self.assertEqual(sock.last_sent, b"-")

    def test_rsp_checksum(self):
        self.assertEqual(HARNESS.RspClient._checksum(b"p11"), b"d2")

    def test_rsp_reads_all_m68k_registers_in_one_exchange(self):
        class FakeSocket:
            def settimeout(self, value):
                pass

            def recv(self, size):
                return b""

            def sendall(self, data):
                pass

        rsp = HARNESS.RspClient(FakeSocket())
        commands = []
        rsp.command = lambda value: (
            commands.append(value)
            or "".join(f"{index:08x}" for index in range(18))
        )
        self.assertEqual(rsp.read_registers_u32(), tuple(range(18)))
        self.assertEqual(commands, ["g"])

    def test_parse_int_accepts_hex(self):
        self.assertEqual(HARNESS.parse_int("0x72c"), 0x72C)

    def test_rsp_runtime_can_disable_diagnostic_timeout(self):
        class FakeSocket:
            timeout = "unset"

            def settimeout(self, value):
                self.timeout = value

        sock = FakeSocket()
        HARNESS.RspClient(sock, timeout=None)
        self.assertIsNone(sock.timeout)


if __name__ == "__main__":
    unittest.main()
