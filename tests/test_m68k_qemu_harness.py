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


def packet(text):
    return HARNESS.RspClient._packet(text)


class ScriptedSocket:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = []

    def settimeout(self, value):
        pass

    def recv(self, size):
        return self.chunks.pop(0) if self.chunks else b""

    def sendall(self, data):
        self.sent.append(data)


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
        # QEMU drops a pending reply when the next packet starts; no '+'.
        self.assertNotIn(b"+", sock.sent)

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

    def test_writes_are_sent_with_the_next_exchange_and_checked(self):
        sock = ScriptedSocket([b"+" + packet("OK") + b"+" + packet("OK") + b"+" + packet("1234")])
        rsp = HARNESS.RspClient(sock)
        rsp.write_memory(0x100, b"\x12")
        self.assertEqual(rsp.command("Z0,74d26,1"), "OK")
        self.assertEqual(sock.sent, [])
        self.assertEqual(rsp.read_memory(0x200, 2), b"\x12\x34")
        self.assertEqual(sock.sent, [packet("M100,1:12") + packet("Z0,74d26,1") + packet("m200,2")])

    def test_rejected_queued_write_raises_at_the_next_exchange(self):
        sock = ScriptedSocket([packet("E03") + packet("00")])
        rsp = HARNESS.RspClient(sock)
        rsp.write_memory(0x100, b"\x12")
        with self.assertRaisesRegex(RuntimeError, "M100,1:12.*failed: E03"):
            rsp.read_memory(0x200, 1)

    def test_halted_memory_and_registers_are_cached_until_resume(self):
        registers = "".join(f"{index:08x}" for index in range(18))
        sock = ScriptedSocket([packet(registers) + packet("aabbccdd"), packet("OK") + packet("S05"),
                               packet("11")])
        rsp = HARNESS.RspClient(sock)
        rsp.prefetch([(0x1000, 4)], registers=True)
        self.assertEqual(len(sock.sent), 1)
        self.assertEqual(rsp.read_memory(0x1001, 2), b"\xbb\xcc")
        self.assertEqual(rsp.read_register_u32(17), 17)
        self.assertEqual(rsp.read_register_snapshot().core_u32, tuple(range(18)))
        rsp.write_memory(0x1002, b"\x55")
        self.assertEqual(rsp.read_memory(0x1000, 4), b"\xaa\xbb\x55\xdd")
        self.assertEqual(len(sock.sent), 1)
        self.assertEqual(rsp.command("s"), "S05")
        self.assertEqual(sock.sent[1], packet("M1002,1:55") + packet("s"))
        # The step may have changed memory: read again from QEMU.
        self.assertEqual(rsp.read_memory(0x1000, 1), b"\x11")
        self.assertEqual(sock.sent[2], packet("m1000,1"))

    def test_queued_writes_are_merged_into_the_fewest_packets(self):
        registers = "".join(f"{index:08x}" for index in range(18))
        sock = ScriptedSocket([packet("00112233445566778899"), packet("OK") * 4 + packet("S05")])
        rsp = HARNESS.RspClient(sock)
        rsp.prefetch([(0x2000, 10)])
        rsp.write_memory(0x18B, b"\x80")
        rsp.write_memory(0x18B, b"\x88")          # overwrites the first write
        rsp.write_memory(0x2001, b"\xaa")
        rsp.write_memory(0x2008, b"\xbb\xcc")     # gap 0x2002..0x2007 known from the cache
        rsp.command("G" + registers)              # superseded by the next block
        rsp.command("Z0,7511c,1")
        rsp.command("G" + registers[:-8] + "00001234")
        self.assertEqual(rsp.command("c"), "S05")
        self.assertEqual(sock.sent[1], b"".join(packet(text) for text in (
            "M18b,1:88", "M2001,9:aa223344556677bbcc", "Z0,7511c,1",
            "G" + registers[:-8] + "00001234", "c")))

    def test_large_contiguous_writes_stay_within_qemu_packet_size(self):
        sock = ScriptedSocket([packet("OK") * 3 + packet("00")])
        rsp = HARNESS.RspClient(sock)
        for offset in range(0, 10 * 1024, 1024):
            rsp.write_memory(0x1000 + offset, bytes(1024))
        rsp.read_memory(0x0, 1)
        sent = sock.sent[0].split(b"$")[1:]
        self.assertEqual([part.split(b":")[0] for part in sent[:-1]],
                         [b"M1000,1000", b"M2000,1000", b"M3000,800"])

    def test_nearby_reads_are_fetched_as_one_packet(self):
        sock = ScriptedSocket([packet("00" * 0x20) + packet("11")])
        rsp = HARNESS.RspClient(sock)
        rsp.prefetch([(0x1E2473, 2), (0x1E247B, 0x10), (0x1E2490, 3), (0x800000, 1)])
        self.assertEqual(sock.sent, [packet("m1e2473,20") + packet("m800000,1")])
        self.assertEqual(rsp.read_memory(0x1E2490, 3), bytes(3))

    def test_register_write_updates_the_cached_block(self):
        registers = "".join(f"{index:08x}" for index in range(18))
        sock = ScriptedSocket([packet(registers)])
        rsp = HARNESS.RspClient(sock)
        snapshot = rsp.read_register_snapshot()
        rsp.write_registers_u32({17: 0x1234}, snapshot=snapshot)
        self.assertEqual(rsp.read_register_u32(17), 0x1234)
        self.assertEqual(rsp.read_register_snapshot().core_u32[17], 0x1234)
        self.assertEqual(len(sock.sent), 1)

    def test_interrupt_keeps_queued_writes_for_the_halted_target(self):
        sock = ScriptedSocket([packet("S02"), packet("OK") + packet("01")])
        rsp = HARNESS.RspClient(sock)
        rsp.write_memory(0x100, b"\x01")
        self.assertEqual(rsp.interrupt(), "S02")
        self.assertEqual(sock.sent, [b"\x03"])
        self.assertEqual(rsp.read_memory(0x200, 1), b"\x01")
        self.assertEqual(sock.sent[1], packet("M100,1:01") + packet("m200,1"))

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
