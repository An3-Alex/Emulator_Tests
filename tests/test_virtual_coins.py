"""Coin inputs must travel through MP telegrams, never balance writes."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cabinet_controls import CabinetControlServer, send_command, validate_command
from cabinet_control_panel import ControlPanel
from event_log_viewer import BridgeLogParser
from m68k_database_bridge import VirtualCoinValidator


class ReadOnlyFirmware:
    def __init__(self):
        self.memory = {}
        self.put(0x001E26B6, b"\x00\xFF")
        self.put(0x001F16CE, bytes((0, 2, 10, 20)) + bytes(12))
        self.put(0x001F8221, b"\x01")
        self.put(0x001F777D, b"\x01")
        self.put(0x001F786F, bytes.fromhex("32 1E BD EC 3E"))
        self.put(0x001F7874, bytes.fromhex("AD DE"))
        self.put(0x001F8222, bytes.fromhex("BA DC FE 01"))
        self.put(0x000775AA, bytes(range(256)))

    def put(self, address, data):
        self.memory.update((address + index, value) for index, value in enumerate(data))

    def read_memory(self, address, size):
        return bytes(self.memory.get(address + index, 0) for index in range(size))


def transmit(device, data):
    reply = None
    for remaining, value in zip(range(len(data), 0, -1), data):
        reply = device.observe_tx(value, remaining)
    return reply


def packet(data):
    return data + bytes([sum(data) & 255])


def enabled_device():
    device = VirtualCoinValidator()
    transmit(device, packet(bytes.fromhex("7E 00 88 88 88 88 88 88 88 88")))
    return device


class VirtualCoinTests(unittest.TestCase):
    def test_button_sends_exactly_one_euro_event_per_activation(self):
        panel = ControlPanel.__new__(ControlPanel)
        panel._send = Mock()
        panel._coin()
        panel._send.assert_called_once_with({"type": "coin", "cents": 100})

    def test_strict_coin_command_and_real_loopback_queue(self):
        for value in (True, 1, 99, 101, 100.0, "100", -100):
            with self.assertRaises(ValueError):
                validate_command({"type": "coin", "cents": value})
        with self.assertRaises(ValueError):
            validate_command({"type": "coin", "cents": 100, "down": True})
        server = CabinetControlServer(0)
        try:
            reply = send_command({"type": "coin", "cents": 100}, server._socket.getsockname()[1])
            self.assertEqual(reply, {"ok": True})
            self.assertEqual(server.events.get(timeout=1), {"type": "coin", "cents": 100})
            self.assertTrue(server.events.empty())
        finally:
            server.close()

    def test_authenticated_euro_uses_live_channel_and_no_firmware_writes(self):
        device, firmware = enabled_device(), ReadOnlyFirmware()
        device.queue_coin(100, 0)
        neutral = transmit(device, packet(bytes.fromhex("7F 26 AD DE")))
        self.assertEqual(neutral, b"\0\0")
        reply, events = device.prepare_coin_reply(firmware, 1)
        expected = device.pairing_expected_word(bytes.fromhex("82 32 1E BD EC 3E AD DE BA DC FE 01"), bytes(range(256)))
        self.assertEqual(reply, packet(b"\x82\x08" + expected.to_bytes(2, "big")))
        self.assertIn("COIN_SENT", events[0])
        self.assertNotIn("CREDITED", events[0])
        self.assertEqual([device.consume_rx() for _ in range(5)], list(reply))
        firmware.put(0x001F1280, b"\x00\x0A")
        transmit(device, packet(bytes.fromhex("7B 01 02 03 04 05")))
        response, events = device.prepare_coin_reply(firmware, 2)
        self.assertIsNone(response)
        self.assertIn("COIN_CREDITED", events[0])

    def test_one_way_session_has_no_idle_reply_but_can_carry_one_coin(self):
        device, firmware = enabled_device(), ReadOnlyFirmware()
        session = packet(bytes.fromhex("7B 01 02 03 04 05"))
        self.assertIsNone(transmit(device, session))
        self.assertEqual(device.prepare_coin_reply(firmware, 0), (None, []))
        self.assertEqual(device.pending_count(), 0)
        device.queue_coin(100, 0)
        self.assertIsNone(transmit(device, session))
        reply, events = device.prepare_coin_reply(firmware, 1)
        self.assertEqual(reply[0:2], b"\x82\x08")
        self.assertEqual(len(events), 1)

    def test_no_coin_when_inhibited_busy_unmapped_or_checksum_invalid(self):
        for address, value in ((0x001E26B6, b"\0\0"), (0x001E2CE8, b"\1"),
                               (0x001F80FC, b"\1"), (0x001E2300, b"\2"),
                               (0x001E2B7D, b"\1"), (0x001F787A, b"\1"),
                               (0x001E22B6, b"\x0A"), (0x001F777D, b"\0"),
                               (0x001F16CE, bytes(16))):
            with self.subTest(address=address):
                device, firmware = enabled_device(), ReadOnlyFirmware()
                firmware.put(address, value)
                device.queue_coin(100, 0)
                transmit(device, packet(bytes.fromhex("7F 26 00 00")))
                self.assertEqual(device.prepare_coin_reply(firmware, 1), (None, []))
                self.assertEqual(device.pending_count(), 2)
        device = VirtualCoinValidator()
        device.queue_coin(100, 0)
        transmit(device, packet(bytes.fromhex("7F 26 00 00")))
        self.assertIsNone(device.prepare_coin_reply(ReadOnlyFirmware(), 1)[0])
        device = enabled_device()
        device.queue_coin(100, 0)
        transmit(device, bytes.fromhex("7F 26 00 00 00"))
        self.assertFalse(device.coin_window)

    def test_queue_bounded_expiring_and_never_retries_sent_coin(self):
        device, firmware = enabled_device(), ReadOnlyFirmware()
        for _ in range(device.COIN_QUEUE_LIMIT):
            device.queue_coin(100, 0)
        with self.assertRaises(ValueError):
            device.queue_coin(100, 0)
        self.assertEqual(len(device.expire_coins(30)), 16)
        self.assertEqual(device.expire_coins(31), [])
        device.queue_coin(100, 31)
        transmit(device, packet(bytes.fromhex("7B 01 02 03 04 05")))
        reply, _ = device.prepare_coin_reply(firmware, 32)
        for _ in reply:
            device.consume_rx()
        transmit(device, packet(bytes.fromhex("7B 01 02 03 04 05")))
        response, events = device.prepare_coin_reply(firmware, 63)
        self.assertIsNone(response)
        self.assertIn("UNCONFIRMED", events[0])

    def test_coin_log_distinguishes_sent_from_booked(self):
        parser = BridgeLogParser()
        sent = parser.feed("DB_VIRTUAL_MP_COIN_SENT id=1 cents=100 channel=2 route=8 wire=82 08 00 00 8A")
        booked = parser.feed("DB_VIRTUAL_MP_COIN_CREDITED id=1 cents=100")
        self.assertIn("sendet", sent[0].title)
        self.assertIn("gebucht", booked[0].title)


if __name__ == "__main__":
    unittest.main()
