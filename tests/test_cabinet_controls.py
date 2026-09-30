"""Offline checks for local cabinet controls and firmware switch mapping."""

from __future__ import annotations

import sys
import json
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cabinet_controls import (
    CabinetControlServer, KEY_CURRENT_BASE, KEY_EVENT_BASE, KEY_IDS,
    KEY_TABLE_BASE, format_tablet_packet, key_location, send_command,
    validate_command,
)
from cabinet_control_panel import ControlPanel, qmp_screendump
from m68k_database_bridge import (
    BUTTON_PULSE_BOARD_SCANS, MP_STATE_ADDRESS, advance_button_pulses,
    publish_cabinet_buttons,
)


class CabinetDisplayTests(unittest.TestCase):
    def test_preview_captures_cabinet_lower_device_not_boot_primary(self) -> None:
        stream = MagicMock()
        stream.readline.side_effect = [b'{"QMP": {}}\n', b'{"return": {}}\n', b'{"return": {}}\n']
        with patch("cabinet_control_panel.socket.create_connection") as connect:
            connect.return_value.__enter__.return_value.makefile.return_value = stream
            qmp_screendump(4444, Path("preview.png"))
        commands = [json.loads(call.args[0]) for call in stream.write.call_args_list]
        self.assertEqual(commands[-1]["arguments"]["device"], "lower")


class MemoryRsp:
    def __init__(self) -> None:
        self.data: dict[int, int] = {MP_STATE_ADDRESS: 0}
        mappings = {
            "menu": 0x1065, "autostart": 0x1005, "einsatz": 0x1067,
            "maxeinsatz": 0x1006, "start": 0x1026,
            "auszahlung": 0x1023, "service": 0x1025,
        }
        for name, mapping in mappings.items():
            self.write_memory(KEY_TABLE_BASE + KEY_IDS[name] * 2,
                              mapping.to_bytes(2, "big"))

    def read_memory(self, address: int, size: int) -> bytes:
        return bytes(self.data.get(address + i, 0) for i in range(size))

    def write_memory(self, address: int, value: bytes) -> None:
        for i, byte in enumerate(value):
            self.data[address + i] = byte


