"""Offline virtual controller, original decoder geometry and RX integration."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import m68k_database_bridge as bridge
from virtual_touch import ACK, POINT_ACK, TARGETS, VirtualTouchController, native_tablet_packet


def firmware_point(packet, wide=False):
    """What the firmware computes (FUN_7FD46/FUN_7FE30): column, row from the bottom."""
    x10 = (packet[1] >> 4) | (packet[2] << 3)
    y10 = (packet[3] >> 4) | (packet[4] << 3)
    return max(0, (x10 * (960 if wide else 800) >> 10) - (80 if wide else 0)), y10 * 600 >> 10


def decode_native(packet, wide=False):
    """The picture pixel a packet stands for."""
    x, row = firmware_point(packet, wide)
    return x, 599 - row


class MemoryRsp:
    def __init__(self, wide=False):
        self.memory = {bridge.TOUCH_WIDE_MODE_ADDRESS: int(wide)}
        self.writes = []

    def read_memory(self, address, length):
        return bytes(self.memory.get(address + offset, 0) for offset in range(length))

    def write_memory(self, address, value):
        self.writes.append((address, value))
        self.memory.update({address + offset: byte for offset, byte in enumerate(value)})

    def consume(self):
        self.memory[bridge.TOUCH_UART_RX_COUNT] = 0


class VirtualTouchTests(unittest.TestCase):
    def test_panel_reset_restores_exact_mapping_and_persists(self):
        from cabinet_controls import validate_command, CabinetControlServer, send_command
        with tempfile.TemporaryDirectory() as directory:
            controller = VirtualTouchController(Path(directory) / "work.img.touch.json")
            self.calibrate(controller, (120, 500), (680, 100))
            self.assertEqual(controller.screen_point(120, 500), TARGETS[0])
            server = CabinetControlServer(port=0)
            try:
                send_command(validate_command({"type": "touch_calibration_reset"}),
                             server._socket.getsockname()[1])
                queued = server.events.pop_types({"touch_calibration_reset"})
            finally:
                server.close()
            self.assertEqual(queued, {"type": "touch_calibration_reset"})
            controller.reset_calibration()
            self.assertIsNone(controller.points)
            for point in ((0, 0), (400, 300), (799, 599), TARGETS[0], TARGETS[1]):
                self.assertEqual(controller.screen_point(*point), point)
            reloaded = VirtualTouchController(controller.state_path)
            self.assertIsNone(reloaded.points)
            self.assertIn("DB_TOUCH_CALIBRATION_RESET source=control_panel", controller.events)

    def test_panel_reset_cannot_interrupt_native_service_calibration(self):
        controller = VirtualTouchController()
        self.calibrate(controller, (120, 500), (680, 100))
        controller.command(b"\x01CX\r")
        with self.assertRaisesRegex(ValueError, "active"):
            controller.reset_calibration()
        self.assertEqual(controller.points, ((120, 500), (680, 100)))

    def calibrate(self, controller, first=TARGETS[0], second=TARGETS[1]):
        self.assertEqual(controller.command(b"\x01CX\r"), ACK)
        self.assertIsNone(controller.touch(*first, True, wide=False))
        self.assertEqual(controller.touch(*first, False, wide=False), POINT_ACK)
        self.assertIsNone(controller.touch(*second, True, wide=False))
        self.assertEqual(controller.touch(*second, False, wide=False), POINT_ACK)
        self.assertFalse(controller.calibrating)
        controller.finish()

    def test_stateless_cx_cannot_pretend_to_calibrate(self):
        self.assertIsNone(bridge.touch_controller_response(b"\x01CX\r"))
        self.assertEqual(VirtualTouchController.initial_response(b"\x01OI\r"), b"\x01A30000\r")
        self.assertIsNone(VirtualTouchController().command(b"\x01UNKNOWN\r"))

    def test_all_horizontal_pixels_round_trip_in_both_native_modes(self):
        for wide in (False, True):
            for x in range(800):
                packet = native_tablet_packet(x, 284, True, wide=wide)
                self.assertEqual(decode_native(packet, wide), (x, 284))
                self.assertEqual(packet[0], 0xC0)
                self.assertTrue(all(byte < 128 for byte in packet[1:]))

    def test_all_vertical_pixels_round_trip_and_release_bit_is_clear(self):
        for wide in (False, True):
            for y in range(600):
                packet = native_tablet_packet(614, y, False, wide=wide)
                self.assertEqual(decode_native(packet, wide), (614, y))
                self.assertEqual(packet[0], 0x80)

    def test_tablet_rows_count_from_the_bottom_edge(self):
        # Top picture line -> highest row, bottom line -> row 0, as on the tablet.
        self.assertEqual(firmware_point(native_tablet_packet(614, 0, True, wide=False)), (614, 599))
        self.assertEqual(firmware_point(native_tablet_packet(614, 599, True, wide=False)), (614, 0))
        self.assertEqual(firmware_point(native_tablet_packet(614, 284, True, wide=False)), (614, 315))

    def test_invalid_input_is_rejected_before_encoding(self):
        for point in ((-1, 100), (800, 100), (100, 600), (True, 100)):
            with self.assertRaises(ValueError):
                native_tablet_packet(*point, True, wide=False)

    def test_cx_requires_liftoff_and_ignores_duplicate_release(self):
        controller = VirtualTouchController()
        self.assertEqual(controller.command(b"\x01CX\r"), ACK)
        self.assertIsNone(controller.touch(100, 525, False, wide=False))
        for _ in range(3):
            self.assertIsNone(controller.touch(100, 525, True, wide=False))
        self.assertEqual(controller.touch(100, 525, False, wide=False), POINT_ACK)
        self.assertIsNone(controller.touch(100, 525, False, wide=False))
        self.assertTrue(controller.calibrating)
        self.assertIsNone(controller.points)

    def test_calibration_uses_last_held_position_not_release_cursor(self):
        controller = VirtualTouchController()
        controller.command(b"\x01CX\r")
        controller.touch(10, 550, True, wide=False)
        controller.touch(100, 525, True, wide=False)
        self.assertEqual(controller.touch(0, 0, False, wide=False), POINT_ACK)
        self.assertEqual(controller.first_point, (100, 525))

    def test_identity_calibration_leaves_screen_points_unchanged(self):
        controller = VirtualTouchController()
        self.calibrate(controller)
        for point in ((0, 0), (400, 300), (614, 284), (799, 599)):
            self.assertEqual(controller.screen_point(*point), point)

    def test_affine_calibration_is_applied_once_before_both_encoders(self):
        controller = VirtualTouchController()
        self.calibrate(controller, (120, 500), (680, 100))
        for wide in (False, True):
            for physical, expected in (((120, 500), (100, 525)),
                                       ((680, 100), (700, 75)), ((400, 300), (400, 300))):
                self.assertEqual(controller.screen_point(*physical), expected)
                self.assertEqual(decode_native(controller.touch(*physical, True, wide=wide), wide), expected)
        self.assertEqual(controller.screen_point(0, 599), (0, 599))

    def test_rejected_second_point_retains_previous_calibration(self):
        controller = VirtualTouchController()
        self.calibrate(controller)
        previous = controller.points
        controller.command(b"\x01CX\r")
        controller.touch(100, 525, True, wide=False)
        controller.touch(100, 525, False, wide=False)
        controller.touch(110, 520, True, wide=False)
        self.assertEqual(controller.touch(110, 520, False, wide=False), ACK)
        self.assertEqual(controller.points, previous)
        self.assertFalse(controller.calibrating)
        self.assertTrue(any("REJECTED" in event for event in controller.events))

    def test_rejected_first_point_and_reset_do_not_replace_saved_values(self):
        controller = VirtualTouchController()
        self.calibrate(controller)
        previous = controller.points
        controller.command(b"\x01CX\r")
        controller.touch(700, 75, True, wide=False)
        self.assertEqual(controller.touch(700, 75, False, wide=False), ACK)
        controller.command(b"\x01CX\r")
        self.assertEqual(controller.command(b"\x01R\r"), ACK)
        self.assertFalse(controller.calibration_session)
        self.assertEqual(controller.points, previous)

    def test_calibration_persists_per_image_and_reset_retains_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "work image.img.touch.json"
            controller = VirtualTouchController(path)
            self.calibrate(controller, (120, 500), (680, 100))
            loaded = VirtualTouchController(path)
            self.assertEqual(loaded.points, controller.points)
            loaded.command(b"\x01R\r")
            self.assertEqual(loaded.points, controller.points)
            self.assertEqual(json.loads(path.read_text())["surface"], [800, 600])
            self.assertFalse(path.with_name(path.name + ".tmp").exists())
            self.assertIsNone(VirtualTouchController(Path(directory) / "other.img.touch.json").points)

    def test_factory_defaults_clear_persisted_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "work.touch.json"
            controller = VirtualTouchController(path)
            self.calibrate(controller)
            self.assertEqual(controller.command(b"\x01RD\r"), ACK)
            self.assertIsNone(VirtualTouchController(path).points)

    def test_reset_state_reports_exact_mapping_instead_of_a_loaded_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "work.touch.json"
            controller = VirtualTouchController(path)
            self.calibrate(controller)
            self.assertEqual(VirtualTouchController(path).events, ["DB_TOUCH_CALIBRATION_LOADED"])
            controller.reset_calibration()
            self.assertEqual(VirtualTouchController(path).events, ["DB_TOUCH_CALIBRATION_EXACT"])

    def test_invalid_state_is_not_applied_or_automatically_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "work.touch.json"
            for text in ("invalid json", "[]", "null", "x" * 2049, json.dumps({"version": 7}),
                         json.dumps({"version": 1, "surface": [800, 600], "points": [[1, 2], [1, 2]]})):
                path.write_text(text)
                controller = VirtualTouchController(path)
                self.assertIsNone(controller.points)
                self.assertEqual(path.read_text(), text)
                self.assertTrue(any("STORAGE_WARNING" in event for event in controller.events))

    def test_stale_temporary_file_is_preserved_and_failure_is_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "work.touch.json"
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text("foreign data")
            controller = VirtualTouchController(path)
            self.calibrate(controller)
            self.assertEqual(temporary.read_text(), "foreign data")
            self.assertFalse(path.exists())
            self.assertTrue(any("STORAGE_WARNING" in event for event in controller.events))

    def test_missing_storage_directory_does_not_crash_touch(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = VirtualTouchController(Path(directory) / "absent" / "work.touch.json")
            self.calibrate(controller)
            self.assertTrue(any("STORAGE_WARNING" in event for event in controller.events))

    def test_geometry_events_only_occur_on_mode_changes(self):
        controller = VirtualTouchController()
        for wide in (False, False, True, True):
            controller.touch(400, 300, True, wide=wide)
        self.assertEqual([event for event in controller.events if "GEOMETRY" in event],
                         ["DB_TOUCH_GEOMETRY width=800 crop=0", "DB_TOUCH_GEOMETRY width=960 crop=80"])

    def test_busy_rx_does_not_consume_contact_or_change_calibration(self):
        controller = VirtualTouchController()
        stream = bridge.TouchPacketStream()
        stream.request(100, 525, True)
        rsp = MemoryRsp()
        rsp.memory[bridge.TOUCH_UART_RX_COUNT] = 3
        self.assertEqual(bridge.service_touch_command(rsp, controller, b"\x01CX\r"), (False, None))
        self.assertFalse(controller.calibrating)
        self.assertIsNone(bridge.deliver_touch_packet(rsp, controller, stream))
        self.assertEqual(len(stream.packets), 1)
        self.assertEqual(rsp.writes, [])

    def test_normal_delivery_samples_current_mode_not_enqueue_time(self):
        controller = VirtualTouchController()
        stream = bridge.TouchPacketStream()
        stream.request(614, 284, True)
        rsp = MemoryRsp(wide=True)
        rsp.memory[bridge.TOUCH_UART_RX_INDEX] = 30
        delivered = bridge.deliver_touch_packet(rsp, controller, stream)
        self.assertEqual(decode_native(delivered[3], True), (614, 284))
        self.assertEqual(delivered[4], (614, 284))
        self.assertEqual(rsp.writes[-1], (bridge.TOUCH_UART_RX_COUNT, b"\x05"))
        self.assertEqual(len(rsp.writes), 3)
        self.assertEqual(len(stream.packets), 0)

    def test_calibration_delivery_is_one_ascii_ack_per_liftoff_not_tablet(self):
        controller = VirtualTouchController()
        rsp = MemoryRsp()
        stream = bridge.TouchPacketStream()
        self.assertEqual(bridge.service_touch_command(rsp, controller, b"\x01CX\r"), (True, ACK))
        rsp.consume()
        for index, point in enumerate(TARGETS):
            ticks = index * 1000
            stream.request(*point, True)
            stream.request(*point, False)
            self.assertIsNone(bridge.deliver_touch_packet(rsp, controller, stream, ticks)[3])
            delivered = bridge.deliver_touch_packet(rsp, controller, stream,
                                                    ticks + bridge.TouchPacketStream.MIN_HOLD_TICKS)
            self.assertEqual(delivered[3], POINT_ACK)
            self.assertTrue(delivered[5])
            self.assertIsNone(delivered[4])
            rsp.consume()
        stream.request(614, 284, True)
        self.assertIsNone(bridge.deliver_touch_packet(rsp, controller, stream, 5000))
        controller.finish()
        self.assertEqual(decode_native(bridge.deliver_touch_packet(rsp, controller, stream, 5000)[3]), (614, 284))

    def test_busy_point_ack_is_retried_without_duplicate_calibration_event(self):
        controller = VirtualTouchController()
        controller.command(b"\x01CX\r")
        stream = bridge.TouchPacketStream()
        stream.request(100, 525, True)
        stream.request(100, 525, False)
        rsp = MemoryRsp()
        bridge.deliver_touch_packet(rsp, controller, stream)
        rsp.memory[bridge.TOUCH_UART_RX_COUNT] = 3
        for _ in range(3):
            self.assertIsNone(bridge.deliver_touch_packet(rsp, controller, stream))
        self.assertIsNone(controller.first_point)
        rsp.consume()
        self.assertEqual(bridge.deliver_touch_packet(rsp, controller, stream,
                                                     bridge.TouchPacketStream.MIN_HOLD_TICKS)[3], POINT_ACK)
        self.assertEqual(sum("CALIBRATION_POINT" in event for event in controller.events), 1)

    def test_short_click_is_held_long_enough_for_the_firmware(self):
        # A mouse click queues down and up together. The firmware debounces
        # contacts, so the release waits for firmware time to pass.
        controller = VirtualTouchController()
        rsp = MemoryRsp()
        stream = bridge.TouchPacketStream()
        stream.request(400, 300, True)
        stream.request(400, 300, False)
        down = bridge.deliver_touch_packet(rsp, controller, stream, 1000)
        self.assertTrue(down[2])
        rsp.consume()
        hold = bridge.TouchPacketStream.MIN_HOLD_TICKS
        # Long enough to register as a press, short enough that the press and
        # liftoff reports stay within one cabinet tap (31-47 ms apart).
        self.assertTrue(30 <= hold <= 50)
        self.assertIsNone(bridge.deliver_touch_packet(rsp, controller, stream, 1000 + hold - 1))
        self.assertEqual(len(stream.packets), 1)
        release = bridge.deliver_touch_packet(rsp, controller, stream, 1000 + hold)
        self.assertFalse(release[2])
        self.assertEqual(decode_native(release[3]), (400, 300))
        # A drag delivers its moves immediately; only the release is held,
        # and the hold counts from the first contact sample.
        rsp.consume()
        stream.request(100, 300, True)
        stream.request(300, 300, True)
        stream.request(300, 300, False)
        self.assertTrue(bridge.deliver_touch_packet(rsp, controller, stream, 5000)[2])
        rsp.consume()
        self.assertTrue(bridge.deliver_touch_packet(rsp, controller, stream, 5001)[2])
        rsp.consume()
        self.assertIsNone(bridge.deliver_touch_packet(rsp, controller, stream, 5002))
        self.assertFalse(bridge.deliver_touch_packet(rsp, controller, stream, 5000 + hold)[2])

    def test_quick_second_tap_waits_until_the_liftoff_was_seen(self):
        # Two clicks queued at once must reach the firmware as press, release,
        # press, release with time between each, never as press, press.
        controller = VirtualTouchController()
        rsp = MemoryRsp()
        stream = bridge.TouchPacketStream()
        for _ in range(2):
            stream.request(400, 300, True)
            stream.request(400, 300, False)
        hold = bridge.TouchPacketStream.MIN_HOLD_TICKS
        gap = bridge.TouchPacketStream.MIN_RELEASE_TICKS
        self.assertTrue(bridge.deliver_touch_packet(rsp, controller, stream, 1000)[2])
        rsp.consume()
        self.assertFalse(bridge.deliver_touch_packet(rsp, controller, stream, 1000 + hold)[2])
        rsp.consume()
        self.assertIsNone(bridge.deliver_touch_packet(rsp, controller, stream, 1000 + hold + gap - 1))
        self.assertTrue(bridge.deliver_touch_packet(rsp, controller, stream, 1000 + hold + gap)[2])
        rsp.consume()
        self.assertIsNone(bridge.deliver_touch_packet(rsp, controller, stream, 1000 + 2 * hold + gap - 1))
        self.assertFalse(bridge.deliver_touch_packet(rsp, controller, stream, 1000 + 2 * hold + gap)[2])
        self.assertEqual(len(stream.packets), 0)

    def test_coordinate_fallback_uses_calibrated_screen_point_once(self):
        controller = VirtualTouchController()
        self.calibrate(controller, (120, 500), (680, 100))
        stream = bridge.TouchPacketStream()
        stream.request(120, 500, True)
        delivered = bridge.deliver_touch_packet(MemoryRsp(wide=True), controller, stream)
        forwarder = bridge.TouchClickForwarder()
        frame = bytes.fromhex("01 02 41 00 3F 2C 00 00 00 00 04")
        outgoing = b"".join(forwarder.feed(bytes([byte]), delivered[4])[0] for byte in frame)
        # Calibrated picture point 100,525 as column and row from the bottom edge.
        self.assertEqual(outgoing[6:10], (100).to_bytes(2, "little") + (599 - 525).to_bytes(2, "little"))

    def test_invalid_rx_index_cannot_mutate_calibration_or_drop_contact(self):
        controller = VirtualTouchController()
        stream = bridge.TouchPacketStream()
        stream.request(100, 525, True)
        rsp = MemoryRsp()
        rsp.memory[bridge.TOUCH_UART_RX_INDEX] = 32
        with self.assertRaises(RuntimeError):
            bridge.service_touch_command(rsp, controller, b"\x01CX\r")
        with self.assertRaises(RuntimeError):
            bridge.deliver_touch_packet(rsp, controller, stream)
        self.assertFalse(controller.calibrating)
        self.assertEqual(len(stream.packets), 1)
        self.assertEqual(rsp.writes, [])


if __name__ == "__main__":
    unittest.main()
