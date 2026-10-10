import importlib.util
from contextlib import redirect_stdout
import io
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "m68k_database_bridge.py"
spec = importlib.util.spec_from_file_location("m68k_database_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)


class DatabaseBridgeTests(unittest.TestCase):
    def test_rtc_fault_snapshot_only_reads_native_calendar_and_check_state(self):
        rsp = mock.Mock()
        registers = [0] * 18
        registers[0:2] = [0x72948690, 0x72948690]
        registers[13] = 0x1F9000
        registers[bridge.REG_A7] = 0x1FFF70
        rsp.read_registers_u32.return_value = registers
        memory = {
            (0x1FFF70, 4): bytes.fromhex("00 07 A1 90"),
            (0x1F9000, 7): bytes.fromhex("16 0E 00 01 02 0D 00"),
            (0x1E2EB8, 4): bytes.fromhex("72 94 86 90"),
            (0x1EFFFE, 1): b"\x01",
            (0x1F8110, 7): bytes.fromhex("16 0E 00 01 02 0D 00"),
            (0x108C, 2): b"07",
        }
        rsp.read_memory.side_effect = lambda address, length: memory[(address, length)]
        snapshot = bridge.read_rtc_fault_snapshot(
            rsp, bridge.dt.datetime(2013, 2, 1, 22, 14))
        self.assertIn("rtc=2013-02-01T22:14:00", snapshot)
        self.assertIn("source_return=0007A190", snapshot)
        self.assertIn("calendar=160E0001020D00", snapshot)
        self.assertIn("timestamp=72948690 invalid_flag=01", snapshot)
        self.assertIn("header_year=3037", snapshot)
        self.assertEqual(len(rsp.read_memory.call_args_list), 6)
        self.assertTrue(all(call[0] in {"read_memory", "read_registers_u32"}
                            for call in rsp.mock_calls))

    def test_rtc_fault_snapshot_does_not_read_invalid_stack_or_calendar_pointer(self):
        rsp = mock.Mock()
        registers = [0] * 18
        registers[13] = 0xFFFFFFF0
        registers[bridge.REG_A7] = 0x1FFFFF
        rsp.read_registers_u32.return_value = registers
        rsp.read_memory.side_effect = lambda address, length: bytes(length)
        snapshot = bridge.read_rtc_fault_snapshot(rsp, bridge.RTC_DEFAULT_TIME)
        self.assertIn("source_return=unavailable", snapshot)
        self.assertIn("calendar=unavailable", snapshot)
        self.assertEqual(len(rsp.read_memory.call_args_list), 4)

    def test_native_factory_is_executed_not_replaced_by_flag_writes(self):
        rsp = mock.Mock()
        rsp.read_register_u32.return_value = 0x408
        rsp.read_memory.side_effect = [b"INIT", bytes(4)]
        rsp.command.return_value = "OK"
        with redirect_stdout(io.StringIO()):
            bridge.run_original_factory_reset(rsp, b"factory-code", 0x1500)
        rsp.write_memory.assert_called_once_with(0x1000, b"factory-code")
        rsp.write_register_u32.assert_any_call(bridge.REG_PC, 0x1500)
        self.assertIn(mock.call("c"), rsp.command.call_args_list)
        rsp.command.assert_any_call("z0,408,1")
        rsp.set_timeout.assert_called_with(60.0)

    def test_wrong_factory_result_blocks_programming_and_removes_breakpoint(self):
        rsp = mock.Mock()
        rsp.read_register_u32.return_value = 0x408
        rsp.read_memory.side_effect = [bytes(4), bytes(4)]
        rsp.command.return_value = "OK"
        with self.assertRaisesRegex(RuntimeError, "native initialization"):
            bridge.run_original_factory_reset(rsp, b"wrong-module", 0x1500)
        rsp.command.assert_any_call("z0,408,1")
        rsp.set_timeout.assert_called_with(60.0)

    def test_cli_defaults_to_ten_ms_without_changing_instruction_clock(self):
        argv = [str(SCRIPT), '--loader', 'loader.bin', '--expected-loader-sha256', 'x',
                '--database', 'database.bin', '--expected-database-sha256', 'y',
                '--config', 'config.bin', '--expected-config-sha256', 'z', '--d3', '1']
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(bridge, 'run_bridge', return_value=0) as run:
            self.assertEqual(bridge.main(), 0)
        args = run.call_args.args[0]
        self.assertEqual(args.timer_interval, 0.01)
        self.assertEqual(args.icount_shift, 6)

    def test_empty_duart_read_clears_stale_shared_baud_status(self):
        device = bridge.VirtualCoinValidator()
        rsp = mock.Mock()
        rsp.read_memory.return_value = b"\xBB"
        self.assertEqual(bridge.consume_coin_validator_byte(device, rsp), (None, 0xBB))
        rsp.write_memory.assert_called_once_with(bridge.BOARD_SCC_CONTROL_B, b"\x04")
        self.assertEqual(device.pending_count(), 0)

    def test_duart_read_publishes_next_byte_without_dropping_fifo(self):
        device = bridge.VirtualCoinValidator()
        for value, remaining in ((0x7F, 3), (0, 2), (0x7F, 1)):
            reply = device.observe_tx(value, remaining)
        rsp = mock.Mock()
        self.assertEqual(bridge.consume_coin_validator_byte(device, rsp), (reply[0], None))
        self.assertEqual(device.pending_count(), len(reply) - 1)
        self.assertEqual(device.data(), reply[1])
        rsp.read_memory.assert_not_called()
        rsp.write_memory.assert_any_call(bridge.BOARD_SCC_DATA_B, bytes([reply[1]]))
        rsp.write_memory.assert_any_call(bridge.BOARD_SCC_CONTROL_B, bytes([device.status()]))

    def test_empty_duart_read_also_publishes_consistent_idle_status(self):
        device = bridge.VirtualCoinValidator()
        rsp = mock.Mock()
        rsp.read_memory.return_value = b"\x04"
        self.assertEqual(bridge.consume_coin_validator_byte(device, rsp), (None, None))
        rsp.write_memory.assert_called_once_with(bridge.BOARD_SCC_CONTROL_B, b"\x04")

    def test_cleanup_exit_is_not_reported_as_spontaneous_emulator_exit(self):
        process = mock.Mock(returncode=1)
        process.poll.return_value = None
        process.communicate.return_value = (b"", b"")
        output = io.StringIO()
        with redirect_stdout(output):
            bridge.stop_database_process(process, "Com3Disconnected")
        process.terminate.assert_called_once_with()
        self.assertIn("trigger=Com3Disconnected", output.getvalue())
        self.assertIn("stopped_by_bridge=True exit_before_cleanup=None", output.getvalue())

    def test_spontaneous_emulator_exit_preserves_its_code_and_stderr(self):
        process = mock.Mock(returncode=7)
        process.poll.return_value = 7
        process.communicate.return_value = (b"", b"emulator fault\ncontext")
        output = io.StringIO()
        with redirect_stdout(output):
            bridge.stop_database_process(process, "EOFError")
        process.terminate.assert_not_called()
        self.assertIn("code=7 stopped_by_bridge=False exit_before_cleanup=7", output.getvalue())
        self.assertIn("emulator fault | context", output.getvalue())

    def test_cleanup_timeout_kills_only_the_owned_database_process(self):
        process = mock.Mock(returncode=1)
        process.poll.return_value = None
        process.communicate.side_effect = [subprocess.TimeoutExpired("qemu", 5), (b"", b"")]
        with redirect_stdout(io.StringIO()):
            bridge.stop_database_process(process, "TimeoutError")
        process.kill.assert_called_once_with()

    def test_direct_log_file_is_live_and_restores_streams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.log"
            original_stdout, original_stderr = sys.stdout, sys.stderr

            def action() -> int:
                print("DB_TEST_EVENT", flush=True)
                self.assertIn("DB_TEST_EVENT", path.read_text(encoding="utf-8"))
                return 7

            self.assertEqual(bridge.run_with_log_file(action, path), 7)
            self.assertIs(sys.stdout, original_stdout)
            self.assertIs(sys.stderr, original_stderr)

    def test_expected_guest_com3_close_is_logged_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.log"

            def action() -> int:
                raise bridge.Com3Disconnected("XP QEMU reset COM3")

            self.assertEqual(bridge.run_with_log_file(action, path), 1)
            recorded = path.read_text(encoding="utf-8")
            self.assertIn("DB_COM3_DISCONNECTED XP QEMU reset COM3", recorded)
            self.assertNotIn("Traceback", recorded)

    def test_aux_packet_codec_roundtrips_original_firmware_layout(self) -> None:
        for plain in (
            bytes.fromhex("00 00 00 00 00 00 01 19 00 12 02"),
            bytes.fromhex("DE AD BE EF 00 00 01 19 00 12 02"),
            bytes(range(5)),
        ):
            with self.subTest(plain=plain):
                encoded = bridge.encode_aux_packet(plain)
                self.assertNotEqual(encoded, plain)
                self.assertEqual(bridge.decode_aux_packet(encoded), plain)

    def test_virtual_aux_identification_echoes_challenge(self) -> None:
        self.assertEqual(bridge.REG_D7, 7)
        for challenge in (0, 0x00110011, 0x00190019, 0xDEADBEEF):
            plain = bridge.decode_aux_packet(
                bridge.virtual_aux_identification_reply(challenge)
            )
            self.assertEqual(len(plain), 11)
            self.assertEqual(plain[:4], challenge.to_bytes(4, "big"))

    def test_virtual_aux_profile_reply_uses_rom_profile_key(self) -> None:
        plain = bridge.decode_aux_packet(
            bridge.virtual_aux_profile_reply(0x00110011)
        )
        self.assertEqual(len(plain), 11)
        self.assertEqual(plain[:6], bytes.fromhex("00 11 00 11 11 55"))

    def test_virtual_aux_value_reply_echoes_challenge_and_slot(self) -> None:
        for index, value in ((0x0D, 11), (0x0E, 55),
                             (0x0F, 11), (0x10, 55)):
            encoded = bridge.virtual_aux_value_reply(0x00190019, index)
            self.assertIsNotNone(encoded)
            self.assertEqual(
                bridge.decode_aux_packet(encoded),
                bytes.fromhex("00 19 00 19") + bytes([value]),
            )
        self.assertIsNone(bridge.virtual_aux_value_reply(0, 0x11))

    def test_virtual_aux_replies_can_use_m90_card_eeprom(self) -> None:
        from admission_card import build_m90_eeprom

        template = bytearray(256)
        template[0x0D:0x11] = bytes.fromhex("08 12 2E 07")
        card = build_m90_eeprom(bytes(template), "123456789")
        challenge = 0x12345678
        identity = bridge.decode_aux_packet(
            bridge.virtual_aux_identification_reply(challenge, card)
        )
        self.assertEqual(identity, bytes.fromhex(
            "12 34 56 78 01 23 45 67 89 06 32"
        ))
        profile = bridge.decode_aux_packet(
            bridge.virtual_aux_profile_reply(challenge, card)
        )
        self.assertEqual(profile, bytes.fromhex(
            "12 34 56 78 11 55 00 00 00 00 00"
        ))
        for index, expected in enumerate(template[0x0D:0x11], start=0x0D):
            reply = bridge.decode_aux_packet(
                bridge.virtual_aux_value_reply(challenge, index, card)
            )
            self.assertEqual(reply, challenge.to_bytes(4, "big") + bytes([expected]))

    def test_virtual_coin_validator_identity_and_scc_end_of_frame(self) -> None:
        device = bridge.VirtualCoinValidator()
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS)
        self.assertIsNone(device.observe_tx(0x78, 2))
        self.assertIsNone(device.observe_tx(0x78, 1))
        self.assertIsNone(device.observe_tx(0x7F, 3))
        self.assertIsNone(device.observe_tx(0x00, 2))
        reply = device.observe_tx(0x7F, 1)
        self.assertIsNotNone(reply)
        self.assertEqual(reply[:3], b"NRI")
        self.assertEqual(reply[0x0A:0x0F], b"eagle")
        self.assertEqual(reply[0x14:0x18], b"FT30")
        self.assertEqual(reply[0x1B:0x1D], b"\x05\x00")
        self.assertEqual(len(reply), 34)
        self.assertEqual(reply[-1], sum(reply[:-1]) & 0xFF)
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS | 1)
        self.assertIsNone(device.observe_tx(0x7F, 3))
        self.assertIsNone(device.observe_tx(0x00, 2))
        self.assertIsNone(device.observe_tx(0x7F, 1))
        for byte in reply[:-1]:
            self.assertEqual(device.data(), byte)
            self.assertEqual(device.consume_rx(), byte)
        self.assertEqual(
            device.status(),
            bridge.BOARD_SCC_IDLE_STATUS
            | bridge.BOARD_SCC_RX_READY
            | bridge.BOARD_SCC_END_OF_FRAME,
        )
        self.assertEqual(device.consume_rx(), reply[-1])
        self.assertIsNone(device.data())
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS)

    def test_virtual_coin_validator_ignores_unrecognized_and_broken_frames(self) -> None:
        device = bridge.VirtualCoinValidator()
        self.assertIsNone(device.observe_tx(0x7F, 3))
        self.assertIsNone(device.observe_tx(0x01, 2))
        self.assertIsNone(device.observe_tx(0x80, 1))
        self.assertIsNone(device.observe_tx(0x7F, 3))
        self.assertIsNone(device.observe_tx(0x7F, 1))
        self.assertEqual(device.pending_count(), 0)

    def test_virtual_coin_validator_secondary_identity_probe(self) -> None:
        device = bridge.VirtualCoinValidator()
        for remaining, value in ((3, 0xC7), (2, 0x00)):
            self.assertIsNone(device.observe_tx(value, remaining))
        reply = device.observe_tx(0xC7, 1)
        self.assertIsNotNone(reply)
        self.assertEqual(reply[:3], b"NRI")
        self.assertEqual(reply[0x0A:0x0F], b"eagle")
        self.assertEqual(reply[0x14:0x18], b"FT30")
        self.assertEqual(len(reply), 34)
        self.assertEqual(reply[-1], sum(reply[:-1]) & 0xFF)

    def test_virtual_coin_validator_hopper_ready_handshake(self) -> None:
        device = bridge.VirtualCoinValidator()
        for probe, expected in ((0xC0, 0x0F), (0xC3, 0x0E),
                                (0xC3, 0x01), (0xC0, 0x0F)):
            self.assertIsNone(device.observe_tx(probe, 2))
            self.assertEqual(device.observe_tx(probe, 1), bytes([expected, expected]))
            self.assertEqual(device.consume_rx(), expected)
            self.assertEqual(device.consume_rx(), expected)
            self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS)

    def test_virtual_coin_validator_secondary_data_probe(self) -> None:
        device = bridge.VirtualCoinValidator()
        self.assertIsNone(device.observe_tx(0xC7, 3))
        self.assertIsNone(device.observe_tx(0x25, 2))
        self.assertEqual(device.observe_tx(0xEC, 1), bytes(5))
        self.assertEqual(device.pending_count(), 5)
        for _ in range(5):
            self.assertEqual(device.consume_rx(), 0)
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS)

    def test_virtual_coin_validator_hopper_status_probe(self) -> None:
        device = bridge.VirtualCoinValidator()
        self.assertIsNone(device.observe_tx(0xC7, 3))
        self.assertIsNone(device.observe_tx(0x26, 2))
        self.assertEqual(device.observe_tx(0xED, 1), bytes(3))
        self.assertEqual(device.pending_count(), 3)
        for _ in range(3):
            self.assertEqual(device.consume_rx(), 0)
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS)

    def test_virtual_coin_validator_hopper_release_ack(self) -> None:
        device = bridge.VirtualCoinValidator()
        self.assertIsNone(device.observe_tx(0xC2, 3))
        self.assertIsNone(device.observe_tx(0xFF, 2))
        self.assertEqual(device.observe_tx(0xC1, 1), b"\x00\x00")
        self.assertEqual(device.consume_rx(), 0)
        self.assertEqual(device.consume_rx(), 0)
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS)

    def test_virtual_coin_validator_hopper_detail_probe(self) -> None:
        device = bridge.VirtualCoinValidator()
        self.assertIsNone(device.observe_tx(0xC7, 3))
        self.assertIsNone(device.observe_tx(0x27, 2))
        self.assertEqual(device.observe_tx(0xEE, 1), bytes(6))
        self.assertEqual(device.pending_count(), 6)
        for _ in range(6):
            self.assertEqual(device.consume_rx(), 0)
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS)

    def test_virtual_coin_validator_hopper_activation_ack(self) -> None:
        device = bridge.VirtualCoinValidator()
        frame = bytes.fromhex("C5 00 00 01 B7 B2 2F")
        for remaining, value in zip(range(len(frame), 1, -1), frame[:-1]):
            self.assertIsNone(device.observe_tx(value, remaining))
        self.assertEqual(device.observe_tx(frame[-1], 1), b"\x5A\x5A")
        self.assertEqual(device.pending_count(), 2)
        self.assertEqual(device.consume_rx(), 0x5A)
        self.assertEqual(device.consume_rx(), 0x5A)
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS)

        broken = frame[:-1] + b"\x00"
        for remaining, value in zip(range(len(broken), 0, -1), broken):
            self.assertIsNone(device.observe_tx(value, remaining))

    def test_virtual_coin_validator_pairing_challenge(self) -> None:
        device = bridge.VirtualCoinValidator()
        frame = bytes.fromhex("7F 27 00 32 1E BD EC 3E DD")
        for remaining, value in zip(range(len(frame), 1, -1), frame[:-1]):
            self.assertIsNone(device.observe_tx(value, remaining))
        self.assertEqual(device.observe_tx(frame[-1], 1), b"\x00" * 4)
        self.assertTrue(device.challenge_pending)

        class PairingRsp:
            def read_memory(self, address: int, length: int) -> bytes:
                if address == device.PAIRING_DYNAMIC_TABLE_FLAG:
                    return b"\x00\x01"
                if address == device.PAIRING_DYNAMIC_TABLE:
                    return bytes([0xFF]) * length
                return bytes(length)

        self.assertEqual(
            device.prepare_pairing_reply(PairingRsp()),
            (0xFFFA, bytes.fromhex("02 FF FA FB")),
        )
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS | 1)
        self.assertEqual([device.consume_rx() for _ in range(3)], [2, 0xFF, 0xFA])
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS | 1 | 0x20)
        self.assertEqual(device.consume_rx(), 0xFB)
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS)

        class FakeRsp:
            def __init__(self) -> None:
                self.register = 0xABCD1234

            def read_memory(self, address: int, length: int) -> bytes:
                assert (address, length) == (
                    bridge.VirtualCoinValidator.PAIRING_EXPECTED_WORD, 2
                )
                return b"\x56\x78"

            def read_register_u32(self, register: int) -> int:
                assert register == bridge.REG_D6
                return self.register

            def write_register_u32(self, register: int, value: int) -> None:
                assert register == bridge.REG_D6
                self.register = value

        rsp = FakeRsp()
        self.assertEqual(device.match_challenge(rsp), (0x1234, 0x5678, True))
        self.assertEqual(rsp.register, 0xABCD5678)
        self.assertIsNone(device.match_challenge(rsp))

        device.challenge_pending = True
        rsp.register = 0xABCD5678
        self.assertEqual(device.match_challenge(rsp), (0x5678, 0x5678, False))
        self.assertEqual(rsp.register, 0xABCD5678)

        broken = bytes.fromhex("7F 27 00 32 1E BD EC 3E DE")
        for remaining, value in zip(range(len(broken), 0, -1), broken):
            self.assertIsNone(device.observe_tx(value, remaining))

    def test_virtual_coin_validator_pairing_transform(self) -> None:
        transform = bridge.VirtualCoinValidator.pairing_expected_word
        self.assertEqual(transform(bytes(12), bytes(256)), 0)
        self.assertEqual(transform(bytes(range(12)), bytes([0xFF]) * 256), 0xFFFA)
        with self.assertRaises(ValueError):
            transform(bytes(11), bytes(256))

    def test_pairing_fallback_requires_observed_tx_watchpoint_gap(self) -> None:
        class FakeRsp:
            register = 0

            def read_memory(self, address: int, length: int) -> bytes:
                self_address = bridge.VirtualCoinValidator.PAIRING_EXPECTED_WORD
                assert (address, length) == (self_address, 2)
                return b"\x12\x34"

            def read_register_u32(self, register: int) -> int:
                assert register == bridge.REG_D6
                return self.register

            def write_register_u32(self, register: int, value: int) -> None:
                assert register == bridge.REG_D6
                self.register = value

        device = bridge.VirtualCoinValidator()
        rsp = FakeRsp()
        self.assertIsNone(device.match_challenge(rsp))
        for value, remaining in ((0x7F, 9), (0x27, 8), (0x00, 6)):
            device.observe_tx(value, remaining)
        self.assertEqual(device.last_pairing_gap, (7, 6))
        self.assertTrue(device.pairing_frame_gap)
        self.assertEqual(device.match_challenge(rsp), (0, 0x1234, True))
        self.assertEqual(rsp.register, 0x1234)
        self.assertFalse(device.pairing_frame_gap)
        self.assertIsNone(device.match_challenge(rsp))

    def test_pairing_recovers_one_missed_scc_write_from_checksum(self) -> None:
        device = bridge.VirtualCoinValidator()
        observed = (
            (9, 0x7F), (8, 0x27), (7, 0x04), (6, 0xE6),
            (5, 0xCD), (3, 0xF6), (2, 0x5E), (1, 0x53),
        )
        for remaining, value in observed[:-1]:
            self.assertIsNone(device.observe_tx(value, remaining))
        self.assertEqual(device.observe_tx(0x53, 1), bytes(4))
        self.assertEqual(device.last_pairing_gap, (4, 3))
        self.assertTrue(device.challenge_pending)
        self.assertEqual(len(device.last_tx_frame), 9)
        self.assertEqual(device.last_tx_frame[-1], sum(device.last_tx_frame[:-1]) & 0xFF)
        self.assertEqual(device.last_tx_frame[:5], bytes.fromhex('7F 27 04 E6 CD'))

        # A gap in an unrelated frame must not invent a pairing challenge.
        other = bridge.VirtualCoinValidator()
        for remaining, value in ((3, 0xC7), (1, 0xC7)):
            other.observe_tx(value, remaining)
        self.assertFalse(other.challenge_pending)

    def test_pairing_counter_read_ahead_keeps_complete_frame(self) -> None:
        # Captured on the second fresh-image boot: the third write was
        # reported with the fourth write's counter value, but no byte was lost.
        frame = bytes.fromhex("7F 27 0A 4D FA CD EB 1E CD")
        observed_remaining = (9, 8, 6, 6, 5, 4, 3, 2, 1)
        device = bridge.VirtualCoinValidator()
        for value, remaining in zip(frame[:-1], observed_remaining[:-1]):
            self.assertIsNone(device.observe_tx(value, remaining))
        self.assertEqual(device.observe_tx(frame[-1], 1), bytes(4))
        self.assertEqual(device.last_tx_frame, frame)
        self.assertTrue(device.challenge_pending)
        self.assertFalse(device.pairing_frame_gap)
        self.assertIsNone(device.last_pairing_gap)

    def test_pairing_counter_read_ahead_on_second_command_byte(self) -> None:
        # Captured with both displays on the GPU: the second write (27) was
        # reported with the third write's counter. The frame was dropped and
        # the firmware failed the challenge (expected 9AB3, no reply).
        frame = bytes.fromhex("7F 27 0A 7D 6E AA 27 A2 0E")
        observed_remaining = (9, 7, 7, 6, 5, 4, 3, 2, 1)
        device = bridge.VirtualCoinValidator()
        for value, remaining in zip(frame[:-1], observed_remaining[:-1]):
            self.assertIsNone(device.observe_tx(value, remaining))
        self.assertEqual(device.observe_tx(frame[-1], 1), bytes(4))
        self.assertEqual(device.last_tx_frame, frame)
        self.assertTrue(device.challenge_pending)
        self.assertFalse(device.pairing_frame_gap)

        # A genuinely missed 27 is still recovered from the checksum.
        missed = bridge.VirtualCoinValidator()
        for value, remaining in zip(frame[:1] + frame[2:-1], (9, 7, 6, 5, 4, 3, 2)):
            missed.observe_tx(value, remaining)
        self.assertEqual(missed.observe_tx(frame[-1], 1), bytes(4))
        self.assertEqual(missed.last_tx_frame, frame)

        # Only a challenge that started at counter 9 is treated this way.
        other = bridge.VirtualCoinValidator()
        for value, remaining in ((0x7F, 5), (0x27, 3), (0x0A, 3)):
            other.observe_tx(value, remaining)
        self.assertFalse(other.challenge_pending)
        self.assertFalse(other.pairing_frame_gap)

    def test_pairing_gap_log_survives_gap_clearing_on_next_frame(self) -> None:
        device = bridge.VirtualCoinValidator()
        for value, remaining in ((0x7F, 9), (0x27, 8), (0x04, 6)):
            device.observe_tx(value, remaining)
        previous = device.last_pairing_gap
        self.assertEqual(previous, (7, 6))
        self.assertEqual(
            bridge.pairing_gap_log_transition(None, previous),
            "DB_VIRTUAL_MP_TX_GAP expected_remaining=07 observed_remaining=06",
        )
        device.observe_tx(0x7F, 9)
        self.assertIsNone(device.last_pairing_gap)
        self.assertEqual(
            bridge.pairing_gap_log_transition(previous, device.last_pairing_gap),
            "DB_VIRTUAL_MP_TX_GAP_CLEARED",
        )
        self.assertIsNone(bridge.pairing_gap_log_transition(None, None))

    def test_pairing_accepts_checksum_with_lagging_state_counter(self) -> None:
        device = bridge.VirtualCoinValidator()
        frame = bytes.fromhex("7F 27 02 B8 73 92 85 C8 B2")
        for remaining, value in zip(range(9, 1, -1), frame[:-1]):
            self.assertIsNone(device.observe_tx(value, remaining))
        self.assertEqual(device.observe_tx(frame[-1], 0), bytes(4))
        self.assertEqual(device.last_tx_frame, frame)
        self.assertTrue(device.challenge_pending)
        self.assertTrue(device.last_pairing_count_lag)

        broken = bridge.VirtualCoinValidator()
        for remaining, value in zip(range(9, 1, -1), frame[:-1]):
            broken.observe_tx(value, remaining)
        self.assertIsNone(broken.observe_tx(0, 0))
        self.assertFalse(broken.challenge_pending)

        combined = bridge.VirtualCoinValidator()
        for remaining, value in (
            (9, 0x7F), (8, 0x27), (7, 0x04), (6, 0xE6),
            (5, 0xCD), (3, 0xF6), (2, 0x5E),
        ):
            combined.observe_tx(value, remaining)
        self.assertEqual(combined.observe_tx(0x53, 0), bytes(4))
        self.assertTrue(combined.challenge_pending)
        self.assertTrue(combined.last_pairing_count_lag)
        self.assertEqual(len(combined.last_tx_frame), 9)

    def test_pairing_repairs_only_a_fully_delivered_corrupt_rx_buffer(self) -> None:
        class FakeRsp:
            def __init__(self, received: bytes, address: int) -> None:
                self.received = received
                self.address = address
                self.writes = []

            def read_register_u32(self, register: int) -> int:
                self.assert_register(register)
                return self.address

            @staticmethod
            def assert_register(register: int) -> None:
                assert register == bridge.REG_A3

            def read_memory(self, address: int, length: int) -> bytes:
                assert address == bridge.VirtualCoinValidator.PAIRING_RECEIVE_BUFFER
                assert length == 4
                return self.received

            def write_memory(self, address: int, data: bytes) -> None:
                self.writes.append((address, data))

        device = bridge.VirtualCoinValidator()
        device.challenge_pending = True
        device.last_pairing_reply = bytes.fromhex("00 E7 D6 BD")
        rsp = FakeRsp(bytes.fromhex("00 E7 E7 D6"), device.PAIRING_RECEIVE_BUFFER)
        self.assertEqual(
            device.reconcile_pairing_receive_buffer(rsp),
            (bytes.fromhex("00 E7 E7 D6"), bytes.fromhex("00 E7 D6 BD")),
        )
        self.assertEqual(rsp.writes, [(device.PAIRING_RECEIVE_BUFFER, device.last_pairing_reply)])

        valid = FakeRsp(device.last_pairing_reply, device.PAIRING_RECEIVE_BUFFER)
        self.assertEqual(
            device.reconcile_pairing_receive_buffer(valid),
            (device.last_pairing_reply, device.last_pairing_reply),
        )
        self.assertEqual(valid.writes, [])
        device.challenge_pending = False
        self.assertIsNone(device.reconcile_pairing_receive_buffer(rsp))
        device.challenge_pending = True
        self.assertIsNone(device.reconcile_pairing_receive_buffer(FakeRsp(b"", 0x1234)))

    def test_both_initvideo_stages_require_systeminfo_not_short_ack(self) -> None:
        self.assertFalse(bridge.initvideo_retry_confirmed(
            "startup", response_pending=True, systeminfo_seen=False,
        ))
        self.assertFalse(bridge.initvideo_retry_confirmed(
            "later", response_pending=True, systeminfo_seen=False,
        ))
        tail = bytearray()
        for byte in b"\x06\x03\x00\x00\x07\x38O\x00M90":
            self.assertFalse(bridge.observe_guest_systeminfo(tail, byte))
        self.assertTrue(bridge.observe_guest_systeminfo(tail, ord("-")))
        self.assertTrue(bridge.initvideo_retry_confirmed(
            "later", response_pending=False, systeminfo_seen=True,
        ))
        self.assertTrue(bridge.initvideo_retry_confirmed(
            "startup", response_pending=False, systeminfo_seen=True,
        ))

    def test_virtual_coin_validator_type_probes_return_selected_type(self) -> None:
        class FakeRsp:
            def read_memory(self, address: int, length: int) -> bytes:
                self_address = bridge.MP_REQUIRED_TYPE_STATE
                assert (address, length) == (self_address, 1)
                return b"\x01"

        for frame in bridge.VirtualCoinValidator.VALIDATOR_TYPE_PROBES:
            device = bridge.VirtualCoinValidator()
            for remaining, value in zip(range(len(frame), 1, -1), frame[:-1]):
                self.assertIsNone(device.observe_tx(value, remaining))
            self.assertEqual(device.observe_tx(frame[-1], 1), b"\x00\x00")
            self.assertEqual(device.last_tx_frame, frame)
            self.assertTrue(device.type_pending)
            self.assertEqual(device.prepare_type_reply(FakeRsp()), (2, b"\x02\x02"))
            self.assertFalse(device.type_pending)
            self.assertEqual(device.consume_rx(), 2)
            self.assertEqual(device.consume_rx(), 2)
            self.assertIsNone(device.consume_rx())

    def test_virtual_coin_validator_status_watchdog(self) -> None:
        device = bridge.VirtualCoinValidator()
        frame = bytes.fromhex("7F 26 ED 28 BA")
        for remaining, value in zip(range(len(frame), 1, -1), frame[:-1]):
            self.assertIsNone(device.observe_tx(value, remaining))
        self.assertEqual(device.observe_tx(frame[-1], 1), b"\x00\x00")
        self.assertEqual(device.consume_rx(), 0)
        self.assertEqual(device.consume_rx(), 0)
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS)

        broken = frame[:-1] + b"\x00"
        for remaining, value in zip(range(len(broken), 0, -1), broken):
            self.assertIsNone(device.observe_tx(value, remaining))

    def test_virtual_coin_validator_session_update_acknowledges_rx_wait(self) -> None:
        device = bridge.VirtualCoinValidator()
        frame = bytes.fromhex("7B 99 9D 5E 2E CB 08")
        for remaining, value in zip(range(len(frame), 1, -1), frame[:-1]):
            self.assertIsNone(device.observe_tx(value, remaining))
        self.assertEqual(device.observe_tx(frame[-1], 1), b"\0\0")
        self.assertEqual(device.consume_rx(), 0)
        self.assertEqual(device.consume_rx(), 0)
        self.assertEqual(device.status(), bridge.BOARD_SCC_IDLE_STATUS)

        broken = frame[:-1] + b"\x00"
        for remaining, value in zip(range(len(broken), 0, -1), broken):
            self.assertIsNone(device.observe_tx(value, remaining))

    def test_original_uart_initializer_restores_cpu_context(self) -> None:
        class FakeRsp:
            def __init__(self) -> None:
                self.original = [0x1000 + index for index in range(18)]
                self.original[bridge.REG_A7] = 0x001FFB00
                self.original[bridge.REG_PC] = bridge.RUNTIME_IO_INIT_RETURN_PC
                self.registers = self.original.copy()
                self.memory_writes = []
                self.timeouts = []

            def read_register_u32(self, index: int) -> int:
                return self.registers[index]

            def read_registers_u32(self) -> tuple[int, ...]:
                return tuple(self.registers)

            def write_register_u32(self, index: int, value: int) -> None:
                self.registers[index] = value

            def write_memory(self, address: int, data: bytes) -> None:
                self.memory_writes.append((address, data))

            def read_memory(self, address: int, length: int) -> bytes:
                if address == bridge.UART_STATE_ADDRESS and length == 2:
                    return bridge.UART_STATE_READY.to_bytes(2, "big")
                if address == self.original[bridge.REG_A7] - 4 and length == 4:
                    return bytes.fromhex("12 34 56 78")
                raise AssertionError("unexpected memory read")

            def set_timeout(self, value: float) -> None:
                self.timeouts.append(value)

            def command(self, text: str) -> str:
                if text.startswith(("Z0,", "z0,")):
                    return "OK"
                if text == "c":
                    self.registers[bridge.REG_PC] = (
                        bridge.UART_INIT_RETURN_SENTINEL
                    )
                    return "T05"
                raise AssertionError(f"unexpected command: {text}")

        rsp = FakeRsp()
        state = bridge.run_original_uart_initializer(rsp)
        self.assertEqual(state, bridge.UART_STATE_READY)
        self.assertEqual(rsp.registers, rsp.original)
        self.assertEqual(
            rsp.memory_writes,
            [
                (
                    rsp.original[bridge.REG_A7] - 4,
                    bridge.UART_INIT_RETURN_SENTINEL.to_bytes(4, "big"),
                ),
                (rsp.original[bridge.REG_A7] - 4, bytes.fromhex("12 34 56 78")),
            ],
        )
        self.assertEqual(
            rsp.timeouts, [bridge.UART_INIT_CALL_TIMEOUT, 60.0]
        )

    def test_interrupt_uses_control_timeout_and_keeps_safe_control_timeout(self) -> None:
        class FakeRsp:
            timeouts = []

            def set_timeout(self, value: float) -> None:
                self.timeouts.append(value)

            def interrupt(self) -> str:
                self.assert_control_timeout()
                return "T05"

            def assert_control_timeout(self) -> None:
                if self.timeouts[-1] != bridge.RSP_INTERRUPT_REPLY_TIMEOUT:
                    raise AssertionError("interrupt did not use control timeout")

        rsp = FakeRsp()
        reply = bridge.interrupt_after_run_slice(rsp)
        self.assertEqual(reply, "T05")
        self.assertEqual(
            rsp.timeouts,
            [bridge.RSP_INTERRUPT_REPLY_TIMEOUT, bridge.RSP_CONTROL_REPLY_TIMEOUT],
        )
        self.assertEqual(bridge.RSP_INTERRUPT_REPLY_TIMEOUT, 30.0)

    def test_slice_interrupt_preserves_late_hardware_stops(self) -> None:
        for reply in (
            "T05watch:fff909;", "T05rwatch:fff909;",
            "T05awatch:fff909;", "T05", "S05",
            "T02watch:fff909;", "W01", "E01",
        ):
            with self.subTest(reply=reply):
                self.assertFalse(bridge.is_run_slice_interrupt(reply))
        for reply in ("S02", "T02", "T02thread:1;"):
            with self.subTest(reply=reply):
                self.assertTrue(bridge.is_run_slice_interrupt(reply))

    def test_timeout_scheduler_dispatches_late_stop_instead_of_timer(self) -> None:
        import ast
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        handlers = [node for node in ast.walk(tree)
                    if isinstance(node, ast.ExceptHandler)
                    and isinstance(node.type, ast.Name)
                    and node.type.id == "TimeoutError"
                    and "interrupt_after_run_slice(rsp)" in ast.unparse(node)]
        self.assertEqual(len(handlers), 1)
        self.assertIn("timer_stop = is_run_slice_interrupt(reply)",
                      ast.unparse(handlers[0]))
        self.assertNotIn("timer_stop = True", ast.unparse(handlers[0]))

    def test_uart_ready_requires_original_firmware_magic(self) -> None:
        self.assertFalse(bridge.uart_state_is_ready(0x0000))
        self.assertFalse(bridge.uart_state_is_ready(0xA756))
        self.assertTrue(bridge.uart_state_is_ready(bridge.UART_STATE_READY))

    def test_uart_state_watchpoint_covers_the_full_word(self) -> None:
        class FakeRsp:
            command_text = ""

            def command(self, text: str) -> str:
                self.command_text = text
                return "OK"

        rsp = FakeRsp()
        bridge.set_watchpoint(
            rsp, 2, bridge.UART_STATE_ADDRESS, True, length=2
        )
        self.assertEqual(rsp.command_text, "Z2,1ebbb0,2")

    def test_database_timing_uses_icount_without_host_pause(self) -> None:
        self.assertIsNone(bridge.validate_timer_interval(0.05))
        self.assertEqual(bridge.board_timer_ticks_per_cycle(0.05), 50)
        self.assertEqual(bridge.GUEST_ICOUNT_SHIFT, 6)
        self.assertEqual(bridge.GUEST_MAX_INSTRUCTIONS_PER_SECOND, 15_625_000)
        self.assertLess(bridge.GUEST_MAX_INSTRUCTIONS_PER_SECOND, 16_000_000)
        self.assertEqual(bridge.qemu_creation_flags(), 0)
        self.assertEqual(bridge.RSP_INTERRUPT_REPLY_TIMEOUT, 30.0)
        self.assertEqual(bridge.qemu_tcg_accelerator(True), "tcg")
        self.assertEqual(
            bridge.qemu_tcg_accelerator(False), "tcg,one-insn-per-tb=on",
        )

    def test_closed_com3_is_detected_without_waiting_for_firmware_rx(self) -> None:
        self.assertIsNone(bridge.ensure_com3_receiver_alive([]))
        error = ConnectionError("guest QEMU closed COM3")
        with self.assertRaises(ConnectionError) as raised:
            bridge.ensure_com3_receiver_alive([error])
        self.assertIs(raised.exception, error)

    def test_virtual_timer_batch_is_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "too many virtual timer ticks"):
            bridge.board_timer_ticks_per_cycle(0.21)

    def test_nested_timer_batches_ticks_under_same_cpu_guard(self) -> None:
        ticks = bridge.board_timer_ticks_per_cycle(0.05)
        self.assertAlmostEqual(bridge.nested_board_schedule(0.05, ticks, 0.0),
                               0.001)
        self.assertAlmostEqual(bridge.nested_board_schedule(0.05, ticks, 0.0495),
                               0.0005)
        self.assertAlmostEqual(bridge.nested_board_schedule(0.05, ticks, 0.05),
                               0.001)

    def test_only_original_unmasked_timer_queue_wait_may_expire_directly(self) -> None:
        slot = bridge.TIMER_QUEUE_BASE + 4
        expire = bridge.expired_timer_queue_slot
        self.assertEqual(expire(bridge.TIMER_QUEUE_WAIT_PC, 0x2000, slot), slot)
        self.assertTrue(bridge.may_fast_forward_timer_wait(0, 0))
        self.assertFalse(bridge.may_fast_forward_timer_wait(1, 0))
        self.assertFalse(
            bridge.may_fast_forward_timer_wait(
                0, bridge.BOARD_SCC_TX_ACTIVE
            )
        )
        self.assertFalse(
            bridge.may_fast_forward_timer_wait(
                0, bridge.BOARD_SCC_REPLY_WAITING
            )
        )
        self.assertEqual(expire(bridge.TIMER_QUEUE_WAIT_PC + 2, 0x2000, slot), slot)
        self.assertIsNone(expire(bridge.TIMER_QUEUE_WAIT_PC + 4, 0x2000, slot))
        self.assertIsNone(expire(bridge.TIMER_QUEUE_WAIT_PC, 0x2700, slot))
        self.assertIsNone(expire(bridge.TIMER_QUEUE_WAIT_PC, 0x2000, slot + 1))
        self.assertIsNone(
            expire(
                bridge.TIMER_QUEUE_WAIT_PC,
                0x2000,
                bridge.TIMER_QUEUE_BASE + bridge.TIMER_QUEUE_SLOTS * 4,
            )
        )

    def test_only_proven_absent_device_poll_may_expire_slot_zero(self) -> None:
        expire = bridge.expired_device_discovery_timer_slot
        for pc in bridge.DEVICE_DISCOVERY_TIMER_WAIT_PCS:
            self.assertEqual(
                expire(pc, 0x2000, bridge.TIMER_QUEUE_BASE, 0x1F, 0x1F),
                bridge.TIMER_QUEUE_BASE,
            )
        valid_pc = min(bridge.DEVICE_DISCOVERY_TIMER_WAIT_PCS)
        self.assertIsNone(
            expire(valid_pc - 2, 0x2000, bridge.TIMER_QUEUE_BASE, 0x1F, 0x1F)
        )
        self.assertIsNone(
            expire(valid_pc, 0x2700, bridge.TIMER_QUEUE_BASE, 0x1F, 0x1F)
        )
        self.assertIsNone(
            expire(valid_pc, 0x2000, bridge.TIMER_QUEUE_BASE + 4, 0x1F, 0x1F)
        )
        self.assertIsNone(
            expire(valid_pc, 0x2000, bridge.TIMER_QUEUE_BASE, 0, 0)
        )
        self.assertIsNone(
            expire(valid_pc, 0x2000, bridge.TIMER_QUEUE_BASE, 0x1F, 0x1E)
        )
        self.assertIsNone(
            expire(valid_pc, 0x2000, bridge.TIMER_QUEUE_BASE, 0x3D, 0x3D)
        )

    def test_too_short_or_nonfinite_timer_interval_is_rejected(self) -> None:
        for interval in (0.0, 0.004, float("nan"), float("inf")):
            with self.subTest(interval=interval):
                with self.assertRaisesRegex(ValueError, "timer run slice"):
                    bridge.validate_timer_interval(interval)

    def test_uart_uses_machine_configuration_9600_baud_8n1(self) -> None:
        self.assertEqual(bridge.UART_BAUD, 9600)
        self.assertEqual(bridge.SERIAL_BITS_PER_BYTE, 10)
        self.assertAlmostEqual(bridge.UART_FRAME_SECONDS, 10 / 9600)
        self.assertEqual(bridge.IDLE_DIAGNOSTIC_SECONDS, 10.0)

    def test_idle_protocol_heartbeat_repeats_without_new_uart_activity(self) -> None:
        due = bridge.idle_protocol_diagnostic_due
        common = {
            "first_frame_reported": True,
            "retry_active": False,
            "response_pending": False,
            "last_uart_activity_at": 100.0,
        }
        self.assertFalse(due(now=109.999, next_diagnostic_at=0.0, **common))
        self.assertTrue(due(now=110.0, next_diagnostic_at=110.0, **common))
        self.assertTrue(due(now=120.0, next_diagnostic_at=120.0, **common))
        self.assertFalse(due(now=120.0, next_diagnostic_at=120.0,
                             **{**common, "response_pending": True}))
        self.assertFalse(due(now=120.0, next_diagnostic_at=120.0,
                             **{**common, "retry_active": True}))

    def test_uart_rx_is_published_only_after_idle_status_watch_is_removed(self) -> None:
        publish = bridge.should_publish_pending_uart_byte
        self.assertTrue(publish(1, rx_status_watch=False,
                                rx_data_watch=False, rx_ready_seen=False))
        self.assertFalse(publish(0, rx_status_watch=False,
                                 rx_data_watch=False, rx_ready_seen=False))
        self.assertFalse(publish(1, rx_status_watch=True,
                                 rx_data_watch=False, rx_ready_seen=False))
        self.assertFalse(publish(1, rx_status_watch=False,
                                 rx_data_watch=True, rx_ready_seen=False))
        self.assertFalse(publish(1, rx_status_watch=False,
                                 rx_data_watch=False, rx_ready_seen=True))

    def test_serial_sender_never_advances_before_byte_deadline(self) -> None:
        class FakeClock:
            def __init__(self) -> None:
                self.now = 0.0
                self.sleep_calls = []

            def monotonic(self) -> float:
                return self.now

            def sleep(self, delay: float) -> None:
                self.sleep_calls.append(delay)
                # Simulate a host sleep that repeatedly wakes too early.
                self.now += min(delay, bridge.UART_FRAME_SECONDS / 3)

        class FakeSocket:
            def __init__(self, fake_clock: FakeClock) -> None:
                self.clock = fake_clock
                self.sent = []

            def sendall(self, data: bytes) -> None:
                self.sent.append((self.clock.monotonic(), data))

        fake_clock = FakeClock()
        sock = FakeSocket(fake_clock)
        elapsed = bridge.send_serial_frame(
            sock,
            b"\x01\x02\x04",
            clock=fake_clock.monotonic,
            sleeper=fake_clock.sleep,
        )

        self.assertEqual([data for _, data in sock.sent], [b"\x01", b"\x02", b"\x04"])
        self.assertGreaterEqual(sock.sent[1][0], bridge.UART_FRAME_SECONDS)
        self.assertGreaterEqual(sock.sent[2][0], 2 * bridge.UART_FRAME_SECONDS)
        self.assertGreater(len(fake_clock.sleep_calls), 2)
        self.assertGreaterEqual(elapsed, 2 * bridge.UART_FRAME_SECONDS)

    def test_initial_frame_retry_is_bounded_and_stops_on_guest_response(self) -> None:
        self.assertEqual(bridge.INITIAL_FRAME_RETRY_SECONDS, 10.0)
        self.assertEqual(bridge.INITIAL_FRAME_MAX_ATTEMPTS, 60)
        self.assertEqual(
            bridge.INITIAL_FRAME_RETRY_SECONDS
            * bridge.INITIAL_FRAME_MAX_ATTEMPTS,
            600.0,
        )
        self.assertTrue(
            bridge.initial_frame_retry_due(
                response_pending=False, attempts=1, now=20.0, retry_at=20.0
            )
        )
        self.assertFalse(
            bridge.initial_frame_retry_due(
                response_pending=True, attempts=1, now=20.0, retry_at=20.0
            )
        )
        self.assertFalse(
            bridge.initial_frame_retry_due(
                response_pending=False,
                attempts=bridge.INITIAL_FRAME_MAX_ATTEMPTS,
                now=20.0,
                retry_at=20.0,
            )
        )
        self.assertFalse(
            bridge.initial_frame_retry_due(
                response_pending=False, attempts=1, now=19.999, retry_at=20.0
            )
        )

    def test_native_loader_runtime_cookie(self) -> None:
        self.assertEqual(bridge.LOADER_RUNTIME_COOKIE, 0x5F72D920)
        self.assertEqual(bridge.LOADER_IDLE_RETURN_PC, 0x0C8A)

    def test_tx_status_overlap_is_not_treated_as_rx(self) -> None:
        self.assertIn(0x19CB0, bridge.TX_STATUS_OVERLAP_STOP_PCS)
        self.assertIn(0xC5F2C, bridge.TX_STATUS_OVERLAP_STOP_PCS)
        self.assertNotIn(0xC57B8, bridge.TX_STATUS_OVERLAP_STOP_PCS)

    def test_filters_bootloader_but_not_runtime_traffic(self) -> None:
        self.assertFalse(bridge.should_forward_tx(0x0C76, b"\xFF"))
        self.assertFalse(bridge.should_forward_tx(0x0CAA, b"\x32"))
        self.assertFalse(bridge.should_forward_tx(0x19CBC, b"\x00"))
        self.assertFalse(bridge.should_forward_tx(0x19CCE, b"\x00"))
        self.assertTrue(bridge.should_forward_tx(0x19CBC, b"\x01"))
        self.assertTrue(bridge.should_forward_tx(0xC5F3C, b"\xFF"))

    def test_database_board_latch_feedback(self) -> None:
        self.assertEqual(bridge.board_latch_feedback(0x18324), 0x13)
        self.assertEqual(bridge.board_latch_feedback(0x1837A), 0x02)
        self.assertIsNone(bridge.board_latch_feedback(0x18320))

    def test_board_port_set_and_clear_aliases_feed_back_to_input(self) -> None:
        value = bridge.apply_board_port_strobe(
            0x81, bridge.BOARD_PORT_SET, 0x0A
        )
        self.assertEqual(value, 0x8B)
        value = bridge.apply_board_port_strobe(
            value, bridge.BOARD_PORT_CLEAR, 0x09
        )
        self.assertEqual(value, 0x82)

    def test_closed_door_sets_only_the_external_input_bit(self) -> None:
        self.assertEqual(
            bridge.apply_cabinet_inputs(0x81, door_closed=True), 0x91
        )
        self.assertEqual(bridge.BOARD_DOOR_CLOSED_MASK, 0x10)

    def test_open_door_clears_only_the_external_input_bit(self) -> None:
        self.assertEqual(
            bridge.apply_cabinet_inputs(0xB1, door_closed=False), 0xA1
        )

    def test_closed_door_is_reasserted_after_board_clear_strobe(self) -> None:
        value = bridge.apply_board_port_strobe(
            0xFF, bridge.BOARD_PORT_CLEAR, 0xFF
        )
        self.assertEqual(value, 0x00)
        self.assertEqual(
            bridge.apply_cabinet_inputs(value, door_closed=True), 0x10
        )

    def test_rtc_control_and_serial_port_are_adjacent(self) -> None:
        self.assertEqual(bridge.RTC_CONTROL_REGISTER, 0xFFF906)
        self.assertEqual(bridge.RTC_PORT_REGISTER, 0xFFF907)
        self.assertEqual(bridge.RTC_CONTROL_WATCH_LENGTH, 1)
        self.assertEqual(bridge.RTC_DATA, 0x80)

    def test_unknown_board_port_strobe_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown board port"):
            bridge.apply_board_port_strobe(0, bridge.BOARD_PORT_INPUT, 0x08)

    def test_board_timer_pending_bit_preserves_other_status_bits(self) -> None:
        self.assertEqual(bridge.board_timer_status(0x81, True), 0x89)
        self.assertEqual(bridge.board_timer_status(0x89, False), 0x81)
        self.assertEqual(bridge.BOARD_TIMER_VECTOR, 64)
        self.assertEqual(bridge.BOARD_TIMER_HANDLER, 0x750DC)
        self.assertEqual(bridge.BOARD_TIMER_RTE_PC, 0x7511C)
        self.assertEqual(bridge.BOARD_SCC_A_VECTOR, 65)
        self.assertEqual(bridge.BOARD_SCC_A_HANDLER, 0x7511E)
        self.assertEqual(bridge.BOARD_SCC_A_RTE_PC, 0x751BA)
        self.assertEqual(bridge.UART_TIMER_RTE_PC, 0xC6C0A)

    def test_interrupt_return_steps_the_real_rte_breakpoint(self) -> None:
        class FakeRsp:
            def __init__(self) -> None:
                self.commands = []

            def command(self, command):
                self.commands.append(command)
                return "S05" if command == "s" else "OK"

        rsp = FakeRsp()
        self.assertEqual(
            bridge.step_past_breakpoint(rsp, bridge.BOARD_TIMER_RTE_PC),
            "S05",
        )
        self.assertEqual(
            rsp.commands,
            ["z0,7511c,1", "s", "Z0,7511c,1"],
        )

    def test_board_scc_pending_follows_tx_state(self) -> None:
        self.assertEqual(
            bridge.board_interrupt_status(
                0x80, timer_asserted=True, scc_tx_active=True
            ),
            0x98,
        )
        self.assertEqual(
            bridge.board_interrupt_status(
                0x90, timer_asserted=True, scc_tx_active=False
            ),
            0x88,
        )

    def test_board_scc_idle_status_is_rx_empty_and_tx_ready(self) -> None:
        self.assertEqual(bridge.BOARD_SCC_IDLE_STATUS & 0x01, 0)
        self.assertNotEqual(bridge.BOARD_SCC_IDLE_STATUS & 0x04, 0)
        self.assertEqual(bridge.BOARD_SCC_A_TX_READY, 0x04)

    def test_board_timer_uses_the_programmed_ivr(self) -> None:
        timer = (
            bridge.BOARD_TIMER_VECTOR,
            bridge.BOARD_TIMER_HANDLER,
            bridge.BOARD_TIMER_RTE_PC,
        )
        scc_a = (
            bridge.BOARD_SCC_A_VECTOR,
            bridge.BOARD_SCC_A_HANDLER,
            bridge.BOARD_SCC_A_RTE_PC,
        )
        self.assertEqual(bridge.board_service_target(64), timer)
        self.assertEqual(bridge.board_service_target(65), scc_a)
        with self.assertRaises(ValueError):
            bridge.board_service_target(0)
        self.assertEqual(
            bridge.board_interrupt_status(
                0x80,
                timer_asserted=True,
                scc_tx_active=False,
                scc_a_rx_pending=True,
            ),
            0x8A,
        )
        # A queued TX command must not fabricate the RX-pending bit. Otherwise
        # the original SCC-A handler loops on 0x80018B and reads nonexistent
        # peripheral bytes instead of reaching its TX dispatcher.
        self.assertEqual(
            bridge.board_interrupt_status(
                0x82, timer_asserted=True, scc_tx_active=True
            ),
            0x98,
        )

    def test_nested_board_timer_is_limited_to_proven_waits(self) -> None:
        self.assertTrue(
            bridge.can_nest_board_timer(
                bridge.TIMER_QUEUE_WAIT_PC, 0x2000, depth=1
            )
        )
        self.assertFalse(
            bridge.can_nest_board_timer(
                bridge.TIMER_QUEUE_WAIT_PC, 0x2700, depth=1
            )
        )
        self.assertTrue(bridge.can_nest_board_timer(0x60444, 0x2000, depth=1))
        self.assertFalse(bridge.can_nest_board_timer(0x60444, 0x2700, depth=1))
        self.assertFalse(bridge.can_nest_board_timer(0x60444, 0x2000, depth=2))
        self.assertFalse(
            bridge.can_nest_board_timer(
                bridge.TIMER_QUEUE_WAIT_PC, 0x2000, depth=2
            )
        )
        for pc in bridge.UART_REPLY_WAIT_PCS:
            self.assertTrue(bridge.can_nest_board_timer(pc, 0x2000, depth=1))
            self.assertFalse(bridge.can_nest_board_timer(pc, 0x2700, depth=1))
            self.assertFalse(bridge.can_nest_board_timer(pc, 0x2000, depth=2))
        for pc in bridge.HOPPER_QUEUE_WAIT_PCS:
            self.assertTrue(bridge.can_nest_board_timer(pc, 0x2000, depth=1))
            self.assertFalse(bridge.can_nest_board_timer(pc, 0x2700, depth=1))
            self.assertFalse(bridge.can_nest_board_timer(pc, 0x2000, depth=2))
        pc = bridge.BOARD_TICK_COUNTER_WAIT_PC
        self.assertTrue(bridge.can_nest_board_timer(pc, 0x2000, depth=1))
        self.assertFalse(bridge.can_nest_board_timer(pc, 0x2700, depth=1))
        self.assertFalse(bridge.can_nest_board_timer(pc, 0x2000, depth=2))
        self.assertFalse(bridge.can_nest_board_timer(pc + 2, 0x2000, depth=1))

    def test_hopper_diagnostics_only_reads_firmware_state(self) -> None:
        class FakeRsp:
            def __init__(self) -> None:
                self.reads = []

            def read_memory(self, address: int, width: int) -> bytes:
                self.reads.append((address, width))
                return bytes([0x01] * width)

        rsp = FakeRsp()
        fields = bridge.read_hopper_diagnostics(rsp)
        self.assertEqual(fields["mode_70"], 1)
        self.assertEqual(fields["counter_c6"], 0x0101)
        self.assertEqual(
            rsp.reads,
            [(address, width) for _, address, width in bridge.HOPPER_DIAGNOSTIC_FIELDS],
        )

    def test_hopper_idle_level_tracks_original_modulation_phase(self) -> None:
        class FakeRsp:
            def __init__(self, counter: int, phase: int) -> None:
                self.counter = counter
                self.phase = phase
                self.writes = []

            def read_memory(self, address: int, width: int) -> bytes:
                if (address, width) == (bridge.HOPPER_MODULATION_COUNTER, 3):
                    return bytes([self.counter, 0, self.phase])
                if (address, width) == (bridge.HOPPER_SENSOR_SHADOW, 2):
                    return bytes.fromhex("08 A4")
                raise AssertionError("unexpected firmware read")

            def write_memory(self, address: int, value: bytes) -> None:
                self.writes.append((address, value))

        for counter, phase, expected in (
            (0, 0, "09 A5"),
            (8, 0, "09 A5"),
            (9, 0, "08 A4"),
            (10, 1, "08 A4"),
            (19, 1, "08 A4"),
            (20, 1, "09 A5"),
        ):
            with self.subTest(counter=counter, phase=phase):
                rsp = FakeRsp(counter, phase)
                self.assertEqual(bridge.publish_idle_hopper_sensors(rsp),
                                 bytes.fromhex(expected))
                expected_writes = [] if expected == "08 A4" else [
                    (bridge.HOPPER_SENSOR_SHADOW, bytes.fromhex(expected))
                ]
                self.assertEqual(rsp.writes, expected_writes)

    def test_return_button_is_forced_released_and_stale_edge_is_cleared(self) -> None:
        class FakeRsp:
            def __init__(self) -> None:
                self.memory = {
                    bridge.RETURN_BUTTON_CURRENT: 0xA1,
                    bridge.RETURN_BUTTON_EVENT: 0xB7,
                }
                self.writes = []

            def read_memory(self, address: int, width: int) -> bytes:
                self.assert_width = width
                return bytes([self.memory[address]])

            def write_memory(self, address: int, value: bytes) -> None:
                self.memory[address] = value[0]
                self.writes.append((address, value))

        rsp = FakeRsp()
        self.assertEqual(bridge.RETURN_BUTTON_KEY_ID, 9)
        self.assertEqual(bridge.RETURN_BUTTON_MAPPING, 0x1023)
        self.assertEqual(bridge.RETURN_BUTTON_CURRENT, 0x1E249E)
        self.assertEqual(bridge.RETURN_BUTTON_EVENT, 0x1E2526)
        self.assertEqual(bridge.publish_released_return_button(rsp), (0xB1, 0xA7))
        self.assertEqual(
            rsp.writes,
            [
                (bridge.RETURN_BUTTON_CURRENT, b"\xB1"),
                (bridge.RETURN_BUTTON_EVENT, b"\xA7"),
            ],
        )

    def test_uart_reply_only_nests_at_unmasked_original_wait(self) -> None:
        allow = bridge.can_nest_uart_reply
        args = (0xC5D6E, 0x2000, 1, bridge.BOARD_TIMER_RTE_PC, True)
        self.assertTrue(allow(*args))
        self.assertFalse(allow(0xC5D6C, *args[1:]))
        self.assertFalse(allow(args[0], 0x2700, *args[2:]))
        self.assertFalse(allow(args[0], args[1], 2, *args[3:]))
        self.assertFalse(allow(args[0], args[1], args[2],
                               bridge.BOARD_SCC_A_RTE_PC, True))
        self.assertFalse(allow(*args[:4], False))

    def test_nested_scc_tx_only_unblocks_command_wait_in_timer_isr(self) -> None:
        allow = bridge.can_nest_scc_a_tx
        args = (
            bridge.SCC_A_COMMAND_WAIT_PC, 0x2010, 1,
            bridge.BOARD_TIMER_RTE_PC, 0x64, 0x04,
        )
        self.assertTrue(allow(*args))
        self.assertFalse(allow(0x7075E, *args[1:]))
        self.assertFalse(allow(args[0], 0x2710, *args[2:]))
        self.assertFalse(allow(args[0], args[1], 2, *args[3:]))
        self.assertFalse(allow(args[0], args[1], 1,
                               bridge.BOARD_SCC_A_RTE_PC, 0x64, 0x04))
        self.assertFalse(allow(*args[:4], 0, 0x04))
        self.assertFalse(allow(*args[:5], 0x00))

    def test_touch_controller_uses_dump_supported_identity(self) -> None:
        self.assertEqual(
            bridge.touch_controller_response(b"\x01Z\x0D"),
            b"\x010\x0D",
        )
        self.assertEqual(
            bridge.touch_controller_response(b"\x01OI\x0D"),
            b"\x01A30000\x0D",
        )
        self.assertIsNone(
            bridge.touch_controller_response(b"\x01UNKNOWN\x0D")
        )

    def test_touch_response_is_published_atomically_with_ring_wrap(self) -> None:
        class FakeRsp:
            def __init__(self) -> None:
                self.memory = {
                    bridge.TOUCH_UART_RX_COUNT: 0,
                    bridge.TOUCH_UART_RX_INDEX: 30,
                }
                self.writes = []

            def read_memory(self, address: int, length: int) -> bytes:
                return bytes(
                    self.memory.get(address + offset, 0)
                    for offset in range(length)
                )

            def write_memory(self, address: int, data: bytes) -> None:
                self.writes.append((address, data))
                for offset, value in enumerate(data):
                    self.memory[address + offset] = value

        rsp = FakeRsp()
        self.assertTrue(
            bridge.inject_touch_controller_response(rsp, b"\x01A3\x0D")
        )
        self.assertEqual(
            rsp.writes,
            [
                (bridge.TOUCH_UART_RX_BUFFER + 30, b"\x01A"),
                (bridge.TOUCH_UART_RX_BUFFER, b"3\x0D"),
                (bridge.TOUCH_UART_RX_COUNT, b"\x04"),
            ],
        )

    def test_completes_only_missing_initvideo_board_profile(self) -> None:
        firmware_frame = bytes.fromhex(
            "01 02 22 00 7C 06 33 01 53 00 F8 30 2D 06 8F 13 3F 2C "
            "01 00 00 00 00 05 D0 07 00 00 00 00 13 11 00 00 00 00 "
            "01 58 04"
        )
        expected = bytes.fromhex(
            "01 02 22 00 7C 06 33 01 53 00 F8 30 2D 06 8F 13 3F 2C "
            "01 00 00 00 00 05 DC 07 02 01 16 0E 13 11 00 00 01 00 "
            "02 FF 04"
        )
        self.assertEqual(
            bridge.complete_initvideo_board_profile(firmware_frame, bridge.RTC_DEFAULT_TIME), expected
        )

    def test_later_initvideo_uses_explicit_time_without_changing_device_fields(self) -> None:
        firmware_frame = bytes.fromhex(
            "01 02 22 00 7C 06 33 01 53 00 F8 30 2D 06 8F 13 3F 2C "
            "01 00 00 00 00 05 75 08 19 2D 2D 55 13 11 00 00 00 00 "
            "01 58 04"
        )
        completed = bridge.complete_initvideo_clock(firmware_frame, bridge.RTC_DEFAULT_TIME)
        self.assertEqual(completed[24:30], bytes.fromhex("DC 07 02 01 16 0E"))
        self.assertEqual(completed[:24], firmware_frame[:24])
        self.assertEqual(completed[30:], firmware_frame[30:])

        forwarder = bridge.InitvideoClockForwarder(lambda: bridge.RTC_DEFAULT_TIME)
        delivered = bytearray()
        completed_count = 0
        for value in firmware_frame:
            chunk, rewritten = forwarder.feed(bytes([value]))
            delivered.extend(chunk)
            completed_count += rewritten
        self.assertEqual(bytes(delivered), completed)
        self.assertEqual(completed_count, 1)

    def test_initvideo_keeps_owner_profile_fields_after_aux_identification(self) -> None:
        firmware_frame = bytes.fromhex(
            "01 02 22 00 7C 06 33 01 53 00 F8 30 2D 06 8F 13 3F 2C "
            "01 00 00 00 00 05 DC 07 02 01 16 0E 13 00 03 5F 00 00 "
            "01 58 04"
        )
        completed = bridge.complete_initvideo_board_profile(firmware_frame, bridge.RTC_DEFAULT_TIME)
        self.assertEqual(completed[30:34], bytes.fromhex("13 00 03 5F"))
        self.assertEqual(completed[24:30], bytes.fromhex("DC 07 02 01 16 0E"))
        self.assertEqual(completed[34:38], bridge.INITVIDEO_DEVICE_FIELDS)

    def test_non_initvideo_stream_is_forwarded_without_changes(self) -> None:
        forwarder = bridge.InitvideoClockForwarder(lambda: bridge.RTC_DEFAULT_TIME)
        frame = bytes.fromhex("01 02 4F 00 09 00 04")
        delivered = bytearray()
        for value in frame:
            chunk, rewritten = forwarder.feed(bytes([value]))
            self.assertFalse(rewritten)
            delivered.extend(chunk)
        self.assertEqual(bytes(delivered), frame)

    def test_touch_click_coordinates_are_completed_from_delivered_input(self) -> None:
        # Menu (3F 2C) and service program (18 2C) receive the same completion:
        # the picture line 284 as the row from the bottom edge, 599 - 284 = 315.
        for target, row, row_bytes in (("3F 2C", 315, "3B 01"), ("18 2C", 315, "3B 01")):
            with self.subTest(target=target):
                forwarder = bridge.TouchClickForwarder()
                frame = bytes.fromhex(f"01 02 41 00 {target} 00 00 00 00 04")
                delivered = bytearray()
                corrections = []
                for value in frame:
                    chunk, corrected = forwarder.feed(bytes([value]), (614, 284))
                    delivered.extend(chunk)
                    if corrected is not None:
                        corrections.append(corrected)
                self.assertEqual(
                    bytes(delivered),
                    bytes.fromhex(f"01 02 41 00 {target} 66 02 {row_bytes} 04"),
                )
                self.assertEqual(corrections, [(614, row)])

    def test_touch_rows_count_from_the_bottom_edge_over_the_whole_height(self) -> None:
        for line, expected in ((0, 599), (26, 573), (599, 0)):
            forwarder = bridge.TouchClickForwarder()
            delivered = bytearray()
            for value in bytes.fromhex("01 02 41 00 18 2C 00 00 00 00 04"):
                delivered.extend(forwarder.feed(bytes([value]), (144, line))[0])
            self.assertEqual(int.from_bytes(delivered[6:8], "little"), 144)
            self.assertEqual(int.from_bytes(delivered[8:10], "little"), expected)

    def test_touch_release_and_other_frames_are_not_changed(self) -> None:
        forwarder = bridge.TouchClickForwarder()
        frames = (
            bytes.fromhex("01 02 41 00 3F 2C FF FF FF FF 04"),
            bytes.fromhex("01 02 41 00 3F 2C 20 00 08 01 04"),
            bytes.fromhex("01 02 4F 00 09 00 04"),
        )
        delivered = bytearray()
        for frame in frames:
            for value in frame:
                chunk, corrected = forwarder.feed(bytes([value]), (614, 284))
                self.assertIsNone(corrected)
                delivered.extend(chunk)
        self.assertEqual(bytes(delivered), b"".join(frames))

    def test_quick_touch_has_one_down_and_immediate_up(self) -> None:
        stream = bridge.TouchPacketStream()
        stream.request(454, 530, True)
        stream.request(454, 530, False)
        self.assertEqual(
            [(x, y, down) for x, y, down, _ in stream.packets],
            [(454, 530, True), (454, 530, False)],
        )
        self.assertIsNone(stream.active_point)

    def test_drag_positions_are_not_duplicated(self) -> None:
        stream = bridge.TouchPacketStream()
        stream.request(300, 300, True)
        stream.request(300, 300, True)
        stream.request(320, 300, True)
        stream.request(320, 300, True)
        stream.request(320, 300, False)
        self.assertEqual(
            [(x, y, down) for x, y, down, _ in stream.packets],
            [(300, 300, True), (320, 300, True), (320, 300, False)],
        )

    def test_second_click_releases_first_before_next_press(self) -> None:
        stream = bridge.TouchPacketStream()
        stream.request(100, 200, True)
        stream.request(100, 200, False)
        stream.request(300, 400, True)
        self.assertEqual(
            [(x, down) for x, _, down, _ in stream.packets],
            [(100, True), (100, False), (300, True)],
        )

    def test_stationary_hold_and_duplicate_up_do_not_generate_events(self) -> None:
        stream = bridge.TouchPacketStream()
        stream.request(400, 200, True)
        for index in range(30):
            stream.request(400, 200, True)
        self.assertEqual(len(stream.packets), 1)
        stream.request(400, 200, False)
        stream.request(400, 200, False)
        self.assertEqual([packet[2] for packet in stream.packets],
                         [True, False])

    def test_touch_release_drops_stale_drag_reports(self) -> None:
        stream = bridge.TouchPacketStream()
        for index in range(20):
            stream.request(100 + index, 200, True)
        stream.request(119, 200, False)
        self.assertEqual(
            [(x, down) for x, _, down, _ in stream.packets],
            [(100, True), (118, True), (119, True), (119, False)],
        )

    def test_touch_press_consumed_before_release_is_not_reinserted(self) -> None:
        stream = bridge.TouchPacketStream()
        stream.request(400, 200, True)
        self.assertTrue(stream.packets.popleft()[2])
        stream.request(400, 200, False)
        self.assertEqual([packet[2] for packet in stream.packets], [False])

    def test_rejects_changed_initvideo_identity_fields(self) -> None:
        frame = bytearray(
            bytes.fromhex(
                "01 02 22 00 7C 06 33 01 53 00 F8 30 2D 06 8F 13 3F 2C "
                "01 00 00 00 00 05 D0 07 00 00 00 00 13 11 00 00 00 00 "
                "01 58 04"
            )
        )
        frame[8] ^= 1
        with self.assertRaisesRegex(ValueError, "identity/content"):
            bridge.complete_initvideo_board_profile(bytes(frame), bridge.RTC_DEFAULT_TIME)

    def test_first_initvideo_ignores_embedded_eot_calendar_and_device_bytes(self):
        frame = bytearray(bytes.fromhex(
            "01 02 22 00 7C 06 33 01 53 00 F8 30 2D 06 8F 13 3F 2C "
            "01 00 00 00 00 05 E8 07 04 04 04 04 13 11 00 00 04 04 04 04 04"
        ))
        for length in range(len(frame)):
            self.assertFalse(bridge.initvideo_frame_complete(frame[:length]))
        self.assertTrue(bridge.initvideo_frame_complete(frame))
        completed = bridge.complete_initvideo_board_profile(frame, bridge.RTC_DEFAULT_TIME)
        self.assertEqual(len(completed), 39)
        self.assertEqual(completed[24:30], bytes.fromhex("DC 07 02 01 16 0E"))

    def test_first_initvideo_still_rejects_bad_prefix_length_and_final_eot(self):
        with self.assertRaisesRegex(ValueError, "prefix"):
            bridge.initvideo_frame_complete(bytes.fromhex("01 02 4F 00"))
        with self.assertRaisesRegex(ValueError, "fixed length"):
            bridge.initvideo_frame_complete(bridge.INITVIDEO_PREFIX + bytes(36))
        frame = (bridge.INITVIDEO_PREFIX + bridge.INITVIDEO_OWNER_FIELDS
                 + bytes(15))
        self.assertTrue(bridge.initvideo_frame_complete(frame))
        with self.assertRaisesRegex(ValueError, "layout"):
            bridge.complete_initvideo_clock(frame, bridge.RTC_DEFAULT_TIME)

    def test_format_zero_interrupt_frame(self) -> None:
        self.assertEqual(
            bridge.format_zero_exception_frame(0x2004, 0x12345678, 134),
            bytes.fromhex("20 04 12 34 56 78 02 18"),
        )


