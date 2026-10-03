import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import event_log_viewer as viewer


class EventLogViewerTests(unittest.TestCase):
    def test_touch_calibration_events_and_geometry_keep_raw_details(self) -> None:
        parser = viewer.BridgeLogParser()
        for line, title, level in (
            ("DB_TOUCH_CALIBRATION_STARTED", "gestartet", "normal"),
            ("DB_TOUCH_CALIBRATION_POINT index=1 x=100 y=525", "Loslassen", "normal"),
            ("DB_TOUCH_CALIBRATION_COMPLETED", "abgeschlossen", "normal"),
            ("DB_TOUCH_CALIBRATION_REJECTED", "ungültig", "warning"),
            ("DB_TOUCH_CALIBRATION_STORAGE_WARNING reason=denied", "gespeichert", "warning"),
            ("DB_TOUCH_GEOMETRY width=960 crop=80", "Bildschirmmodus", "normal"),
        ):
            event = parser.feed(line)[0]
            self.assertIn(title, event.title)
            self.assertEqual(event.details, line)
            self.assertEqual(event.level, level)

    def test_rtc_fault_snapshot_is_visible_and_retains_native_values(self) -> None:
        line = ("DB_RTC_FAULT_SNAPSHOT rtc=2013-02-01T22:14:00 "
                "source_return=0007A190 calendar=160E0001020D00 invalid_flag=01")
        event = viewer.BridgeLogParser().feed(line)[0]
        self.assertEqual(event.direction, "Fehler")
        self.assertEqual(event.level, "warning")
        self.assertIn("F_UHR", event.title)
        self.assertEqual(event.details, line)

    def test_log_encoding_detects_windows_powershell_utf16(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "events.log"
            path.write_text("DB_FRAME data=01 02 22 04\n", encoding="utf-16")
            encoding = viewer.detect_log_encoding(path)
            self.assertEqual(encoding, "utf-16")
            with path.open("r", encoding=encoding) as source:
                self.assertEqual(source.readline(), "DB_FRAME data=01 02 22 04\n")

    def test_idle_status_is_visible_but_throttled(self) -> None:
        parser = viewer.BridgeLogParser()
        self.assertEqual(
            parser.feed("DB_IDLE_PROTOCOL_SNAPSHOT PC=00060442", now=1.0)[0].direction,
            "Status",
        )
        self.assertEqual(
            parser.feed("DB_IDLE_PROTOCOL_SNAPSHOT PC=00060442", now=5.0),
            [],
        )
        self.assertEqual(
            len(parser.feed("DB_IDLE_PROTOCOL_SNAPSHOT PC=00060442", now=11.0)),
            1,
        )

    def test_display_frame_is_readable_and_retains_raw_bytes(self) -> None:
        frame = (
            b"\x01\x02\x46\x00\x10\x00\x00\xFF\x00\x00"
            + "FOUL F".encode("utf-16-le")
            + b"\x04"
        )
        event = viewer.describe_frame(frame)
        self.assertEqual(event.direction, "DB → PC")
        self.assertEqual(event.title, "Anzeigetext: FOUL F")
        self.assertIn("46 00 10 00", event.details)

    def test_original_command_names_remain_attached_to_hex_frames(self) -> None:
        event = viewer.describe_frame(bytes.fromhex("01 02 3E 00 81 2D 01 04"))
        self.assertEqual(event.title, "VIEW (0x3E)")
        self.assertIn("81 2D 01", event.details)
        following = viewer.describe_frame(bytes.fromhex("01 02 3A 00 31 2E 04"))
        self.assertEqual(following.title, "DESTROY (0x3A)")

    def test_turbobuchen_sound_is_a_visible_readiness_signal(self) -> None:
        parser = viewer.BridgeLogParser()
        event = parser.feed("DB_FRAME data=01 02 42 00 ED 00 04")[0]
        self.assertEqual(event.level, "ready")
        self.assertIn("Turbobuchen", event.title)
        self.assertIn("keine Bestaetigung", event.details)
        self.assertEqual(parser.ready_signal_count, 1)
        other = parser.feed("DB_FRAME data=01 02 42 00 EC 00 04")[0]
        self.assertEqual(other.title, "PLAYSOUND (0x42)")
        self.assertEqual(parser.ready_signal_count, 1)

    def test_coin_validator_session_and_reply_parser_are_visible(self) -> None:
        parser = viewer.BridgeLogParser()
        sent = parser.feed(
            "DB_SCC_B_TX_FRAME data=07 7B 18 9D 5A 37 B7 78"
        )
        self.assertEqual(sent[0].direction, "DB → Prüfer")
        self.assertIn("Sessionpaket", sent[0].title)
        reply = parser.feed("DB_VIRTUAL_MP_REPLY data=00 00")
        self.assertEqual(reply[0].direction, "Prüfer → DB")
        self.assertIn("Sessionpaket", reply[0].title)
        self.assertIn("00 00", reply[0].details)

    def test_coin_validator_challenge_override_is_not_labeled_as_real_match(self) -> None:
        parser = viewer.BridgeLogParser()
        event = parser.feed(
            "DB_VIRTUAL_MP_CHALLENGE_COMPARE register_override=True "
            "received_before_override=0000 forced_expected=80F3 remaining=0"
        )[0]
        self.assertEqual(event.direction, "Diagnose")
        self.assertIn("Registereingriff", event.title)
        self.assertIn("received_before_override=0000", event.details)

    def test_coin_validator_challenge_wire_match_is_visible(self) -> None:
        parser = viewer.BridgeLogParser()
        event = parser.feed(
            "DB_VIRTUAL_MP_CHALLENGE_COMPARE wire_match=True "
            "register_override=False received_before_override=7887 "
            "forced_expected=7887 remaining=0"
        )[0]
        self.assertEqual(event.direction, "Prüfer → DB")
        self.assertIn("auf der Leitung bestätigt", event.title)

    def test_coin_validator_type_probe_is_visible(self) -> None:
        parser = viewer.BridgeLogParser()
        event = parser.feed("DB_SCC_B_TX_FRAME data=03 7F 04 83")[0]
        self.assertEqual(event.direction, "DB → Prüfer")
        self.assertIn("Typabfrage", event.title)

    def test_coin_validator_identity_and_board_exchange_are_visible(self) -> None:
        parser = viewer.BridgeLogParser()
        identity = parser.feed("DB_VIRTUAL_MP_REPLY data=4E 52 49 00 00")
        self.assertEqual(identity[0].title, "Virtueller Prüfer meldet NRI")
        profile = (
            b"NRI" + bytes(7) + b"eagle" + bytes(5) + b"FT30" + bytes(10)
        )
        detailed = parser.feed(f"DB_VIRTUAL_MP_REPLY data={profile.hex(' ').upper()}")
        self.assertIn("NRI eagle / FT30", detailed[0].title)
        request = parser.feed("DB_AUX_TRANSACTION_START command=35 length=03")
        response = parser.feed("DB_VIRTUAL_AUX_REPLY command=35 wire=00")
        self.assertEqual(request[0].direction, "DB → Board")
        self.assertEqual(response[0].direction, "Board → DB")

    def test_exact_frame_replaces_bytewise_fallback(self) -> None:
        parser = viewer.BridgeLogParser()
        for value in (1, 2, 0x22, 4):
            self.assertEqual(
                parser.feed(f"DB_TO_COM3 {value:02X} pc=000C5F3C"), []
            )
        events = parser.feed("DB_FRAME data=01 02 22 04")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "INITVIDEO 1: Boardprofil gesendet")
        self.assertEqual(parser.feed("DB_WAITING_FOR_COM3"), [])

    def test_second_initvideo_is_labeled_and_matched_to_pc_ack(self) -> None:
        parser = viewer.BridgeLogParser()
        first = parser.feed(
            "DB_INITVIDEO_BOARD_PROFILE_COMPLETED length=39 data=01 02 22 04"
        )
        self.assertEqual(first[0].title, "INITVIDEO 1: Originalprofil gesendet")
        parser.feed("COM3_TO_DB 06 03 00 00", now=1.0)
        ack = parser.feed("DB_WAITING_FOR_COM3", now=1.1)
        self.assertEqual(ack[0].title, "INITVIDEO 1: vom PC bestätigt (ACK)")

        second = parser.feed("DB_FRAME data=01 02 22 00 04")
        self.assertEqual(second[0].title, "INITVIDEO 2: Boardprofil gesendet")
        parser.feed("COM3_TO_DB 06 03 00 00", now=2.0)
        ack = parser.feed("DB_WAITING_FOR_COM3", now=2.1)
        self.assertEqual(ack[0].title, "INITVIDEO 2: vom PC bestätigt (ACK)")

    def test_clock_completion_displays_reported_year(self) -> None:
        parser = viewer.BridgeLogParser()
        events = parser.feed("DB_INITVIDEO_CLOCK_COMPLETED year=2012 data=01 02")
        self.assertEqual(events[0].title, "INITVIDEO-Uhr ergänzt: 2012")

    def test_clock_events_use_selected_date_not_hardcoded_2012(self) -> None:
        parser = viewer.BridgeLogParser()
        for marker, field in (("DB_INITVIDEO_CLOCK_COMPLETED", "time"),
                              ("DB_RTC4543_ENABLED", "initial")):
            with self.subTest(marker=marker):
                events = parser.feed(f"{marker} {field}=2026-10-03T12:34:00")
                self.assertIn("2026-10-03T12:34:00", events[0].title)
                self.assertNotIn("2012", events[0].title)
                events = parser.feed(marker)
                self.assertNotIn("2012", events[0].title)

    def test_r4543_events_are_visible(self) -> None:
        parser = viewer.BridgeLogParser()
        enabled = parser.feed("DB_RTC4543_ENABLED initial=2012-02-01T22:14:00")
        read = parser.feed("DB_RTC4543_READ count=1 bcd=00 14 22 04 01 02 12")
        self.assertIn("R4543", enabled[0].title)
        self.assertIn("R4543", read[0].title)

    def test_config_ram_event_is_visible(self) -> None:
        parser = viewer.BridgeLogParser()
        events = parser.feed("DB_CONFIG_RAM_PROGRAMMED sha256=ABC")
        self.assertIn("Config", events[0].title)

    def test_first_completed_frame_does_not_duplicate_byte_log(self) -> None:
        parser = viewer.BridgeLogParser()
        for value in (1, 2, 0x22, 4):
            parser.feed(f"DB_TO_COM3 {value:02X} pc=000C5F3C")
        events = parser.feed("DB_INITVIDEO_BOARD_PROFILE_COMPLETED data=01 02 22 04")
        self.assertEqual(len(events), 1)
        self.assertEqual(parser.initvideo_count, 1)

    def test_legacy_frame_can_contain_one_and_is_published_before_ack(self) -> None:
        parser = viewer.BridgeLogParser()
        for value in (1, 2, 0x22, 0, 1, 0x58, 4):
            parser.feed(f"DB_TO_COM3 {value:02X} pc=000C5F3C")
        sent = parser.feed("COM3_TO_DB 06 03 00 00", now=1.0)
        self.assertEqual(sent[0].title, "INITVIDEO 1: Boardprofil gesendet")
        ack = parser.feed("DB_WAITING_FOR_COM3", now=1.1)
        self.assertEqual(ack[0].title, "INITVIDEO 1: vom PC bestätigt (ACK)")

    def test_pc_fragments_are_grouped_and_ack_is_labeled(self) -> None:
        parser = viewer.BridgeLogParser()
        self.assertEqual(parser.feed("COM3_TO_DB 06 03", now=1.0), [])
        self.assertEqual(parser.feed("COM3_TO_DB 00 00", now=1.1), [])
        events = parser.feed("DB_WAITING_FOR_COM3", now=1.2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "Bestätigung (ACK)")
        self.assertIn("06 03 00 00", events[0].details)

    def test_nak_and_scc_service_are_visible(self) -> None:
        parser = viewer.BridgeLogParser()
        parser.feed("COM3_TO_DB 15", now=1.0)
        self.assertEqual(parser.flush_idle(now=1.6)[0].level, "warning")
        events = parser.feed(
            "DB_SCC_A_TX_SERVICE command=64 vector=65 pc=0007075C"
        )
        self.assertIn("Serieller Kanal A", events[0].title)
        nested = parser.feed(
            "DB_NESTED_SCC_A_TX_SERVICE command=64 pc=0007075C sr=2010"
        )
        self.assertIn("wartenden Timer", nested[0].title)
        uart = parser.feed("DB_NESTED_UART_REPLY pc=000C5D6E pending=4")
        self.assertIn("PC-Antwort", uart[0].title)
        self.assertEqual(
            parser.feed("DB_ACTIVE_BOARD_ISR_SNAPSHOT pc=0004BDBE return=0007511C"),
            [],
        )
        snapshot = parser.feed(
            "DB_ACTIVE_BOARD_ISR_SNAPSHOT pc=0007075C return=000751BA"
        )
        self.assertEqual(snapshot[0].title, "SCC-A-Interrupt aktiv (Diagnose)")

    def test_bounded_timer_clock_is_visible_as_status(self) -> None:
        parser = viewer.BridgeLogParser()
        line = "DB_TIMER_CLOCK source=monotonic-wall batch=4 pending=4 coalesced=100 foreground_ms=1.007"
        events = parser.feed(line)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "Timer-Zeitbasis aktiv; Interrupt-Pakete begrenzt")
        self.assertEqual(events[0].details, line)

    def test_failed_pairing_is_not_labeled_register_override(self) -> None:
        parser = viewer.BridgeLogParser()
        failed = parser.feed(
            "DB_VIRTUAL_MP_CHALLENGE_COMPARE frame_gap_fallback=False "
            "gap=(7, 6) wire_match=False register_override=False "
            "received_before_override=-001 forced_expected=-001 remaining=0"
        )
        self.assertEqual(failed[0].title, "Prüfer-Challenge ohne gültige Antwort")
        self.assertEqual(failed[0].level, "warning")
        error = parser.feed("DB_VIRTUAL_MP_CHALLENGE_FAILED pending=False")
        self.assertIn("fehlgeschlagen", error[0].title)

    def test_expected_com3_disconnect_is_status_not_traceback(self) -> None:
        event = viewer.BridgeLogParser().feed(
            "DB_COM3_DISCONNECTED XP QEMU reset COM3"
        )[0]
        self.assertEqual(event.title, "COM3-Verbindung zum Spiel-PC beendet")
        self.assertEqual(event.level, "normal")


if __name__ == "__main__":
    unittest.main()
