import datetime as dt
import importlib.util
import struct
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "vidcom_init_bridge.py"
SPEC = importlib.util.spec_from_file_location("vidcom_init_bridge", MODULE_PATH)
assert SPEC and SPEC.loader
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


class InitVideoFrameTests(unittest.TestCase):
    def test_emulator_session_allows_only_ordered_protocol_phases(self) -> None:
        session = BRIDGE.EmulatorSession()
        expected = (
            BRIDGE.EmulatorPhase.WAITING_FOR_INIT,
            BRIDGE.EmulatorPhase.INITIALIZED,
            BRIDGE.EmulatorPhase.DISCOVERY,
            BRIDGE.EmulatorPhase.STARTUP_TEXT,
            BRIDGE.EmulatorPhase.RENDERING,
            BRIDGE.EmulatorPhase.INTERACTIVE,
        )
        for phase in expected:
            session.transition(phase)
        self.assertEqual(session.phase, BRIDGE.EmulatorPhase.INTERACTIVE)
        with self.assertRaisesRegex(RuntimeError, "invalid emulator transition"):
            session.transition(BRIDGE.EmulatorPhase.DISCOVERY)

    def test_serial_sender_chunks_and_clocks_frame_at_9600_baud(self) -> None:
        class FakeSocket:
            def __init__(self):
                self.chunks = []

            def sendall(self, data):
                self.chunks.append(data)

        sock = FakeSocket()
        frame = bytes(range(20))
        with patch.object(BRIDGE.time, "sleep") as sleep:
            BRIDGE.send_serial_frame(sock, frame, baud=9600, chunk_size=8)

        self.assertEqual(sock.chunks, [frame[:8], frame[8:16], frame[16:]])
        self.assertEqual(sleep.call_count, 2)
        self.assertAlmostEqual(sleep.call_args_list[0].args[0], 8 * 10 / 9600)
        self.assertAlmostEqual(sleep.call_args_list[1].args[0], 8 * 10 / 9600)

    def test_default_serial_sender_uses_single_byte_chunks(self) -> None:
        self.assertEqual(BRIDGE.DEFAULT_SERIAL_CHUNK_SIZE, 1)

    def test_bridge_lock_rejects_second_sender(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge.lock"
            first = BRIDGE.acquire_single_instance_lock(path)
            try:
                with self.assertRaisesRegex(RuntimeError, "another VidCom bridge"):
                    BRIDGE.acquire_single_instance_lock(path)
            finally:
                first.close()
            replacement = BRIDGE.acquire_single_instance_lock(path)
            replacement.close()

    def test_frame_layout_and_explicit_time_substitution(self) -> None:
        when = dt.datetime(2026, 9, 12, 11, 47)
        frame = BRIDGE.build_initvideo_frame(when)

        self.assertEqual(len(frame), 39)
        self.assertEqual(frame[:4], b"\x01\x02\x22\x00")
        self.assertEqual(frame[-1], 0x04)
        self.assertEqual(frame[4:24], BRIDGE.RECORDED_INITVIDEO_PAYLOAD[:20])
        self.assertEqual(frame[30:-1], BRIDGE.RECORDED_INITVIDEO_PAYLOAD[26:])
        self.assertEqual(struct.unpack("<HBBBB", frame[24:30]), (2026, 9, 12, 11, 47))

    def test_default_time_is_selected_historical_database_time(self) -> None:
        frame = BRIDGE.build_initvideo_frame()
        self.assertEqual(struct.unpack("<HBBBB", frame[24:30]), (2012, 2, 1, 22, 14))

    def test_original_identity_and_video_configuration_are_unchanged(self) -> None:
        payload = BRIDGE.build_initvideo_payload(dt.datetime(2026, 1, 2, 3, 4))
        self.assertEqual(struct.unpack_from("<I", payload, 0)[0], 20121212)
        self.assertEqual(struct.unpack_from("<H", payload, 4)[0], 83)
        self.assertEqual(struct.unpack_from("<H", payload, 6)[0], 12536)
        self.assertEqual(struct.unpack_from("<H", payload, 8)[0], 1581)
        self.assertEqual(struct.unpack_from("<H", payload, 10)[0], 5007)
        self.assertEqual(struct.unpack_from("<H", payload, 12)[0], 11327)
        self.assertEqual(payload[26:], bytes.fromhex("13 11 00 00 01 00 02 FF"))

    def test_safe_discovery_matches_evidenced_command_order(self) -> None:
        commands = [item[0] for item in BRIDGE.SAFE_DISCOVERY_SEQUENCE]
        payloads = [item[1] for item in BRIDGE.SAFE_DISCOVERY_SEQUENCE]
        self.assertEqual(commands, [79, 73] + [79] * 9)
        self.assertEqual(payloads, [b"\x09\x00", b"\x00"] + [bytes([i, 0]) for i in range(9)])

    def test_safe_discovery_excludes_request_key_and_monetary_commands(self) -> None:
        commands = {item[0] for item in BRIDGE.SAFE_DISCOVERY_SEQUENCE}
        self.assertEqual(commands, {73, 79})
        self.assertNotIn(69, commands)
        for command, payload, _ in BRIDGE.SAFE_DISCOVERY_SEQUENCE:
            frame = BRIDGE.build_fixed_frame(command, payload)
            self.assertEqual(frame[:2], b"\x01\x02")
            self.assertEqual(struct.unpack("<H", frame[2:4])[0], command)
            self.assertEqual(frame[4:-1], payload)
            self.assertEqual(frame[-1], 4)

    def test_init_ack_signature_matches_split_live_capture(self) -> None:
        chunks = (b"\x06", b"\x03\x00", b"\x00\x02\x01\x85\x2d")
        received = bytearray()
        matched = []
        for chunk in chunks:
            received.extend(chunk)
            matched.append(BRIDGE.INITVIDEO_ACK in received)
        self.assertEqual(matched, [False, False, True])

    def test_ack_tracker_counts_split_and_coalesced_acknowledgements(self) -> None:
        tracker = BRIDGE.AckTracker()
        self.assertEqual(tracker.feed(b"\x06\x03"), 0)
        self.assertEqual(tracker.feed(b"\x00\x00noise\x06\x03\x00\x00"), 2)
        self.assertEqual(tracker.snapshot(), 2)
        self.assertTrue(tracker.wait_after(1, 0.01))
        self.assertFalse(tracker.wait_after(2, 0.01))

    def test_nak_takes_priority_over_coalesced_ack(self) -> None:
        tracker = BRIDGE.AckTracker()
        previous_ack, previous_nak = tracker.response_snapshot()
        self.assertEqual(tracker.feed(b"\x15\x03"), 0)
        self.assertEqual(tracker.feed(b"\x00\x05\x06\x03\x00\x00"), 1)
        self.assertEqual(
            tracker.wait_response_after(previous_ack, previous_nak, 0.01),
            ("nak", 5),
        )
        self.assertEqual(tracker.last_nak(), (1, 5))

    def test_data_frame_tracker_decodes_fragmented_systeminfo_reply(self) -> None:
        tracker = BRIDGE.DataFrameTracker()
        previous = tracker.snapshot(BRIDGE.VID_COM_SYSTEMINFO)
        self.assertEqual(tracker.feed(b"noise\x07\x06\x4f"), [])
        frames = tracker.feed(b"\x00M90-")
        self.assertEqual(frames, [(BRIDGE.VID_COM_SYSTEMINFO, b"M90-")])
        self.assertTrue(tracker.wait_after(BRIDGE.VID_COM_SYSTEMINFO, previous, 0.01))

    def test_data_frame_tracker_resynchronizes_after_ack(self) -> None:
        tracker = BRIDGE.DataFrameTracker()
        frames = tracker.feed(b"\x06\x03\x00\x00\x07\x04\x4f\x00\x32\x00")
        self.assertEqual(frames, [(BRIDGE.VID_COM_SYSTEMINFO, b"\x32\x00")])

    def test_startup_text_payloads_match_historical_records(self) -> None:
        expected = (
            "2C000000FF00" + "ADP1486             ".encode("utf-16le").hex(),
            "26000000FF00" + "Ergoline  Mode: 3".encode("utf-16le").hex(),
            "1A000000FF00" + "TOUCH<B8N1>".encode("utf-16le").hex(),
            "3A000000FF00" + "MP WHM9A01.216 Wck921.13 v5".encode("utf-16le").hex(),
            "1E0000FF0000" + "KEIN AKZEPTOR".encode("utf-16le").hex(),
            "200000FF0000" + "KEIN DISPENSER".encode("utf-16le").hex(),
        )
        actual = []
        for _, message, clear, red, green, blue in BRIDGE.SAFE_STARTUP_TEXT_SEQUENCE:
            payload = BRIDGE.build_startup_text_payload(
                message, clear=clear, red=red, green=green, blue=blue
            )
            actual.append(payload.hex().upper())
        self.assertEqual(tuple(actual), tuple(item.upper() for item in expected))

    def test_owner_render_prefix_requires_matching_hash_and_allowlist(self) -> None:
        def make_record(tick, command, payload):
            return struct.pack("<IHH", tick, command, len(payload)) + payload + b"\r\n"

        # Source order is newest first, like the real VidCom logs.
        data = (
            make_record(
                BRIDGE.SAFE_RENDER_END_TICK,
                64,
                b"\x03\x00\x34\x12\x66abc",
            )
            + make_record(BRIDGE.SAFE_RENDER_START_TICK, 57, b"\x3f\x2c")
        )
        import hashlib

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.part_01.txt"
            path.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            actual_digest, records = BRIDGE.load_safe_render_prefix(path, digest)
            self.assertEqual(actual_digest, digest.upper())
            self.assertEqual([item.tick for item in records], [66343, 74718])
            self.assertEqual([item.command for item in records], [57, 64])
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                BRIDGE.load_safe_render_prefix(path, "00" * 32)

    def test_owner_render_prefix_rejects_request_key(self) -> None:
        import hashlib

        def make_record(tick, command):
            payload = b"\x00"
            return struct.pack("<IHH", tick, command, len(payload)) + payload + b"\r\n"

        data = make_record(74718, 64) + make_record(70000, 69) + make_record(66343, 57)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.txt"
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "non-allowlisted.*69"):
                BRIDGE.load_safe_render_prefix(path, hashlib.sha256(data).hexdigest())

    def test_display_continuation_stops_before_touch_input(self) -> None:
        import hashlib

        def make_record(tick, command, payload=b"\x00"):
            return struct.pack("<IHH", tick, command, len(payload)) + payload + b"\r\n"

        data = (
            make_record(BRIDGE.SAFE_CONTINUATION_END_TICK, 55)
            + make_record(320000, 64, b"\x03\x00\x34\x12\x66abc")
            + make_record(BRIDGE.SAFE_CONTINUATION_START_TICK, 58)
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.part_00.txt"
            path.write_bytes(data)
            _, records = BRIDGE.load_safe_display_continuation(
                path, hashlib.sha256(data).hexdigest()
            )
            self.assertEqual([item.tick for item in records], [314890, 320000, 373156])
            self.assertNotIn(65, [item.command for item in records])

    def test_display_continuation_rejects_touch_input(self) -> None:
        import hashlib

        def make_record(tick, command):
            payload = b"\x00"
            return struct.pack("<IHH", tick, command, 1) + payload + b"\r\n"

        data = (
            make_record(BRIDGE.SAFE_CONTINUATION_END_TICK, 55)
            + make_record(320000, 65)
            + make_record(BRIDGE.SAFE_CONTINUATION_START_TICK, 58)
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.part_00.txt"
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "non-allowlisted.*65"):
                BRIDGE.load_safe_display_continuation(
                    path, hashlib.sha256(data).hexdigest()
                )

    def test_rejects_varipara_with_inconsistent_numwerte(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload size mismatch"):
            BRIDGE.validate_evidenced_payload(
                BRIDGE.VID_COM_COMMAND_VARIPARA,
                struct.pack("<H", 486) + b"\x3F\x2C\x66\x00",
            )

    def test_describes_menu_setactivegames_batch(self) -> None:
        payload = (
            struct.pack(
                "<HHB",
                6,
                BRIDGE.GO_MENUE,
                BRIDGE.GAME_MENUE_SETACTIVEGAMES,
            )
            + b"\x01\x00\x01\x02\x00\x01"
        )
        BRIDGE.validate_evidenced_payload(BRIDGE.VID_COM_COMMAND_VARIPARA, payload)
        description = BRIDGE.describe_evidenced_payload(
            BRIDGE.VID_COM_COMMAND_VARIPARA, payload
        )
        self.assertIn("Menue.SETACTIVEGAMES entries=2", description)

    def test_rejects_partial_menu_setactivegames_entry(self) -> None:
        payload = struct.pack(
            "<HHB",
            1,
            BRIDGE.GO_MENUE,
            BRIDGE.GAME_MENUE_SETACTIVEGAMES,
        ) + b"\x00"
        with self.assertRaisesRegex(ValueError, "three-byte entries"):
            BRIDGE.validate_evidenced_payload(
                BRIDGE.VID_COM_COMMAND_VARIPARA, payload
            )

    def test_owner_records_stop_only_on_exact_tick_boundary(self) -> None:
        records = [
            type("Record", (), {"tick": 10})(),
            type("Record", (), {"tick": 20})(),
            type("Record", (), {"tick": 30})(),
        ]
        selected, stopped = BRIDGE.owner_records_through_tick(records, 20)
        self.assertTrue(stopped)
        self.assertEqual([item.tick for item in selected], [10, 20])

        selected, stopped = BRIDGE.owner_records_through_tick(records, 25)
        self.assertFalse(stopped)
        self.assertEqual([item.tick for item in selected], [10, 20, 30])

    def test_owner_records_skip_only_selected_validated_ticks(self) -> None:
        records = [
            type("Record", (), {"tick": 10})(),
            type("Record", (), {"tick": 20})(),
            type("Record", (), {"tick": 30})(),
        ]
        selected = BRIDGE.owner_records_without_ticks(records, [20])
        self.assertEqual([item.tick for item in selected], [10, 30])


if __name__ == "__main__":
    unittest.main()