class RegisterMemoryRsp:
    """Halted CPU with core registers and sparse memory."""

    def __init__(self, core, memory=None):
        self.core = list(core)
        self.memory = dict(memory or {})
        self.commands = []

    def read_memory(self, address, length):
        return bytes(self.memory.get(address + index, 0) for index in range(length))

    def write_memory(self, address, data):
        for index, value in enumerate(data):
            self.memory[address + index] = value

    def read_register_snapshot(self):
        raw = "".join(f"{value:08x}" for value in self.core)
        return bridge.M68kRegisterSnapshot(raw, tuple(self.core))

    def write_registers_u32(self, updates, *, snapshot):
        for register, value in updates.items():
            self.core[register] = value
        return self.read_register_snapshot()

    def command(self, text):
        self.commands.append(text)
        return "S05" if text == "s" else "OK"


class HookReturnTests(unittest.TestCase):
    def cpu(self, *, pc, sr, a7, memory=None):
        core = [0] * 18
        core[bridge.REG_PC], core[bridge.REG_SR], core[bridge.REG_A7] = pc, sr, a7
        return RegisterMemoryRsp(core, memory)

    def test_rte_returns_from_an_injected_frame_without_a_step(self):
        frame = bridge.format_zero_exception_frame(0x2004, 0x000C5B6E, 64)
        rsp = self.cpu(pc=bridge.BOARD_TIMER_RTE_PC, sr=0x2700, a7=0x1FFB00,
                       memory={0x1FFB00 + index: value for index, value in enumerate(frame)})
        bridge.return_from_hooked_rte(rsp, bridge.BOARD_TIMER_RTE_PC)
        self.assertEqual(rsp.core[bridge.REG_PC], 0x000C5B6E)
        self.assertEqual(rsp.core[bridge.REG_SR], 0x2004)
        self.assertEqual(rsp.core[bridge.REG_A7], 0x1FFB08)
        self.assertEqual(rsp.commands, [])

    def test_rte_of_other_frames_is_single_stepped(self):
        cases = (
            ("format 2", 0x2004, 0x2080),        # six-word frame, not injected
            ("user mode", 0x0004, 0x0100),       # would switch the stack pointer
            ("master stack", 0x3004, 0x0100),    # M bit changes the active stack
        )
        for label, sr, format_word in cases:
            with self.subTest(label):
                frame = (sr.to_bytes(2, "big") + (0x1234).to_bytes(4, "big")
                         + format_word.to_bytes(2, "big"))
                rsp = self.cpu(pc=bridge.UART_TIMER_RTE_PC, sr=0x2700, a7=0x1FFB00,
                               memory={0x1FFB00 + index: value for index, value in enumerate(frame)})
                bridge.return_from_hooked_rte(rsp, bridge.UART_TIMER_RTE_PC)
                self.assertEqual(rsp.commands, ["z0,c6c0a,1", "s", "Z0,c6c0a,1"])
                self.assertEqual(rsp.core[bridge.REG_A7], 0x1FFB00)

    def test_scan_jsr_is_executed_in_registers(self):
        rsp = self.cpu(pc=bridge.BOARD_SCAN_COMPLETE_PC, sr=0x2700, a7=0x1FFB48)
        target = bridge.absolute_jsr_target(bytes.fromhex("4EB9 00007B10"))
        bridge.emulate_absolute_jsr(rsp, rsp.read_register_snapshot(), target)
        self.assertEqual(rsp.core[bridge.REG_PC], 0x7B10)
        self.assertEqual(rsp.core[bridge.REG_A7], 0x1FFB44)
        self.assertEqual(rsp.read_memory(0x1FFB44, 4), (bridge.BOARD_SCAN_COMPLETE_PC + 6).to_bytes(4, "big"))

    def test_only_an_absolute_jsr_is_emulated(self):
        self.assertIsNone(bridge.absolute_jsr_target(bytes.fromhex("4EB8 7B10 0000")))
        self.assertIsNone(bridge.absolute_jsr_target(bytes.fromhex("4EB9 0000")))

    def test_breakpoint_prefetch_follows_the_expected_stop(self):
        class Hints:
            def __init__(self, stack):
                self.stack = stack

            def last_known_register(self, register):
                return self.stack if register == bridge.REG_A7 else None

        self.assertEqual(bridge.breakpoint_prefetch(Hints(0x1FFB48), scan_expected=True),
                         bridge.SCAN_PREFETCH)
        hints = bridge.breakpoint_prefetch(Hints(0x1FFB48), scan_expected=False)
        self.assertEqual(hints[:len(bridge.TICK_PREFETCH)], bridge.TICK_PREFETCH)
        start, length = hints[-1]
        # The RTE frame sits 0x40 above the stack seen at the scan stop.
        self.assertTrue(start <= 0x1FFB48 + 0x40 and 0x1FFB48 + 0x48 <= start + length)
        self.assertEqual(bridge.breakpoint_prefetch(Hints(None), scan_expected=False),
                         bridge.TICK_PREFETCH)

    def test_interrupt_handlers_are_not_cut_by_short_run_slices(self):
        # The old effective Windows timeout was about 16 ms.
        self.assertGreaterEqual(bridge.isr_run_slice(0.001), 0.015)
        self.assertEqual(bridge.isr_run_slice(0.05), 0.05)

    def test_windows_timer_resolution_is_raised_and_restored(self):
        winmm = mock.Mock()
        winmm.timeBeginPeriod.return_value = 0
        with mock.patch.object(bridge.sys, "platform", "win32"), \
                mock.patch.object(bridge.ctypes, "WinDLL", return_value=winmm, create=True):
            with bridge.host_timer_resolution() as raised:
                self.assertTrue(raised)
                winmm.timeEndPeriod.assert_not_called()
        winmm.timeBeginPeriod.assert_called_once_with(1)
        winmm.timeEndPeriod.assert_called_once_with(1)