class CabinetControlTests(unittest.TestCase):
    def test_preview_touch_deduplicates_press_and_recovers_missed_release(self) -> None:
        panel = ControlPanel.__new__(ControlPanel)
        panel.pad_touch = None
        panel.qemu_touch = None
        panel._pad_point = lambda _event: (320, 240)
        events = []
        panel._send = events.append

        panel._touch_press(object())
        panel._touch_press(object())
        panel._release_pad_if_button_up(True)
        panel._release_pad_if_button_up(False)
        panel._touch_release(object())

        self.assertEqual(events, [
            {"type": "touch", "x": 320, "y": 240, "down": True},
            {"type": "touch", "x": 320, "y": 240, "down": False},
        ])

    def test_qemu_mouse_sends_one_contact_and_releases_outside_window(self) -> None:
        panel = ControlPanel.__new__(ControlPanel)
        panel.pad_touch = panel.qemu_touch = None
        panel.previous_left_down = False
        events = []
        panel._send = events.append
        panel._poll_qemu_touch(True, (100, 200), True)
        for _ in range(10):
            panel._poll_qemu_touch(True, (100, 200), True)
        panel._poll_qemu_touch(True, (120, 200), True)
        panel._poll_qemu_touch(True, None, True)
        panel._poll_qemu_touch(False, None, True)
        panel._poll_qemu_touch(False, None, True)
        self.assertEqual(events, [
            {"type": "touch", "x": 100, "y": 200, "down": True},
            {"type": "touch", "x": 120, "y": 200, "down": True},
            {"type": "touch", "x": 120, "y": 200, "down": False},
        ])

    def test_dragging_from_preview_to_qemu_does_not_start_second_contact(self) -> None:
        panel = ControlPanel.__new__(ControlPanel)
        panel.pad_touch = panel.qemu_touch = None
        panel.previous_left_down = False
        panel._pad_point = lambda _event: (320, 240)
        events = []
        panel._send = events.append
        panel._touch_press(object())
        panel._poll_qemu_touch(True, (100, 200), True)
        panel._release_pad_if_button_up(False)
        panel._poll_qemu_touch(False, None, True)
        self.assertEqual([event["down"] for event in events], [True, False])

    def test_disabling_qemu_mouse_releases_contact_once(self) -> None:
        panel = ControlPanel.__new__(ControlPanel)
        panel.pad_touch = panel.qemu_touch = None
        panel.previous_left_down = False
        events = []
        panel._send = events.append
        panel._poll_qemu_touch(True, (100, 200), True)
        panel._poll_qemu_touch(True, None, False)
        panel._poll_qemu_touch(True, (100, 200), True)
        panel._poll_qemu_touch(False, None, True)
        self.assertEqual([event["down"] for event in events], [True, False])

    def test_format_tablet_packet_has_status_and_7_bit_coordinates(self) -> None:
        self.assertEqual(format_tablet_packet(0, 0, True), b"\xC0\0\0\0\0")
        self.assertEqual(format_tablet_packet(799, 599, False),
                         b"\x80\x7f\x7f\x7f\x7f")
        self.assertEqual(format_tablet_packet(400, 300, True)[0], 0xC0)
        with self.assertRaises(ValueError):
            format_tablet_packet(800, 100, True)

    def test_command_validation_rejects_unbounded_or_invalid_input(self) -> None:
        self.assertEqual(validate_command({"type": "door", "open": True}),
                         {"type": "door", "open": True})
        for command in (
            {"type": "touch", "x": -1, "y": 0, "down": True},
            {"type": "touch", "x": 0, "y": 0, "down": 1},
            {"type": "button", "name": "unknown", "down": True},
            {"type": "door", "open": True, "extra": 1},
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                validate_command(command)

    def test_original_key_location_resolves_current_and_edge(self) -> None:
        self.assertEqual(key_location(0x1023),
                         (KEY_CURRENT_BASE + 0x23, KEY_EVENT_BASE + 0x23, 0x10))
        self.assertIsNone(key_location(0x9022))

    def test_buttons_press_edge_hold_and_release(self) -> None:
        rsp = MemoryRsp()
        self.assertEqual(publish_cabinet_buttons(rsp, set(), set(), set(KEY_IDS))["menu"],
                         0x1065)
        current, event, mask = key_location(0x1065)
        self.assertEqual(rsp.read_memory(current, 1), bytes([mask]))
        edges = {"menu"}
        publish_cabinet_buttons(rsp, {"menu"}, edges)
        self.assertEqual(edges, set())
        self.assertEqual(rsp.read_memory(current, 1), b"\0")
        self.assertEqual(rsp.read_memory(event, 1), bytes([mask]))
        rsp.write_memory(event, b"\0")  # firmware consumed the edge
        publish_cabinet_buttons(rsp, {"menu"}, edges)
        self.assertEqual(rsp.read_memory(event, 1), b"\0")
        publish_cabinet_buttons(rsp, set(), edges, {"menu"})
        self.assertEqual(rsp.read_memory(current, 1), bytes([mask]))

    def test_short_click_survives_multiple_timer_batches_then_releases(self) -> None:
        rsp = MemoryRsp()
        pressed = {"menu"}
        pending_edges = {"menu"}
        released: set[str] = set()
        release_after_scans = {"menu": BUTTON_PULSE_BOARD_SCANS}
        publish_cabinet_buttons(rsp, pressed, pending_edges, released)
        current, event, mask = key_location(0x1065)
        self.assertEqual(rsp.read_memory(current, 1), b"\0")
        self.assertEqual(rsp.read_memory(event, 1), bytes([mask]))
        self.assertEqual(pressed, {"menu"})
        for _ in range(BUTTON_PULSE_BOARD_SCANS - 1):
            self.assertEqual(
                advance_button_pulses(
                    pressed, pending_edges, released, release_after_scans,
                ), set(),
            )
            self.assertEqual(pressed, {"menu"})
        self.assertEqual(
            advance_button_pulses(
                pressed, pending_edges, released, release_after_scans,
            ), {"menu"},
        )
        self.assertEqual(pressed, set())
        self.assertEqual(released, {"menu"})
        self.assertEqual(release_after_scans, {})
        publish_cabinet_buttons(rsp, pressed, pending_edges, released)
        self.assertEqual(rsp.read_memory(current, 1), bytes([mask]))

    def test_floating_scanner_keeps_only_gui_pressed_key_active(self) -> None:
        rsp = MemoryRsp()
        for name, mapping in {
            "menu": 0x1065, "autostart": 0x1005, "einsatz": 0x1067,
            "maxeinsatz": 0x1006, "start": 0x1026,
            "auszahlung": 0x1023, "service": 0x1025,
        }.items():
            current, event, mask = key_location(mapping)
            rsp.write_memory(current, b"\0")
            rsp.write_memory(event, bytes([mask]))
        publish_cabinet_buttons(rsp, set(), set())
        for mapping in (0x1065, 0x1005, 0x1067, 0x1006, 0x1026,
                        0x1023, 0x1025):
            current, event, mask = key_location(mapping)
            self.assertEqual(rsp.read_memory(current, 1)[0] & mask, mask)
            self.assertEqual(rsp.read_memory(event, 1)[0] & mask, 0)

        current, event, mask = key_location(0x1065)
        edges = {"menu"}
        publish_cabinet_buttons(rsp, {"menu"}, edges)
        self.assertEqual(rsp.read_memory(current, 1)[0] & mask, 0)
        self.assertEqual(rsp.read_memory(event, 1), bytes([mask]))
        publish_cabinet_buttons(rsp, set(), edges, {"menu"})
        self.assertEqual(rsp.read_memory(current, 1)[0] & mask, mask)
        self.assertEqual(rsp.read_memory(event, 1)[0] & mask, 0)

    def test_loopback_control_server_accepts_valid_event(self) -> None:
        server = CabinetControlServer(0)
        try:
            port = server._socket.getsockname()[1]
            self.assertEqual(send_command({"type": "ping"}, port), {"ok": True})
            send_command({"type": "button", "name": "start", "down": True}, port)
            self.assertEqual(server.events.get(timeout=1.0),
                             {"type": "button", "name": "start", "down": True})
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
