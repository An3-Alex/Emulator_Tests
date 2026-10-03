import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "m68k_qemu_harness.py"
SPEC = importlib.util.spec_from_file_location("m68k_qemu_harness", MODULE_PATH)
assert SPEC and SPEC.loader
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)

BRIDGE_PATH = MODULE_PATH.with_name("m68k_database_bridge.py")
BRIDGE_SPEC = importlib.util.spec_from_file_location(
    "m68k_database_bridge_rsp_batch_tests", BRIDGE_PATH
)
assert BRIDGE_SPEC and BRIDGE_SPEC.loader
BRIDGE = importlib.util.module_from_spec(BRIDGE_SPEC)
sys.modules[BRIDGE_SPEC.name] = BRIDGE
BRIDGE_SPEC.loader.exec_module(BRIDGE)


def register_block(core=None, extra=""):
    words = tuple(range(18)) if core is None else core
    return "".join(f"{word:08x}" for word in words) + extra


def fake_rsp(raw, write_reply="OK"):
    rsp = HARNESS.RspClient(mock.Mock())
    commands = []

    def command(text):
        commands.append(text)
        return raw if text == "g" else write_reply

    rsp.command = command
    return rsp, commands


class RspRegisterBatchTests(unittest.TestCase):
    def test_snapshot_preserves_extra_register_bytes_and_case(self):
        extra = "00112233445566778899AABBCCDDEEFF" * 3 + "A1B2C3D4"
        raw = register_block(extra=extra)
        rsp, commands = fake_rsp(raw)
        snapshot = rsp.read_register_snapshot()
        self.assertEqual(snapshot.raw_hex, raw)
        self.assertEqual(snapshot.core_u32, tuple(range(18)))
        self.assertEqual(commands, ["g"])

    def test_three_interrupt_registers_use_one_G_preserving_every_other_byte(self):
        extra = "000102030405060708090A0B" * 8 + "0A0B0C0D" * 3
        raw = register_block(extra=extra)
        rsp, commands = fake_rsp(raw)
        snapshot = rsp.read_register_snapshot()
        updates = {15: 0x001FFE80, 16: 0x2700, 17: 0x00074CF2}
        changed = rsp.write_registers_u32(updates, snapshot=snapshot)
        expected_core = list(range(18))
        for register, value in updates.items():
            expected_core[register] = value
        expected = register_block(expected_core, extra)
        self.assertEqual(commands, ["g", "G" + expected])
        self.assertEqual(changed.raw_hex, expected)
        self.assertEqual(changed.core_u32, tuple(expected_core))
        self.assertEqual(snapshot.raw_hex, raw)
        self.assertEqual(snapshot.core_u32, tuple(range(18)))

    def test_snapshot_is_explicit_and_not_replaced_by_a_later_read(self):
        first_raw = register_block()
        second_raw = register_block([100 + index for index in range(18)])
        rsp, commands = fake_rsp(first_raw)
        first = rsp.read_register_snapshot()
        rsp.command = lambda text: commands.append(text) or (
            second_raw if text == "g" else "OK"
        )
        second = rsp.read_register_snapshot()
        changed = rsp.write_registers_u32({0: 0xFFFFFFFF}, snapshot=second)
        self.assertEqual(changed.core_u32[1:], second.core_u32[1:])
        self.assertNotEqual(changed.core_u32[1:], first.core_u32[1:])
        self.assertEqual([text[0] for text in commands], ["g", "g", "G"])

    def test_returned_snapshot_can_chain_halted_register_updates(self):
        rsp, commands = fake_rsp(register_block(extra="ABCDEF"))
        first = rsp.read_register_snapshot()
        second = rsp.write_registers_u32({15: 0x1234}, snapshot=first)
        third = rsp.write_registers_u32({17: 0x5678}, snapshot=second)
        self.assertEqual(third.core_u32[15], 0x1234)
        self.assertEqual(third.core_u32[17], 0x5678)
        self.assertEqual(third.raw_hex[18 * 8:], "ABCDEF")
        self.assertEqual([text[0] for text in commands], ["g", "G", "G"])

    def test_empty_updates_do_not_send_packet(self):
        rsp, commands = fake_rsp(register_block())
        snapshot = rsp.read_register_snapshot()
        self.assertIs(rsp.write_registers_u32({}, snapshot=snapshot), snapshot)
        self.assertEqual(commands, ["g"])

    def test_short_malformed_and_unavailable_blocks_are_rejected(self):
        invalid = [
            "E01", register_block()[:-1],
            "zz" + register_block()[2:],
            register_block() + "x", register_block() + "xx",
            register_block() + " 0",
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                rsp, commands = fake_rsp(raw)
                with self.assertRaises(RuntimeError):
                    rsp.read_register_snapshot()
                self.assertEqual(commands, ["g"])

    def test_core_only_reader_keeps_compatibility_with_unavailable_optional_registers(self):
        rsp, commands = fake_rsp(register_block(extra="xx" * 16))
        self.assertEqual(rsp.read_registers_u32(), tuple(range(18)))
        self.assertEqual(commands, ["g"])

    def test_invalid_updates_fail_before_sending_any_write(self):
        invalid = [
            {-1: 1}, {18: 1}, {True: 1}, {"15": 1},
            {15: -1}, {15: 0x100000000}, {15: True}, {15: "1"},
            {15: 1, 17: -1},
        ]
        for updates in invalid:
            with self.subTest(updates=updates):
                rsp, commands = fake_rsp(register_block())
                snapshot = rsp.read_register_snapshot()
                with self.assertRaises(ValueError):
                    rsp.write_registers_u32(updates, snapshot=snapshot)
                self.assertEqual(commands, ["g"])

    def test_inconsistent_or_missing_snapshot_is_rejected(self):
        rsp, commands = fake_rsp(register_block())
        snapshot = rsp.read_register_snapshot()
        forged = HARNESS.M68kRegisterSnapshot(snapshot.raw_hex, tuple([42] * 18))
        with self.assertRaises(ValueError):
            rsp.write_registers_u32({15: 1}, snapshot=forged)
        with self.assertRaises(TypeError):
            rsp.write_registers_u32({15: 1}, snapshot=None)
        with self.assertRaises(TypeError):
            rsp.write_registers_u32({15: 1})
        self.assertEqual(commands, ["g"])

    def test_server_failure_is_not_hidden_or_retried(self):
        for response in ("", "E01", "unsupported"):
            with self.subTest(response=response):
                rsp, commands = fake_rsp(register_block(), write_reply=response)
                snapshot = rsp.read_register_snapshot()
                with self.assertRaisesRegex(RuntimeError, "bulk register write failed"):
                    rsp.write_registers_u32({15: 1}, snapshot=snapshot)
                self.assertEqual([text[0] for text in commands], ["g", "G"])

    def test_packet_framing_for_bulk_write_uses_existing_checksum_and_ack(self):
        sock = mock.Mock()
        raw = register_block(extra="00000000")
        g_response = raw.encode("ascii")
        sock.recv.side_effect = [
            b"+$" + g_response + b"#" + HARNESS.RspClient._checksum(g_response),
            b"+$OK#9a",
        ]
        rsp = HARNESS.RspClient(sock)
        snapshot = rsp.read_register_snapshot()
        changed = rsp.write_registers_u32({17: 0x70000}, snapshot=snapshot)
        payload = ("G" + changed.raw_hex).encode("ascii")
        packets = [call.args[0] for call in sock.sendall.call_args_list]
        self.assertEqual(packets, [
            b"$g#67", b"+",
            b"$" + payload + b"#" + HARNESS.RspClient._checksum(payload), b"+",
        ])


class InterruptBatchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.core = list(range(18))
        self.core[15] = 0x001FFF00
        self.core[16] = 0x2011
        self.core[17] = 0x000C5B6E
        self.extra = "0123456789ABCDEFFEDCBA98" * 8 + "F0E1D2C3" * 3
        self.raw = register_block(self.core, self.extra)

    def assert_interrupt_packets(self, packets):
        # Native 68020 format-zero frame: SR, interrupted PC, vector offset.
        self.assertEqual(packets[-2], "M1ffef8,8:2011000c5b6e0104")
        expected = list(self.core)
        expected[15] = 0x001FFEF8
        expected[16] = 0x2711
        expected[17] = 0x00074CF2
        self.assertEqual(packets[-1], "G" + register_block(expected, self.extra))
        written = HARNESS.RspClient._decode_register_snapshot(packets[-1][1:])
        self.assertEqual(written.core_u32, tuple(expected))
        self.assertEqual(written.raw_hex[18 * 8:], self.extra)

    def board_rsp(self, *, scc_active=False, failing_memory_address=None):
        rsp = HARNESS.RspClient(mock.Mock())
        commands = []
        reads = {
            f"m{BRIDGE.BOARD_TIMER_STATUS:x},1": "a2",
            f"m{BRIDGE.BOARD_SCC_STATE:x},1": "01" if scc_active else "00",
        }

        def command(text):
            commands.append(text)
            if text == "g":
                return self.raw
            if text in reads:
                return reads[text]
            if failing_memory_address is not None and text.startswith(
                f"M{failing_memory_address:x},"
            ):
                return "E03"
            if text.startswith(("M", "G")):
                return "OK"
            raise AssertionError(f"unexpected command {text}")

        rsp.command = command
        return rsp, commands

    def test_interrupt_uses_g_M_G_preserving_native_frame_and_entire_context(self):
        rsp, commands = fake_rsp(self.raw)
        result = BRIDGE.inject_interrupt(rsp, 65, 0x00074CF2)
        self.assertEqual(result, (0x000C5B6E, 0x2011))
        self.assertEqual([text[0] for text in commands], ["g", "M", "G"])
        self.assert_interrupt_packets(commands)

    def test_current_explicit_snapshot_eliminates_extra_g(self):
        rsp, commands = fake_rsp(self.raw)
        snapshot = rsp.read_register_snapshot()
        commands.clear()
        result = BRIDGE.inject_interrupt(rsp, 65, 0x00074CF2, snapshot=snapshot)
        self.assertEqual(result, (0x000C5B6E, 0x2011))
        self.assertEqual([text[0] for text in commands], ["M", "G"])
        self.assert_interrupt_packets(commands)
        self.assertEqual(snapshot.raw_hex, self.raw)

    def test_exception_frame_memory_error_prevents_any_G(self):
        rsp, commands = fake_rsp(self.raw, write_reply="E03")
        with self.assertRaisesRegex(RuntimeError, "memory write failed: E03"):
            BRIDGE.inject_interrupt(rsp, 65, 0x00074CF2)
        self.assertEqual([text[0] for text in commands], ["g", "M"])

    def test_board_tick_preserves_status_bits_and_full_register_context(self):
        for scc_active, expected_status in ((False, "a8"), (True, "b8")):
            with self.subTest(scc_active=scc_active):
                rsp, commands = self.board_rsp(scc_active=scc_active)
                snapshot = rsp.read_register_snapshot()
                commands.clear()
                result = BRIDGE.inject_board_tick(
                    rsp, (65, 0x00074CF2, 0x00074E00), snapshot=snapshot
                )
                self.assertEqual(result, (0x000C5B6E, 0x2011))
                self.assertEqual([text[0] for text in commands], ["m", "m", "M", "M", "G"])
                self.assertEqual(commands[:3], [
                    f"m{BRIDGE.BOARD_TIMER_STATUS:x},1",
                    f"m{BRIDGE.BOARD_SCC_STATE:x},1",
                    f"M{BRIDGE.BOARD_TIMER_STATUS:x},1:{expected_status}",
                ])
                self.assert_interrupt_packets(commands)

    def test_board_tick_without_snapshot_reads_g_only_once(self):
        rsp, commands = self.board_rsp()
        BRIDGE.inject_board_tick(rsp, (65, 0x00074CF2, 0x00074E00))
        self.assertEqual([text[0] for text in commands], ["m", "m", "M", "g", "M", "G"])
        self.assert_interrupt_packets(commands)

    def test_board_status_memory_error_stops_before_interrupt_or_G(self):
        rsp, commands = self.board_rsp(failing_memory_address=BRIDGE.BOARD_TIMER_STATUS)
        with self.assertRaisesRegex(RuntimeError, "memory write failed: E03"):
            BRIDGE.inject_board_tick(rsp, (65, 0x00074CF2, 0x00074E00))
        self.assertEqual([text[0] for text in commands], ["m", "m", "M"])


if __name__ == "__main__":
    unittest.main()