class DatabaseVersionTests(unittest.TestCase):
    @staticmethod
    def images():
        runtime = bytes((index * 7 + 1) & 0xFF for index in range(0x1C3138))
        loader = bytes((index * 5 + 2) & 0xFF for index in range(0xBC0))
        return runtime, loader

    @staticmethod
    def groups_for(runtime, loader):
        """The hook groups with digests of the given images instead of the owner's."""
        import hashlib
        groups = []
        for group, name, source, addresses, _ in bridge.DATABASE_HOOK_GROUPS:
            image, base = ((loader, bridge.LOADER_LOAD_ADDRESS) if source == "loader"
                           else (runtime, bridge.DATABASE_RUNTIME_START))
            digest = hashlib.sha256()
            for address in addresses:
                offset = address - base
                digest.update(address.to_bytes(4, "big") + image[offset:offset + bridge.HOOK_WINDOW])
            groups.append((group, name, source, addresses, digest.hexdigest()[:16]))
        return tuple(groups)

    def test_unknown_build_reports_every_group(self):
        runtime, loader = self.images()
        mismatches = bridge.database_hook_mismatches(runtime, loader)
        self.assertEqual([group for group, _ in mismatches],
                         [group for group, *_ in bridge.DATABASE_HOOK_GROUPS])

    def test_changed_code_reports_only_its_group(self):
        runtime, loader = self.images()
        with mock.patch.object(bridge, "DATABASE_HOOK_GROUPS", self.groups_for(runtime, loader)):
            self.assertEqual(bridge.database_hook_mismatches(runtime, loader), [])
            changed = bytearray(runtime)
            changed[bridge.TOUCH_CALIBRATION_FINISH_PC - bridge.DATABASE_RUNTIME_START] ^= 0xFF
            self.assertEqual(bridge.database_hook_mismatches(bytes(changed), loader),
                             [("touch", "Touch")])
            other_loader = bytearray(loader)
            other_loader[bridge.LOADER_IDLE_TX_STOP_PC - bridge.LOADER_LOAD_ADDRESS] ^= 1
            self.assertEqual(bridge.database_hook_mismatches(runtime, bytes(other_loader)),
                             [("loader", "Loader")])

    def test_every_hooked_code_address_is_covered(self):
        covered = {address for *_, addresses, _ in bridge.DATABASE_HOOK_GROUPS for address in addresses}
        for address in (bridge.COIN_ENTRY_READ_PC, bridge.UART_INIT_ENTRY, bridge.RTC_FAULT_ENTRY,
                        bridge.BOARD_TIMER_RTE_PC, bridge.VirtualCoinValidator.PAIRING_COMPARE_PC,
                        *bridge.TX_STATUS_OVERLAP_STOP_PCS, *bridge.HOPPER_QUEUE_WAIT_PCS,
                        *bridge.DEVICE_DISCOVERY_TIMER_WAIT_PCS, *bridge.BOARD_LATCH_FEEDBACK_BY_STOP_PC):
            self.assertIn(address, covered)

    def test_other_database_keeps_its_initvideo_identity(self):
        frame = bytearray(bridge.INITVIDEO_PREFIX + bytes(34) + b"\x04")
        frame[4:24] = bytes(range(20))
        with self.assertRaisesRegex(ValueError, "identity/content fields changed"):
            bridge.complete_initvideo_clock(bytes(frame), bridge.RTC_DEFAULT_TIME)
        completed = bridge.complete_initvideo_board_profile(
            bytes(frame), bridge.RTC_DEFAULT_TIME, strict=False)
        self.assertEqual(completed[4:24], bytes(range(20)))
        forwarder = bridge.InitvideoClockForwarder(lambda: bridge.RTC_DEFAULT_TIME, strict=False)
        sent = b"".join(forwarder.feed(bytes([value]))[0] for value in frame)
        self.assertEqual(sent[4:24], bytes(range(20)))


if __name__ == "__main__":
    unittest.main()
